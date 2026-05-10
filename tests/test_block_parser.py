import pytest
from mrag.core.chunking.block_parser import (
    BLOCK_CODE,
    BLOCK_HEADING,
    BLOCK_HR,
    BLOCK_LIST,
    BLOCK_PARAGRAPH,
    BLOCK_TABLE,
    BlockParser,
)


class TestHeadingStack:
    def test_h1_h2_h3(self):
        text = "# A\n## B\n### C\ntext"
        blocks = BlockParser().parse(text)
        para = [b for b in blocks if b.block_type == BLOCK_PARAGRAPH][0]
        assert para.heading_path == ["A", "B", "C"]

    def test_stack_rewind_on_higher_heading(self):
        text = "# A\n## B\n## C\ntext"
        blocks = BlockParser().parse(text)
        para = [b for b in blocks if b.block_type == BLOCK_PARAGRAPH][0]
        assert para.heading_path == ["A", "C"]

    def test_h1_resets_all(self):
        text = "# A\n## B\n# X\ntext"
        blocks = BlockParser().parse(text)
        para = [b for b in blocks if b.block_type == BLOCK_PARAGRAPH][0]
        assert para.heading_path == ["X"]

    def test_no_heading(self):
        blocks = BlockParser().parse("plain text")
        assert blocks[0].heading_path == []

    def test_heading_block_has_correct_level(self):
        text = "## Section"
        blocks = BlockParser().parse(text)
        assert blocks[0].block_type == BLOCK_HEADING
        assert blocks[0].heading_level == 2


class TestTableDetection:
    def test_simple_table(self):
        text = "| A | B |\n|---|---|\n| 1 | 2 |"
        blocks = BlockParser().parse(text)
        assert len(blocks) == 1
        assert blocks[0].block_type == BLOCK_TABLE
        assert blocks[0].metadata["columns"] == ["A", "B"]

    def test_table_with_surrounding_text(self):
        text = "intro\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\noutro"
        blocks = BlockParser().parse(text)
        types = [b.block_type for b in blocks]
        assert BLOCK_TABLE in types

    def test_no_false_positive_without_separator(self):
        text = "| A | B |\n| 1 | 2 |"
        blocks = BlockParser().parse(text)
        assert all(b.block_type != BLOCK_TABLE for b in blocks)

    def test_table_inherits_heading_path(self):
        text = "# Doc\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        blocks = BlockParser().parse(text)
        table = [b for b in blocks if b.block_type == BLOCK_TABLE][0]
        assert table.heading_path == ["Doc"]


class TestCodeBlockDetection:
    def test_fenced_with_language(self):
        text = "```rust\nfn main() {}\n```"
        blocks = BlockParser().parse(text)
        assert blocks[0].block_type == BLOCK_CODE
        assert blocks[0].metadata["language"] == "rust"

    def test_fenced_no_language(self):
        text = "```\ncode\n```"
        blocks = BlockParser().parse(text)
        assert blocks[0].block_type == BLOCK_CODE
        assert blocks[0].metadata["language"] == ""

    def test_unclosed_fence(self):
        text = "```python\ncode here"
        blocks = BlockParser().parse(text)
        assert blocks[0].block_type == BLOCK_CODE

    def test_code_after_heading(self):
        text = "# Guide\n\n```bash\necho hello\n```"
        blocks = BlockParser().parse(text)
        code = [b for b in blocks if b.block_type == BLOCK_CODE][0]
        assert code.heading_path == ["Guide"]


class TestMiscBlocks:
    def test_horizontal_rule(self):
        blocks = BlockParser().parse("---")
        assert blocks[0].block_type == BLOCK_HR

    def test_list_item(self):
        text = "- item one\n- item two"
        blocks = BlockParser().parse(text)
        assert blocks[0].block_type == BLOCK_LIST

    def test_block_ids_are_sequential(self):
        text = "# H\nparagraph\n\n| A |\n|---|\n| 1 |"
        blocks = BlockParser().parse(text)
        ids = [b.block_id for b in blocks]
        assert ids == [f"blk_{i:04d}" for i in range(len(ids))]

    def test_empty_text(self):
        assert BlockParser().parse("") == []

    def test_whitespace_only(self):
        assert BlockParser().parse("   \n\n   ") == []
