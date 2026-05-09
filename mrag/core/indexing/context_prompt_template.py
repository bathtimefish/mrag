"""Default prompt template for contextual augmentation.

The template is written to profiles/context_prompt.txt at `mrag init` time
so users can tune it per project without touching code.

Placeholders (required):
  {document}  — the full (or truncated) document text
  {chunk}     — the chunk to be situated
"""

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
