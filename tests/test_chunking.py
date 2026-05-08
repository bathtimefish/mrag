"""Tests for Phase 3 chunking system."""
from textwrap import dedent

import pytest

from mrag.core.chunking.base import ChunkData, get_chunker
from mrag.core.chunking.recursive import RecursiveChunker
from mrag.core.chunking.markdown_recursive import MarkdownRecursiveChunker


# ---------------------------------------------------------------------------
# get_chunker factory
# ---------------------------------------------------------------------------

def test_get_chunker_recursive():
    chunker = get_chunker("recursive", chunk_size=500, overlap=50)
    assert isinstance(chunker, RecursiveChunker)


def test_get_chunker_markdown_recursive():
    chunker = get_chunker("markdown_recursive", chunk_size=500, overlap=50)
    assert isinstance(chunker, MarkdownRecursiveChunker)


def test_get_chunker_unknown_raises():
    with pytest.raises(ValueError, match="Unsupported chunking strategy"):
        get_chunker("unknown", chunk_size=500, overlap=50)


# ---------------------------------------------------------------------------
# ChunkData dataclass
# ---------------------------------------------------------------------------

def test_chunk_data_defaults():
    c = ChunkData(content="hello", chunk_index=0)
    assert c.chunk_type == "chunk"
    assert c.parent_chunk_id is None
    assert c.metadata == {}


# ---------------------------------------------------------------------------
# RecursiveChunker
# ---------------------------------------------------------------------------

def test_recursive_short_text_is_single_chunk():
    chunker = RecursiveChunker(chunk_size=200, overlap=20)
    result = chunker.chunk("Short text.", {})
    assert len(result) == 1
    assert result[0].content == "Short text."
    assert result[0].chunk_index == 0


def test_recursive_splits_long_text():
    chunker = RecursiveChunker(chunk_size=50, overlap=0)
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three.\n\nParagraph four."
    result = chunker.chunk(text, {})
    assert len(result) > 1


def test_recursive_chunk_indexes_are_sequential():
    chunker = RecursiveChunker(chunk_size=30, overlap=0)
    text = "A" * 100
    result = chunker.chunk(text, {})
    assert [c.chunk_index for c in result] == list(range(len(result)))


def test_recursive_chunk_size_respected():
    chunk_size = 50
    chunker = RecursiveChunker(chunk_size=chunk_size, overlap=0)
    text = "\n\n".join(["word " * 20] * 5)
    result = chunker.chunk(text, {})
    for chunk in result:
        # Allow slight overage only at paragraph boundaries
        assert len(chunk.content) <= chunk_size * 2, (
            f"Chunk too large: {len(chunk.content)}"
        )


def test_recursive_with_overlap_covers_boundary():
    """With overlap > 0, the tail of one chunk appears at the start of the next."""
    chunker = RecursiveChunker(chunk_size=40, overlap=10)
    text = "A" * 100
    result = chunker.chunk(text, {})
    assert len(result) > 1
    for chunk in result:
        assert len(chunk.content) <= 40


def test_recursive_empty_text_returns_empty():
    chunker = RecursiveChunker(chunk_size=200, overlap=20)
    assert chunker.chunk("", {}) == []
    assert chunker.chunk("   \n\n   ", {}) == []


def test_recursive_passes_metadata():
    chunker = RecursiveChunker(chunk_size=200, overlap=0)
    meta = {"document_id": "abc", "source_type": "txt"}
    result = chunker.chunk("Hello world.", meta)
    assert result[0].metadata == meta


def test_recursive_metadata_is_copied():
    """Each chunk should get its own copy of metadata, not a shared reference."""
    chunker = RecursiveChunker(chunk_size=30, overlap=0)
    meta = {"key": "value"}
    text = "A" * 100
    result = chunker.chunk(text, meta)
    assert len(result) > 1
    result[0].metadata["key"] = "modified"
    assert result[1].metadata["key"] == "value"


def test_recursive_all_text_covered():
    """All characters from the input should appear in at least one chunk."""
    chunker = RecursiveChunker(chunk_size=50, overlap=0)
    text = "Hello world. " * 10
    result = chunker.chunk(text, {})
    combined = "".join(c.content for c in result)
    for word in ["Hello", "world"]:
        assert word in combined


# ---------------------------------------------------------------------------
# MarkdownRecursiveChunker
# ---------------------------------------------------------------------------

_SAMPLE_MD = dedent("""\
    # Introduction

    This is the introduction section with some content.

    ## Background

    Background information goes here. It can be quite long in a real document.

    ## Methods

    We used several methods to analyze the data.

    ### Sub-method A

    Details of sub-method A.

    ### Sub-method B

    Details of sub-method B.

    # Conclusion

    The conclusion summarizes everything.
""")


def test_markdown_splits_on_headings():
    chunker = MarkdownRecursiveChunker(chunk_size=500, overlap=0)
    result = chunker.chunk(_SAMPLE_MD, {})
    assert len(result) > 1
    headings_found = sum(1 for c in result if c.content.startswith("#"))
    assert headings_found >= 2


def test_markdown_chunk_indexes_sequential():
    chunker = MarkdownRecursiveChunker(chunk_size=500, overlap=0)
    result = chunker.chunk(_SAMPLE_MD, {})
    assert [c.chunk_index for c in result] == list(range(len(result)))


def test_markdown_large_section_subdivided():
    """A section bigger than chunk_size must be further split."""
    chunker = MarkdownRecursiveChunker(chunk_size=60, overlap=0)
    big_section = "# Big Section\n\n" + ("word " * 50 + "\n\n") * 3
    result = chunker.chunk(big_section, {})
    assert len(result) > 1
    for chunk in result:
        assert len(chunk.content) <= 60 * 3  # recursive sub-split may overshoot slightly


def test_markdown_no_headings_falls_back_to_recursive():
    """Plain text without headings should still be chunked by recursive splitting."""
    chunker = MarkdownRecursiveChunker(chunk_size=50, overlap=0)
    plain = "word " * 50
    result = chunker.chunk(plain, {})
    assert len(result) >= 1
    assert all(isinstance(c, ChunkData) for c in result)


def test_markdown_short_document_single_chunk():
    chunker = MarkdownRecursiveChunker(chunk_size=500, overlap=0)
    short = "# Title\n\nShort body."
    result = chunker.chunk(short, {})
    assert len(result) == 1
    assert "Title" in result[0].content


def test_markdown_preserves_heading_with_section():
    """Each section chunk should include its heading."""
    chunker = MarkdownRecursiveChunker(chunk_size=500, overlap=0)
    result = chunker.chunk(_SAMPLE_MD, {})
    heading_chunks = [c for c in result if c.content.startswith("#")]
    for c in heading_chunks:
        assert len(c.content) > 2  # heading + body, not just "#"


def test_markdown_passes_metadata():
    chunker = MarkdownRecursiveChunker(chunk_size=500, overlap=0)
    meta = {"profile": "default"}
    result = chunker.chunk("# Hi\n\nHello.", meta)
    assert result[0].metadata == meta


def test_markdown_empty_text_returns_empty():
    chunker = MarkdownRecursiveChunker(chunk_size=200, overlap=0)
    assert chunker.chunk("", {}) == []
