"""What a text is embedded as under the profile's task prefixes.

Some embedding models were trained with a task prefix on each input and lose
accuracy without it: EmbeddingGemma 2 expects ``task: search result | query: ``
before a query and ``title: {title} | text: `` before a document. The two
prefixes are profile settings (``embedding.query_prefix`` and
``embedding.document_prefix``); unset, a text is embedded exactly as written.
Only the embedding input changes: keyword search, stored variants and results
keep the text without a prefix.
"""

import unicodedata
from pathlib import PurePath

from mrag.config.profile import EMBEDDING_PREFIX_TITLE

# The model card's word for a document without a title.
UNTITLED = "none"


def query_input(query: str, prefix: str | None) -> str:
    """The text a search query is embedded as."""
    return query if prefix is None else prefix + query


def document_title(filename: str | None) -> str:
    """The title a document's chunks are embedded under: its file name without
    the extension, normalized as the chunk text is, or ``none`` without one."""
    name = PurePath(filename).name if filename else ""
    stem = name.rsplit(".", 1)[0].strip()
    return unicodedata.normalize("NFKC", stem) if stem else UNTITLED


def document_input(text: str, prefix: str | None, title: str) -> str:
    """The text a chunk is embedded as; ``title`` fills ``{title}``."""
    if prefix is None:
        return text
    return prefix.replace(EMBEDDING_PREFIX_TITLE, title, 1) + text
