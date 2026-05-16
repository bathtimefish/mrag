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
        parent_texts = self._split_into_parents(text)
        return self._build_parent_child(parent_texts, metadata)

    def _split_into_parents(self, text: str) -> list[str]:
        if self.parent_strategy == "fixed_size":
            return _split_text(text, self.parent_chunk_size, overlap=0)
        if self.parent_strategy == "section":
            return self._split_section_parents(text)
        raise ValueError(f"Unsupported parent_strategy: {self.parent_strategy}")

    def _split_section_parents(self, text: str) -> list[str]:
        """Split parents at Markdown heading boundaries.

        Each Markdown section (heading + content until next heading) becomes one
        parent. Sections exceeding ``parent_chunk_size`` are recursively split
        with ``_split_text``. Documents with no headings degrade naturally to
        ``fixed_size`` behaviour (the entire document is treated as one section).
        """
        from mrag.core.chunking.markdown_recursive import _split_by_headings

        sections = _split_by_headings(text)
        parents: list[str] = []
        for section in sections:
            if not section.strip():
                continue
            if len(section) <= self.parent_chunk_size:
                parents.append(section)
            else:
                parents.extend(_split_text(section, self.parent_chunk_size, overlap=0))
        return parents

    def _build_parent_child(
        self, parent_texts: list[str], metadata: dict[str, Any]
    ) -> list[ChunkData]:
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
