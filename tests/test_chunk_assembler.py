import pytest
from mrag.core.chunking.block_parser import BlockParser
from mrag.core.chunking.chunk_assembler import ChunkAssembler


def parse_and_assemble(text, chunk_size=800, **kwargs):
    blocks = BlockParser().parse(text)
    return ChunkAssembler(chunk_size=chunk_size, **kwargs).assemble(blocks)


class TestTableAtomicity:
    def test_table_not_split_under_limit(self):
        table = "| A | B |\n|---|---|\n" + "\n".join(f"| {i} | {i} |" for i in range(5))
        chunks = parse_and_assemble(table, chunk_size=2000)
        assert all(not c.metadata.get("table_split") for c in chunks)

    def test_table_split_when_oversized(self):
        rows = "\n".join(f"| row{i} | val{i} |" for i in range(50))
        table = f"| A | B |\n|---|---|\n{rows}"
        chunks = parse_and_assemble(table, chunk_size=200)
        split = [c for c in chunks if c.metadata.get("table_split")]
        assert len(split) > 1
        assert all(c.metadata["table_header_repeated"] for c in split)
        ids = {c.metadata["table_id"] for c in split}
        assert len(ids) == 1

    def test_table_columns_in_metadata(self):
        table = "| Name | Value |\n|---|---|\n| a | 1 |"
        chunks = parse_and_assemble(table, chunk_size=2000)
        assert chunks[0].metadata["table_columns"] == ["Name", "Value"]

    def test_table_not_split_when_preserve_false(self):
        rows = "\n".join(f"| row{i} | val{i} |" for i in range(50))
        table = f"| A | B |\n|---|---|\n{rows}"
        chunks = parse_and_assemble(table, chunk_size=200, preserve_tables=False)
        assert all(not c.metadata.get("table_split") for c in chunks)


class TestCodeAtomicity:
    def test_code_not_split_under_limit(self):
        code = "```rust\nfn main() {\n    println!(\"hello\");\n}\n```"
        chunks = parse_and_assemble(code, chunk_size=2000)
        assert len(chunks) == 1
        assert chunks[0].metadata["contains_code"] is True

    def test_code_language_in_metadata(self):
        code = "```python\nprint('hello')\n```"
        chunks = parse_and_assemble(code, chunk_size=2000)
        assert chunks[0].metadata["language"] == "python"

    def test_code_not_split_when_preserve_false(self):
        # With preserve_code_blocks=False the oversized block is NOT split (treated as normal block).
        # With preserve_code_blocks=True it IS split by _split_code, yielding more chunks.
        code = "```python\n" + "x = 1\n" * 100 + "```"
        chunks_preserved = parse_and_assemble(code, chunk_size=200, preserve_code_blocks=True)
        chunks_raw = parse_and_assemble(code, chunk_size=200, preserve_code_blocks=False)
        assert len(chunks_raw) == 1
        assert len(chunks_preserved) > 1


class TestHeadingMetadata:
    def test_heading_path_in_metadata(self):
        text = "# Doc\n## Section\n\nparagraph text"
        chunks = parse_and_assemble(text)
        assert any(c.metadata.get("heading_path_text") == "Doc > Section" for c in chunks)

    def test_section_id_slugified(self):
        text = "# SIM7080G\n## AT Commands\n\ntext"
        chunks = parse_and_assemble(text)
        assert any(c.metadata.get("section_id") == "sim7080g/at-commands" for c in chunks)

    def test_section_title(self):
        text = "# Root\n## Leaf\n\nsome content"
        chunks = parse_and_assemble(text)
        content_chunks = [c for c in chunks if "some content" in c.content]
        assert content_chunks[0].metadata["section_title"] == "Leaf"

    def test_no_heading_doc(self):
        chunks = parse_and_assemble("plain paragraph")
        assert len(chunks) >= 1
        assert not chunks[0].metadata.get("heading_path")

    def test_preserve_heading_path_false(self):
        text = "# Root\n\nparagraph"
        chunks = ChunkAssembler().assemble(
            BlockParser().parse(text),
            preserve_heading_path=False,
        )
        assert not any(c.metadata.get("heading_path") for c in chunks)


class TestGrouping:
    def test_small_blocks_merged(self):
        text = "para one\n\npara two\n\npara three"
        chunks = parse_and_assemble(text, chunk_size=2000)
        assert len(chunks) == 1

    def test_large_text_split_into_multiple(self):
        text = "\n\n".join(f"paragraph {i} " + "x" * 200 for i in range(10))
        chunks = parse_and_assemble(text, chunk_size=300)
        assert len(chunks) > 1

    def test_base_metadata_propagated(self):
        text = "content"
        blocks = BlockParser().parse(text)
        chunks = ChunkAssembler().assemble(blocks, base_metadata={"document_id": "doc1"})
        assert all(c.metadata.get("document_id") == "doc1" for c in chunks)

    def test_chunk_index_sequential(self):
        text = "\n\n".join(f"paragraph {i} " + "x" * 300 for i in range(5))
        chunks = parse_and_assemble(text, chunk_size=400)
        indices = [c.chunk_index for c in chunks]
        assert indices == list(range(len(chunks)))
