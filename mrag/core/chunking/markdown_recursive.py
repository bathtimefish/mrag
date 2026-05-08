import re
from typing import Any

from mrag.core.chunking.base import BaseChunker, ChunkData
from mrag.core.chunking.recursive import _split_text

# Matches ATX headings: # … through ###### …
_HEADING_RE = re.compile(r"^(#{1,6})\s+", re.MULTILINE)


def _split_by_headings(text: str) -> list[str]:
    """Split text at Markdown heading boundaries, keeping the heading with its section."""
    boundaries = [m.start() for m in _HEADING_RE.finditer(text)]
    if not boundaries:
        return [text]

    sections: list[str] = []
    # Text before the first heading (preamble)
    if boundaries[0] > 0:
        preamble = text[: boundaries[0]].strip()
        if preamble:
            sections.append(preamble)

    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(text)
        section = text[start:end].strip()
        if section:
            sections.append(section)

    return sections


class MarkdownRecursiveChunker(BaseChunker):
    """
    Split at Markdown heading boundaries first, then apply recursive splitting
    to any section that still exceeds chunk_size.
    """

    def __init__(self, chunk_size: int = 800, overlap: int = 120) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, text: str, metadata: dict[str, Any]) -> list[ChunkData]:
        sections = _split_by_headings(text)
        pieces: list[str] = []

        for section in sections:
            if len(section) <= self.chunk_size:
                if section.strip():
                    pieces.append(section)
            else:
                pieces.extend(_split_text(section, self.chunk_size, self.overlap))

        return [
            ChunkData(content=piece, chunk_index=i, metadata=dict(metadata))
            for i, piece in enumerate(pieces)
        ]
