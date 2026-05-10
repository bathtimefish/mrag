from typing import Any

from mrag.core.chunking.base import BaseChunker, ChunkData
from mrag.core.chunking.block_parser import BlockParser
from mrag.core.chunking.chunk_assembler import ChunkAssembler


class BlockAwareChunker(BaseChunker):
    """
    Markdown-aware chunker using Block Parser + Chunk Assembler.
    Strategy name: 'block_aware'. source_format should be 'markdown'.
    Preserves tables, fenced code blocks, and heading section metadata.
    """

    def __init__(
        self,
        chunk_size: int = 800,
        overlap: int = 120,
        preserve_heading_path: bool = True,
        preserve_tables: bool = True,
        preserve_code_blocks: bool = True,
    ) -> None:
        self._parser = BlockParser()
        self._assembler = ChunkAssembler(
            chunk_size=chunk_size,
            overlap=overlap,
            preserve_tables=preserve_tables,
            preserve_code_blocks=preserve_code_blocks,
        )
        self.preserve_heading_path = preserve_heading_path

    def chunk(self, text: str, metadata: dict[str, Any]) -> list[ChunkData]:
        blocks = self._parser.parse(text)
        if not blocks:
            return []
        return self._assembler.assemble(
            blocks,
            base_metadata=metadata,
            preserve_heading_path=self.preserve_heading_path,
        )
