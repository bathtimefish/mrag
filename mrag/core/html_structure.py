"""What a structure record says of the lines a chunk holds (experimental
branch ``exp/html-structure``).

Ported from MRAG Plus (``structure.rs``, ``criteria.rs``,
``structured_content.rs`` and the page lookup of ``store.rs``) and held to
the same outputs by ``tests/fixtures/html_parity/``:

- a table's rows read under their headers, and a chunk's text with its rows
  written that way (``structured_content``);
- the criterion values on a chunk's lines, each with the labels a table puts
  it under, the heading above it and the page it is on (``criteria``);
- the printed page numbers a chunk's lines are on.

A chunk is given as the lines of ``extracted.md`` it holds:
``(line, text, whole)``, the line counted from 1, its text as the chunk has
it, and whether the chunk holds the whole line.
"""

from __future__ import annotations

from typing import Any, Iterable

from mrag.extractors.html_structure import squeeze, trim

Held = tuple[int, str, bool]

MAX_CRITERIA_PER_RESULT = 15


# ---------------------------------------------------------------------------
# A table's labels (one rule, TODO-M15-136 of MRAG Plus).

def cell_text(cell: dict[str, Any]) -> str:
    out = []
    for kind, *value in cell["segments"]:
        if kind == "text":
            out.append(value[0])
        elif kind == "break":
            out.append(" ／ ")
        else:
            out.append("［注" + value[0] + "］")
    return squeeze("".join(out))


def has_header_cells(table: dict[str, Any]) -> bool:
    return any(cell["header"] for row in table["rows"] for cell in row if cell is not None)


def row_labels(table: dict[str, Any], row: int, except_origin: int | None = None) -> list[str]:
    """The header cells of one row, those carried down from above included,
    each cell once, but `except_origin`; read the same in every row."""
    if row < 0 or row >= len(table["rows"]):
        return []
    seen: list[int] = []
    out = []
    for cell in table["rows"][row]:
        if cell is None:
            continue
        first = cell["origin"] not in seen
        seen.append(cell["origin"])
        if first and cell["header"] and cell["origin"] != except_origin:
            text = cell_text(cell)
            if trim(text):
                out.append(text)
    return out


def column_label_parts(table: dict[str, Any], column: int, above: int) -> list[str]:
    parts: list[str] = []
    for row in table["rows"][:above]:
        cell = row[column] if column < len(row) else None
        if cell is None:
            continue
        text = cell_text(cell)
        if trim(text) and (not parts or parts[-1] != text):
            parts.append(text)
    return parts


def column_labels(table: dict[str, Any]) -> list[str]:
    width = len(table["rows"][0]) if table["rows"] else 0
    return ["・".join(column_label_parts(table, column, table["header_rows"])) for column in range(width)]


def rendered_rows(table: dict[str, Any]) -> list[str | None]:
    header_rows = table["header_rows"]
    labels = column_labels(table)
    out: list[str | None] = []
    for index, row in enumerate(table["rows"]):
        if index < header_rows:
            out.append(None)
            continue
        own = row_labels(table, index)
        if header_rows == 0 and not own:
            out.append("| " + " | ".join(cell_text(cell) for cell in row if cell is not None) + " |")
            continue
        seen: list[int] = []
        values = []
        for column, cell in enumerate(row):
            if cell is None or cell["header"] or cell["origin"] in seen:
                continue
            seen.append(cell["origin"])
            text = cell_text(cell)
            value = text if text else "(空欄)"
            if header_rows == 0:
                values.append(value)
                continue
            label = labels[column] if column < len(labels) and labels[column] else f"列{column + 1}"
            values.append(f"「{label}」= {value}")
        out.append("・" + " > ".join(own) + ": " + "；".join(values))
    return out


# ---------------------------------------------------------------------------
# Pages.

def page_span(pages: list[dict[str, Any]], start_line: int, end_line: int) -> tuple[int, int] | None:
    """The pages lines `start_line..end_line` are on: a line belongs to the
    last page that begins at or before it; pages without text own no line."""
    def begun(line: int) -> list[tuple[int, int]]:
        return [
            (page["start_line"], page["page_number"])
            for page in pages
            if page["start_line"] is not None and page["start_line"] <= line
        ]

    first = begun(start_line)
    if not first:
        return None
    first_line = max(start for start, _ in first)
    start = min(number for line, number in first if line == first_line)
    last = begun(max(end_line, start_line))
    last_line = max(line for line, _ in last)
    end = max(number for line, number in last if line == last_line)
    return start, max(end, start)


def page_label(pages: list[dict[str, Any]], number: int) -> str | None:
    return next((page["label"] for page in pages if page["page_number"] == number), None)


def chunk_pages(pages: list[dict[str, Any]], held: list[Held]) -> dict[str, Any] | None:
    lines = [line for line, text, _ in held if trim(text)]
    if not pages or not lines:
        return None
    span = page_span(pages, min(lines), max(lines))
    if span is None:
        return None
    return {
        "page_start": span[0],
        "page_end": span[1],
        "page_label_start": page_label(pages, span[0]),
        "page_label_end": page_label(pages, span[1]),
    }


# ---------------------------------------------------------------------------
# Criterion values.

def _labels(table: dict[str, Any], origin: int) -> tuple[list[str], list[str]]:
    for at, row in enumerate(table["rows"]):
        for column, cell in enumerate(row):
            if cell is not None and cell["origin"] == origin and not cell["carried"]:
                above = min(table["header_rows"], at)
                return row_labels(table, at, origin), column_label_parts(table, column, above)
    return [], []


def chunk_criteria(record: dict[str, Any], lines: Iterable[int]) -> list[dict[str, Any]]:
    """The criterion values on the lines a chunk holds, in document order."""
    held = set(lines)
    pages = record.get("pages", [])
    out = []
    for criterion in record.get("criteria", []):
        if criterion["line"] not in held:
            continue
        table = None
        if criterion["table"] is not None and criterion["table"] < len(record["tables"]):
            table = record["tables"][criterion["table"]]
        if table is not None and criterion["cell"] is not None:
            rows, columns = _labels(table, criterion["cell"])
        else:
            rows, columns = [], []
        heading = None
        for candidate in record.get("headings", []):
            if candidate["lines"][0] <= criterion["line"]:
                heading = candidate["text"]
        span = page_span(pages, criterion["line"], criterion["line"]) if pages else None
        page = span[0] if span else None
        out.append({
            "value": criterion["value"],
            "limit": criterion["limit"],
            "unit": criterion["unit"],
            "quantity": criterion["quantity"],
            "anchor": criterion["anchor"],
            "line": criterion["line"],
            "table_caption": table["caption"] if table else None,
            "table_group_caption": table["group_caption"] if table else None,
            "row_labels": rows,
            "column_labels": columns,
            "heading": heading,
            "page": page,
            "page_label": page_label(pages, page) if page is not None else None,
        })
    return out


# ---------------------------------------------------------------------------
# A chunk's text with its rows under their headers.

def _row_lines(record: dict[str, Any]) -> dict[int, tuple[int, int | None]]:
    """The lines rows are written on: (table, body row) or (table, None) for
    a header row and the rule under them."""
    lines: dict[int, tuple[int, int | None]] = {}
    for at, table in enumerate(record.get("tables", [])):
        rows = table["rows"]
        header_rows = table["header_rows"]
        if not rows or header_rows >= len(rows) or (header_rows == 0 and not has_header_cells(table)):
            continue
        rule = 1 if header_rows > 0 else 0
        written = len(rows) + rule
        first = table["lines"][1] + 1 - written
        if first < table["lines"][0]:
            continue
        for row in range(len(rows)):
            below_rule = 1 if rule and row >= header_rows else 0
            line = first + row + below_rule
            if row < header_rows:
                lines[line] = (at, None)
            elif header_rows > 0 or row_labels(table, row):
                lines[line] = (at, row)
        if rule:
            lines[first + header_rows] = (at, None)
    return lines


def _missing_titles(table: dict[str, Any], chunk_lines: list[str]) -> list[str]:
    return [
        title
        for title in (table["group_caption"], table["caption"])
        if title and not any(trim(line) == title for line in chunk_lines)
    ]


def structured_content(record: dict[str, Any], held: list[Held]) -> str | None:
    rows = _row_lines(record)
    if not rows or not held:
        return None
    tables = record["tables"]
    chunk_lines = [text for _, text, _ in held]
    captions = {
        tables[at]["lines"][0]: at
        for at in sorted({at for at, _ in rows.values()})
        if tables[at]["caption"] is not None
    }
    rendered: dict[int, list[str | None]] = {}
    titled: list[int] = []
    out: list[str] = []
    resolved = False

    def title(at: int) -> None:
        if at in titled:
            return
        titled.append(at)
        for missing in _missing_titles(tables[at], chunk_lines):
            out.append(missing)
            out.append("")

    for line, text, whole in held:
        kind = rows.get(line) if whole else None
        if kind is not None and kind[1] is None:
            continue
        if kind is not None:
            at, row = kind
            title(at)
            if at not in rendered:
                rendered[at] = rendered_rows(tables[at])
            row_text = rendered[at][row] if row < len(rendered[at]) else None
            if row_text is not None:
                resolved = True
                out.append(row_text)
            else:
                out.append(text)
            continue
        if whole and line in captions:
            title(captions[line])
        out.append(text)
    if not resolved:
        return None
    return trim("\n".join(out))


def whole_document(text: str) -> list[Held]:
    """Every line of a text as one chunk holds them."""
    lines = text.split("\n")
    if text.endswith("\n"):
        lines = lines[:-1]
    return [(number, line, True) for number, line in enumerate(lines, 1)]


def limited_criteria(criteria: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """At most MAX_CRITERIA_PER_RESULT, in document order, and how many were left out."""
    kept = criteria[:MAX_CRITERIA_PER_RESULT]
    return kept, len(criteria) - len(kept)


STRUCTURE_KEY = "html_structure"


def attach_structure(record: dict[str, Any], text: str, chunks: list[Any]) -> None:
    """Gives each chunk what `record` says of the lines it holds, under
    ``metadata["html_structure"]``: its rows under their headers
    (``structured_content``), the criterion values on its lines
    (``criteria``) and the pages it is on (``pages``). `text` is the text the
    chunks were cut from, whose lines the chunks' ``source_lines`` name.

    A chunk whose lines are not known, or that the record says nothing of, is
    left as it is."""
    source = text.splitlines()
    for chunk in chunks:
        if not chunk.source_lines:
            continue
        # A line the chunker wrote itself (the blank line between two blocks,
        # a split table's label) is kept as text and names no line: 0.
        lines: list[Held] = []
        for line, at in zip(chunk.content.split("\n"), chunk.source_lines):
            if at is None or at >= len(source):
                lines.append((0, line, False))
            else:
                lines.append((at + 1, line, line == source[at]))
        held = [entry for entry in lines if entry[0] > 0]
        if not held:
            continue
        said: dict[str, Any] = {}
        content = structured_content(record, lines)
        if content is not None:
            said["structured_content"] = content
        criteria = chunk_criteria(record, (line for line, _, _ in held))
        if criteria:
            said["criteria"] = criteria
        pages = chunk_pages(record.get("pages", []), held)
        if pages is not None:
            said["pages"] = pages
        if said:
            chunk.metadata[STRUCTURE_KEY] = said
