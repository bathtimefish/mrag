from typing import Any

from mrag.core.chunking.base import BaseChunker, ChunkData
from mrag.core.chunking.block_parser import BlockParser
from mrag.core.chunking.chunk_assembler import ChunkAssembler


class BlockAwareWrapper(BaseChunker):
    """
    Wraps any BaseChunker with Markdown block-aware preprocessing.

    Behaviour
    ---------
    * Tables and code blocks are kept intact (or split row-by-row for oversized
      tables) with heading-path metadata injected.
    * Contiguous text blocks are batched and handed to the *inner* chunker for
      splitting, so the chunking strategy (recursive, parent_child, …) still
      governs how text is divided.
    * Heading-path metadata is injected into every returned chunk when
      ``preserve_heading_path=True``.

    This class is instantiated automatically by ``get_chunker()`` whenever
    ``source_format="markdown"`` is combined with any preserve option.  The
    ``block_aware`` strategy name remains supported as an alias for
    ``RecursiveChunker`` wrapped by this class.
    """

    def __init__(
        self,
        inner_chunker: BaseChunker,
        chunk_size: int = 800,
        overlap: int = 120,
        preserve_tables: bool = True,
        preserve_code_blocks: bool = True,
        preserve_heading_path: bool = True,
    ) -> None:
        self._inner = inner_chunker
        self._parser = BlockParser()
        self._assembler = ChunkAssembler(
            chunk_size=chunk_size,
            overlap=overlap,
            preserve_tables=preserve_tables,
            preserve_code_blocks=preserve_code_blocks,
        )
        self._preserve_heading_path = preserve_heading_path

    def chunk(self, text: str, metadata: dict[str, Any]) -> list[ChunkData]:
        blocks = self._parser.parse(text)
        if not blocks:
            return []
        return self._assembler.assemble(
            blocks,
            base_metadata=metadata,
            preserve_heading_path=self._preserve_heading_path,
            text_chunker=self._inner,
        )


class BlockAwareChunker(BlockAwareWrapper):
    """
    Backward-compatible alias: ``strategy: block_aware``.

    Equivalent to ``BlockAwareWrapper(RecursiveChunker(...), ...)``.
    Direct instantiation and the ``block_aware`` strategy name both continue to
    work unchanged.
    """

    def __init__(
        self,
        chunk_size: int = 800,
        overlap: int = 120,
        preserve_heading_path: bool = True,
        preserve_tables: bool = True,
        preserve_code_blocks: bool = True,
    ) -> None:
        from mrag.core.chunking.recursive import RecursiveChunker
        super().__init__(
            inner_chunker=RecursiveChunker(chunk_size=chunk_size, overlap=overlap),
            chunk_size=chunk_size,
            overlap=overlap,
            preserve_tables=preserve_tables,
            preserve_code_blocks=preserve_code_blocks,
            preserve_heading_path=preserve_heading_path,
        )
