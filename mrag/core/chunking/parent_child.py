import uuid
from typing import Any

from mrag.core.chunking.base import BaseChunker, ChunkData
from mrag.core.chunking.recursive import _split_text  # 内部 API — recursive.py 変更時は合わせて確認


class ParentChildChunker(BaseChunker):
    def __init__(
        self,
        parent_chunk_size: int = 3000,
        child_chunk_size: int = 600,
        child_overlap: int = 100,
        parent_strategy: str = "fixed_size",
    ) -> None:
        self.parent_chunk_size = parent_chunk_size
        self.child_chunk_size = child_chunk_size
        self.child_overlap = child_overlap
        self.parent_strategy = parent_strategy

    def chunk(self, text: str, metadata: dict[str, Any]) -> list[ChunkData]:
        if not text.strip():
            return []
        if self.parent_strategy == "fixed_size":
            return self._chunk_fixed_size(text, metadata)
        raise ValueError(f"Unsupported parent_strategy: {self.parent_strategy}")

    def _chunk_fixed_size(self, text: str, metadata: dict[str, Any]) -> list[ChunkData]:
        parent_texts = _split_text(text, self.parent_chunk_size, overlap=0)
        all_chunks: list[ChunkData] = []
        global_idx = 0

        for parent_text in parent_texts:
            parent_hint = str(uuid.uuid4())  # temporary ID resolved by pipeline
            parent_chunk = ChunkData(
                content=parent_text,
                chunk_index=global_idx,
                chunk_type="parent",
                parent_chunk_id=None,
                metadata={**metadata, "_parent_id_hint": parent_hint},  # hint を後置して確実に優先させる
            )
            all_chunks.append(parent_chunk)
            global_idx += 1

            child_texts = _split_text(parent_text, self.child_chunk_size, self.child_overlap)
            for child_text in child_texts:
                child_chunk = ChunkData(
                    content=child_text,
                    chunk_index=global_idx,
                    chunk_type="child",
                    parent_chunk_id=parent_hint,  # pipeline resolves to real UUID
                    metadata=dict(metadata),
                )
                all_chunks.append(child_chunk)
                global_idx += 1

        return all_chunks
