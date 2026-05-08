# mrag — Micro RAG

**A lightweight, local-first retrieval runtime for building RAG pipelines.**

mrag is a minimal, disposable, project-scoped RAG tool. One command initialises a self-contained knowledge base; another indexes your documents; a third lets you search — from the CLI or over HTTP. Everything is stored locally: SQLite is the source of truth, Qdrant holds the vector index and can be rebuilt at any time.

---

## Features

- **Hybrid retrieval** — keyword (FTS5 BM25), vector (dense), or RRF-fused hybrid
- **Japanese-first tokenization** — auto-detects [sqlite-vaporetto](https://github.com/daac-tools/sqlite-vaporetto) (morphological); falls back to SQLite's built-in trigram tokenizer
- **Multilingual embeddings** — defaults to `bge-m3` via [Ollama](https://ollama.com) (any Ollama-compatible model works)
- **Differential indexing** — re-running `mrag index` skips already-indexed documents
- **Retrieval profiles** — per-project YAML profiles control chunking, embedding, and retrieval strategy independently
- **FastAPI server** — expose the knowledge base as an HTTP API with optional Bearer-token authentication
- **Dify compatible** — `mrag serve` implements the [Dify External Knowledge API](https://docs.dify.ai/ja/use-dify/knowledge/external-knowledge-api) spec; use mrag as an external knowledge source in Dify with no extra adapter
- **Pure local stack** — no cloud dependencies; Qdrant runs as a local server

---

## Requirements

| Component | Notes |
|-----------|-------|
| Python 3.11+ | |
| [Ollama](https://ollama.com) | Must be running; `bge-m3` pulled by default |
| [Qdrant](https://qdrant.tech) | Local server on `localhost:6333` |
| `libsqlite_vaporetto` | Optional; enables Japanese morphological tokenization |
| `apsw >= 3.43` | Required only when using the vaporetto tokenizer |

---

## Installation

```bash
pip install mrag
```

### With vaporetto (recommended for Japanese documents)

```bash
pip install mrag[vaporetto]
```

Then place `libsqlite_vaporetto.dylib` (macOS) or `libsqlite_vaporetto.so` (Linux) in:

```
~/.mrag/extensions/
```

Or point to a custom path via the environment variable:

```bash
export MRAG_VAPORETTO_LIB=/path/to/libsqlite_vaporetto.dylib
```

> **Note:** On macOS, the system `sqlite3` is compiled with `OMIT_LOAD_EXTENSION`, so vaporetto requires `apsw` — the alternative SQLite binding that always supports extension loading. `pip install mrag[vaporetto]` installs it automatically.

### Pull the default embedding model

```bash
ollama pull bge-m3
```

`bge-m3` is a multilingual model (1024-dim) that works well across Japanese, English, and other languages. Any Ollama-compatible embedding model can be substituted in the profile YAML.

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
| Marker | `--extractor marker` | High-accuracy extraction for complex layouts. Requires `pip install "mrag[marker]"`. |

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
✓ Indexed: 12  Skipped: 0
```

Re-running `mrag index` after adding new files only processes the new documents.

### 4. Search

```bash
# Hybrid (default)
mrag search "熱電対の温度測定"

# Keyword only
mrag search "接点出力 ON OFF" --strategy keyword

# Vector only
mrag search "temperature sensing" --strategy vector

# Limit results
mrag search "Bluetooth LE" --top-k 3
```

### 5. Serve as an API

```bash
mrag serve
```

Starts a FastAPI server at `http://127.0.0.1:8000`. See [API Reference](#api-reference) below.

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `mrag init [--name NAME]` | Create a new project in a subdirectory |
| `mrag add <file> [file…] [--extractor pymupdf\|marker] [--force]` | Ingest documents (extract & store; no indexing) |
| `mrag index [--profile P]` | Differential index (skips up-to-date docs) |
| `mrag reindex [--profile P]` | Force-rebuild the entire index for a profile |
| `mrag search <query>` | Search (`--strategy keyword\|vector\|hybrid`, `--top-k N`) |
| `mrag serve` | Start FastAPI server (`--host`, `--port`) |
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
  "query": "接点出力の制御方法",
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
  "query": "接点出力の制御方法",
  "profile": "default",
  "strategy": "hybrid",
  "results": [
    {
      "chunk_id": "...",
      "document_id": "...",
      "filename": "manual.pdf",
      "score": 6.39,
      "content": "…接点出力ポートに対してON/OFF制御を…",
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
  "query": "接点出力の制御方法",
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
      "content": "…接点出力ポートに対してON/OFF制御を…",
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
  source_format: text
  chunk_size: 800
  overlap: 120

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

keyword:
  provider: sqlite_fts5
  tokenizer: vaporetto       # set at init time; trigram if vaporetto not found
  fallback_tokenizer: trigram
```

Create additional profiles by placing new YAML files in `profiles/` and indexing with `--profile <name>`.

### Chunking Strategies

The `chunking.strategy` field controls how documents are split into chunks before indexing. Two strategies are available:

| Strategy | Description |
|----------|-------------|
| `recursive` | Splits text recursively by separator hierarchy (paragraphs → line breaks → sentences). Best for plain text and PDF. **Default.** |
| `markdown_recursive` | Splits first by Markdown heading structure, then applies recursive splitting within each section. Use with `source_format: markdown`. |

**Configuration fields:**

```yaml
chunking:
  strategy: recursive       # recursive | markdown_recursive
  source_format: text       # text | markdown
  chunk_size: 800           # target chunk size in characters
  overlap: 120              # overlap between adjacent chunks in characters
```

**Choosing a strategy:**

- **`recursive`** — use for plain text and PDF. Works well across all languages including Japanese.
- **`markdown_recursive`** — use when documents have clear heading structure (e.g. technical docs, wikis exported as Markdown). Preserves section context within each chunk, which tends to improve retrieval precision.

> **Note:** Changing `chunking.strategy`, `chunk_size`, or `overlap` invalidates the existing index. Run `mrag reindex` after any chunking change to rebuild from scratch.

### Retrieval Strategies

The `retrieval.strategy` field in a profile controls how search is performed. Three strategies are available:

| Strategy | Description |
|----------|-------------|
| `hybrid` | Combines keyword (BM25) and vector search results using Reciprocal Rank Fusion (RRF). **Default and recommended for most use cases.** |
| `keyword` | Full-text search only, using SQLite FTS5 BM25 scoring. Fast; no embedding required at query time. |
| `vector` | Dense vector search only, via Qdrant cosine similarity. Good for semantic/paraphrase queries where exact terms may differ. |

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

> **Note:** The strategy is set per **profile**, not globally. You can maintain multiple profiles with different strategies and switch between them at index/query time with `--profile <name>`.

---

## Architecture

```
mrag CLI
  ├── mrag add      → extracts text → SQLite (documents table)
  ├── mrag index    → chunks → embeds (Ollama) → SQLite (chunks) + Qdrant + FTS5
  └── mrag search   → keyword (FTS5 BM25) + vector (Qdrant) → RRF fusion → results

mrag serve  → FastAPI → same retrieval pipeline over HTTP
```

- **SQLite** — source of truth for documents, chunks, profiles, and FTS5 index
- **Qdrant** — rebuildable vector index (`mrag reindex` recreates it from SQLite)
- **FTS5 tokenizer** — vaporetto (Japanese morphological) or trigram (universal)
- **apsw** — required for vaporetto; provides SQLite extension loading on macOS

---

## Project Structure

```
my-project/
├── mrag.yaml                  # project config (name, KB ID, tokenizer, Qdrant)
├── mrag.db                    # SQLite database
├── profiles/
│   └── default.yaml           # retrieval profile
├── data/
│   └── documents/
│       └── <doc-id>/
│           ├── original.pdf   # copy of original file
│           ├── extracted.txt  # extracted plain text
│           ├── extracted.md   # extracted markdown
│           └── extraction_meta.json
├── qdrant/                    # Qdrant storage (when using embedded mode)
└── cache/
    └── embeddings/            # optional embedding cache
```

---

## License

MIT License

Copyright (c) 2026 BathTimeFish KK.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
