import pytest
from mrag.core.chunking.block_aware import BlockAwareChunker


class TestBlockAwareChunker:
    def test_empty_text(self):
        assert BlockAwareChunker().chunk("", {}) == []

    def test_no_heading_doc(self):
        chunks = BlockAwareChunker().chunk("plain paragraph", {})
        assert len(chunks) >= 1
        assert not chunks[0].metadata.get("heading_path")

    def test_table_preserved(self):
        text = (
            "# Manual\n## Commands\n\n"
            "| Cmd | Desc |\n|---|---|\n"
            "| AT | Test |\n\nmore text"
        )
        chunks = BlockAwareChunker(chunk_size=2000).chunk(text, {})
        table_chunks = [c for c in chunks if c.metadata.get("contains_table")]
        assert table_chunks

    def test_code_preserved(self):
        text = "# Guide\n\n```python\nprint('hello')\n```\n\nafter"
        chunks = BlockAwareChunker(chunk_size=2000).chunk(text, {})
        code_chunks = [c for c in chunks if c.metadata.get("contains_code")]
        assert code_chunks

    def test_heading_path_propagated(self):
        text = "# Root\n## Sub\n### Leaf\n\ncontent here"
        chunks = BlockAwareChunker().chunk(text, {})
        content_chunks = [c for c in chunks if "content here" in c.content]
        assert content_chunks
        assert content_chunks[0].metadata["heading_path"] == ["Root", "Sub", "Leaf"]

    def test_base_metadata_in_output(self):
        text = "# H\n\nparagraph"
        chunks = BlockAwareChunker().chunk(text, {"document_id": "doc42"})
        assert all(c.metadata.get("document_id") == "doc42" for c in chunks)

    def test_section_id_generated(self):
        text = "# Root\n## Sub Section\n\ntext"
        chunks = BlockAwareChunker().chunk(text, {})
        text_chunks = [c for c in chunks if c.metadata.get("section_id")]
        assert any(c.metadata["section_id"] == "root/sub-section" for c in text_chunks)

    def test_section_display_text(self):
        text = "# Doc\n## Commands\n\nsome content"
        chunks = BlockAwareChunker().chunk(text, {})
        content_chunks = [c for c in chunks if "some content" in c.content]
        assert content_chunks[0].metadata["heading_path_text"] == "Doc > Commands"

    def test_multi_document_independence(self):
        text_a = "# A\n\nfoo"
        text_b = "# B\n\nbar"
        chunker = BlockAwareChunker()
        chunks_a = chunker.chunk(text_a, {})
        chunks_b = chunker.chunk(text_b, {})
        paths_a = [c.metadata.get("heading_path") for c in chunks_a if c.metadata.get("heading_path")]
        paths_b = [c.metadata.get("heading_path") for c in chunks_b if c.metadata.get("heading_path")]
        assert all("A" in p for p in paths_a)
        assert all("B" in p for p in paths_b)
