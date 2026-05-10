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


def get_chunker(
    strategy: str,
    chunk_size: int,
    overlap: int,
    preserve_heading_path: bool = True,
    preserve_tables: bool = True,
    preserve_code_blocks: bool = True,
    source_format: str = "text",
    parent_config=None,   # ParentConfig | None — used only for parent_child strategy
    child_config=None,    # ChildConfig | None — used only for parent_child strategy
) -> "BaseChunker":
    """Return a chunker for the given strategy.

    When ``source_format="markdown"`` and at least one preserve option is True,
    the returned chunker is automatically wrapped with ``BlockAwareWrapper`` so
    that tables, code blocks, and heading-path metadata are handled correctly
    regardless of the underlying strategy.

    The ``block_aware`` strategy name is kept for backward compatibility and is
    equivalent to ``strategy="recursive"`` with all preserve options enabled and
    ``source_format="markdown"``.
    """
    # ------------------------------------------------------------------ #
    # Build the inner (strategy-specific) chunker                          #
    # ------------------------------------------------------------------ #
    if strategy == "block_aware":
        # Alias: recursive inner + force block-aware wrapping below
        from mrag.core.chunking.recursive import RecursiveChunker
        inner: BaseChunker = RecursiveChunker(chunk_size=chunk_size, overlap=overlap)
        _needs_wrap = True
    elif strategy == "recursive":
        from mrag.core.chunking.recursive import RecursiveChunker
        inner = RecursiveChunker(chunk_size=chunk_size, overlap=overlap)
        _needs_wrap = False
    elif strategy == "markdown_recursive":
        from mrag.core.chunking.markdown_recursive import MarkdownRecursiveChunker
        inner = MarkdownRecursiveChunker(chunk_size=chunk_size, overlap=overlap)
        _needs_wrap = False
    elif strategy == "parent_child":
        from mrag.core.chunking.parent_child import ParentChildChunker
        p = parent_config
        c = child_config
        inner = ParentChildChunker(
            parent_chunk_size=p.max_chars if p else 3000,
            child_chunk_size=c.chunk_size if c else 600,
            child_overlap=c.overlap if c else 100,
            parent_strategy=p.strategy if p else "fixed_size",
        )
        _needs_wrap = False
    else:
        raise ValueError(f"Unsupported chunking strategy: {strategy}")

    # ------------------------------------------------------------------ #
    # Apply BlockAwareWrapper when markdown source + any preserve option   #
    # ------------------------------------------------------------------ #
    needs_block_wrap = _needs_wrap or (
        source_format == "markdown"
        and (preserve_tables or preserve_code_blocks or preserve_heading_path)
    )
    if needs_block_wrap:
        from mrag.core.chunking.block_aware import BlockAwareWrapper
        return BlockAwareWrapper(
            inner_chunker=inner,
            chunk_size=chunk_size,
            overlap=overlap,
            preserve_tables=preserve_tables,
            preserve_code_blocks=preserve_code_blocks,
            preserve_heading_path=preserve_heading_path,
        )

    return inner
