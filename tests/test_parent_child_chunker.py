"""Tests for ParentChildChunker (Step 9 — V03 parent-child)."""
import pytest

from mrag.core.chunking.parent_child import ParentChildChunker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chunker(**kwargs) -> ParentChildChunker:
    return ParentChildChunker(
        parent_chunk_size=kwargs.get("parent_chunk_size", 200),
        child_chunk_size=kwargs.get("child_chunk_size", 80),
        child_overlap=kwargs.get("child_overlap", 10),
    )


def _long_text(n_chars: int) -> str:
    word = "word "
    return (word * (n_chars // len(word) + 1))[:n_chars]


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------

def test_chunk_returns_list():
    chunker = _make_chunker()
    result = chunker.chunk("Some text.", {})
    assert isinstance(result, list)


def test_empty_text_returns_empty():
    chunker = _make_chunker()
    assert chunker.chunk("", {}) == []
    assert chunker.chunk("   ", {}) == []


def test_chunk_types_are_parent_and_child():
    chunker = _make_chunker()
    result = chunker.chunk(_long_text(300), {})
    types = {c.chunk_type for c in result}
    assert "parent" in types
    assert "child" in types


def test_short_text_produces_one_parent_and_children():
    chunker = _make_chunker(parent_chunk_size=500)
    text = _long_text(100)
    result = chunker.chunk(text, {})
    parents = [c for c in result if c.chunk_type == "parent"]
    assert len(parents) == 1


def test_long_text_produces_multiple_parents():
    chunker = _make_chunker(parent_chunk_size=100, child_chunk_size=40, child_overlap=5)
    text = _long_text(350)
    result = chunker.chunk(text, {})
    parents = [c for c in result if c.chunk_type == "parent"]
    assert len(parents) >= 2


def test_every_parent_has_children():
    chunker = _make_chunker(parent_chunk_size=200, child_chunk_size=60, child_overlap=10)
    text = _long_text(500)
    result = chunker.chunk(text, {})
    parents = [c for c in result if c.chunk_type == "parent"]
    children = [c for c in result if c.chunk_type == "child"]
    assert len(parents) >= 1
    assert len(children) >= len(parents)


# ---------------------------------------------------------------------------
# Parent-hint mechanism
# ---------------------------------------------------------------------------

def test_parent_has_parent_id_hint_in_metadata():
    chunker = _make_chunker()
    result = chunker.chunk(_long_text(300), {})
    parents = [c for c in result if c.chunk_type == "parent"]
    for p in parents:
        assert "_parent_id_hint" in p.metadata


def test_child_parent_chunk_id_matches_hint():
    chunker = _make_chunker(parent_chunk_size=200, child_chunk_size=60, child_overlap=10)
    text = _long_text(300)
    result = chunker.chunk(text, {})

    hints = {c.metadata["_parent_id_hint"] for c in result if c.chunk_type == "parent"}
    for child in (c for c in result if c.chunk_type == "child"):
        assert child.parent_chunk_id in hints


def test_hints_are_unique_per_parent():
    chunker = _make_chunker(parent_chunk_size=100, child_chunk_size=40, child_overlap=5)
    text = _long_text(400)
    result = chunker.chunk(text, {})
    hints = [c.metadata["_parent_id_hint"] for c in result if c.chunk_type == "parent"]
    assert len(hints) == len(set(hints))


# ---------------------------------------------------------------------------
# Metadata passthrough
# ---------------------------------------------------------------------------

def test_metadata_passed_to_children():
    chunker = _make_chunker()
    meta = {"source": "doc.pdf", "page": 3}
    result = chunker.chunk(_long_text(300), meta)
    for child in (c for c in result if c.chunk_type == "child"):
        assert child.metadata.get("source") == "doc.pdf"
        assert child.metadata.get("page") == 3


def test_hint_overrides_caller_hint_in_metadata():
    """If caller passes _parent_id_hint in metadata, chunker's own hint wins."""
    chunker = _make_chunker()
    meta = {"_parent_id_hint": "caller-hint"}
    result = chunker.chunk(_long_text(200), meta)
    parents = [c for c in result if c.chunk_type == "parent"]
    for p in parents:
        assert p.metadata["_parent_id_hint"] != "caller-hint"


# ---------------------------------------------------------------------------
# Chunk indexes
# ---------------------------------------------------------------------------

def test_chunk_indexes_are_globally_sequential():
    chunker = _make_chunker(parent_chunk_size=200, child_chunk_size=60, child_overlap=10)
    text = _long_text(400)
    result = chunker.chunk(text, {})
    indexes = [c.chunk_index for c in result]
    assert indexes == list(range(len(result)))


# ---------------------------------------------------------------------------
# Unsupported strategy
# ---------------------------------------------------------------------------

def test_unsupported_parent_strategy_raises():
    chunker = ParentChildChunker(parent_strategy="section")
    with pytest.raises(ValueError, match="Unsupported parent_strategy"):
        chunker.chunk("some text", {})
