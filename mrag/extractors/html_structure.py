"""The ``html-structure`` extractor (experimental branch ``exp/html-structure``).

Reads an HTML file statically — no script runs, nothing outside the file is
fetched — and writes it as Markdown, keeping beside it a structure record of
what the Markdown alone loses: the headings, each table as a grid of cells
with its header cells and merged cells laid out, the values the document
marks as criteria (``data-criterion``), and the page breaks it marks
(``role="doc-pagebreak"``).

This is a port of part of MRAG Plus's converter of the same name. The two are
held to the same outputs by the fixtures under
``tests/fixtures/html_parity/`` (copied from MRAG Plus's
``quality/oss-html-parity/``), so behaviour here follows Plus's rules, quirks
included, rather than what might seem more natural in Python.

The parser is ``html5lib``, which implements the HTML standard's parsing
algorithm as Plus's ``html5ever`` does, so a broken document is read the way
a browser reads it. It is an optional dependency: ``pip install
'mrag[html]'``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mrag.extractors.base import BaseExtractor, ExtractionResult

CONVERTER_NAME = "html-structure"
STRUCTURE_FILE_NAME = "structure.json"
RECORD_VERSION = 1

MAX_HTML_BYTES = 16 * 1024 * 1024
MAX_DEPTH = 512
MAX_TABLE_POSITIONS = 100_000
MAX_GRID_BYTES = 64 * 1024 * 1024
GRID_POSITION_BYTES = 96
MAX_PAGE_LABEL_CHARS = 32

# Elements whose content is not the document's text.
SKIPPED = frozenset({
    "head", "script", "style", "template", "noscript", "svg", "math", "iframe", "object", "canvas",
    "select", "audio", "video", "nav",
})

# Elements that start a block of their own.
BLOCKS = frozenset({
    "address", "article", "aside", "blockquote", "body", "dd", "details", "dialog", "div", "dl",
    "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hgroup", "hr", "html", "li", "main", "ol", "p", "pre", "section", "table", "ul",
})

CRITERION_LIMITS = ("min", "max", "range", "exact", "tolerance")

# Unicode White_Space, the set Rust's `char::is_whitespace` (and so Plus's
# `split_whitespace` and `trim`) uses. Python's `str.isspace` also counts
# U+001C..U+001F, which Plus keeps as text.
_WHITE_SPACE = (
    "\t\n\x0b\x0c\r \x85\xa0        "
    "        　"
)
_WS_RUN = re.compile("[" + re.escape(_WHITE_SPACE) + "]+")
# What `str.splitlines` breaks lines at besides the line feed. The index of
# this branch splits Markdown with it, while the record counts lines by line
# feeds alone, so none of these may stand in the Markdown.
_OTHER_LINE_BREAKS = re.compile("[\x0b\x0c\x1c\x1d\x1e\x85  \r]")

_HTML_NAMESPACE_PREFIX = "{"


def squeeze(text: str) -> str:
    """Runs of white space as one space, none at either end."""
    return " ".join(part for part in _WS_RUN.split(text) if part)


def trim(text: str) -> str:
    return text.strip(_WHITE_SPACE)


def ascii_lower(text: str) -> str:
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in text)


def _parse_unsigned(value: str) -> int | None:
    """`str::parse::<usize>` after a trim: an optional `+`, then digits."""
    value = trim(value)
    if not re.fullmatch(r"\+?[0-9]+", value):
        return None
    number = int(value)
    return number if number < 2**64 else None


def _parse_signed(value: str) -> int | None:
    """`str::parse::<i64>` after a trim."""
    value = trim(value)
    if not re.fullmatch(r"[+-]?[0-9]+", value):
        return None
    number = int(value)
    return number if -(2**63) <= number < 2**63 else None


def parse_limit(value: str) -> str | None:
    value = ascii_lower(trim(value))
    return value if value in CRITERION_LIMITS else None


# ---------------------------------------------------------------------------
# Decoding and the tree.

_WHATWG_LABELS = {
    "shift_jis": "cp932", "shift-jis": "cp932", "sjis": "cp932", "windows-31j": "cp932",
    "x-sjis": "cp932", "ms_kanji": "cp932", "csshiftjis": "cp932",
    "euc-jp": "euc_jp", "x-euc-jp": "euc_jp", "cseucpkdfmtjapanese": "euc_jp",
    "iso-2022-jp": "iso2022_jp", "csiso2022jp": "iso2022_jp",
    "us-ascii": "cp1252", "ascii": "cp1252", "iso-8859-1": "cp1252", "latin1": "cp1252",
    "l1": "cp1252", "iso8859-1": "cp1252", "windows-1252": "cp1252",
    "utf8": "utf-8", "unicode-1-1-utf-8": "utf-8",
}


def decode(data: bytes, warnings: list[str]) -> str:
    """By the byte-order mark, else a `charset` the first bytes declare, else UTF-8."""
    if data.startswith(b"\xef\xbb\xbf"):
        encoding, data = "utf-8", data[3:]
    elif data.startswith(b"\xff\xfe"):
        encoding, data = "utf-16-le", data[2:]
    elif data.startswith(b"\xfe\xff"):
        encoding, data = "utf-16-be", data[2:]
    else:
        encoding = "utf-8"
        lower = ascii_lower(data[:4096].decode("utf-8", errors="replace"))
        at = lower.find("charset=")
        if at >= 0:
            rest = lower[at + len("charset="):].lstrip("\"' ")
            label = re.match(r"[a-z0-9_-]*", rest).group(0)
            candidate = _WHATWG_LABELS.get(label, label)
            try:
                "".encode(candidate)
                encoding = candidate
            except LookupError:
                encoding = "utf-8"
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        warnings.append(f"some bytes were not valid {encoding} and were replaced")
        return data.decode(encoding, errors="replace")


@dataclass
class Node:
    tag: str | None = None          # HTML element's local name; None for other elements
    element: bool = False
    attrs: dict[str, str] = field(default_factory=dict)
    text: str | None = None         # a text node
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = None


def _flatten(element: Any, parent: Node) -> None:
    """html5lib's ElementTree as an ordered tree of elements and text."""
    tag = element.tag
    if not isinstance(tag, str):      # a comment: its tail is still text
        if element.tail:
            _append_text(parent, element.tail)
        return
    node = Node(
        tag=None if tag.startswith(_HTML_NAMESPACE_PREFIX) else tag,
        element=True,
        attrs={str(k): str(v) for k, v in element.attrib.items()},
        parent=parent,
    )
    parent.children.append(node)
    # A template's content is apart from the tree, as Plus's parser keeps it.
    if node.tag != "template":
        if element.text:
            _append_text(node, element.text)
        for child in element:
            _flatten(child, node)
    if element.tail:
        _append_text(parent, element.tail)


def _append_text(parent: Node, text: str) -> None:
    if parent.children and parent.children[-1].text is not None:
        parent.children[-1].text += text
    else:
        parent.children.append(Node(text=text, parent=parent))


def parse(text: str) -> Node:
    import html5lib

    root = Node()
    _flatten(html5lib.parse(text, treebuilder="etree", namespaceHTMLElements=False), root)
    return root


def depth(root: Node) -> int:
    deepest, stack = 0, [(root, 0)]
    while stack:
        node, level = stack.pop()
        deepest = max(deepest, level)
        stack.extend((child, level + 1) for child in node.children)
    return deepest


def raw_text(node: Node) -> str:
    out, stack = [], [node]
    while stack:
        current = stack.pop()
        if current.text is not None:
            out.append(current.text)
        stack.extend(reversed(current.children))
    return "".join(out)


def node_id(node: Node) -> str | None:
    value = node.attrs.get("id")
    return value if value else None


def internal_target(node: Node) -> str | None:
    if node.tag != "a":
        return None
    href = node.attrs.get("href")
    if href is None or not href.startswith("#") or len(href) == 1:
        return None
    return href[1:]


def element_children(node: Node, tag: str) -> list[Node]:
    return [child for child in node.children if child.tag == tag]


def is_page_break(node: Node) -> bool:
    role = node.attrs.get("role")
    if role is not None and ascii_lower(trim(role)) == "doc-pagebreak":
        return True
    kind = node.attrs.get("epub:type")
    return kind is not None and any(ascii_lower(part) == "pagebreak" for part in _WS_RUN.split(kind) if part)


def page_label(node: Node) -> str | None:
    candidates = [squeeze(node.attrs[name]) for name in ("aria-label", "title") if name in node.attrs]
    candidates.append(squeeze(raw_text(node)))
    label = next((label for label in candidates if label), None)
    return label if label is not None and len(label) <= MAX_PAGE_LABEL_CHARS else None


def criterion_of(node: Node) -> dict[str, Any] | None:
    if "data-criterion" not in node.attrs:
        return None

    def named(name: str) -> str | None:
        value = node.attrs.get(name)
        value = trim(value) if value is not None else None
        return value or None

    limit = node.attrs.get("data-limit")
    return {
        "value": squeeze(raw_text(node)),
        "limit": parse_limit(limit) if limit is not None else None,
        "unit": named("data-unit"),
        "quantity": named("data-quantity"),
        "anchor": node_id(node),
    }


# ---------------------------------------------------------------------------
# Cells and tables.

def cell_text(segments: list[list[str]]) -> str:
    """A cell's content as one line: breaks as ` ／ `, note references as `［注…］`."""
    out = []
    for kind, *value in segments:
        if kind == "text":
            out.append(value[0])
        elif kind == "break":
            out.append(" ／ ")
        else:
            out.append("［注" + value[0] + "］")
    return squeeze("".join(out))


def escape(cell: str) -> str:
    return cell.replace("|", "\\|")


@dataclass
class Inline:
    text: str = ""
    leading_marks: list[str | None] = field(default_factory=list)
    trailing_marks: list[str | None] = field(default_factory=list)
    criteria: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Context:
    list_depth: int = 0
    table: int | None = None


class Extractor:
    def __init__(self, root: Node, warnings: list[str]) -> None:
        self.root = root
        self.warnings = warnings
        self.lines: list[str] = []
        self.headings: list[dict[str, Any]] = []
        self.blocks: list[dict[str, Any]] = []
        self.tables: list[dict[str, Any]] = []
        self.criteria: list[dict[str, Any]] = []
        self.pages: list[dict[str, Any]] = []
        self.waiting_pages: list[int] = []
        self.grid_bytes = 0
        self.notes: set[str] = set()
        stack = [root]
        while stack:
            node = stack.pop()
            identifier = node_id(node) if node.element else None
            if identifier and ascii_lower(node.attrs.get("role", "")) == "note":
                self.notes.add(identifier)
            stack.extend(node.children)

    # -- writing -----------------------------------------------------------

    def write(self, block: list[str]) -> tuple[int, int]:
        if self.lines and self.lines[-1] != "":
            self.lines.append("")
        start = len(self.lines) + 1
        for at in self.waiting_pages:
            self.pages[at]["line"] = start
        self.waiting_pages = []
        self.lines.extend(block)
        return start, max(len(self.lines), start)

    def place_pages(self, inline: Inline, line: int | None) -> None:
        for label in inline.leading_marks:
            if line is None:
                self.waiting_pages.append(len(self.pages))
            self.pages.append({"label": label, "line": line})
        inline.leading_marks = []
        for label in inline.trailing_marks:
            self.waiting_pages.append(len(self.pages))
            self.pages.append({"label": label, "line": None})
        inline.trailing_marks = []

    def keep_criteria(self, pending: list[dict[str, Any]], line: int, table: int | None, cell: int | None) -> None:
        for criterion in pending:
            self.criteria.append({**criterion, "line": line, "table": table, "cell": cell})

    def block(self, kind: str, line: str, inline: Inline, context: Context) -> int:
        lines = self.write([line])
        self.place_pages(inline, lines[0])
        at = len(self.blocks)
        self.blocks.append({"kind": kind, "lines": list(lines), "table": context.table})
        self.keep_criteria(inline.criteria, lines[0], context.table, None)
        return at

    # -- inline content ----------------------------------------------------

    def inline(self, nodes: list[Node], out: Inline) -> None:
        for node in nodes:
            if node.text is not None:
                out.text += node.text
                continue
            if not node.element:
                continue
            tag = node.tag
            if tag in SKIPPED:
                continue
            if tag == "br":
                out.text += " "
                continue
            if is_page_break(node):
                label = page_label(node)
                (out.leading_marks if not trim(out.text) else out.trailing_marks).append(label)
                continue
            criterion = criterion_of(node)
            if criterion is not None:
                out.criteria.append(criterion)
            self.inline(node.children, out)

    # -- blocks ------------------------------------------------------------

    @staticmethod
    def is_block(node: Node) -> bool:
        return node.tag in BLOCKS

    def has_block_child(self, node: Node) -> bool:
        return any(self.is_block(child) for child in node.children)

    def is_inline_only(self, node: Node) -> bool:
        stack = [node]
        while stack:
            for child in stack.pop().children:
                if self.is_block(child):
                    return False
                stack.append(child)
        return True

    def flow(self, container: Node, context: Context) -> None:
        run: list[Node] = []
        for child in container.children:
            if self.is_block(child):
                self.flush(run, context)
                self.element(child, context)
            elif child.tag in SKIPPED:
                pass
            elif child.element and not self.is_inline_only(child):
                self.flush(run, context)
                self.flow(child, context)
            else:
                run.append(child)
        self.flush(run, context)

    def flush(self, run: list[Node], context: Context) -> None:
        inline = Inline()
        self.inline(run, inline)
        run.clear()
        text = squeeze(inline.text)
        if not text:
            self.place_pages(inline, None)
        else:
            self.block("paragraph", text, inline, context)

    def element(self, node: Node, context: Context) -> None:
        tag = node.tag
        if tag is None:
            return
        if is_page_break(node):
            self.waiting_pages.append(len(self.pages))
            self.pages.append({"label": page_label(node), "line": None})
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading(node, tag)
        elif tag in ("ul", "ol"):
            self.list(node, tag == "ol", context)
        elif tag == "dl":
            self.definition_list(node, context)
        elif tag == "table":
            self.table(node, None, context)
        elif tag == "figure":
            self.figure(node, context)
        elif tag == "pre":
            self.preformatted(node, context)
        elif tag == "hr":
            pass
        else:
            self.text_or_flow(node, tag, context)

    def text_or_flow(self, node: Node, tag: str, context: Context) -> None:
        if self.has_block_child(node):
            self.flow(node, context)
            return
        marked_note = ascii_lower(node.attrs.get("role", "")) == "note"
        inline = Inline()
        self.inline(node.children, inline)
        text = squeeze(inline.text)
        if not text:
            self.place_pages(inline, None)
            return
        kind = "note" if marked_note else "caption" if tag == "figcaption" else "paragraph"
        self.block(kind, text, inline, context)

    def heading(self, node: Node, tag: str) -> None:
        level = min(max(int(tag[1]), 1), 6)
        inline = Inline()
        self.inline(node.children, inline)
        text = squeeze(inline.text)
        if not text:
            self.place_pages(inline, None)
            return
        lines = self.write(["#" * level + " " + text])
        self.place_pages(inline, lines[0])
        self.headings.append({"level": level, "text": text, "lines": list(lines)})
        self.keep_criteria(inline.criteria, lines[0], None, None)

    def list(self, node: Node, ordered: bool, context: Context) -> None:
        start = node.attrs.get("start")
        number = _parse_signed(start) if start is not None else None
        number = 1 if number is None else number
        for item in element_children(node, "li"):
            value = item.attrs.get("value")
            value = _parse_signed(value) if value is not None else None
            if value is not None:
                number = value
            own = [child for child in item.children if not self.is_block(child)]
            inline = Inline()
            self.inline(own, inline)
            text = squeeze(inline.text)
            marker = f"{number}." if ordered else "-"
            self.block("list_item", "  " * context.list_depth + marker + " " + text, inline, context)
            inner = Context(list_depth=context.list_depth + 1, table=context.table)
            for child in item.children:
                if self.is_block(child):
                    self.element(child, inner)
            number += 1

    def definition_list(self, node: Node, context: Context) -> None:
        group: list[Node] = []
        seen_description = False
        for child in node.children:
            if child.tag == "div":
                self.definition_group(group, context)
                seen_description = False
                inner = [c for c in child.children if c.tag in ("dt", "dd")]
                self.definition_group(inner, context)
            elif child.tag == "dt":
                if seen_description:
                    self.definition_group(group, context)
                    seen_description = False
                group.append(child)
            elif child.tag == "dd":
                seen_description = True
                group.append(child)
        self.definition_group(group, context)

    def definition_group(self, group: list[Node], context: Context) -> None:
        if not group:
            return
        inline = Inline()
        parts: list[str] = []
        for part in group:
            piece = Inline()
            self.inline(part.children, piece)
            text = squeeze(piece.text)
            if text:
                parts.append(text)
            inline.criteria.extend(piece.criteria)
            if not parts:
                inline.leading_marks.extend(piece.leading_marks)
            else:
                inline.trailing_marks.extend(piece.leading_marks)
            inline.trailing_marks.extend(piece.trailing_marks)
        group.clear()
        text = " ".join(parts)
        if not text:
            self.place_pages(inline, None)
        else:
            self.block("definition_entry", text, inline, context)

    def preformatted(self, node: Node, context: Context) -> None:
        text = raw_text(node)
        if not trim(text):
            return
        body = []
        for line in text.strip("\n").split("\n"):
            body.append(line[:-1] if line.endswith("\r") else line)
        lines = self.write(["```", *body, "```"])
        self.blocks.append({"kind": "paragraph", "lines": list(lines), "table": context.table})

    def figure(self, node: Node, context: Context) -> None:
        captions = element_children(node, "figcaption")
        group_caption = squeeze(raw_text(captions[0])) if captions else ""
        group_caption = group_caption or None
        context = Context(list_depth=context.list_depth, table=context.table)
        run: list[Node] = []
        for child in node.children:
            if child.tag == "table":
                self.flush(run, context)
                table = self.table(child, group_caption, context)
                if table is not None:
                    context.table = table
            elif self.is_block(child):
                self.flush(run, context)
                self.element(child, context)
            elif child.tag not in SKIPPED:
                run.append(child)
        self.flush(run, context)

    # -- tables ------------------------------------------------------------

    @staticmethod
    def cells_of(row: Node) -> list[Node]:
        return [cell for cell in row.children if cell.tag in ("td", "th")]

    def table_rows(self, node: Node) -> tuple[list[Node], int]:
        rows: list[tuple[bool, Node]] = []
        for part in node.children:
            if part.tag == "thead":
                rows.extend((True, row) for row in element_children(part, "tr"))
            elif part.tag in ("tbody", "tfoot"):
                rows.extend((False, row) for row in element_children(part, "tr"))
            elif part.tag == "tr":
                rows.append((False, part))
        header_rows = 0
        for head, _ in rows:
            if not head:
                break
            header_rows += 1
        if header_rows == 0:
            for _, row in rows:
                cells = self.cells_of(row)
                if not cells or not all(cell.tag == "th" for cell in cells):
                    break
                header_rows += 1
            if header_rows == len(rows):
                header_rows = 0
        return [row for _, row in rows], header_rows

    def numbered_cells(self, rows: list[Node]) -> list[list[tuple[Node, int]]]:
        origin, out = 0, []
        for row in rows:
            numbered = []
            for cell in self.cells_of(row):
                numbered.append((cell, origin))
                origin += 1
            out.append(numbered)
        return out

    def cell_segments(self, nodes: list[Node], out: list[list[str]]) -> None:
        for node in nodes:
            if node.text is not None:
                if out and out[-1][0] == "text":
                    out[-1][1] += node.text
                else:
                    out.append(["text", node.text])
                continue
            if not node.element:
                continue
            if node.tag in SKIPPED:
                continue
            if node.tag == "br":
                out.append(["break"])
                continue
            if is_page_break(node):
                continue
            target = internal_target(node)
            if target is not None and target in self.notes:
                out.append(["note", squeeze(raw_text(node))])
                continue
            self.cell_segments(node.children, out)

    @staticmethod
    def span(cell: Node, name: str, most: int) -> int:
        value = cell.attrs.get(name)
        number = _parse_unsigned(value) if value is not None else None
        return min(max(number if number is not None else 1, 1), most)

    def grid(self, rows: list[list[tuple[Node, int]]], budget: int) -> tuple[list[list[Any]], int]:
        grid: list[list[Any]] = [[] for _ in rows]
        positions = 0
        used = 0
        for at, row in enumerate(rows):
            column = 0
            for cell, origin in row:
                while column < len(grid[at]) and grid[at][column] is not None:
                    column += 1
                across = self.span(cell, "colspan", 1000)
                down = min(self.span(cell, "rowspan", 65_534), len(rows) - at)
                positions += across * down
                if positions > MAX_TABLE_POSITIONS:
                    raise _GridOmitted("the table expands to more positions than the limit")
                segments: list[list[str]] = []
                self.cell_segments(cell.children, segments)
                text = sum(len(s[1].encode("utf-8")) for s in segments if s[0] in ("text", "note"))
                used += across * down * (text + GRID_POSITION_BYTES)
                if used > budget:
                    raise _GridOmitted("the document's tables expand to more bytes than the limit")
                header = cell.tag == "th"
                for offset in range(down):
                    line = grid[at + offset]
                    if len(line) < column + across:
                        line.extend([None] * (column + across - len(line)))
                    for position in range(column, column + across):
                        line[position] = {
                            "segments": [list(s) for s in segments],
                            "header": header,
                            "origin": origin,
                            "carried": offset > 0,
                        }
                column += across
        width = max((len(line) for line in grid), default=0)
        for line in grid:
            line.extend([None] * (width - len(line)))
        return grid, used

    @staticmethod
    def grid_lines(grid: list[list[Any]], header_rows: int) -> list[str]:
        width = len(grid[0]) if grid else 0
        block = []
        for at, line in enumerate(grid):
            seen: set[int] = set()
            cells = []
            for position in line:
                if position is not None and not position["carried"] and position["origin"] not in seen:
                    seen.add(position["origin"])
                    cells.append(escape(cell_text(position["segments"])))
                else:
                    cells.append("")
            block.append("| " + " | ".join(cells) + " |")
            if header_rows > 0 and at + 1 == header_rows:
                block.append("|" + " --- |" * max(width, 1))
        return block

    def table(self, node: Node, group_caption: str | None, context: Context) -> int | None:
        rows, header_rows = self.table_rows(node)
        captions = element_children(node, "caption")
        caption_node = captions[0] if captions else None
        caption = squeeze(raw_text(caption_node)) if caption_node is not None else ""
        caption = caption or None
        cells = self.numbered_cells(rows)
        omitted = None
        try:
            grid, used = self.grid(cells, max(MAX_GRID_BYTES - self.grid_bytes, 0))
            self.grid_bytes += used
        except _GridOmitted as reason:
            grid, omitted = [], str(reason)
        if omitted is not None:
            self.warnings.append("a table was too large to expand and keeps its rows as text")
            block = [
                "| " + " | ".join(escape(squeeze(raw_text(cell))) for cell, _ in row) + " |"
                for row in cells
            ]
        else:
            block = self.grid_lines(grid, header_rows)
        if not block and caption is None:
            return None
        table_index = len(self.tables)
        inner = Context(list_depth=context.list_depth, table=table_index)
        first_line = None
        if caption is not None:
            inline = Inline()
            if caption_node is not None:
                self.inline(caption_node.children, inline)
            at = self.block("caption", caption, inline, inner)
            first_line = self.blocks[at]["lines"][0]
        if not block:
            lines = [first_line or 1, first_line or 1]
        else:
            written = self.write(block)
            ruled = header_rows if omitted is None and header_rows > 0 else None
            self.place_in_rows(rows, cells, written, ruled, table_index)
            lines = [first_line if first_line is not None else written[0], written[1]]
        self.tables.append({
            "anchor": node_id(node),
            "group_caption": group_caption,
            "caption": caption,
            "header_rows": 0 if omitted is not None else header_rows,
            "rows": grid,
            "lines": lines,
            "grid_omitted": omitted,
        })
        return table_index

    def place_in_rows(self, rows, cells, written, ruled, table) -> None:
        def line_of(row: int) -> int:
            below_rule = 1 if ruled is not None and row >= ruled else 0
            return min(written[0] + row + below_rule, written[1])

        pages, criteria = self.row_contents(rows, cells)
        for row, label in pages:
            self.pages.append({"label": label, "line": line_of(row)})
        for row, cell, criterion in criteria:
            self.keep_criteria([criterion], line_of(row), table, cell)

    def row_contents(self, rows, cells):
        """The page breaks and criterion marks in a table's rows, by row,
        read in one walk: a page break anywhere and nothing inside one; a
        criterion mark inside a cell, not inside an element that is not the
        document's text."""
        pages, criteria = [], []
        for at, row in enumerate(rows):
            numbered = cells[at] if at < len(cells) else []
            stack = [(row, None, True, True)]
            while stack:
                node, cell, read_pages, read_criteria = stack.pop()
                if read_pages and is_page_break(node):
                    pages.append((at, page_label(node)))
                    read_pages = False
                if node.tag in SKIPPED:
                    read_criteria = False
                if read_criteria and cell is not None and node.element:
                    criterion = criterion_of(node)
                    if criterion is not None:
                        criteria.append((at, cell, criterion))
                if not (read_pages or read_criteria):
                    continue
                for child in reversed(node.children):
                    if node is row:
                        child_cell = next((origin for c, origin in numbered if c is child), None)
                    else:
                        child_cell = cell
                    stack.append((child, child_cell, read_pages, read_criteria))
        return pages, criteria

    # -- the result --------------------------------------------------------

    def run(self) -> None:
        self.flow(self.root, Context())

    def finish(self) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        markdown = "\n".join(self.lines)
        if markdown:
            markdown += "\n"
        pages = list(self.pages)
        page_breaks = len(pages)
        opens_the_document = bool(pages) and pages[0]["line"] == 1
        if pages and self.lines and not opens_the_document:
            pages.insert(0, {"label": None, "line": 1})
        record = {
            "version": RECORD_VERSION,
            "headings": self.headings,
            "blocks": self.blocks,
            "tables": self.tables,
            "criteria": self.criteria,
            "page_breaks": page_breaks,
        }
        return markdown, record, pages


class _GridOmitted(Exception):
    pass


class HtmlTooLargeError(ValueError):
    pass


def page_records(markdown: str, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The page record of a conversion: each page numbered in order, beginning
    on the line its text begins, when that line holds text."""
    lines = len(markdown.split("\n")) - 1 if markdown.endswith("\n") else len(markdown.split("\n"))
    lines = lines if markdown else 0
    return [
        {
            "page_number": number,
            "start_line": page["line"] if page["line"] is not None and page["line"] <= lines else None,
            "label": page["label"],
        }
        for number, page in enumerate(pages, 1)
    ]


def convert_html(data: bytes) -> tuple[str, dict[str, Any], list[str]]:
    """HTML bytes as (Markdown, structure record, warnings).

    The record holds the page record under ``pages``.
    """
    if len(data) > MAX_HTML_BYTES:
        raise HtmlTooLargeError("html_too_large: the HTML file is larger than this converter reads")
    warnings: list[str] = []
    text = decode(data, warnings)
    root = parse(text)
    if depth(root) > MAX_DEPTH:
        raise ValueError("html_too_deep: the HTML nests elements deeper than this converter reads")
    extractor = Extractor(root, warnings)
    extractor.run()
    markdown, record, pages = extractor.finish()
    if _OTHER_LINE_BREAKS.search(markdown):
        # The index of this branch splits lines where `str.splitlines` does;
        # the record counts line feeds. Keep the two the same lines.
        markdown = _OTHER_LINE_BREAKS.sub(" ", markdown)
    record["pages"] = page_records(markdown, pages)
    return markdown, record, warnings


def markdown_to_text(markdown: str) -> str:
    """The plain-text sibling of the Markdown, as every extractor writes one."""
    return markdown


class HtmlStructureExtractor(BaseExtractor):
    def extract(self, file_path: Path) -> ExtractionResult:
        markdown, record, warnings = convert_html(Path(file_path).read_bytes())
        return ExtractionResult(
            markdown=markdown,
            text=markdown_to_text(markdown),
            warnings=warnings,
            metadata={"converter": CONVERTER_NAME},
            structure=record,
        )


def load_record(directory: Path) -> dict[str, Any] | None:
    """A document's structure record, from the directory of its extraction."""
    path = Path(directory) / STRUCTURE_FILE_NAME
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
