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
"""
from __future__ import annotations

import time
from typing import Callable

import httpx

_RETRYABLE_HTTP_CODES = {500, 502, 503, 504}


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
