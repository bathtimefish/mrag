# Chunking strategies

This document covers the four chunking strategies implemented in mrag.

Chunking is the process of slicing documents into search-friendly units. The tutorial ran with the default settings, but the right way to slice depends on what kind of document you are working with (plain text, Markdown, converted documents containing tables, and so on).

In mrag, the strategy is selected via the `chunking.strategy` field in `profiles/<profile-name>.yaml`.


## The four strategies

| Strategy | Intended format | Description |
|----------|-----------------|-------------|
| `recursive` | Plain text | Default. Splits recursively in the order paragraph → line → character |
| `markdown_recursive` | Markdown with headings | Splits on heading boundaries first |
| `block_aware` | Markdown containing tables and code blocks | Recognizes paragraphs, tables, and code as typed "blocks"; tables and code that fit within `chunk_size` are never split mid-block |
| `parent_child` | Cases that need both precise retrieval and rich context | Small child chunks score the hit; the larger parent chunk is what gets returned |

Quick selection guide:

- **Mostly plain or assorted text formats** → `recursive`
- **Documents with clear heading structure** → `markdown_recursive`
- **Manuals or specifications with many tables and code blocks** → `block_aware`
- **You want to surface broader surrounding context on a hit** → `parent_child`

> When you choose `parent_child`, you must also set `retrieval.strategy` to `parent_child` to match.

## `recursive` — the default

The most general-purpose strategy for plain and unstructured text.

The default profile created by `mrag init` uses this strategy with the sizes below. It also writes `source_format: markdown` and sets the three `preserve_*` options to `true`, so the block-aware preprocessing described later on this page is applied on top of it.

```yaml
chunking:
  strategy: recursive
  chunk_size: 800       # max characters per chunk
  overlap: 120          # characters of overlap between adjacent chunks (softens context breaks)
```

How it works:

1. First tries to split on paragraphs (blank-line boundaries)
2. Paragraphs that don't fit in `chunk_size` are split further by line
3. Lines that still don't fit are split by character
4. Adjacent chunks overlap by `overlap` characters

Because it prefers natural boundaries (paragraph → line) while still accommodating long text, this strategy is highly general-purpose.

> About the options: increasing `chunk_size` packs more information per chunk but lowers retrieval precision (the resolution of *where* in the document a fact lives). `overlap` is the buffer that reduces meaning being cut at chunk boundaries — around 10–20% of `chunk_size` is a safe default.


## `markdown_recursive` — for Markdown

A strategy for Markdown documents with structured headings (`#`, `##`, ...). To **prevent chunks from spanning a heading boundary**, it first splits the document at headings and then applies the same recursive splitting as `recursive` within each section.

```yaml
chunking:
  strategy: markdown_recursive
  chunk_size: 800
  overlap: 120
```

How it works:

1. Split the document into sections by heading
2. Split the text within each section using the same logic as `recursive`
3. As a result, no single chunk ever spans multiple headings


## `block_aware` — protect tables and code blocks

A strategy that **does not split tables and code blocks mid-way** in Markdown, as long as they fit within `chunk_size`. It suits documents like manuals, API references, and datasheets where tables and code carry the core meaning.

```yaml
chunking:
  strategy: block_aware
  chunk_size: 800
  overlap: 120
```

How it works:

1. Parse the Markdown into typed blocks — paragraphs, headings, tables, code blocks, etc.
2. A table or code block that fits within `chunk_size` is **never split** (it may share a chunk with neighbouring blocks). One that exceeds `chunk_size` is split: a table row by row, with the header rows repeated in every part under a `[Table: H1 > H2 (n/N)]` label, and a code block into pieces that are each wrapped in their own code fence
3. Other blocks are split using the same logic as `recursive`
4. Each chunk is annotated with its heading path (`H1 > H2 > H3`) as metadata


> The features of `block_aware` can also be applied as preprocessing on top of other chunking strategies by setting `chunking.source_format: markdown` (the default is `text`). `chunking.preserve_heading_path`, `chunking.preserve_tables`, and `chunking.preserve_code_blocks` select which parts apply; all three default to `true`.


## `parent_child` — precise retrieval with rich context

A strategy that **scores the hit with small child chunks and returns the larger parent chunk on display**. Effective for long documents where you want "precise queries to land on the right spot, but the returned context to be wide".

```yaml
chunking:
  strategy: parent_child
  source_format: markdown        # to apply block-aware preprocessing to child chunks
  parent:
    strategy: fixed_size         # fixed_size | section
    max_chars: 3000
  child:
    strategy: recursive
    chunk_size: 600
    overlap: 100

retrieval:
  strategy: parent_child         # ← must be set in tandem (important)
  top_k: 8
  dense_top_k: 60                # leave headroom — multiple children collapse into one parent
  keyword_top_k: 60
```

How it works:

1. Split the document into **parent chunks** (around `max_chars` ≒ 3000 characters)
2. Further split each parent into **child chunks** (around `chunk_size` ≒ 600 characters)
3. Index **only the child chunks** (embedding vectors are also at the child level)
4. At query time, match against child chunks and finally **return the parent chunk** the matched child belongs to
5. Multiple children matched under the same parent are deduplicated down to one parent

`parent.strategy` comes in two flavors:

- **`fixed_size`** — split parents mechanically by character count (default)
- **`section`** — split parents at Markdown heading boundaries (works well for Markdown with headings)

> Important: `chunking.strategy: parent_child` and `retrieval.strategy: parent_child` must be **set together**. Setting only one triggers a profile validation error.

> Note: `dense_top_k` / `keyword_top_k` are eventually narrowed down to `top_k`, so set them to roughly `top_k × 3` to keep the candidate net wide at the child level (you need extra candidates at the retrieval stage because multiple children can collapse into the same parent).

> Note: combining a `parent_child` profile with `rerank.enabled: true` truncates the ~3000-character parent chunk down to 512 tokens at the reranker, which degrades the reliability of rerank scores. Keep this in mind when using `parent_child`.


## Block-aware preprocessing (cross-strategy)

The table and code block handling described under `block_aware` (kept whole up to `chunk_size`, split by row or into fenced pieces above it) can be added to **any strategy**. The switch is `source_format`: it defaults to `text`, and setting it to `markdown` turns the preprocessing on. The `preserve_*` options default to `true`; set one to `false` to turn that part off:

```yaml
chunking:
  strategy: recursive             # also works with markdown_recursive or parent_child
  source_format: markdown
  chunk_size: 800
  overlap: 120
  preserve_heading_path: true     # attach the H1 > H2 > H3 breadcrumb to each chunk
  preserve_tables: true           # keep tables whole while they fit within chunk_size
  preserve_code_blocks: true      # keep fenced code blocks whole while they fit within chunk_size
```

With `parent_child`, the limit that applies here is `parent.max_chars` and it governs the parent chunks; child chunks are still cut at `child.chunk_size`, so a child can end inside a table or code block.

This lets you use `recursive` while still selectively asking "I just want tables protected" or "I just want the heading path".


## What happens when you change settings

When any `chunking.*` field changes, the **profile's hash value changes**, which triggers a **full re-index of that profile** on the next `mrag index` run.

This is a safety mechanism — previously indexed chunks lose their meaning once the chunking conditions change. Re-indexing takes time, so when iterating on settings against a large knowledge base, it is more efficient to create a separate profile and a separate index, then compare performance side by side:

```bash
mrag index --profile experimental
mrag search "your query" --profile experimental
```
