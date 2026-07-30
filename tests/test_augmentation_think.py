"""Tests for augmentation.think and the capability probe that guards it.

Reasoning models spend most of their generation budget on tokens Ollama strips
from the response, so contextual augmentation paid for them and discarded them.
`think` turns that off, but Ollama rejects the parameter for models without the
capability, so it may only be sent after probing.
"""
from unittest.mock import patch

import httpx
import pytest

from mrag.config.profile import (
    AugmentationConfig,
    AugmentationFailurePolicyConfig,
    OllamaRetryConfig,
    ProfileConfig,
)
from mrag.core.indexing.augmentation import generate_context
from mrag.core.ollama_client import model_capabilities, reset_capability_cache


@pytest.fixture(autouse=True)
def _clear_capability_cache():
    reset_capability_cache()
    yield
    reset_capability_cache()


def _config(think: bool = False) -> AugmentationConfig:
    return AugmentationConfig(
        strategy="contextual",
        provider="ollama",
        model="test-model",
        endpoint="http://localhost:11434",
        think=think,
        retry=OllamaRetryConfig(max_attempts=1, initial_delay_seconds=0.0),
        failure_policy=AugmentationFailurePolicyConfig(mode="fail_document"),
    )


def _capture_payload(capabilities: list[str] | None, config: AugmentationConfig) -> dict:
    """Runs one augmentation call and returns the payload sent to Ollama."""
    captured: dict = {}

    def _fake_post(endpoint, path, payload, **kwargs):
        captured.update(payload)
        return {"response": "context"}

    with patch(
        "mrag.core.indexing.augmentation.model_capabilities",
        return_value=frozenset(capabilities or []),
    ):
        with patch("mrag.core.indexing.augmentation.ollama_post", side_effect=_fake_post):
            generate_context("chunk body", "document body", config)
    return captured


# ---------------------------------------------------------------------------
# What gets sent
# ---------------------------------------------------------------------------

class TestRequestPayload:
    def test_thinking_model_receives_think_false_by_default(self):
        payload = _capture_payload(["completion", "thinking"], _config())
        assert payload["think"] is False

    def test_thinking_model_receives_think_true_when_configured(self):
        payload = _capture_payload(["completion", "thinking"], _config(think=True))
        assert payload["think"] is True

    def test_non_thinking_model_receives_no_think_key(self):
        """Ollama errors when `think` reaches a model without the capability."""
        payload = _capture_payload(["completion"], _config())
        assert "think" not in payload

    def test_non_thinking_model_receives_no_think_key_even_when_requested(self):
        payload = _capture_payload(["completion"], _config(think=True))
        assert "think" not in payload

    def test_unknown_capabilities_fall_back_to_omitting_think(self):
        """An older Ollama that reports nothing must keep working."""
        payload = _capture_payload([], _config())
        assert "think" not in payload

    def test_the_rest_of_the_payload_is_unchanged(self):
        payload = _capture_payload(["completion", "thinking"], _config())
        assert payload["model"] == "test-model"
        assert payload["stream"] is False
        assert "chunk body" in payload["prompt"]
        assert "document body" in payload["prompt"]


# ---------------------------------------------------------------------------
# Capability probe
# ---------------------------------------------------------------------------

class TestCapabilityProbe:
    @staticmethod
    def _response(url: str, **kwargs) -> httpx.Response:
        """Builds a response bound to its request so raise_for_status works."""
        return httpx.Response(request=httpx.Request("POST", url), **kwargs)

    def test_reads_capabilities_from_api_show(self):
        seen = []

        def _fake_post(url, **kwargs):
            seen.append((url, kwargs.get("json")))
            return self._response(url, status_code=200, json={"capabilities": ["completion", "thinking"]})

        with patch("httpx.post", side_effect=_fake_post):
            assert "thinking" in model_capabilities("http://localhost:11434", "m")

        assert seen[0][0] == "http://localhost:11434/api/show"
        assert seen[0][1] == {"model": "m"}

    def test_result_is_cached_per_endpoint_and_model(self):
        calls = []

        def _fake_post(url, **kwargs):
            calls.append(url)
            return self._response(url, status_code=200, json={"capabilities": ["thinking"]})

        with patch("httpx.post", side_effect=_fake_post):
            model_capabilities("http://localhost:11434", "m")
            model_capabilities("http://localhost:11434", "m")
            model_capabilities("http://localhost:11434", "other")

        assert len(calls) == 2, "the same model must only be probed once"

    def test_a_failed_probe_reports_no_capabilities(self):
        with patch("httpx.post", side_effect=httpx.ConnectError("down")):
            assert model_capabilities("http://localhost:11434", "m") == frozenset()

    def test_an_error_status_reports_no_capabilities(self):
        def _fake_post(url, **kwargs):
            return self._response(url, status_code=404, json={"error": "not found"})

        with patch("httpx.post", side_effect=_fake_post):
            assert model_capabilities("http://localhost:11434", "m") == frozenset()

    def test_a_non_json_response_reports_no_capabilities(self):
        def _fake_post(url, **kwargs):
            return self._response(url, status_code=200, text="not json")

        with patch("httpx.post", side_effect=_fake_post):
            assert model_capabilities("http://localhost:11434", "m") == frozenset()

    def test_a_probe_failure_does_not_break_augmentation(self):
        captured: dict = {}

        def _fake_post(endpoint, path, payload, **kwargs):
            captured.update(payload)
            return {"response": "context"}

        with patch("httpx.post", side_effect=httpx.ConnectError("down")):
            with patch("mrag.core.indexing.augmentation.ollama_post", side_effect=_fake_post):
                generate_context("chunk", "document", _config())

        assert "think" not in captured
        assert captured["model"] == "test-model"


# ---------------------------------------------------------------------------
# Index identity
# ---------------------------------------------------------------------------

class TestProfileHash:
    def _profile(self, think: bool, strategy: str = "contextual") -> ProfileConfig:
        profile = ProfileConfig(name="default")
        profile.augmentation.strategy = strategy
        profile.augmentation.think = think
        return profile

    def test_changing_think_changes_the_index_identity(self):
        """Thinking changes the generated context, so it changes the index."""
        assert self._profile(False).compute_hash() != self._profile(True).compute_hash()

    def test_the_same_setting_hashes_the_same(self):
        assert self._profile(False).compute_hash() == self._profile(False).compute_hash()

    def test_think_is_ignored_when_augmentation_is_off(self):
        """With strategy `none` no context is generated, so nothing depends on it."""
        assert (
            self._profile(False, strategy="none").compute_hash()
            == self._profile(True, strategy="none").compute_hash()
        )

    def test_default_is_off(self):
        assert AugmentationConfig().think is False
