from typing import Any

from mrag.core.chunking.base import BaseChunker, ChunkData

_SEPARATORS = ["\n\n", "\n", " ", ""]


def _split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Recursively split text using decreasing-priority separators."""
    return _split_with_separators(text, _SEPARATORS, chunk_size, overlap)


def _split_with_separators(
    text: str, separators: list[str], chunk_size: int, overlap: int
) -> list[str]:
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    sep = ""
    remaining = list(separators)
    while remaining:
        sep = remaining.pop(0)
        if sep == "" or sep in text:
            break

    if sep == "":
        # Character-level fallback: slice directly with overlap
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunks.append(text[start:end])
            start += chunk_size - overlap
        return chunks

    parts = text.split(sep)
    chunks: list[str] = []
    current = ""

    for part in parts:
        candidate = (current + sep + part).lstrip(sep) if current else part
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current.strip():
                chunks.append(current)
            if len(part) > chunk_size:
                # Part itself is too big — recurse with next separator
                sub = _split_with_separators(part, remaining or [""], chunk_size, overlap)
                chunks.extend(sub)
                current = ""
            else:
                # Start a new chunk, carrying overlap from the previous one
                if overlap > 0 and current:
                    tail = current[-overlap:]
                    current = (tail + sep + part).lstrip(sep)
                else:
                    current = part

    if current.strip():
        chunks.append(current)

    return chunks


class RecursiveChunker(BaseChunker):
    def __init__(self, chunk_size: int = 800, overlap: int = 120) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, text: str, metadata: dict[str, Any]) -> list[ChunkData]:
        pieces = _split_text(text, self.chunk_size, self.overlap)
        return [
            ChunkData(content=piece, chunk_index=i, metadata=dict(metadata))
            for i, piece in enumerate(pieces)
        ]
