"""Tests for contextual augmentation against a server that refuses large prompts.

The document excerpt is capped in characters while a server refuses prompts by
tokens, so a prompt can be refused well inside the cap. A refused prompt used to
be retried unchanged — the same failure every time — and the chunk then fell
back to raw. The refusal is now not retried, and the excerpt is halved instead.
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
from mrag.core.indexing.augmentation import augment_chunks, generate_context
from mrag.core.ollama_client import OllamaInputTooLargeError, ollama_post

# The body a llama.cpp server behind Ollama sends for a prompt over its batch.
_TOO_LARGE_BODY = (
    '{"error":"input (2123 tokens) is too large to process. '
    'increase the physical batch size (current batch size: 2048)"}'
)

# Renders the excerpt alone before the separator, so a test can measure it.
_TEMPLATE = "{document}\x1f{chunk}"


def _config(mode: str = "raw_fallback", max_attempts: int = 3) -> AugmentationConfig:
    return AugmentationConfig(
        strategy="contextual",
        provider="ollama",
        model="test-model",
        endpoint="http://localhost:11434",
        retry=OllamaRetryConfig(max_attempts=max_attempts, initial_delay_seconds=0.0),
        failure_policy=AugmentationFailurePolicyConfig(mode=mode),
    )


def _response(url: str, status_code: int, text: str) -> httpx.Response:
    """Builds a response bound to its request so raise_for_status works."""
    return httpx.Response(status_code, text=text, request=httpx.Request("POST", url))


def _server(accepts_up_to: int | None):
    """A fake ollama_post that refuses any excerpt longer than `accepts_up_to`.

    Returns the fake and the list of excerpt lengths it was sent, in order.
    `None` refuses everything.
    """
    sent: list[int] = []

    def _fake_post(endpoint, path, payload, **kwargs):
        excerpt = payload["prompt"].split("\x1f", 1)[0]
        sent.append(len(excerpt))
        if accepts_up_to is None or len(excerpt) > accepts_up_to:
            raise OllamaInputTooLargeError(f"Ollama returned HTTP 500: {_TOO_LARGE_BODY}")
        return {"response": "context"}

    return _fake_post, sent


def _generate(full_text: str, fake_post, config=None, on_retry=None) -> str:
    with patch(
        "mrag.core.indexing.augmentation.model_capabilities",
        return_value=frozenset(),
    ):
        with patch("mrag.core.indexing.augmentation.ollama_post", side_effect=fake_post):
            return generate_context(
                "chunk body",
                full_text,
                config or _config(),
                prompt_template=_TEMPLATE,
                on_retry=on_retry,
            )


# ---------------------------------------------------------------------------
# The client: a refusal is not retried
# ---------------------------------------------------------------------------

class TestClientRefusal:
    def test_a_too_large_refusal_is_raised_at_once(self):
        calls = []

        def _fake_post(url, **kwargs):
            calls.append(url)
            return _response(url, 500, _TOO_LARGE_BODY)

        with patch("httpx.post", side_effect=_fake_post), patch("time.sleep") as sleep:
            with pytest.raises(OllamaInputTooLargeError, match="too large to process"):
                ollama_post("http://localhost:11434", "/api/generate", {}, max_attempts=3)

        assert len(calls) == 1, "the same prompt cannot succeed on a second attempt"
        sleep.assert_not_called()

    def test_the_refusal_is_recognised_under_a_client_error_status_too(self):
        def _fake_post(url, **kwargs):
            return _response(url, 400, _TOO_LARGE_BODY)

        with patch("httpx.post", side_effect=_fake_post):
            with pytest.raises(OllamaInputTooLargeError):
                ollama_post("http://localhost:11434", "/api/generate", {}, max_attempts=3)

    def test_the_refusal_is_still_a_runtime_error_for_other_callers(self):
        assert issubclass(OllamaInputTooLargeError, RuntimeError)

    def test_any_other_server_error_is_still_retried(self):
        calls = []

        def _fake_post(url, **kwargs):
            calls.append(url)
            return _response(url, 500, '{"error":"model runner crashed"}')

        with patch("httpx.post", side_effect=_fake_post), patch("time.sleep"):
            with pytest.raises(RuntimeError) as raised:
                ollama_post("http://localhost:11434", "/api/generate", {}, max_attempts=3)

        assert len(calls) == 3
        assert not isinstance(raised.value, OllamaInputTooLargeError)


# ---------------------------------------------------------------------------
# Augmentation: the excerpt is halved
# ---------------------------------------------------------------------------

class TestExcerptShortening:
    def test_an_accepting_server_gets_one_request_with_the_full_excerpt(self):
        fake_post, sent = _server(accepts_up_to=10_000)
        assert _generate("x" * 20_000, fake_post) == "context"
        assert sent == [8000], "a server that accepts must see exactly the earlier request"

    def test_a_refused_excerpt_is_halved_until_it_fits(self):
        fake_post, sent = _server(accepts_up_to=2000)
        assert _generate("x" * 20_000, fake_post) == "context"
        assert sent == [8000, 4000, 2000]

    def test_each_shortening_is_reported_through_on_retry(self):
        fake_post, _ = _server(accepts_up_to=2000)
        reported = []
        _generate(
            "x" * 20_000,
            fake_post,
            on_retry=lambda attempt, total, exc: reported.append((attempt, total, str(exc))),
        )
        assert [(attempt, total) for attempt, total, _ in reported] == [(1, 4), (2, 4)]
        assert "retrying with 4000" in reported[0][2]
        assert "retrying with 2000" in reported[1][2]

    def test_a_short_document_starts_from_its_own_length(self):
        fake_post, sent = _server(accepts_up_to=None)
        with pytest.raises(OllamaInputTooLargeError):
            _generate("x" * 3000, fake_post)
        assert sent == [3000, 1500]

    def test_it_gives_up_at_the_floor_and_says_so(self):
        fake_post, sent = _server(accepts_up_to=None)
        with pytest.raises(OllamaInputTooLargeError) as raised:
            _generate("x" * 20_000, fake_post)
        assert sent == [8000, 4000, 2000, 1000]
        # The fallback log shows the first 120 characters of the reason.
        assert str(raised.value)[:120].startswith(
            "still too large with a 1000-character document excerpt"
        )

    def test_other_failures_are_not_answered_by_shortening(self):
        sent = []

        def _fake_post(endpoint, path, payload, **kwargs):
            sent.append(payload)
            raise RuntimeError("Ollama request to /api/generate failed after 3 attempts")

        with pytest.raises(RuntimeError, match="failed after 3 attempts"):
            _generate("x" * 20_000, _fake_post)
        assert len(sent) == 1


# ---------------------------------------------------------------------------
# The failure policy still decides what an unanswerable chunk becomes
# ---------------------------------------------------------------------------

class TestFailurePolicy:
    def test_a_chunk_refused_at_every_length_falls_back_to_raw(self):
        fake_post, _ = _server(accepts_up_to=None)
        fallbacks = []
        with patch(
            "mrag.core.indexing.augmentation.model_capabilities",
            return_value=frozenset(),
        ):
            with patch("mrag.core.indexing.augmentation.ollama_post", side_effect=fake_post):
                results = augment_chunks(
                    [ChunkData(content="chunk body", chunk_index=0)],
                    "x" * 20_000,
                    _config(mode="raw_fallback"),
                    prompt_template=_TEMPLATE,
                    on_chunk_fallback=lambda cur, total, exc: fallbacks.append(exc),
                )
        assert results == [None]
        assert isinstance(fallbacks[0], OllamaInputTooLargeError)

    def test_fail_document_still_raises(self):
        fake_post, _ = _server(accepts_up_to=None)
        with patch(
            "mrag.core.indexing.augmentation.model_capabilities",
            return_value=frozenset(),
        ):
            with patch("mrag.core.indexing.augmentation.ollama_post", side_effect=fake_post):
                with pytest.raises(OllamaInputTooLargeError):
                    augment_chunks(
                        [ChunkData(content="chunk body", chunk_index=0)],
                        "x" * 20_000,
                        _config(mode="fail_document"),
                        prompt_template=_TEMPLATE,
                    )
