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
├── mrag.yaml                    # Project config — read this to understand the project
├── mrag.db                      # SQLite database — source of truth
├── profiles/
│   ├── default.yaml             # Retrieval profile (chunking, embedding, search)
│   └── context_prompt.txt       # Editable LLM prompt for contextual augmentation
├── data/documents/<doc-id>/     # Per-document: original file + extracted text
├── qdrant/                      # Qdrant storage
└── cache/embeddings/            # Optional embedding cache
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
3. **`mrag index`** — Embeds chunks and writes to SQLite FTS5 + Qdrant. Differential: only processes un-indexed documents. Always writes a JSON run log to `logs/` (timestamped). Use `--skip-list-json <log>` to skip documents that failed in a previous run.
4. **`mrag search <query>`** or **`mrag serve`** — Retrieve results. Output includes chunk text, score stats (min/max/mean/σ), and document distribution.

Use **`mrag eval <query>`** at any time after indexing for deeper quality inspection: duplicate chunk detection and multi-profile comparison.

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

### Qdrant mode determines whether an external server is needed

Check `mrag.yaml` for the `qdrant.mode` value before starting any indexing task.

```yaml
qdrant:
  mode: local    # default — no external process required
```

| Mode | Requirement |
|------|------------|
| `local` (default) | Nothing — Qdrant runs embedded in-process; data stored in `./qdrant/` |
| `server` | A running Qdrant server: `docker run -d -p 6333:6333 qdrant/qdrant` |

`mrag init` always creates `mode: local`. Projects without a `mode` key default to `server`.

`mrag search --strategy keyword` works without Qdrant in either mode.

### Ollama and the embedding model must be available for `mrag index`

```bash
ollama serve           # must be running
ollama pull bge-m3     # must be pulled before first index
```

If a profile has `augmentation.strategy: contextual`, the **generation model** must also be pulled before indexing:

```bash
ollama pull gemma4:e4b   # default augmentation model
```

The augmentation model is separate from the embedding model — it generates a short context text per chunk at index time. Embedding still uses `bge-m3` (or whatever is set in `embedding.model`).

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
mrag search "error handling retry"    # AND: chunks containing all three terms
mrag search "error handling"          # phrase: exact token sequence
```

- Input text is **NFKC-normalized** at both index time and query time. PDF files that use Kangxi radicals (e.g. ⼒ U+2F12) are automatically normalized to standard CJK (力 U+529B).

### `trigram` (universal fallback)

- Always available; no extra dependencies
- Matches any 3-character substring; no language-specific tokenization
- Works for any language but less precise for Japanese

---

## Chunking strategies

The `chunking.strategy` field in a profile controls how documents are split. Four strategies are available:

| Strategy | Best for | Notes |
|----------|----------|-------|
| `recursive` | Plain text, PDF | Default; splits by paragraph → line → character |
| `markdown_recursive` | Markdown with headings | Splits on heading boundaries first |
| `block_aware` | Markdown with tables / code | Parses into typed blocks; tables and code blocks are never split; attaches heading-path to every chunk |
| `parent_child` | Precise search + rich context | Indexes small child chunks; returns large parent chunks; deduplicates automatically. Must pair with `retrieval.strategy: parent_child`. |

**Block-aware preprocessing (universal)**

Setting `source_format: markdown` and enabling any `preserve_*` option wraps the inner chunker with block-aware preprocessing. This works for **all strategies** — not just `block_aware`:

```yaml
chunking:
  strategy: recursive          # or markdown_recursive, parent_child
  source_format: markdown
  chunk_size: 800
  overlap: 120
  preserve_heading_path: true  # attach H1 > H2 > H3 breadcrumb to each chunk
  preserve_tables: true        # keep tables atomic — never split mid-row
  preserve_code_blocks: true   # keep fenced code blocks atomic
```

The `block_aware` strategy name is a backward-compatible alias for `recursive` + `source_format: markdown` + all preserve options enabled.

When heading-path metadata is present, `mrag search` results include a `section:` line:

```
[1] score=0.8421  doc=manual.md  chunk=a3f2b1c4...
    section: SIM7080G > MQTT > KeepAlive
    MQTT keepalive settings can be configured with AT+CMQTTKEEPALIVE...
```

**Parent-child profile fields** (under `chunking:` and `retrieval:`):

```yaml
chunking:
  strategy: parent_child
  source_format: markdown   # optionally enable block-aware wrapping for child chunks
  parent:
    strategy: fixed_size      # fixed_size | section
    max_chars: 3000
  child:
    strategy: recursive
    chunk_size: 600
    overlap: 100

retrieval:
  strategy: parent_child
  top_k: 8
  dense_top_k: 60   # must be >= top_k * 3 — multiple children can map to same parent
  keyword_top_k: 60
```

Changing any `chunking` field (including `preserve_*`) invalidates the profile hash and triggers full re-indexing on the next `mrag index`.

---

## Search strategies

| Strategy | Description | Requires |
|----------|-------------|---------|
| `keyword` | FTS5 BM25 full-text search | SQLite only |
| `vector` | Dense vector similarity (Qdrant) | Qdrant + Ollama |
| `hybrid` | Keyword + vector fused via `retrieval.fusion: rrf` (default) or `weighted` | Qdrant + Ollama |
| `parent_child` | Fetches child chunks, resolves to parent chunks, deduplicates | Qdrant + Ollama; requires `chunking.strategy: parent_child` |

The default strategy is set in `profiles/default.yaml` under `retrieval.strategy`.

## Reranking

When `rerank.enabled: true` in a profile, mrag runs a CrossEncoder reranker (sentence-transformers) after retrieval. The reranker fetches `rerank.top_n` candidates, re-scores them, and the caller trims to the requested final count (`--top-k` for CLI or `top_k` for API requests).

Reranking is **retrieval-time only** — it does not affect the stored index. Changing `rerank` settings in a profile YAML takes effect on the next search without re-indexing.

**`max_length` and token limits.** BERT-based rerankers (including all `hotchpotch/japanese-reranker-cross-encoder-*` variants) have a hard limit of 514 position embeddings. `rerank.max_length: 512` (the default) tells the tokenizer to truncate inputs before they reach the model. Do not raise `max_length` above 512 for BERT-based models — doing so re-introduces the out-of-bounds crash.

**`parent_child` profiles.** When `retrieval.strategy: parent_child`, reranking operates on parent chunks (~3000 chars, ~1361 tokens) after parent resolution. The 512-token truncation discards most of the parent content, making reranking scores unreliable. mrag emits a `WARN` at runtime when `rerank.enabled: true` and `strategy: parent_child` are both active. For `parent_child` profiles, leave `rerank.enabled: false`.

To disable reranking at runtime:

```bash
mrag search "query" --no-rerank      # single search
mrag eval "query" --no-rerank        # evaluation run
mrag serve --no-rerank               # entire server session
```

Install the reranker dependency:

```bash
uv pip install -e ".[reranker]"
```

---

## Contextual Augmentation

When `augmentation.strategy: contextual` is set in a profile, mrag calls an Ollama LLM once per chunk during `mrag index` to generate a short context description. This context text is prepended to the chunk content before embedding, improving semantic retrieval without changing keyword search.

```yaml
augmentation:
  strategy: contextual   # none (default) | contextual
  provider: ollama
  model: gemma4:e4b      # generation model — separate from embedding.model
  endpoint: http://localhost:11434
  retry:                 # optional — these are the defaults
    max_attempts: 3
    initial_delay_seconds: 2.0
    backoff_multiplier: 2.0
    max_delay_seconds: 30.0
  failure_policy:        # optional — controls behavior when LLM fails after all retries
    mode: raw_fallback   # raw_fallback (default) | fail_document
```

The same `retry` block is available under `embedding`. Changing `retry` and `failure_policy` values does **not** invalidate the index (excluded from `profile_hash`).

**Key behaviour:**
- `strategy: none` (default) — no LLM call, indexing runs at normal speed
- `strategy: contextual` — one Ollama `/api/generate` call per chunk; expect slower indexing depending on model and document size
- Transient Ollama timeouts and HTTP 5xx errors are retried automatically; watch for `↻ retry` lines in the index log
- Documents with ≥ 300 chunks print a `⚠ large document` warning at the start of augmentation — this is informational
- FTS5 keyword index always stores the original chunk content — keyword search is unaffected by augmentation
- `variant_type` in the database is `contextual` (vs `raw` for non-augmented chunks)
- Changing `augmentation.strategy` changes the `profile_hash`, which triggers full re-indexing on the next `mrag index`

**Failure policy (`failure_policy.mode`):**

| Mode | Behaviour |
|------|-----------|
| `raw_fallback` (default) | Per-chunk LLM failure is recovered: chunk is stored as `raw` variant with original content; document indexing continues |
| `fail_document` | Per-chunk LLM failure propagates as an error; the whole document is marked `error` (pre-0.7 behaviour) |

When `raw_fallback` is active:
- A `⤵ fallback` line is printed in the index log for each affected chunk
- The `chunk_variants.metadata_json` column records `{"augmentation_status": "fallback_raw", "augmentation_error": "..."}` for auditability
- The document-level summary shows `(N raw fallback)` when any chunks fell back
- `IndexResult.raw_fallback_chunks` reflects the total count across all documents in the run

**Customising the prompt:**

`mrag init` creates `profiles/context_prompt.txt` with the default prompt template. Edit this file to tune the LLM's instructions for your domain. The template must contain `{document}` and `{chunk}` placeholders.

```bash
# Preview the default prompt
cat profiles/context_prompt.txt

# Edit to match your domain (e.g. Japanese technical documents)
nano profiles/context_prompt.txt
```

Changes to `context_prompt.txt` are picked up at the next `mrag index` run. Since the prompt is not part of `profile_hash`, editing it does not automatically trigger re-indexing — run `mrag reindex` manually if you want all existing chunks re-augmented with the new prompt.

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
| `Collection not found` error | Qdrant server not running (`mode: server`) | Start Qdrant, or switch to `mode: local` |
| Indexing fails with connection error | Ollama not running or embedding model not pulled | `ollama serve` + `ollama pull bge-m3` |
| Indexing fails with connection error (contextual) | Augmentation model not pulled | `ollama pull gemma4:e4b` (or whatever `augmentation.model` is set to) |
| `mrag index` very slow | `augmentation.strategy: contextual` calls LLM once per chunk | Expected; use `strategy: none` to skip augmentation |
| Contextual indexing fails on large documents | Many sequential LLM calls increase exposure to transient Ollama errors | Retry is automatic (3 attempts); increase `augmentation.retry.max_attempts` for very large documents; `failure_policy.mode: raw_fallback` (default) prevents document-level failure |
| Log shows many `↻ retry` lines | Ollama under load or model stalling | Retry is working; if all retries fail, `raw_fallback` stores raw variant instead of failing the document |
| Log shows `⤵ fallback` lines | Chunks that exceeded retry budget fell back to raw variant | Expected with noisy/table-heavy documents; check `chunk_variants.metadata_json` for `augmentation_status: fallback_raw` |
| Japanese query returns no results | Tokenizer mismatch or Kangxi radicals in PDF | NFKC normalization is automatic; verify `fts_tokenizer: vaporetto` |
| `401 Unauthorized` from API | `MRAG_API_KEY` set but key not sent | Add `Authorization: Bearer <key>` header |
| `mrag eval` hybrid scores all identical | RRF score range is always compressed (σ ≈ 0.001) | Use `--strategy vector` to see discriminative cosine scores |
| Reranker `ImportError` | `sentence-transformers` not installed | `uv pip install -e ".[reranker]"` |
| Contextual prompt not taking effect | `context_prompt.txt` edited but no reindex run | Run `mrag reindex` to re-augment all chunks with the new prompt |
| `mrag search` results have no `section:` line | `preserve_heading_path` not enabled or `source_format` is not `markdown` | Set `source_format: markdown` + `preserve_heading_path: true` in chunking config; run `mrag reindex` |
| Tables or code blocks split across chunks | `preserve_tables`/`preserve_code_blocks` not enabled, or `source_format` is not `markdown` | Set `source_format: markdown` + `preserve_tables: true` + `preserve_code_blocks: true`; run `mrag reindex` |
| `parent_child` profile validation error | `chunking.strategy` and `retrieval.strategy` must both be `parent_child` | Update the profile so both fields are `parent_child` |
| `parent_child` returning fewer results than `top_k` | `dense_top_k` / `keyword_top_k` too low; deduplication reduces candidates | Increase both to at least `top_k × 3` (e.g. `top_k: 8` → `dense_top_k: 60`) |
| Specific documents always fail during `mrag index` | Oversized PDF, too many chunks, or extraction issue | Pass the run log as a skip list: `mrag index --skip-list-json logs/<ts>-index.json`; investigate the failing document separately |
