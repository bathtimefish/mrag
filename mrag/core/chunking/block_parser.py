from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

BLOCK_HEADING    = "heading"
BLOCK_PARAGRAPH  = "paragraph"
BLOCK_LIST       = "list"
BLOCK_TABLE      = "table"
BLOCK_CODE       = "code_block"
BLOCK_BLOCKQUOTE = "blockquote"
BLOCK_HR         = "horizontal_rule"

ATOMIC_TYPES = frozenset({BLOCK_TABLE, BLOCK_CODE})

_HEADING_RE  = re.compile(r"^(#{1,6})\s+(.*)")
_TABLE_ROW   = re.compile(r"^\|.+\|")
_TABLE_SEP   = re.compile(r"^\|[\s\-:|]+\|")
_FENCE_OPEN  = re.compile(r"^(`{3,})(\w*)")
_FENCE_CLOSE = re.compile(r"^`{3,}\s*$")
_LIST_ITEM   = re.compile(r"^(\s*)([-*+]|\d+\.)\s")
_BLOCKQUOTE  = re.compile(r"^>")
_HR          = re.compile(r"^(\*{3,}|-{3,}|_{3,})\s*$")


@dataclass
class BlockData:
    block_id: str
    block_type: str
    content: str
    start_line: int
    end_line: int
    heading_path: list[str]
    heading_level: int
    metadata: dict[str, Any] = field(default_factory=dict)


class BlockParser:
    def parse(self, text: str) -> list[BlockData]:
        lines = text.splitlines()
        self._blocks: list[BlockData] = []
        self._blk_counter = 0
        self._heading_stack: list[tuple[int, str]] = []

        i = 0
        while i < len(lines):
            m = _FENCE_OPEN.match(lines[i])
            if m:
                fence_char = m.group(1)
                language = m.group(2)
                i, block = self._read_code_block(lines, i, fence_char, language)
                self._blocks.append(block)
                continue

            if _TABLE_ROW.match(lines[i]) and self._is_table_start(lines, i):
                i, block = self._read_table(lines, i)
                self._blocks.append(block)
                continue

            m = _HEADING_RE.match(lines[i])
            if m:
                level = len(m.group(1))
                title = m.group(2).strip()
                self._update_heading_stack(level, title)
                block = BlockData(
                    block_id=self._next_id(),
                    block_type=BLOCK_HEADING,
                    content=lines[i],
                    start_line=i,
                    end_line=i,
                    heading_path=self._current_path(),
                    heading_level=level,
                )
                self._blocks.append(block)
                i += 1
                continue

            if _HR.match(lines[i]):
                block = BlockData(
                    block_id=self._next_id(),
                    block_type=BLOCK_HR,
                    content=lines[i],
                    start_line=i,
                    end_line=i,
                    heading_path=self._current_path(),
                    heading_level=0,
                )
                self._blocks.append(block)
                i += 1
                continue

            if lines[i].strip():
                i, block = self._read_paragraph(lines, i)
                self._blocks.append(block)
                continue

            i += 1

        return self._blocks

    def _update_heading_stack(self, level: int, title: str) -> None:
        self._heading_stack = [(l, t) for l, t in self._heading_stack if l < level]
        self._heading_stack.append((level, title))

    def _current_path(self) -> list[str]:
        return [t for _, t in self._heading_stack]

    def _is_table_start(self, lines: list[str], i: int) -> bool:
        if i + 1 >= len(lines):
            return False
        return bool(_TABLE_SEP.match(lines[i + 1]))

    def _read_table(self, lines: list[str], start: int) -> tuple[int, BlockData]:
        header_cells = [c.strip() for c in lines[start].split("|") if c.strip()]
        i = start
        while i < len(lines) and _TABLE_ROW.match(lines[i]):
            i += 1
        content = "\n".join(lines[start:i])
        return i, BlockData(
            block_id=self._next_id(),
            block_type=BLOCK_TABLE,
            content=content,
            start_line=start,
            end_line=i - 1,
            heading_path=self._current_path(),
            heading_level=0,
            metadata={
                "table_format": "markdown_pipe",
                "columns": header_cells,
            },
        )

    def _read_code_block(
        self, lines: list[str], start: int, fence: str, language: str
    ) -> tuple[int, BlockData]:
        i = start + 1
        while i < len(lines):
            if lines[i].startswith(fence) and _FENCE_CLOSE.match(lines[i]):
                i += 1
                break
            i += 1
        content = "\n".join(lines[start:i])
        return i, BlockData(
            block_id=self._next_id(),
            block_type=BLOCK_CODE,
            content=content,
            start_line=start,
            end_line=i - 1,
            heading_path=self._current_path(),
            heading_level=0,
            metadata={"language": language},
        )

    def _read_paragraph(self, lines: list[str], start: int) -> tuple[int, BlockData]:
        block_type = self._infer_paragraph_type(lines[start])
        collected = [lines[start]]
        i = start + 1
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                break
            if _HEADING_RE.match(line):
                break
            if _FENCE_OPEN.match(line):
                break
            if _TABLE_ROW.match(line) and self._is_table_start(lines, i):
                break
            collected.append(line)
            i += 1
        content = "\n".join(collected)
        return i, BlockData(
            block_id=self._next_id(),
            block_type=block_type,
            content=content,
            start_line=start,
            end_line=i - 1,
            heading_path=self._current_path(),
            heading_level=0,
        )

    def _infer_paragraph_type(self, line: str) -> str:
        if _BLOCKQUOTE.match(line):
            return BLOCK_BLOCKQUOTE
        if _LIST_ITEM.match(line):
            return BLOCK_LIST
        return BLOCK_PARAGRAPH

    def _next_id(self) -> str:
        blk_id = f"blk_{self._blk_counter:04d}"
        self._blk_counter += 1
        return blk_id
