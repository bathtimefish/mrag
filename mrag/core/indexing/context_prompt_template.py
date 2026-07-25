"""Default prompt template for contextual augmentation.

The template is written to profiles/context_prompt.txt at `mrag init` time
so users can tune it per project without touching code.

Placeholders (required):
  {document}  — the full (or truncated) document text
  {chunk}     — the chunk to be situated
"""

from string import Formatter

DEFAULT_CONTEXT_PROMPT_TEMPLATE = """\
<document>
{document}
</document>

Here is the chunk we want to situate within the whole document:
<chunk>
{chunk}
</chunk>

Please give a short succinct context to situate this chunk within the overall document \
for the purposes of improving search retrieval of the chunk. \
Answer only with the succinct context and nothing else. \
Always respond in the same language as the document and chunk. \
Prefer specific technical terms, parameter names, identifiers, and concrete values \
over general category descriptions. \
Do not begin your response with self-referential phrases like "This chunk" or "This section"."""


class ContextPromptTemplateError(ValueError):
    """Raised when a contextual augmentation prompt cannot be rendered safely."""


_REQUIRED_PLACEHOLDERS = frozenset({"document", "chunk"})


def validate_context_prompt_template(template: str) -> None:
    """Validate the complete contextual prompt placeholder contract."""
    if not isinstance(template, str) or not template:
        raise ContextPromptTemplateError(
            "Context prompt template must be a non-empty string."
        )

    try:
        parsed = list(Formatter().parse(template))
    except ValueError as exc:
        raise ContextPromptTemplateError(
            "Context prompt template contains malformed braces."
        ) from exc

    placeholders: set[str] = set()
    for _, field_name, format_spec, conversion in parsed:
        if field_name is None:
            continue
        if field_name not in _REQUIRED_PLACEHOLDERS:
            raise ContextPromptTemplateError(
                "Context prompt template contains an unsupported placeholder."
            )
        if format_spec or conversion is not None:
            raise ContextPromptTemplateError(
                "Context prompt placeholders do not support conversions or format specs."
            )
        placeholders.add(field_name)

    if placeholders != _REQUIRED_PLACEHOLDERS:
        raise ContextPromptTemplateError(
            "Context prompt template must contain {document} and {chunk} placeholders."
        )


def render_context_prompt(template: str, *, document: str, chunk: str) -> str:
    """Validate and render one contextual augmentation prompt."""
    validate_context_prompt_template(template)
    return template.format_map({"document": document, "chunk": chunk})


__all__ = [
    "ContextPromptTemplateError",
    "DEFAULT_CONTEXT_PROMPT_TEMPLATE",
    "render_context_prompt",
    "validate_context_prompt_template",
]
