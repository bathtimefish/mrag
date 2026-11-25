"""exp/html-structure: a chunk knows which line of the chunked text each of
its lines is, however the block-aware chunker cut it."""

from __future__ import annotations

import pytest

from mrag.core.chunking.base import get_chunker

TEXT = "\n".join(
    ["# Garden", "", "Water the beds every morning before the sun is high.", "",
     "Weed them on Saturdays, and mulch the paths in autumn.", "", "## Spacing", "",
     "| Crop | Gap |", "| --- | --- |"]
    + [f"| crop {n} | {n * 5} cm |" for n in range(1, 25)]
    + ["", "Thin the seedlings once they show two leaves."]
) + "\n"


def _check(chunks, text: str) -> int:
    lines = text.splitlines()
    checked = 0
    for chunk in chunks:
        assert chunk.source_lines is not None, chunk.content[:40]
        content = chunk.content.split("\n")
        assert len(content) == len(chunk.source_lines), chunk.content[:40]
        for line, source in zip(content, chunk.source_lines):
            if source is None:
                continue
            # A piece cut inside a line holds part of it.
            assert line in lines[source], (line, source)
            checked += 1
    return checked


@pytest.mark.parametrize("strategy", ["recursive", "markdown_recursive", "block_aware"])
def test_each_line_of_a_chunk_names_the_line_it_is(strategy):
    chunks = get_chunker(strategy, chunk_size=120, overlap=20, source_format="markdown").chunk(TEXT, {})
    assert len(chunks) > 3
    assert _check(chunks, TEXT) > 20


def test_a_split_table_part_names_its_header_lines_and_its_own_rows():
    chunks = get_chunker("recursive", chunk_size=200, overlap=0, source_format="markdown").chunk(TEXT, {})
    parts = [chunk for chunk in chunks if chunk.metadata.get("table_split")]
    assert len(parts) > 1
    for part in parts:
        assert part.source_lines[:4] == [None, None, 8, 9]
    rows = [line for part in parts for line in part.source_lines[4:]]
    assert rows == list(range(10, 34)), "every row once, in order"
    _check(parts, TEXT)


def test_parent_and_child_chunks_name_their_lines():
    from mrag.config.profile import ChildConfig, ParentConfig

    chunker = get_chunker(
        "parent_child", chunk_size=400, overlap=0, source_format="markdown",
        parent_config=ParentConfig(max_chars=300), child_config=ChildConfig(chunk_size=80, overlap=10),
    )
    chunks = chunker.chunk(TEXT, {})
    kinds = [chunk.chunk_type for chunk in chunks]
    assert "parent" in kinds and "child" in kinds, kinds
    assert _check([chunk for chunk in chunks if chunk.chunk_type in ("parent", "child")], TEXT) > 5
