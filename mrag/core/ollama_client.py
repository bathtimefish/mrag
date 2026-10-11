"""Shared Ollama HTTP client with retry and exponential backoff.

All Ollama API calls in mrag go through ollama_post() so that transient
server-side failures (timeouts, HTTP 5xx, empty responses) are retried in
one place rather than duplicated across embedding and augmentation layers.

Retry policy:
  - httpx.TimeoutException  → retried (transient load / model stall)
  - HTTP 500/502/503/504    → retried (transient server error)
  - RuntimeError from validate() → retried (e.g. empty Ollama response)
  - httpx.ConnectError      → NOT retried; raised as ConnectionError immediately
  - HTTP 4xx                → NOT retried; raised as RuntimeError immediately
  - "too large to process"  → NOT retried, whatever the status; raised as
                              OllamaInputTooLargeError immediately
  - "exceed_context_size_error" → NOT retried, whatever the status; raised as
                              OllamaPromptExceedsWindowError immediately
"""
from __future__ import annotations

import re
import time
from typing import Callable

import httpx

_RETRYABLE_HTTP_CODES = {500, 502, 503, 504}

# A llama.cpp server refuses a prompt that does not fit its physical batch with
# "input (N tokens) is too large to process. increase the physical batch size",
# and Ollama passes the message on. Observed from Ollama 0.33.2 serving
# gemma4:e2b with a 2,048-token batch; Ollama 0.34.0 on Apple Silicon accepted
# 14,621 tokens, so whether a prompt is refused depends on the server.
_INPUT_TOO_LARGE_MARKER = "too large to process"


class OllamaInputTooLargeError(RuntimeError):
    """The server refused the prompt as larger than it can process at once.

    Never retried: the same prompt fails the same way every time, so retrying
    only spends the backoff. A caller that can send less catches this, and
    every other caller still sees a RuntimeError.
    """


# Asked with `truncate: false`, Ollama refuses a prompt longer than its context
# window instead of keeping the prompt's end and answering HTTP 200, which
# silently drops the opening. Observed from Ollama 0.34.4: HTTP 400 with
# `"type":"exceed_context_size_error","n_prompt_tokens":N,"n_ctx":W` inside
# the error text, after tokenizing alone (about 3 ms).
_PROMPT_EXCEEDS_WINDOW_MARKER = "exceed_context_size_error"


def _count_after(text: str, field: str) -> int | None:
    """The number following `field` in a refusal body, which nests JSON as text."""
    match = re.search(rf'{field}\\?"?\s*:\s*(\d+)', text)
    return int(match.group(1)) if match else None


class OllamaPromptExceedsWindowError(RuntimeError):
    """The server refused the prompt as longer than its context window.

    Never retried: the same prompt fails the same way every time. Unlike
    OllamaInputTooLargeError it is not a reason to send less: shortening the
    excerpt would buy a context written from less of the document. The token
    counts are the server's, or None when its message does not carry them.
    """

    def __init__(
        self,
        message: str,
        prompt_tokens: int | None = None,
        window_tokens: int | None = None,
    ) -> None:
        super().__init__(message)
        self.prompt_tokens = prompt_tokens
        self.window_tokens = window_tokens

# Model capabilities never change while a process runs, and augmentation asks
# once per chunk, so the answer is cached per (endpoint, model) rather than
# re-probed hundreds of times.
_CAPABILITY_CACHE: dict[tuple[str, str], frozenset[str]] = {}


def model_capabilities(endpoint: str, model: str, timeout: float = 10.0) -> frozenset[str]:
    """Return the capabilities Ollama reports for a model, or empty on failure.

    Used to decide whether a request may carry parameters that only some models
    accept — notably `think`, which Ollama rejects for models without the
    "thinking" capability. Failure returns an empty set so callers fall back to
    the plain request rather than breaking on an older Ollama that does not
    report capabilities at all.
    """
    key = (endpoint.rstrip("/"), model)
    cached = _CAPABILITY_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        response = httpx.post(
            f"{key[0]}/api/show", json={"model": model}, timeout=timeout
        )
        response.raise_for_status()
        capabilities = frozenset(response.json().get("capabilities") or [])
    except (httpx.HTTPError, ValueError):
        capabilities = frozenset()

    _CAPABILITY_CACHE[key] = capabilities
    return capabilities


def reset_capability_cache() -> None:
    """Clear the memoized capability lookups (tests and long-lived servers)."""
    _CAPABILITY_CACHE.clear()


def probe_connection(endpoint: str, timeout: float = 10.0) -> None:
    """Verify the Ollama endpoint is reachable. Raises ConnectionError if not.

    Uses GET /api/version — a lightweight read-only endpoint that does not
    require a model to be loaded.
    """
    url = f"{endpoint.rstrip('/')}/api/version"
    try:
        httpx.get(url, timeout=timeout)
    except httpx.ConnectError as exc:
        raise ConnectionError(
            f"Cannot connect to Ollama at {endpoint}. "
            "Is Ollama running? (ollama serve)"
        ) from exc


def ollama_post(
    endpoint: str,
    path: str,
    payload: dict,
    *,
    timeout: float = 120.0,
    max_attempts: int = 3,
    initial_delay: float = 2.0,
    backoff_multiplier: float = 2.0,
    max_delay: float = 30.0,
    validate: Callable[[dict], None] | None = None,
    on_retry: Callable[[int, int, Exception], None] | None = None,
) -> dict:
    """POST to an Ollama API endpoint with retry and exponential backoff.

    Args:
        endpoint:           Base URL, e.g. "http://localhost:11434".
        path:               API path, e.g. "/api/generate".
        payload:            JSON body dict.
        timeout:            Per-request timeout in seconds.
        max_attempts:       Total number of attempts (1 = no retry).
        initial_delay:      Seconds to wait before the second attempt.
        backoff_multiplier: Multiplier applied to delay on each successive retry.
        max_delay:          Upper bound on inter-attempt delay.
        validate:           Optional callable receiving the parsed response dict.
                            Should raise RuntimeError if the response is invalid.
                            Validation failures are treated as retryable.
        on_retry:           Called just before sleeping between attempts with
                            (attempt_that_failed, max_attempts, exception).

    Returns:
        Parsed JSON response dict.

    Raises:
        ConnectionError: Ollama is not reachable (ConnectError — not retried).
        OllamaInputTooLargeError: The server refused the prompt as too large
                        (not retried).
        OllamaPromptExceedsWindowError: The server refused the prompt as
                        longer than its context window (not retried).
        RuntimeError:   Non-retryable HTTP error or retry exhaustion.
    """
    url = f"{endpoint.rstrip('/')}{path}"
    last_exc: Exception = RuntimeError("No attempts made")

    for attempt in range(1, max_attempts + 1):
        try:
            resp = httpx.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if validate is not None:
                validate(data)
            return data

        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"Cannot connect to Ollama at {endpoint}. "
                "Is Ollama running? (ollama serve)"
            ) from exc

        except httpx.HTTPStatusError as exc:
            if _PROMPT_EXCEEDS_WINDOW_MARKER in exc.response.text:
                raise OllamaPromptExceedsWindowError(
                    f"Ollama returned HTTP {exc.response.status_code}: "
                    f"{exc.response.text}",
                    prompt_tokens=_count_after(exc.response.text, "n_prompt_tokens"),
                    window_tokens=_count_after(exc.response.text, "n_ctx"),
                ) from exc
            if _INPUT_TOO_LARGE_MARKER in exc.response.text.lower():
                raise OllamaInputTooLargeError(
                    f"Ollama returned HTTP {exc.response.status_code}: "
                    f"{exc.response.text}"
                ) from exc
            if exc.response.status_code not in _RETRYABLE_HTTP_CODES:
                raise RuntimeError(
                    f"Ollama returned HTTP {exc.response.status_code}: "
                    f"{exc.response.text}"
                ) from exc
            last_exc = exc

        except (httpx.TimeoutException, RuntimeError) as exc:
            last_exc = exc

        if attempt < max_attempts:
            delay = min(
                initial_delay * (backoff_multiplier ** (attempt - 1)),
                max_delay,
            )
            if on_retry is not None:
                on_retry(attempt, max_attempts, last_exc)
            time.sleep(delay)

    raise RuntimeError(
        f"Ollama request to {path} failed after {max_attempts} attempts: {last_exc}"
    ) from last_exc
