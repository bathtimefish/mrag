# HTML with its structure kept (experimental)

This page describes the experimental branch `exp/html-structure`. It is not
part of a release, and what it describes may change or be removed.

A table row says a value; which quantity it is, and what it belongs to, are in
the header row above and the header cell beside it, and a chunk does not always
hold those. The `html-structure` extractor reads an HTML file and keeps them:
search results then give each table row under its headers, the values the
document marks as criteria with the labels and printed page of each, and the
printed page numbers a result's text is on.

It is a port of part of MRAG Plus's converter of the same name, held to the
same outputs by the fixtures in `tests/fixtures/html_parity/`.

## Turning it on

```sh
pip install 'mrag[html]'      # the html5lib parser
```

and in `mrag.yaml`:

```yaml
conversion:
  converter:
    html: html-structure
```

Then `mrag add page.html` (and `.htm`) reads the file in-process: no script
runs and nothing outside the file is fetched. Without the setting, HTML is
refused as before. The profile must chunk Markdown (`source_format: markdown`,
the default of `mrag init`); under `text` no structure is given.

## What a search result gives

`mrag search --json`, `POST /api/v1/retrieve` and the MCP `search` tool give
every result these fields; they are `null` or empty for a document without a
structure record.

- `structured_content` — the result's text with every table row written as
  `・row labels: 「column label」= value；…`, the header rows and the rule under
  them dropped, and the table's titles put in front where the text does not
  hold them. Row labels are the row's header cells (`th`), however many header
  rows the table has; in a table without header rows a value has no column
  label: `・Beans: 10 to 15；weekly`. A row without header cells stays as it is.
  Searching does not read this text: the keyword index and the embedding read
  the rows as the document wrote them.
- `criteria`, `criteria_omitted` — the values on the result's lines that the
  document marks with `data-criterion`, at most 15, each with its `limit`
  (`min`, `max`, `range`, `exact` or `tolerance`, from `data-limit`), `unit`
  (`data-unit`), `quantity` (`data-quantity`), the `row_labels` and
  `column_labels` a table puts it under, its `table_caption`, the `heading`
  above it, and its `page` and printed `page_label`.
- in `reference`, `page_start`, `page_end`, `page_label_start` and
  `page_label_end` — the pages the text is on, from the page breaks the
  document marks (`role="doc-pagebreak"` or `epub:type="pagebreak"`, named by
  `aria-label`, `title` or their text).

```html
<span role="doc-pagebreak" aria-label="12"></span>
<table><caption>Spacing</caption>
<thead><tr><th>Crop</th><th>Gap</th></tr></thead>
<tr><th>Beans</th><td><span data-criterion data-limit="range" data-unit="cm">10 to 15</span></td></tr>
</table>
```

## What is not ported

MRAG Plus's `html-structure` also keeps definitions (`dfn`) and the places
that refer to them, the identifiers of named elements, links inside the
document, and JSON-LD; it cuts chunks along the document's structure, and
offers `inspect` commands and MCP resources for them. None of these is on this
branch.

## Differences from MRAG Plus by design

- A chunk is cut by this project's chunkers, so its text — and with it its
  `structured_content` and the criteria on its lines — follows them. A split
  table's part carries its `[Table: …]` label in `structured_content` too.
- Characters other than the line feed that Python reads as line breaks
  (`\x0b`, `\x0c`, `\x1c`–`\x1e`, `\x85`, ` `, ` `) are written as
  spaces in the Markdown, so that the chunker's lines are the record's.
