"""What a search result says of its table rows, criterion values and pages
(experimental branch ``exp/html-structure``).

The index keeps them in a chunk's ``metadata_json`` under
``html_structure`` (``mrag.core.html_structure.attach_structure``). A result
gives them as MRAG Plus does, in fields of their own:

- ``structured_content``: the text with every table row written out under
  its headers; ``null`` when the text holds no such row, and for a document
  without a structure record;
- ``criteria`` and ``criteria_omitted``: the values the document marks as
  criteria on the result's lines, at most 15, with what a citation needs;
- in ``reference``, ``page_start``, ``page_end``, ``page_label_start`` and
  ``page_label_end``: the pages of the original the text is on, counted from
  1, and the page numbers printed on them; ``null`` without pages on record.

Every result carries every field, ``null`` or empty when there is nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mrag.core.html_structure import STRUCTURE_KEY, limited_criteria

PAGE_FIELDS = ("page_start", "page_end", "page_label_start", "page_label_end")
# A criterion value as a result gives it: the record's line is the index's own.
CRITERION_FIELDS = (
    "value", "limit", "unit", "quantity", "table_caption", "table_group_caption",
    "row_labels", "column_labels", "heading", "page", "page_label", "anchor",
)


def fetch_structure(db_path: Path, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
    """``{chunk_id: what the structure record said of it}`` for the chunks
    that have anything."""
    if not chunk_ids:
        return {}
    from mrag.db.connection import open_connection

    conn = open_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, metadata_json FROM chunks WHERE id IN (%s)" % ",".join("?" * len(chunk_ids)),
            list(chunk_ids),
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not row["metadata_json"]:
            continue
        said = json.loads(row["metadata_json"]).get(STRUCTURE_KEY)
        if said:
            out[row["id"]] = said
    return out


def structure_fields(said: dict[str, Any] | None) -> dict[str, Any]:
    said = said or {}
    criteria, omitted = limited_criteria(said.get("criteria", []))
    return {
        "structured_content": said.get("structured_content"),
        "criteria": [{key: criterion.get(key) for key in CRITERION_FIELDS} for criterion in criteria],
        "criteria_omitted": omitted,
    }


def page_fields(said: dict[str, Any] | None) -> dict[str, Any]:
    pages = (said or {}).get("pages") or {}
    return {key: pages.get(key) for key in PAGE_FIELDS}


def decorate(entry: dict[str, Any], said: dict[str, Any] | None) -> dict[str, Any]:
    """Gives one result entry its structure fields, and takes the index's own
    key out of its ``metadata``."""
    metadata = entry.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop(STRUCTURE_KEY, None)
    entry.update(structure_fields(said))
    if isinstance(entry.get("reference"), dict):
        entry["reference"].update(page_fields(said))
    return entry
