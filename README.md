# mrag — Micro RAG

**A lightweight, local-first retrieval runtime for building RAG pipelines.**

mrag is a CLI for building and operating small-scale RAG knowledge bases. It provides everything from document indexing to search, with a variety of strategies for building custom RAG pipelines to fit your needs. Skills for AI agents let you expose your knowledge base to any AI agent.

---

## Features

- **Hybrid retrieval** — keyword (FTS5 BM25), vector (dense), or RRF-fused hybrid
- **Japanese tokenizer for SQLite** — supports [sqlite-vaporetto](https://github.com/daac-tools/sqlite-vaporetto) (morphological analysis)
- **Multilingual embeddings** — defaults to `bge-m3` via [Ollama](https://ollama.com) (any Ollama-compatible model works)
- **Differential indexing** — re-running `mrag index` skips already-indexed documents
- **Retrieval profiles** — per-project YAML profiles control chunking, embedding, and retrieval strategy independently
- **Block-aware chunking** — any chunking strategy can preserve tables, fenced code blocks, and heading-path metadata when `source_format: markdown` is set; `mrag search` results show `section: H1 > H2 > H3` breadcrumbs
- **Parent-child retrieval** — indexes small child chunks for precision and returns large parent chunks for context; eliminates duplicates automatically
- **Contextual augmentation** — optional index-time LLM context generation per chunk (Anthropic contextual retrieval pattern); prompt is editable per project via `profiles/context_prompt.txt`
- **Reranking** — optional CrossEncoder reranking (sentence-transformers) after retrieval; disabled per-request with `--no-rerank`
- **Retrieval evaluation** — `mrag eval` inspects retrieval quality: scores, duplicates, document distribution, multi-profile diff
- **Dify External Knowledge API** — `mrag serve` starts a [Dify External Knowledge API](https://docs.dify.ai/ja/use-dify/knowledge/external-knowledge-api) server; use mrag as an external knowledge source in Dify

---

## Requirements

| Component | Notes |
|-----------|-------|
| Python 3.11+ | |
| [Ollama](https://ollama.com) | `ollama serve` must be running; uses `bge-m3` and `gemma4:e4b` |
| [Qdrant](https://qdrant.tech) | Docker-based Qdrant required only for `mode: server` |

---

## Installation

mrag is installed from source. [uv](https://docs.astral.sh/uv/) is recommended.

```bash
git clone https://github.com/bathtimefish/mrag.git
cd mrag
uv venv
uv pip install -e ".[vaporetto,reranker]"
```

This installs mrag with Japanese morphological tokenization (`vaporetto`) and CrossEncoder reranking (`reranker`) included as standard.

### Vaporetto native library

The `vaporetto` extra installs `apsw` (required for SQLite extension loading on macOS), but the native shared library must be placed separately:

1. Go to [sqlite-vaporetto releases](https://github.com/hotchpotch/sqlite-vaporetto/releases) and download the latest **`-with-model.tar.gz`** for your OS and architecture.

   | OS / arch | File |
   |-----------|------|
   | macOS (Apple Silicon) | `sqlite-vaporetto-vX.Y.Z-macos-aarch64-with-model.tar.gz` |
   | macOS (Intel) | `sqlite-vaporetto-vX.Y.Z-macos-x86_64-with-model.tar.gz` |
   | Linux (x86_64) | `sqlite-vaporetto-vX.Y.Z-linux-x86_64-with-model.tar.gz` |

   > Use the **`-with-model`** variant — it includes the tokenization model data required for Japanese morphological analysis.

2. Extract the archive and place the shared library in `~/.mrag/extensions/`:

   ```bash
   tar -xzf sqlite-vaporetto-vX.Y.Z-macos-aarch64-with-model.tar.gz
   EXTRACTED_DIR=$(tar -tzf sqlite-vaporetto-vX.Y.Z-macos-aarch64-with-model.tar.gz | head -1 | cut -d/ -f1)
   mkdir -p ~/.mrag/extensions
   cp "${EXTRACTED_DIR}/libsqlite_vaporetto.dylib" ~/.mrag/extensions/   # macOS
   # cp "${EXTRACTED_DIR}/libsqlite_vaporetto.so" ~/.mrag/extensions/    # Linux
   ```

Or point to a custom path via the environment variable:

```bash
export MRAG_VAPORETTO_LIB=/path/to/libsqlite_vaporetto.dylib
```

If vaporetto is not available at `mrag init` time, mrag falls back to the trigram tokenizer automatically. Run `mrag doctor` to confirm which tokenizer was detected.

### With Marker (optional, for scanned / complex-layout PDFs)

```bash
uv pip install -e ".[marker]"
```

Enables `--extractor marker` in `mrag add` for high-accuracy extraction of scanned or complex-layout PDFs.

### Pull the default embedding model

```bash
ollama pull bge-m3
```

`bge-m3` is a multilingual model (1024-dim) that works well across Japanese, English, and other languages. Any Ollama-compatible embedding model can be substituted in the profile YAML.

> **No Docker needed for Qdrant.** mrag defaults to `qdrant.mode: local`, which runs Qdrant embedded in-process and stores data in the project's `qdrant/` directory. Docker is only required if you explicitly set `qdrant.mode: server`.

---

## Quick Start

### 1. Initialize a project

```bash
mrag init --name my-project
cd my-project
```

`mrag init` creates a `my-project/` subdirectory in the current directory with:
- `mrag.yaml` — project configuration
- `profiles/default.yaml` — retrieval profile
- `profiles/context_prompt.txt` — LLM prompt template for contextual augmentation (editable)
- `mrag.db` — SQLite database
- Supporting directories (`data/`, `qdrant/`, `cache/`, …)

The tokenizer is auto-detected at init time. If vaporetto is found, the project is configured to use it:

```
✓ vaporetto tokenizer detected (libsqlite_vaporetto.dylib)
✓ Created directory structure
✓ Generated mrag.yaml
✓ Generated profiles/default.yaml
✓ Initialized mrag.db
```

### 2. Add documents

```bash
mrag add report.pdf
mrag add manual.pdf notes.txt
```

Documents are extracted and stored in `data/documents/`. Supported formats: PDF, plain text, Markdown.

**Extractor options (PDF only)**

| Extractor | Option | Notes |
|-----------|--------|-------|
| PyMuPDF | `--extractor pymupdf` | Default. Fast text-layer extraction. Warns if the PDF appears to be scanned/image-based. |
| Marker | `--extractor marker` | High-accuracy extraction for complex layouts. Requires `uv pip install -e ".[marker]"`. |

```bash
# Use the default extractor (PyMuPDF)
mrag add report.pdf

# Use Marker for a scanned or complex-layout PDF
mrag add scanned.pdf --extractor marker

# Re-add an already-registered document (overwrites extracted content)
mrag add report.pdf --force
```

The default extractor can be set project-wide in `mrag.yaml` under `default_extraction.pdf.provider`.

### 3. Build the index

```bash
mrag index
```

This embeds all un-indexed documents and builds the FTS5 + Qdrant index:

```
✓ Indexed: 12  Up-to-date: 0  List-skipped: 0
Log: logs/20260514103000-index.json
```

Every run writes a JSON log to `logs/` automatically. Re-running `mrag index` after adding new files only processes the new documents.

To skip documents that consistently fail (e.g. oversized PDFs), pass the previous run's log as a skip list:

```bash
mrag index --skip-list-json logs/20260514103000-index.json
```

### 4. Search

```bash
# Hybrid (default)
mrag search "installation guide"

# Keyword only
mrag search "error handling retry" --strategy keyword

# Vector only
mrag search "temperature sensing" --strategy vector

# Limit results
mrag search "Bluetooth LE" --top-k 3

# Disable reranking for this search
mrag search "installation guide" --no-rerank
```

### 5. Evaluate retrieval quality (optional)

`mrag search` already outputs score stats (min/max/mean/σ) and document distribution after each query. Use σ as a signal: a low σ means results are clustered and the query may be too broad; a high σ means a top result clearly stands out.

Use `mrag eval` for deeper analysis: duplicate chunk detection and multi-profile comparison:

```bash
mrag eval "installation guide" --profile default --profile second --strategy vector
```

### 6. Serve as an API

```bash
mrag serve

# Disable reranking for all API requests
mrag serve --no-rerank
```

Starts a FastAPI server at `http://127.0.0.1:8000`. See [API Reference](#api-reference) below.

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `mrag init [--name NAME]` | Create a new project in a subdirectory |
| `mrag add <file> [file…] [--extractor pymupdf\|marker] [--force]` | Ingest documents (extract & store; no indexing) |
| `mrag index [--profile P] [--output-log PATH] [--skip-list-json PATH]` | Differential index (skips up-to-date docs); always writes a JSON run log |
| `mrag reindex [--profile P] [--output-log PATH] [--skip-list-json PATH]` | Force-rebuild the entire index for a profile; always writes a JSON run log |
| `mrag search <query>` | Search (`--strategy keyword\|vector\|hybrid`, `--top-k N`, `--no-rerank`) |
| `mrag eval <query>` | Evaluate retrieval quality (`--profile P`, `--strategy S`, `--top-k N`, `--no-rerank`) |
| `mrag serve` | Start FastAPI server (`--host`, `--port`, `--no-rerank`) |
| `mrag remove <doc-id>` | Dry-run removal (use `--force` to actually delete) |
| `mrag profiles list` | List profiles registered in the database |
| `mrag profiles show <name>` | Show profile configuration |
| `mrag extract <file>` | Preview extracted text (dry-run, nothing stored) |
| `mrag show-extracted <doc-id>` | Print stored extracted content |
| `mrag export-extracted <doc-id>` | Export extracted content to file |
| `mrag doctor` | Check environment (SQLite, vaporetto, Qdrant, Ollama) |

---

## API Reference

Start the server with `mrag serve` then call:

### `POST /api/v1/retrieve`

Retrieve chunks from the knowledge base.

**Request body:**

```json
{
  "query": "access control policy",
  "strategy": "hybrid",
  "top_k": 5,
  "profile": "default"
}
```

`strategy` — `"hybrid"` (default), `"keyword"`, or `"vector"`  
`profile` — profile name; defaults to the project's `default_profile`

**Response:**

```json
{
  "query": "access control policy",
  "profile": "default",
  "strategy": "hybrid",
  "results": [
    {
      "chunk_id": "...",
      "document_id": "...",
      "filename": "manual.pdf",
      "score": 6.39,
      "content": "…access control policy defines the permitted operations…",
      "metadata": {}
    }
  ]
}
```

`POST /api/v1/search` is an alias for the same endpoint.

### `GET /api/v1/documents`

List all documents in the knowledge base.

### `GET /api/v1/documents/{document_id}`

Get a document by ID, including chunk count.

### `GET /api/v1/profiles`

List retrieval profiles registered in the database.

### `GET /api/v1/profiles/{profile_name}`

Get full profile configuration.

### Authentication

Set the `MRAG_API_KEY` environment variable before starting the server to require Bearer-token authentication:

```bash
MRAG_API_KEY=your-secret-key mrag serve
```

All requests must then include:

```
Authorization: Bearer your-secret-key
```

Requests without a valid key return `401 Unauthorized`.

---

## Dify External Knowledge API

`mrag serve` implements the [Dify External Knowledge API](https://docs.dify.ai/ja/use-dify/knowledge/external-knowledge-api) spec out of the box. You can connect a running mrag instance directly to Dify as an external knowledge source — no adapter layer required.

### `POST /retrieval`

**Request body:**

```json
{
  "knowledge_id": "<your-knowledge-id>",
  "query": "access control policy",
  "retrieval_setting": {
    "top_k": 5,
    "score_threshold": 0.5
  }
}
```

`knowledge_id` — must match the `knowledge_id` value in `mrag.yaml`  
`top_k` — maximum number of results (1–100)  
`score_threshold` — minimum normalized score (0.0–1.0); results below this value are filtered out

**Response:**

```json
{
  "records": [
    {
      "content": "…access control policy defines the permitted operations…",
      "score": 0.87,
      "title": "manual.pdf",
      "metadata": {}
    }
  ]
}
```

All scores are normalized to `[0, 1]`: BM25 keyword scores are mapped via `score / (1 + score)`; vector and hybrid scores are already in range and clamped.

**Error responses** follow the Dify spec:

| HTTP | `error_code` | Meaning |
|------|-------------|---------|
| 404 | 2001 | `knowledge_id` not found |
| 401 | 1001 | Missing or malformed `Authorization` header |
| 401 | 1002 | Wrong API key |

### Connecting mrag to Dify

1. Start the server bound to all interfaces so Docker can reach it:
   ```bash
   MRAG_API_KEY=your-secret-key mrag serve --host 0.0.0.0 --port 8000
   ```
2. In Dify, go to **Knowledge → External Knowledge API → Add**.
3. Set **Endpoint URL** — the value depends on how Dify is deployed:

   | Dify deployment | Endpoint URL |
   |-----------------|-------------|
   | Docker Desktop (macOS / Windows) | `http://host.docker.internal:8000` |
   | Docker on Linux | `http://172.17.0.1:8000` (docker0 bridge) |
   | Same LAN / VM | `http://<host-LAN-IP>:8000` |

   > `http://127.0.0.1:8000` will **not** work — inside the Dify container, `127.0.0.1` refers to the container itself, not your host machine.

4. Set **API Key** to the value of `MRAG_API_KEY` (leave blank if auth is not enabled).
5. Use the `knowledge_id` from `mrag.yaml` when creating the knowledge base in Dify.

---

## Retrieval Profiles

A profile is a YAML file in `profiles/` that controls chunking, embedding, and retrieval. The default profile generated by `mrag init`:

```yaml
name: default

chunking:
  strategy: recursive
  source_format: markdown
  chunk_size: 800
  overlap: 120
  preserve_heading_path: true
  preserve_tables: true
  preserve_code_blocks: true

embedding:
  provider: ollama
  model: bge-m3
  endpoint: http://localhost:11434

retrieval:
  strategy: hybrid
  top_k: 8
  dense_top_k: 20
  keyword_top_k: 20
  fusion: rrf

augmentation:
  strategy: none            # none (default) | contextual

keyword:
  provider: sqlite_fts5
  tokenizer: vaporetto       # set at init time; trigram if vaporetto not found
  fallback_tokenizer: trigram
```

Create additional profiles by placing new YAML files in `profiles/` and indexing with `--profile <name>`.

### Chunking Strategies

The `chunking.strategy` field controls how documents are split into chunks before indexing. Four strategies are available:

| Strategy | Description |
|----------|-------------|
| `recursive` | Splits text recursively by separator hierarchy (paragraphs → line breaks → sentences). Best for plain text and PDF. **Default.** |
| `markdown_recursive` | Splits first by Markdown heading structure, then applies recursive splitting within each section. Use with `source_format: markdown`. |
| `block_aware` | Markdown-aware; parses the document into typed blocks (heading, paragraph, table, code block, …) and groups them into chunks. Tables and fenced code blocks are kept intact as atomic units. Heading path is embedded in chunk metadata and shown in search results. Use with `source_format: markdown`. |
| `parent_child` | Indexes small **child chunks** for precise search and returns large **parent chunks** as context. Eliminates duplicate parent results automatically. Must be paired with `retrieval.strategy: parent_child`. |

**Configuration fields:**

```yaml
chunking:
  strategy: recursive       # recursive | markdown_recursive | block_aware | parent_child
  source_format: markdown   # text | markdown  (default: markdown)
  chunk_size: 800           # target chunk size in characters
  overlap: 120              # overlap between adjacent chunks in characters
  # --- Block-aware options: active for any strategy when source_format: markdown ---
  preserve_heading_path: true   # attach H1 > H2 > H3 breadcrumb to each chunk (default: true)
  preserve_tables: true         # keep tables as atomic units (default: true)
  preserve_code_blocks: true    # keep fenced code blocks as atomic units (default: true)
  # --- parent_child only ---
  # parent:
  #   strategy: fixed_size
  #   max_chars: 3000
  # child:
  #   strategy: recursive
  #   chunk_size: 600
  #   overlap: 100
```

**Block-aware preprocessing (universal)**

Setting `source_format: markdown` and enabling any `preserve_*` option wraps the inner chunker with block-aware preprocessing, regardless of the `strategy` setting. This means `recursive`, `markdown_recursive`, and `parent_child` all benefit from table/code-block preservation and heading-path injection when used with Markdown documents:

```yaml
chunking:
  strategy: recursive          # or markdown_recursive, parent_child
  source_format: markdown      # enables block-aware wrapping
  preserve_heading_path: true
  preserve_tables: true
  preserve_code_blocks: true
```

The `block_aware` strategy name is kept for backward compatibility and is equivalent to `strategy: recursive` with `source_format: markdown` and all preserve options enabled.

**Choosing a strategy:**

- **`recursive`** — use for plain text and PDF. Works well across all languages including Japanese.
- **`markdown_recursive`** — use when documents have clear heading structure (e.g. technical docs, wikis exported as Markdown). Preserves section context within each chunk, which tends to improve retrieval precision.
- **`block_aware`** — use for Markdown documents that contain tables, code blocks, or nested headings. Tables and code blocks are never split across chunk boundaries. Every chunk carries heading-path metadata (`section: H1 > H2 > H3`) which is displayed in `mrag search` results.
- **`parent_child`** — use when you want precise child-chunk matching with richer parent-chunk context in results. Pairs with `retrieval.strategy: parent_child`. Set `dense_top_k` / `keyword_top_k` to at least `top_k × 3` to ensure enough parent candidates after deduplication.

**Search result display with heading-path metadata:**

```
[1] score=0.8421  doc=manual.md  chunk=a3f2b1c4...
    section: SIM7080G > MQTT > KeepAlive
    MQTT keepalive settings can be configured with AT+CMQTTKEEPALIVE...
```

> **Note:** Changing `chunking.strategy`, `chunk_size`, `overlap`, or any `preserve_*` flag invalidates the existing index. Run `mrag reindex` after any chunking change to rebuild from scratch.

### Retrieval Strategies

The `retrieval.strategy` field in a profile controls how search is performed. Four strategies are available:

| Strategy | Description |
|----------|-------------|
| `hybrid` | Combines keyword (BM25) and vector search results using Reciprocal Rank Fusion (RRF). **Default and recommended for most use cases.** |
| `keyword` | Full-text search only, using SQLite FTS5 BM25 scoring. Fast; no embedding required at query time. |
| `vector` | Dense vector search only, via Qdrant cosine similarity. Good for semantic/paraphrase queries where exact terms may differ. |
| `parent_child` | Retrieves child chunks, resolves them to parent chunks, deduplicates, and returns parent-level content. Must be paired with `chunking.strategy: parent_child`. |

**Configuration fields per strategy:**

```yaml
retrieval:
  strategy: hybrid      # hybrid | keyword | vector
  top_k: 8              # final number of results returned
  dense_top_k: 20       # candidates fetched from Qdrant before fusion (hybrid/vector)
  keyword_top_k: 20     # candidates fetched from FTS5 before fusion (hybrid/keyword)
  fusion: rrf           # fusion algorithm (rrf is the only supported option)
```

`dense_top_k` and `keyword_top_k` are only used when the corresponding sub-search is active. Setting them higher than `top_k` gives the fusion step more candidates to re-rank, which generally improves result quality at a small latency cost.

**Choosing a strategy:**

- **`hybrid`** — best default. Handles both exact-term queries (e.g. product codes, Japanese keywords) and semantic queries robustly.
- **`keyword`** — use when queries are expected to contain exact terms from the documents (e.g. part numbers, error codes). Also useful when Ollama / Qdrant is unavailable at query time.
- **`vector`** — use when queries are phrased differently from the source text (e.g. questions about concepts rather than exact wording). Requires Ollama to be running at query time.
- **`parent_child`** — use with `chunking.strategy: parent_child` profiles. Searches over small child chunks for precision, then returns deduplicated parent chunks for richer context. Set `dense_top_k` / `keyword_top_k` to at least `top_k × 3` (e.g. `top_k: 8` → `dense_top_k: 60`) to compensate for deduplication reducing the candidate pool.

> **Note:** The strategy is set per **profile**, not globally. You can maintain multiple profiles with different strategies and switch between them at index/query time with `--profile <name>`.

### Reranking

When `rerank.enabled: true`, mrag runs a CrossEncoder reranker after retrieval to improve result ordering. The reranker fetches `top_n` candidates, re-scores them, and returns `top_k` results.

```yaml
rerank:
  enabled: true
  provider: sentence-transformers
  model: hotchpotch/japanese-reranker-cross-encoder-small-v1
  max_length: 512  # token truncation limit; keep at 512 for BERT-based models
  top_n: 30        # candidates fetched before reranking
  top_k: 8         # final results after reranking
```

Reranking is applied at query time only — changing `rerank` settings never triggers re-indexing. Requires `uv pip install -e ".[reranker]"`.

Disable at runtime with `--no-rerank` on `mrag search`, `mrag eval`, or `mrag serve`.

> **Note: reranking with `parent_child` profiles.** When `retrieval.strategy: parent_child`, reranking is applied to parent chunks (~3000 chars, ~1361 tokens) after parent resolution. BERT-based rerankers (including all `hotchpotch/japanese-reranker-cross-encoder-*` variants) truncate at 512 tokens, discarding most of the parent chunk content. mrag emits a `WARN` at runtime when this combination is detected. For `parent_child` profiles, consider leaving `rerank.enabled: false` — child-chunk matching already provides the retrieval precision, and the broad parent context is the primary value.

### Contextual Augmentation

When `augmentation.strategy: contextual` is set, mrag calls an Ollama LLM once per chunk during `mrag index` to generate a short context description. This context is prepended to the chunk content before embedding, helping the model understand each chunk in the context of the full document.

```yaml
augmentation:
  strategy: contextual        # none (default) | contextual
  provider: ollama
  model: gemma4:e4b           # generation model — separate from embedding.model
  endpoint: http://localhost:11434
```

This follows the [Anthropic contextual retrieval](https://www.anthropic.com/news/contextual-retrieval) pattern. The generation model is independent of the embedding model: `bge-m3` embeds the augmented text, while `gemma4:e4b` (or any other Ollama generation model) produces the context.

**Important notes:**

- `strategy: none` (default) — no LLM call; indexing speed is unchanged
- Keyword search (FTS5) always indexes original chunk content — augmentation only affects vector embeddings
- Changing `augmentation.strategy` invalidates the index; run `mrag reindex` to rebuild
- Indexing with `strategy: contextual` is slower: one LLM call per chunk
- Transient Ollama timeouts and HTTP 5xx errors are automatically retried with exponential backoff; monitor logs for `↻ retry` lines
- If a chunk still fails after all retries (e.g. OCR/table noise causing repeated empty responses), mrag falls back to storing the raw chunk instead of failing the whole document — the success line shows `(N raw fallback)` and `⤵ fallback` log lines identify affected chunks
- Documents with 300 or more chunks print a `⚠ large document` warning at index time — this is informational, not an error

**Retry and failure policy (optional):**

The default retry policy (3 attempts, 2 s initial delay, ×2 backoff, 30 s cap) works for most setups. The default failure policy (`raw_fallback`) ensures a single problematic chunk does not fail the whole document. Override per profile if needed:

```yaml
augmentation:
  strategy: contextual
  provider: ollama
  model: gemma4:e4b
  endpoint: http://localhost:11434
  retry:
    max_attempts: 5
    initial_delay_seconds: 3.0
    backoff_multiplier: 2.0
    max_delay_seconds: 60.0
  failure_policy:
    mode: raw_fallback   # raw_fallback (default) | fail_document
```

`failure_policy.mode: fail_document` restores the pre-0.7 behaviour where any chunk failure marks the entire document as failed. `retry` and `failure_policy` settings do not invalidate the index.

The same `retry` block is available under `embedding` for controlling retry behaviour of embedding calls.

**Per-project prompt customisation:**

`mrag init` creates `profiles/context_prompt.txt` with the default prompt template. Edit this file to tailor the LLM instructions for your domain:

```bash
# View the current prompt
cat profiles/context_prompt.txt

# Edit it (must keep {document} and {chunk} placeholders)
nano profiles/context_prompt.txt

# Rebuild the index with the new prompt
mrag reindex
```

The prompt file is picked up automatically at index time. Changes to it are not reflected in already-indexed chunks until `mrag reindex` is run.

---

## Architecture

```
mrag CLI
  ├── mrag add      → extracts text → SQLite (documents table)
  ├── mrag index    → chunks → [contextual augmentation (LLM, optional)] → embeds (Ollama)
  │                         → SQLite (chunks + chunk_variants) + Qdrant + FTS5
  └── mrag search   → keyword (FTS5 BM25) + vector (Qdrant) → RRF fusion → [reranker] → results

mrag serve  → FastAPI → same retrieval pipeline over HTTP
```

- **SQLite** — source of truth for documents, chunks, profiles, and FTS5 index
- **Qdrant** — rebuildable vector index (`mrag reindex` recreates it from SQLite). Runs embedded (`mode: local`, default) or as an external server (`mode: server`).
- **FTS5 tokenizer** — vaporetto (Japanese morphological) or trigram (universal)
- **apsw** — required for vaporetto; provides SQLite extension loading on macOS

---

## Project Structure

```
my-project/
├── mrag.yaml                    # project config (name, KB ID, tokenizer, Qdrant)
├── mrag.db                      # SQLite database
├── profiles/
│   ├── default.yaml             # retrieval profile
│   └── context_prompt.txt       # LLM prompt for contextual augmentation (editable)
├── data/
│   └── documents/
│       └── <doc-id>/
│           ├── original.pdf     # copy of original file
│           ├── extracted.txt    # extracted plain text
│           ├── extracted.md     # extracted markdown
│           └── extraction_meta.json
├── qdrant/                      # Qdrant vector storage (mode: local writes here)
└── cache/
    └── embeddings/              # optional embedding cache
```

---

## Qdrant Modes

mrag supports two Qdrant operation modes, configured in `mrag.yaml`:

```yaml
# Default — no Docker required
qdrant:
  mode: local

# External server — requires a running Qdrant instance
qdrant:
  mode: server
  host: localhost
  port: 6333
```

| Mode | Qdrant process | Data location | Use case |
|------|---------------|---------------|---------|
| `local` (default) | Embedded in-process | `qdrant/` inside the project directory | Development, CI, lightweight deployments |
| `server` | External (Docker or native) | Managed by the Qdrant server | Production, multi-project shared indexing |

`mrag init` always generates `mode: local`. Existing projects without a `mode` key are treated as `mode: server` for backward compatibility.

---

## Migrating a Project to Another Host

Because `mode: local` stores all Qdrant data inside the project directory, migration is a simple directory copy — no re-indexing required.

```bash
# On the source host: archive the project
tar -czf my-project.tar.gz my-project/

# Transfer to the target host
scp my-project.tar.gz user@target-host:~/

# On the target host: extract and use immediately
tar -xzf my-project.tar.gz
cd my-project
mrag search "query"   # works without mrag reindex
```

**What gets transferred:**

| Path | Contents |
|------|---------|
| `mrag.yaml` | Project config |
| `mrag.db` | SQLite — documents, chunks, FTS5 index |
| `profiles/` | Retrieval profile YAML files + `context_prompt.txt` |
| `data/documents/` | Original files + extracted text |
| `qdrant/` | Qdrant vector data (local mode only) |

> **Prerequisite:** Ollama with the same embedding model must be available on the target host for new indexing or vector/hybrid searches. The `qdrant/` data already contains the pre-built vectors, so existing documents can be searched immediately without re-embedding.

If you are using `mode: server`, copy everything except `qdrant/` and run `mrag reindex` on the target host after starting the Qdrant server.

---

## License

Licensed under the [GNU Affero General Public License v3.0 or later](./LICENSE) (AGPL-3.0-or-later).

Copyright (c) 2026 BathTimeFish KK.

---

## Acknowledgements

mrag uses [PyMuPDF](https://github.com/pymupdf/pymupdf) for PDF text extraction and table detection. PyMuPDF is developed and maintained by [Artifex Software](https://artifex.com) and licensed under AGPL-3.0.

mrag uses [sqlite-vaporetto](https://github.com/hotchpotch/sqlite-vaporetto) by [@hotchpotch](https://github.com/hotchpotch) for Japanese morphological tokenization via SQLite FTS5.

- **sqlite-vaporetto** — licensed under `MIT OR Apache-2.0`
- **bundled model** (`bccwj-suw+unidic_pos+kana.model.zst`, included in `-with-model` releases) — licensed under [BSD-3-Clause](https://opensource.org/license/BSD-3-Clause), sourced from [daac-tools/vaporetto-models](https://github.com/daac-tools/vaporetto-models/releases)

If you redistribute mrag together with the sqlite-vaporetto library or its bundled model, the BSD-3-Clause copyright notice for the model must be included in your distribution.
