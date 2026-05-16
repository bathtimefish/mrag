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
    chunker = ParentChildChunker(parent_strategy="unknown")
    with pytest.raises(ValueError, match="Unsupported parent_strategy"):
        chunker.chunk("some text", {})


# ---------------------------------------------------------------------------
# parent_strategy: "section"
# ---------------------------------------------------------------------------

def _make_section_chunker(**kwargs) -> ParentChildChunker:
    return ParentChildChunker(
        parent_chunk_size=kwargs.get("parent_chunk_size", 500),
        child_chunk_size=kwargs.get("child_chunk_size", 80),
        child_overlap=kwargs.get("child_overlap", 10),
        parent_strategy="section",
    )


def test_section_strategy_splits_by_headings():
    """Each Markdown heading section becomes one parent."""
    text = (
        "# Section A\n\nContent of A.\n\n"
        "# Section B\n\nContent of B.\n\n"
        "# Section C\n\nContent of C."
    )
    chunker = _make_section_chunker(parent_chunk_size=500)
    result = chunker.chunk(text, {})
    parents = [c for c in result if c.chunk_type == "parent"]
    assert len(parents) == 3
    assert "Section A" in parents[0].content
    assert "Section B" in parents[1].content
    assert "Section C" in parents[2].content


def test_section_strategy_preamble_before_first_heading():
    """Text before the first heading becomes its own parent."""
    text = "Preamble text.\n\n# Section A\n\nContent."
    chunker = _make_section_chunker(parent_chunk_size=500)
    result = chunker.chunk(text, {})
    parents = [c for c in result if c.chunk_type == "parent"]
    assert len(parents) == 2
    assert "Preamble" in parents[0].content
    assert "Section A" in parents[1].content


def test_section_strategy_oversized_section_is_split():
    """Sections exceeding parent_chunk_size are sub-split."""
    big_section = "# Big\n\n" + _long_text(800)
    chunker = _make_section_chunker(parent_chunk_size=200)
    result = chunker.chunk(big_section, {})
    parents = [c for c in result if c.chunk_type == "parent"]
    assert len(parents) >= 2
    for p in parents:
        assert len(p.content) <= 200 or "Big" in p.content  # heading-attached one may slightly exceed


def test_section_strategy_no_headings_falls_back():
    """Documents without headings degrade to fixed_size-like behaviour."""
    text = _long_text(400)
    chunker = _make_section_chunker(parent_chunk_size=200)
    result = chunker.chunk(text, {})
    parents = [c for c in result if c.chunk_type == "parent"]
    assert len(parents) >= 1


def test_section_strategy_children_inherit_parent_hint():
    text = "# A\n\nAlpha content.\n\n# B\n\nBeta content."
    chunker = _make_section_chunker(parent_chunk_size=500)
    result = chunker.chunk(text, {})
    hints = {c.metadata["_parent_id_hint"] for c in result if c.chunk_type == "parent"}
    for child in (c for c in result if c.chunk_type == "child"):
        assert child.parent_chunk_id in hints


def test_section_strategy_chunk_indexes_sequential():
    text = "# A\n\nAlpha.\n\n# B\n\nBeta.\n\n# C\n\nGamma."
    chunker = _make_section_chunker(parent_chunk_size=500)
    result = chunker.chunk(text, {})
    indexes = [c.chunk_index for c in result]
    assert indexes == list(range(len(result)))


# ---------------------------------------------------------------------------
# parent_child + block-aware wrapping (via get_chunker)
# Verifies that BlockAwareWrapper uses parent.max_chars (not chunk_size) when
# wrapping ParentChildChunker — otherwise parents collapse to small groups.
# ---------------------------------------------------------------------------

def test_parent_child_with_block_aware_uses_parent_max_chars():
    """When parent_child + source_format: markdown, BlockAwareWrapper must group
    at parent.max_chars so that parents reach their intended size."""
    from mrag.core.chunking.base import get_chunker
    from mrag.config.profile import ParentConfig, ChildConfig

    chunker = get_chunker(
        strategy="parent_child",
        chunk_size=800,        # ← should NOT be used as the parent grouping size
        overlap=120,
        preserve_heading_path=True,
        preserve_tables=True,
        preserve_code_blocks=True,
        source_format="markdown",
        parent_config=ParentConfig(max_chars=3000, strategy="fixed_size"),
        child_config=ChildConfig(chunk_size=600, overlap=100),
    )

    body = ("Paragraph filler. " * 100)
    text = (
        "# Section A\n\n" + body + "\n\n"
        "# Section B\n\n" + body + "\n\n"
        "# Section C\n\n" + body
    )
    chunks = chunker.chunk(text, {"document_id": "d1", "profile_name": "p1"})
    parents = [c for c in chunks if c.chunk_type == "parent"]
    children = [c for c in chunks if c.chunk_type == "child"]

    assert parents, "expected at least one parent"
    assert children, "expected children"

    # Parents should NOT be capped at chunk_size (800). At least one parent
    # should exceed chunk_size; otherwise the bug is present.
    max_parent_size = max(len(p.content) for p in parents)
    assert max_parent_size > 800, (
        f"parents collapsed to chunk_size ({max_parent_size} <= 800) — "
        "BlockAwareWrapper is not using parent.max_chars"
    )
    # And every parent should be at or below parent.max_chars (3000) within tolerance
    for p in parents:
        assert len(p.content) <= 3000 + 200, (
            f"parent exceeds parent.max_chars: {len(p.content)} chars"
        )
