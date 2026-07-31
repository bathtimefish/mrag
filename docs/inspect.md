# Inspecting the index — `mrag inspect`

This document covers the `mrag inspect` command group, used to examine indexed documents, chunks, and section structure.

`mrag inspect` is a **read-only** command group designed so that you can pull structured information out of the database from the CLI, without having to query SQLite by hand. Every subcommand supports `--json`, so it works equally well for human inspection and for AI-agent consumption.

> Prerequisite: run `mrag inspect` from inside a project directory (the one that contains `mrag.yaml` and `mrag.db`). At least one document must have gone through `mrag add` → `mrag index`.


## The four subcommands

| Subcommand | When to use |
|---|---|
| `mrag inspect document <doc-id>` | Find out, for a single document, "which profiles indexed it, how many chunks each produced, and whether augmentation is healthy" |
| `mrag inspect chunks <doc-id>` | List per-chunk metadata so you can decide which one to drill into |
| `mrag inspect chunk <chunk-id>` | See the full body, the LLM-generated context, and all metadata of a single chunk |
| `mrag inspect sections <doc-id>` | Visualize the heading hierarchy or the `parent_child` parent-child tree |


## `mrag inspect document` — per-document summary

```bash
# Regular table output
mrag inspect document <doc-id>

# JSON output
mrag inspect document <doc-id> --json

# Restrict to a specific profile
mrag inspect document <doc-id> --profile default
```

Main fields in the output:

- Filename, source type (md / txt), extraction provider (`plain`; pre-1.0 documents may show `pymupdf`)
- **Chunk count per profile** (`parent_child` profiles also show parent and child counts separately)
- **Augmentation status**: `succeeded` / `raw_fallback` counts
- **Embedding status** (v0.21.0+): `embedded` / `fallback_no_vector` counts

> The augmentation totals cover only variants where augmentation was actually attempted. Profiles with `augmentation.strategy: none` do not produce an Augmentation Status section.

> The Embedding Status section is only emitted when **at least one chunk has `fallback_no_vector`**. When every chunk was embedded successfully, the section is omitted (same rule as Augmentation Status). Chunks marked `fallback_no_vector` have no Qdrant point and will not appear in vector search, but they remain searchable via FTS5 keyword search (→ "Failure behavior — `embedding.failure_policy`" in [contextual-retrieval.md](./contextual-retrieval.md)).

When `--profile` is omitted, the command reports **every profile** under which the document has been indexed.


## `mrag inspect chunks` — chunk list

```bash
# List all chunks under a profile (default: no count limit)
mrag inspect chunks <doc-id> --profile default --json

# Page through a document with many chunks, 50 at a time
mrag inspect chunks <doc-id> --profile default --limit 50 --offset 0 --json
mrag inspect chunks <doc-id> --profile default --limit 50 --offset 50 --json

# Include the full chunk body (default output is lightweight, with no body)
mrag inspect chunks <doc-id> --profile default --show-content --json

# Also include the LLM-generated context text
mrag inspect chunks <doc-id> --profile default --show-context --json
```

Each entry in the output carries metadata such as `chunk_id` / `chunk_type` / `chunk_index` / `parent_chunk_id` / `char_count` / `token_count`. `--show-content` and `--show-context` add the chunk body and the LLM context text respectively.

The `variant` object contains these fields:

- `type` — `raw` / `contextual`
- `qdrant_collection` — Qdrant collection name this chunk belongs to
- `augmentation_status` — `fallback_raw` / `null`
- **`embedding_status`** — `fallback_no_vector` / `null` (v0.21.0+. Chunks with `fallback_no_vector` are excluded from vector search.)
- **`has_qdrant_point`** — `true` / `false` (v0.21.0+. `false` for fallback chunks.)

> Paging rule of thumb: for large documents with hundreds of chunks, prefer `--limit 50` so the terminal doesn't flood. For small- and mid-sized documents, you can omit the limit and dump everything.

> Profile resolution: when `--profile` is omitted, mrag auto-selects the profile if the document has been indexed under **exactly one** profile. If it's been indexed under multiple profiles, the command exits 1 with the candidate list — pass `--profile <name>` to disambiguate.


## `mrag inspect chunk` — show a single chunk

```bash
mrag inspect chunk <chunk-id> --json
```

This subcommand always returns the body and `context_text` together. Since `chunk_id` is the database's primary key, `--profile` is not needed (each chunk necessarily belongs to a specific profile).

A typical flow is to take the `chunk_id` from `mrag search --json` output and pass it directly to `mrag inspect chunk` to inspect the full body and the LLM context.


## `mrag inspect sections` — heading hierarchy / parent-child tree

```bash
# Visualize the heading hierarchy of a regular profile
mrag inspect sections <doc-id> --profile default

# Visualize the parent → child layers of a parent_child profile
mrag inspect sections <doc-id> --profile parent-child
```

- For regular profiles with `preserve_heading_path: true`, the heading hierarchy (e.g. `H1 > H2 > H3`) is shown as a tree
- For `parent_child` profiles, parent chunks are shown with their child chunks layered underneath

> If the profile has no heading metadata (`preserve_heading_path: false`), the command exits 1 with "no section structure" — fall back to `mrag inspect chunks` for a flat listing.


## Recommended pattern — two-stage workflow

When you're driving inspection from an agent, or just want to scan a document, going in two stages — **fetch metadata → visualize chunks** — is the most efficient flow.

```bash
# Stage 1: fetch metadata only, then filter to chunks of interest
mrag inspect chunks <doc-id> --profile default --json \
  | jq '.chunks[] | select(.metadata.contains_table or .metadata.contains_code)'

# Stage 2: fetch body + LLM context for the candidate chunks
mrag inspect chunk <chunk-id> --json
```

This flow has two benefits:

- Stage 1 returns lightweight metadata only, so the output stays small even for documents with hundreds of chunks
- Stage 2 fetches the body only for the handful of chunks you actually need, which keeps an agent's context window lean


## Finding a document ID

Noting down the `document_id` printed by `mrag add` is the easiest path, but you can also recover it later:

```bash
# Query SQLite directly
sqlite3 mrag.db "SELECT id, filename FROM documents;"

# Or via the API
GET /api/v1/documents
```


## Tips

- Every subcommand supports `--json`, with **stdout for the payload and stderr for warnings and errors** — easy to use in pipe-based scripts.
- `mrag inspect chunks --show-context --json | jq` is handy for surveying contextual augmentation output (`context_text`) at a glance (→ [contextual-retrieval.md](./contextual-retrieval.md)).
- To check the structure of a `parent_child` profile, `mrag inspect sections` is the most direct path — it makes the chunk granularity (parent / child) and count balance easy to read.
- Reviewing the extraction outcome (filename, extraction provider visible in `mrag inspect document`) is a quick way to verify, before and after `mrag index`, that the document was ingested as intended.
