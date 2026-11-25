"""exp/html-structure: the html-structure extractor gives what MRAG Plus gives.

``tests/fixtures/html_parity/`` is a copy of MRAG Plus's
``quality/oss-html-parity/``: HTML fixtures, and for each what Plus's
converter of the same name gives — the Markdown, headings, tables with their
rows read under their headers, the criterion values with labels and printed
pages, the page of every line, and the structured text of the whole document
as one chunk. When Plus records them again, copy both directories here.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

pytest.importorskip("html5lib")

from mrag.core.html_structure import (  # noqa: E402
    chunk_criteria,
    column_labels,
    page_span,
    rendered_rows,
    structured_content,
    whole_document,
)
from mrag.extractors.html_structure import convert_html  # noqa: E402

PARITY = Path(__file__).parent / "fixtures" / "html_parity"
FIXTURES = sorted((PARITY / "fixtures").glob("*.html"))


def test_every_fixture_has_what_plus_gives():
    assert len(FIXTURES) >= 10
    for fixture in FIXTURES:
        assert (PARITY / "expected" / (fixture.stem + ".json")).is_file(), fixture.stem


def _given(html: bytes) -> dict:
    markdown, record, _warnings = convert_html(html)
    pages = record["pages"]
    lines = markdown.count("\n")

    def label_of(line: int):
        span = page_span(pages, line, line)
        if span is None:
            return None
        return next((page["label"] for page in pages if page["page_number"] == span[0]), None)

    # As the index reads it: normalized, every line held whole.
    held = whole_document(unicodedata.normalize("NFKC", markdown))
    return {
        "markdown": markdown,
        "headings": [[h["level"], h["text"], h["lines"][0]] for h in record["headings"]],
        "tables": [
            {
                "caption": t["caption"],
                "group_caption": t["group_caption"],
                "header_rows": t["header_rows"],
                "lines": t["lines"],
                "grid_omitted": t["grid_omitted"] is not None,
                "column_labels": column_labels(t),
                "rendered_rows": rendered_rows(t),
            }
            for t in record["tables"]
        ],
        "criteria": chunk_criteria(record, range(1, lines + 1)),
        "pages": [[p["page_number"], p["start_line"], p["label"]] for p in pages],
        "line_pages": [label_of(line) for line in range(1, lines + 1)],
        "structured_content": structured_content(record, held),
    }


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda path: path.stem)
def test_the_extractor_gives_what_plus_gives(fixture: Path):
    expected = json.loads((PARITY / "expected" / (fixture.stem + ".json")).read_text(encoding="utf-8"))
    given = _given(fixture.read_bytes())
    for key in expected:
        assert given[key] == expected[key], key
