"""Tests for contextual augmentation against a prompt longer than the window.

Ollama keeps the end of a prompt longer than `num_ctx` and answers HTTP 200, so
the document excerpt — the prompt's opening — was dropped and the context was
written from the chunk alone, with nothing reported. With a window configured,
the request now asks the server to refuse such a prompt, and the chunk follows
the failure policy with its excerpt left whole.
"""
from unittest.mock import patch

import httpx
import pytest

from mrag.config.profile import (
    AugmentationConfig,
    AugmentationFailurePolicyConfig,
    OllamaRetryConfig,
)
from mrag.core.chunking.base import ChunkData
from mrag.core.indexing.augmentation import augment_chunks
from mrag.core.ollama_client import (
    OllamaInputTooLargeError,
    OllamaPromptExceedsWindowError,
    ollama_post,
)

# Ollama 0.34.4's answer to a 3,009-token prompt with `num_ctx` 2,048 and
# `truncate: false`, verbatim.
_EXCEEDS_WINDOW_BODY = (
    '{"error":"{\\"error\\":{\\"code\\":400,\\"message\\":\\"request (3009 tokens) '
    'exceeds the available context size (2048 tokens), try increasing it\\",'
    '\\"type\\":\\"exceed_context_size_error\\",\\"n_prompt_tokens\\":3009,'
    '\\"n_ctx\\":2048}}"}'
)

# Renders the excerpt alone before the separator, so a test can measure it.
_TEMPLATE = "{document}\x1f{chunk}"


def _config(mode: str = "raw_fallback", window: int | None = 2048) -> AugmentationConfig:
    return AugmentationConfig(
        strategy="contextual",
        provider="ollama",
        model="test-model",
        endpoint="http://localhost:11434",
        context_window_tokens=window,
        retry=OllamaRetryConfig(max_attempts=3, initial_delay_seconds=0.0),
        failure_policy=AugmentationFailurePolicyConfig(mode=mode),
    )


def _response(url: str, status_code: int, text: str) -> httpx.Response:
    """Builds a response bound to its request so raise_for_status works."""
    return httpx.Response(status_code, text=text, request=httpx.Request("POST", url))


def _augment(config: AugmentationConfig, fake_post, **callbacks):
    with patch(
        "mrag.core.indexing.augmentation.model_capabilities",
        return_value=frozenset(),
    ):
        with patch("mrag.core.indexing.augmentation.ollama_post", side_effect=fake_post):
            return augment_chunks(
                [ChunkData(content="chunk body", chunk_index=0)],
                "x" * 20_000,
                config,
                prompt_template=_TEMPLATE,
                **callbacks,
            )


def _refusing_server():
    """A fake ollama_post that refuses every prompt as past the window.

    Returns the fake and the payloads it was sent, in order.
    """
    sent: list[dict] = []

    def _fake_post(endpoint, path, payload, **kwargs):
        sent.append(payload)
        raise OllamaPromptExceedsWindowError(
            f"Ollama returned HTTP 400: {_EXCEEDS_WINDOW_BODY}",
            prompt_tokens=3009,
            window_tokens=2048,
        )

    return _fake_post, sent


# ---------------------------------------------------------------------------
# The request: the server is asked to refuse rather than cut
# ---------------------------------------------------------------------------

class TestRequest:
    @staticmethod
    def _payload(config: AugmentationConfig) -> dict:
        sent = []

        def _fake_post(endpoint, path, payload, **kwargs):
            sent.append(payload)
            return {"response": "context"}

        _augment(config, _fake_post)
        return sent[0]

    def test_a_window_asks_the_server_to_refuse_a_longer_prompt(self):
        assert self._payload(_config(window=2048))["truncate"] is False

    def test_a_profile_without_a_window_keeps_its_request(self):
        assert "truncate" not in self._payload(_config(window=None))


# ---------------------------------------------------------------------------
# The client: the refusal is recognised, counted and not retried
# ---------------------------------------------------------------------------

class TestClientRefusal:
    def test_the_refusal_is_raised_at_once_with_the_servers_counts(self):
        calls = []

        def _fake_post(url, **kwargs):
            calls.append(url)
            return _response(url, 400, _EXCEEDS_WINDOW_BODY)

        with patch("httpx.post", side_effect=_fake_post), patch("time.sleep") as sleep:
            with pytest.raises(OllamaPromptExceedsWindowError) as raised:
                ollama_post("http://localhost:11434", "/api/generate", {}, max_attempts=3)

        assert len(calls) == 1, "the same prompt cannot succeed on a second attempt"
        sleep.assert_not_called()
        assert raised.value.prompt_tokens == 3009
        assert raised.value.window_tokens == 2048

    def test_a_refusal_without_counts_is_still_recognised(self):
        def _fake_post(url, **kwargs):
            return _response(url, 400, '{"error":"exceed_context_size_error"}')

        with patch("httpx.post", side_effect=_fake_post):
            with pytest.raises(OllamaPromptExceedsWindowError) as raised:
                ollama_post("http://localhost:11434", "/api/generate", {}, max_attempts=3)

        assert raised.value.prompt_tokens is None
        assert raised.value.window_tokens is None

    def test_it_is_not_a_reason_to_send_less(self):
        assert not issubclass(OllamaPromptExceedsWindowError, OllamaInputTooLargeError)
        assert issubclass(OllamaPromptExceedsWindowError, RuntimeError)


# ---------------------------------------------------------------------------
# Augmentation: the excerpt is kept and the failure policy decides
# ---------------------------------------------------------------------------

class TestFailurePolicy:
    def test_a_refused_chunk_falls_back_to_raw_with_its_excerpt_never_shortened(self):
        fake_post, sent = _refusing_server()
        retries, fallbacks = [], []
        results = _augment(
            _config(mode="raw_fallback"),
            fake_post,
            on_chunk_retry=lambda *args: retries.append(args),
            on_chunk_fallback=lambda cur, total, exc: fallbacks.append(exc),
        )

        assert results == [None]
        assert [len(p["prompt"].split("\x1f", 1)[0]) for p in sent] == [8000]
        assert retries == []
        assert isinstance(fallbacks[0], OllamaPromptExceedsWindowError)
        # The fallback log shows the first 120 characters of the reason.
        assert str(fallbacks[0])[:120].startswith(
            "3009-token prompt exceeds augmentation.context_window_tokens (2048)"
        )

    def test_fail_document_still_raises(self):
        fake_post, _ = _refusing_server()
        with pytest.raises(OllamaPromptExceedsWindowError) as raised:
            _augment(_config(mode="fail_document"), fake_post)
        assert raised.value.prompt_tokens == 3009
