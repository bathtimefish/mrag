# AGENTS.md — mrag Agent Reference

This document describes how AI agents should understand and interact with **mrag** — a local-first RAG retrieval runtime. Read this before using any mrag CLI command or API endpoint.

---

## What mrag does

mrag manages a project-scoped knowledge base. Each project has:
- A **SQLite database** (`mrag.db`) as the authoritative source of documents, chunks, and FTS5 index
- A **Qdrant vector index** (local server) that is rebuildable at any time from SQLite
- A **retrieval profile** (`profiles/default.yaml`) that controls chunking, embedding, and search strategy

One project = one knowledge base. Do not mix documents from unrelated domains in the same project.

---

## Project layout

```
<project-dir>/
├── mrag.yaml                  # Project config — read this to understand the project
├── mrag.db                    # SQLite database — source of truth
├── profiles/
│   └── default.yaml           # Retrieval profile (chunking, embedding, search)
├── data/documents/<doc-id>/   # Per-document: original file + extracted text
├── qdrant/                    # Qdrant storage
└── cache/embeddings/          # Optional embedding cache
```

**Always `cd` into the project directory** before running any mrag command. All commands resolve `mrag.yaml` and `mrag.db` relative to the current working directory.

---

## Lifecycle: the four steps

Every mrag project follows this sequence. Steps must be executed in order.

```
mrag init   →   mrag add   →   mrag index   →   mrag search / mrag serve
```

1. **`mrag init --name <name>`** — Creates `<name>/` subdirectory in cwd. Run from the *parent* directory.
2. **`mrag add <file>`** — Extracts text and stores metadata. No indexing yet.
3. **`mrag index`** — Embeds chunks and writes to SQLite FTS5 + Qdrant. Differential: only processes un-indexed documents.
4. **`mrag search <query>`** or **`mrag serve`** — Retrieve results.

---

## Critical constraints

### `mrag init` creates a subdirectory — not in-place

```bash
# CORRECT: run from parent directory
cd /workspace
mrag init --name my-kb
cd my-kb          # project is at /workspace/my-kb

# WRONG: running from inside the intended project directory
cd /workspace/my-kb
mrag init --name my-kb   # creates /workspace/my-kb/my-kb — double nesting
```

### `mrag index` must run before any search

`mrag add` only extracts text. Searching before `mrag index` returns zero results.

### Qdrant must be running for `mrag index` and `mrag serve`

```bash
# Start Qdrant (Docker)
docker run -d -p 6333:6333 qdrant/qdrant
```

If Qdrant is unavailable, `mrag index` fails. `mrag search --strategy keyword` still works without Qdrant.

### Ollama and the embedding model must be available for `mrag index`

```bash
ollama serve           # must be running
ollama pull bge-m3     # must be pulled before first index
```

### Do not change the tokenizer after `mrag init`

The FTS5 tokenizer (`vaporetto` or `trigram`) is chosen at init time and stored in `mrag.yaml`. Changing it after indexing causes MATCH failures. To change tokenizer: `mrag reindex` after updating the profile YAML.

---

## Tokenizer behaviour

mrag supports two FTS5 tokenizers. The active tokenizer is shown in `mrag.yaml` as `fts_tokenizer`.

### `vaporetto` (Japanese morphological — preferred)

- Requires: `libsqlite_vaporetto.dylib` in `~/.mrag/extensions/` and `apsw` installed
- Tokenizes Japanese text into morphemes; handles compound words correctly
- **Space-separated query terms = AND** (each term independently matched)
- **Continuous Japanese text = phrase query** (all tokens must appear adjacent)

```bash
mrag search "接点出力 ON OFF"   # AND: chunks containing all three terms
mrag search "接点出力のON制御"  # phrase: exact token sequence
```

- Input text is **NFKC-normalized** at both index time and query time. PDF files that use Kangxi radicals (e.g. ⼒ U+2F12) are automatically normalized to standard CJK (力 U+529B).

### `trigram` (universal fallback)

- Always available; no extra dependencies
- Matches any 3-character substring; no language-specific tokenization
- Works for any language but less precise for Japanese

---

## Search strategies

| Strategy | Description | Requires |
|----------|-------------|---------|
| `keyword` | FTS5 BM25 full-text search | SQLite only |
| `vector` | Dense vector similarity (Qdrant) | Qdrant + Ollama |
| `hybrid` | RRF fusion of keyword + vector (default) | Qdrant + Ollama |

The default strategy is set in `profiles/default.yaml` under `retrieval.strategy`.

---

## Document IDs

Document IDs are UUID strings (e.g. `91f28863-b47d-44b6-a534-820b46f06aae`). Retrieve them with:

```bash
mrag profiles list                        # check what is indexed
# or query SQLite directly:
sqlite3 mrag.db "SELECT id, filename FROM documents;"
# or via API:
GET /api/v1/documents
```

`mrag remove <doc-id>` is a dry-run by default. Pass `--force` to actually delete.

---

## Reading project state

Use `mrag doctor` to check the full environment:

```bash
mrag doctor
```

Read `mrag.yaml` to understand project configuration:

```bash
cat mrag.yaml
```

Key fields in `mrag.yaml`:

| Field | Meaning |
|-------|---------|
| `knowledge_base.id` | Unique KB identifier used for FTS5 and Qdrant namespacing |
| `fts_tokenizer` | `vaporetto` or `trigram` — do not change after init |
| `default_profile` | Which profile YAML to use when `--profile` is omitted |
| `qdrant.host/port` | Qdrant connection (default: localhost:6333) |

---

## API server

Start with `mrag serve` (must be run from inside the project directory).

```bash
cd my-kb
mrag serve --port 8000
```

**All API requests must include `Content-Type: application/json`** for POST endpoints.

If `MRAG_API_KEY` is set in the environment before starting the server, all requests require:
```
Authorization: Bearer <key>
```

The interactive API docs are available at `http://127.0.0.1:8000/docs`.

---

## Common failure patterns

| Symptom | Cause | Fix |
|---------|-------|-----|
| `mrag.yaml not found` | Not in project directory | `cd <project-dir>` |
| Zero search results | `mrag index` not run, or wrong tokenizer | Run `mrag index`; check `fts_tokenizer` in `mrag.yaml` |
| `Collection not found` error | Qdrant not running | Start Qdrant |
| Indexing fails with connection error | Ollama not running or model not pulled | `ollama serve` + `ollama pull bge-m3` |
| Japanese query returns no results | Tokenizer mismatch or Kangxi radicals in PDF | NFKC normalization is automatic; verify `fts_tokenizer: vaporetto` |
| `401 Unauthorized` from API | `MRAG_API_KEY` set but key not sent | Add `Authorization: Bearer <key>` header |
