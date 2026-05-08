from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ChunkData:
    content: str
    chunk_index: int
    chunk_type: str = "chunk"
    parent_chunk_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseChunker(ABC):
    @abstractmethod
    def chunk(self, text: str, metadata: dict[str, Any]) -> list[ChunkData]: ...


def get_chunker(strategy: str, chunk_size: int, overlap: int) -> "BaseChunker":
    if strategy == "recursive":
        from mrag.core.chunking.recursive import RecursiveChunker
        return RecursiveChunker(chunk_size=chunk_size, overlap=overlap)
    if strategy == "markdown_recursive":
        from mrag.core.chunking.markdown_recursive import MarkdownRecursiveChunker
        return MarkdownRecursiveChunker(chunk_size=chunk_size, overlap=overlap)
    raise ValueError(f"Unsupported chunking strategy: {strategy}")
