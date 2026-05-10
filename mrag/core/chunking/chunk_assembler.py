from __future__ import annotations

import re
import uuid
from typing import Any

from mrag.core.chunking.base import ChunkData
from mrag.core.chunking.block_parser import ATOMIC_TYPES, BLOCK_CODE, BLOCK_TABLE, BlockData


def _make_section_id(heading_path: list[str]) -> str:
    parts = [re.sub(r"[^\w]+", "-", h.lower()).strip("-") for h in heading_path]
    return "/".join(parts)


class ChunkAssembler:
    def __init__(
        self,
        chunk_size: int = 800,
        overlap: int = 120,
        preserve_tables: bool = True,
        preserve_code_blocks: bool = True,
    ) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.preserve_tables = preserve_tables
        self.preserve_code_blocks = preserve_code_blocks

    def assemble(
        self,
        blocks: list[BlockData],
        base_metadata: dict[str, Any] | None = None,
        preserve_heading_path: bool = True,
    ) -> list[ChunkData]:
        base_metadata = base_metadata or {}
        result: list[ChunkData] = []
        group: list[BlockData] = []
        chunk_idx = 0

        def flush_group() -> None:
            nonlocal chunk_idx, group
            if not group:
                return
            chunk = self._group_to_chunk(group, chunk_idx, base_metadata, preserve_heading_path)
            result.append(chunk)
            chunk_idx += 1
            group = []

        for block in blocks:
            is_atomic = block.block_type in ATOMIC_TYPES and (
                (block.block_type == BLOCK_TABLE and self.preserve_tables)
                or (block.block_type == BLOCK_CODE and self.preserve_code_blocks)
            )
            block_len = len(block.content)

            if is_atomic:
                if block_len <= self.chunk_size:
                    group_len = sum(len(b.content) for b in group)
                    if group and group_len + 1 + block_len > self.chunk_size:
                        flush_group()
                    group.append(block)
                else:
                    flush_group()
                    split_chunks = self._split_atomic(block, chunk_idx, base_metadata, preserve_heading_path)
                    result.extend(split_chunks)
                    chunk_idx += len(split_chunks)
            else:
                group_len = sum(len(b.content) for b in group)
                if group and group_len + 1 + block_len > self.chunk_size:
                    flush_group()
                group.append(block)

        flush_group()
        return result

    def _group_to_chunk(
        self,
        group: list[BlockData],
        chunk_idx: int,
        base_metadata: dict[str, Any],
        preserve_heading_path: bool,
    ) -> ChunkData:
        content = "\n\n".join(b.content for b in group)
        block_types = sorted({b.block_type for b in group})
        contains_table = any(b.block_type == BLOCK_TABLE for b in group)
        contains_code  = any(b.block_type == BLOCK_CODE for b in group)
        table_columns: list[str] = []
        for b in group:
            if b.block_type == BLOCK_TABLE:
                table_columns = b.metadata.get("columns", [])
                break
        language = next(
            (b.metadata.get("language") for b in group if b.block_type == BLOCK_CODE),
            None,
        )

        heading_path = group[-1].heading_path if group else []
        heading_path_text = " > ".join(heading_path) if heading_path else ""
        section_id = _make_section_id(heading_path) if heading_path else ""
        section_title = heading_path[-1] if heading_path else ""

        metadata: dict[str, Any] = dict(base_metadata)
        if preserve_heading_path and heading_path:
            metadata.update({
                "heading_path": heading_path,
                "heading_path_text": heading_path_text,
                "section_title": section_title,
                "section_id": section_id,
                "section_start_line": group[0].start_line,
                "section_end_line": group[-1].end_line,
            })
        metadata.update({
            "block_types": block_types,
            "contains_table": contains_table,
            "contains_code": contains_code,
        })
        if contains_table:
            metadata["table_count"] = sum(1 for b in group if b.block_type == BLOCK_TABLE)
            metadata["table_columns"] = table_columns
            metadata["table_split"] = False
        if contains_code and language is not None:
            metadata["language"] = language

        return ChunkData(
            content=content,
            chunk_index=chunk_idx,
            chunk_type="chunk",
            parent_chunk_id=None,
            metadata=metadata,
        )

    def _split_atomic(
        self,
        block: BlockData,
        start_idx: int,
        base_metadata: dict[str, Any],
        preserve_heading_path: bool,
    ) -> list[ChunkData]:
        if block.block_type == BLOCK_TABLE:
            return self._split_table(block, start_idx, base_metadata, preserve_heading_path)
        else:
            return self._split_code(block, start_idx, base_metadata, preserve_heading_path)

    def _split_table(
        self,
        block: BlockData,
        start_idx: int,
        base_metadata: dict[str, Any],
        preserve_heading_path: bool,
    ) -> list[ChunkData]:
        lines = block.content.splitlines()
        if len(lines) < 3:
            return [self._group_to_chunk([block], start_idx, base_metadata, preserve_heading_path)]

        header_line = lines[0]
        sep_line    = lines[1]
        data_lines  = lines[2:]
        header_block = header_line + "\n" + sep_line
        columns = [c.strip() for c in header_line.split("|") if c.strip()]
        table_id = f"tbl_{uuid.uuid4().hex[:8]}"

        parts: list[list[str]] = []
        current: list[str] = []
        for data_line in data_lines:
            candidate = header_block + "\n" + "\n".join(current + [data_line])
            if current and len(candidate) > self.chunk_size:
                parts.append(current)
                current = [data_line]
            else:
                current.append(data_line)
        if current:
            parts.append(current)

        total = len(parts)
        heading_path = block.heading_path
        heading_path_text = " > ".join(heading_path) if heading_path else ""

        chunks: list[ChunkData] = []
        for part_idx, rows in enumerate(parts, 1):
            label = (
                f"[Table: {heading_path_text} ({part_idx}/{total})]"
                if heading_path_text
                else f"[Table ({part_idx}/{total})]"
            )
            content = label + "\n\n" + header_block + "\n" + "\n".join(rows)
            metadata: dict[str, Any] = dict(base_metadata)
            if preserve_heading_path and heading_path:
                metadata["heading_path"] = heading_path
                metadata["heading_path_text"] = heading_path_text
                metadata["section_id"] = _make_section_id(heading_path)
            metadata.update({
                "block_types": [BLOCK_TABLE],
                "contains_table": True,
                "contains_code": False,
                "table_count": 1,
                "table_columns": columns,
                "table_split": True,
                "table_id": table_id,
                "table_part": part_idx,
                "table_parts": total,
                "table_header_repeated": True,
            })
            chunks.append(ChunkData(
                content=content,
                chunk_index=start_idx + part_idx - 1,
                chunk_type="chunk",
                parent_chunk_id=None,
                metadata=metadata,
            ))
        return chunks

    def _split_code(
        self,
        block: BlockData,
        start_idx: int,
        base_metadata: dict[str, Any],
        preserve_heading_path: bool,
    ) -> list[ChunkData]:
        from mrag.core.chunking.recursive import _split_text

        lines = block.content.splitlines()
        fence_open  = lines[0] if lines else ""
        fence_close = lines[-1] if len(lines) > 1 else "```"
        code_body   = "\n".join(lines[1:-1]) if len(lines) > 2 else ""

        available_size = max(self.chunk_size - len(fence_open) - len(fence_close) - 2, 200)
        pieces = _split_text(code_body, available_size, 0)

        language = block.metadata.get("language", "")
        heading_path = block.heading_path
        heading_path_text = " > ".join(heading_path) if heading_path else ""

        chunks: list[ChunkData] = []
        for i, piece in enumerate(pieces):
            content = f"```{language}\n{piece}\n```"
            metadata: dict[str, Any] = dict(base_metadata)
            if preserve_heading_path and heading_path:
                metadata["heading_path"] = heading_path
                metadata["heading_path_text"] = heading_path_text
                metadata["section_id"] = _make_section_id(heading_path)
            metadata.update({
                "block_types": [BLOCK_CODE],
                "contains_table": False,
                "contains_code": True,
                "language": language,
            })
            chunks.append(ChunkData(
                content=content,
                chunk_index=start_idx + i,
                chunk_type="chunk",
                parent_chunk_id=None,
                metadata=metadata,
            ))
        return chunks
