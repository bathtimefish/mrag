"""Contextual augmentation prompt validation and identity tests."""

from unittest.mock import patch

import pytest

from mrag.config.profile import AugmentationConfig, ProfileConfig
from mrag.core.indexing.augmentation import generate_context
from mrag.core.indexing.context_prompt_template import (
    ContextPromptTemplateError,
    DEFAULT_CONTEXT_PROMPT_TEMPLATE,
    render_context_prompt,
    validate_context_prompt_template,
)
from mrag.core.indexing.pipeline import _load_context_prompt


def test_default_and_literal_braces_render_exactly():
    validate_context_prompt_template(DEFAULT_CONTEXT_PROMPT_TEMPLATE)

    template = 'JSON {{"scope": "fixture"}}\ndoc={document}\nchunk={chunk}'
    assert render_context_prompt(
        template,
        document="document text",
        chunk="chunk text",
    ) == 'JSON {"scope": "fixture"}\ndoc=document text\nchunk=chunk text'


@pytest.mark.parametrize(
    "template",
    [
        "",
        "   ",
        "{document}",
        "{chunk}",
        "{document} {unknown} {chunk}",
        "{document.name} {chunk}",
        "{document!r} {chunk}",
        "{document} {chunk:>20}",
        "{document} {chunk",
        "{} {document} {chunk}",
    ],
)
def test_invalid_templates_are_rejected(template):
    with pytest.raises(ContextPromptTemplateError):
        validate_context_prompt_template(template)


def test_generate_context_uses_the_exact_template_and_rejects_empty_before_http():
    template = "document={document}\nchunk={chunk}"
    captured = {}

    def fake_post(endpoint, path, payload, **kwargs):
        captured.update(payload)
        return {"response": "  generated context  "}

    with patch(
        "mrag.core.indexing.augmentation.ollama_post",
        side_effect=fake_post,
    ) as post:
        result = generate_context(
            "chunk text",
            "document text",
            AugmentationConfig(),
            prompt_template=template,
        )
        assert result == "generated context"
        assert captured["prompt"] == "document=document text\nchunk=chunk text"

        with pytest.raises(ContextPromptTemplateError):
            generate_context(
                "chunk text",
                "document text",
                AugmentationConfig(),
                prompt_template="",
            )
        assert post.call_count == 1


def test_pipeline_loader_defaults_only_when_file_is_absent(tmp_path):
    assert _load_context_prompt(tmp_path) == DEFAULT_CONTEXT_PROMPT_TEMPLATE

    profiles = tmp_path / "profiles"
    profiles.mkdir()
    prompt = profiles / "context_prompt.txt"
    prompt.write_text("", encoding="utf-8")
    with pytest.raises(ContextPromptTemplateError):
        _load_context_prompt(tmp_path)

    custom = "doc={document}\nchunk={chunk}"
    prompt.write_text(custom, encoding="utf-8")
    assert _load_context_prompt(tmp_path) == custom


def test_contextual_identity_hashes_the_same_validated_template_that_is_rendered():
    profile = ProfileConfig(
        name="contextual",
        augmentation={"strategy": "contextual"},
    )
    first = "first={document}\nchunk={chunk}"
    second = "second={document}\nchunk={chunk}"

    assert profile.compute_hash(context_prompt=first) != profile.compute_hash(
        context_prompt=second
    )
    for invalid in ("", "{document}", "{chunk}"):
        with pytest.raises(ContextPromptTemplateError):
            profile.compute_hash(context_prompt=invalid)
