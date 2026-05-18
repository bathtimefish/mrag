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
# Interactive — prompts for project name and knowledge base ID
mrag init --name my-project
cd my-project

# Non-interactive — uses defaults for all unspecified fields (no prompts)
mrag init --name my-project --non-interactive
cd my-project
```

`mrag init` creates a `my-project/` subdirectory in the current directory with:
- `mrag.yaml` — project configuration (runtime settings)
- `kb_information.yaml` — agent-facing KB metadata (description / tags / preferred profiles)
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
✓ Generated kb_information.yaml
✓ Initialized mrag.db
```

**LLM-driven project creation:** Pass a JSON file containing the full KB description for fully populated `kb_information.yaml` generation. Recommended for agents:

```bash
mrag init ./knowledges/kb-device --non-interactive --kb-info-json kb_info.json
```

See [`kb_information.yaml`](#kb-information-agent-facing-kb-metadata) below for the file's role and schema.

### 2. Add documents

```bash
mrag add report.pdf
mrag add manual.pdf notes.txt
```

Documents are extracted and stored in `data/documents/`. Supported formats: PDF, plain text, Markdown.

**PDF extraction** uses PyMuPDF for fast text-layer extraction with table detection (`find_tables()`). A warning is printed if the PDF appears to be scanned/image-based.

```bash
# Add a PDF, plain text, or Markdown file
mrag add report.pdf

# Re-add an already-registered document (overwrites extracted content)
mrag add report.pdf --force
```

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
| `mrag init [PROJECT_DIR] [--name NAME] [--kb-id ID] [--non-interactive] [--kb-info-json PATH] [--print-kb-info-schema] [--force]` | Create a new project. Use `--non-interactive` to skip prompts, `--kb-info-json` for LLM-driven KB metadata, or `--print-kb-info-schema` to print the input JSON Schema |
| `mrag add <file> [file…] [--force]` | Ingest documents (extract & store; no indexing) |
| `mrag index [--profile P] [--output-log PATH] [--skip-list-json PATH]` | Differential index (skips up-to-date docs); always writes a JSON run log |
| `mrag reindex [--profile P] [--output-log PATH] [--skip-list-json PATH]` | Force-rebuild the entire index for a profile; always writes a JSON run log |
| `mrag search <query> [--json]` | Search (`--strategy keyword\|vector\|hybrid`, `--top-k N`, `--no-rerank`, `--json` for machine-readable output) |
| `mrag eval <query>` | Evaluate retrieval quality (`--profile P`, `--strategy S`, `--top-k N`, `--no-rerank`) |
| `mrag serve` | Start FastAPI server (`--host`, `--port`, `--no-rerank`) |
| `mrag remove <doc-id>` | Dry-run removal (use `--force` to actually delete) |
| `mrag profiles list` | List profiles registered in the database |
| `mrag profiles show <name>` | Show profile configuration |
| `mrag kb-info show` | Print the current project's `kb_information.yaml` |
| `mrag kb-info validate` | Validate the current project's `kb_information.yaml` |
| `mrag kb-info schema` | Print the JSON Schema for `--kb-info-json` input |
| `mrag inspect document <doc-id> [--profile P] [--json]` | Per-profile chunk & augmentation summary for a document |
| `mrag inspect chunks <doc-id> [--profile P] [--limit N] [--offset N] [--show-content] [--show-context] [--json]` | List chunks with metadata (default returns all; agent-first) |
| `mrag inspect chunk <chunk-id> [--json]` | Single-chunk deep-dive (content + context_text always included) |
| `mrag inspect sections <doc-id> [--profile P] [--json]` | Visualize heading hierarchy or parent/child layered view |
| `mrag registry generate <root_dir> [--output PATH] [--dry-run]` | Aggregate `<root>/*/kb_information.yaml` into `knowledge_registry.yaml` |
| `mrag registry validate <registry_path> [--json]` | Validate a `knowledge_registry.yaml` against the filesystem |
| `mrag extract <file>` | Preview extracted text (dry-run, nothing stored) |
| `mrag show-extracted <doc-id>` | Print stored extracted content |
| `mrag export-extracted <doc-id>` | Export extracted content to file |
| `mrag doctor` | Check the mrag runtime environment (SQLite, FTS5, vaporetto, Ollama) — project-independent |

---

## KB Information (agent-facing KB metadata) <a id="kb-information-agent-facing-kb-metadata"></a>

Every mrag project ships with **`kb_information.yaml`** — a self-describing metadata file consumed by external AI agents (Agentic RAG workflows), not by mrag itself at runtime.

### Role separation

```
mrag.yaml             → runtime configuration (mrag reads this)
kb_information.yaml   → agent-facing semantic description (external agents read this)
```

`mrag.yaml` controls execution (Qdrant mode, default profile, tokenizer, …).
`kb_information.yaml` describes *what the KB is for* so an agent can decide whether to query it.

### Example

```yaml
version: 1

knowledge_base:
  id: kb_device
  name: Device Development Knowledge
  description: >
    Knowledge base for M5Stack, SIM7080G, MQTT, LTE, BraveJIG,
    embedded device development, and field troubleshooting.

agent_usage:
  tags:
    - m5stack
    - sim7080g
    - mqtt
    - lte

  best_for:
    - SIM7080G / LTE module troubleshooting
    - MQTT publish stop and keepalive issues

  avoid_for:
    - Contract review
    - Accounting

  preferred_profiles:
    - default

  example_queries:
    - SIM7080G MQTT publish stops after several hours
```

### Init modes

| Mode | Command | Result |
|---|---|---|
| Interactive | `mrag init --name kb-device` | Prompts for name / kb_id / description; other fields stay empty (edit later) |
| Non-interactive | `mrag init --name kb-device --non-interactive` | Minimal template (empty description, default profile only) |
| JSON-driven | `mrag init --non-interactive --kb-info-json kb_info.json` | Fully populated from the JSON input — recommended for LLM agents |

### JSON input schema

Get the JSON Schema for the `--kb-info-json` input file:

```bash
mrag init --print-kb-info-schema
# or
mrag kb-info schema
```

Required top-level fields: `project.name`, `knowledge_base.{id, name, description}`.
Optional: `agent_usage.{tags, best_for, avoid_for, preferred_profiles, example_queries}`.

Example input JSON:

```json
{
  "project": {"name": "device-kb"},
  "knowledge_base": {
    "id": "kb_device",
    "name": "Device Development Knowledge",
    "description": "Embedded device development knowledge."
  },
  "agent_usage": {
    "tags": ["m5stack", "sim7080g"],
    "best_for": ["LTE troubleshooting"],
    "preferred_profiles": ["default"]
  }
}
```

### Inspecting and validating

```bash
mrag kb-info show       # print the current project's kb_information.yaml
mrag kb-info validate   # validate against the v1 schema
mrag kb-info schema     # print the JSON Schema (equivalent to --print-kb-info-schema)
```

### Machine-readable search output

For Agentic RAG callers, `mrag search --json` emits a single JSON object to stdout (status lines and warnings go to stderr):

```bash
mrag search "MQTT keepalive" --json
```

The payload includes `query`, `profile`, `strategy`, `reranked`, `result_count`, `results[]`, `score_stats`, and `document_distribution`.

---

## Inspecting documents and chunks <a id="inspecting-documents-and-chunks"></a>

`mrag inspect` is a read-only command group for **agent- and developer-driven debugging** of chunking and indexing results. It replaces ad-hoc SQL with structured human and JSON output, and is designed to be called from AI agents (every subcommand supports `--json`).

```bash
# Per-profile chunk count + augmentation status for a document
mrag inspect document <doc-id> [--profile P] [--json]

# All chunks for a document (default: every chunk; use --limit/--offset for paging)
mrag inspect chunks <doc-id> [--profile P] [--show-content] [--show-context] [--json]

# Single chunk deep-dive — content + context_text are always included
mrag inspect chunk <chunk-id> [--json]

# Heading hierarchy or parent_child layered tree
mrag inspect sections <doc-id> [--profile P] [--json]
```

### Typical two-stage workflow for agents

```bash
# Stage 1 — lightweight metadata survey
mrag inspect chunks abc123 --json | jq '.chunks[] | select(.metadata.contains_table)'

# Stage 2 — full body + LLM-generated context for the candidate chunk
mrag inspect chunk c-014 --json
```

### Profile resolution rule

`inspect chunks` / `inspect sections` require exactly one profile context:

- `--profile` given → used as-is
- omitted + only one profile indexed this document → auto-selected
- omitted + multiple profiles → **exit 1** with a candidate list (so agents notice the ambiguity instead of silently picking the wrong one)

### Augmentation status semantics

`mrag inspect document` reports `succeeded` / `raw_fallback` only for variants where augmentation was actually attempted. Profiles without augmentation (e.g. `parent_child` with `augmentation.strategy: none`) produce no Augmentation Status section at all.

---

## Aggregating multiple KBs (`knowledge_registry.yaml`) <a id="aggregating-multiple-kbs-knowledge-registry-yaml"></a>

A `knowledge_registry.yaml` aggregates several mrag projects under one root directory so that an external Agentic RAG agent can discover them and pick the right KB for a query.

```text
knowledges/
├── knowledge_registry.yaml     ← generated artifact (agent reads this)
├── kb-device/
│   ├── mrag.yaml
│   ├── kb_information.yaml
│   └── ...
└── kb-contract/
    └── ...
```

### Generate

```bash
mrag registry generate ./knowledges
# → ./knowledges/knowledge_registry.yaml
```

- Scans `<root>/*/kb_information.yaml` (one level deep, no recursion)
- Skips subdirectories without `kb_information.yaml` / `mrag.yaml` with a warning
- Exits 1 if no KBs are found (catches typos and misplaced KBs)
- `--dry-run` writes to stdout; `--output PATH` overrides the destination

The `knowledge_bases[].path` field is a POSIX relative path **from the directory containing the registry file itself** — so the whole tree can be moved or synced to another machine without breaking.

### Validate

```bash
mrag registry validate ./knowledges/knowledge_registry.yaml
mrag registry validate ./knowledges/knowledge_registry.yaml --json
```

Aggregates **all** issues in one pass (rather than stopping on the first one) so an agent can fix everything in a single round. Stable issue keys for branching:

| Key | Meaning |
|---|---|
| `path_not_found` | `knowledge_bases[].path` does not exist |
| `mrag_yaml_not_found` | KB directory missing `mrag.yaml` |
| `kb_information_yaml_not_found` | KB directory missing `kb_information.yaml` |
| `preferred_profile_not_found` | `<path>/profiles/<name>.yaml` missing |
| `duplicate_id` | Two entries share the same `knowledge_base.id` |

Fatal errors (YAML parse failure, schema mismatch, missing registry file) exit immediately.

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
  #   strategy: fixed_size   # fixed_size | section
  #   max_chars: 3000
  # child:
  #   strategy: recursive
  #   chunk_size: 600
  #   overlap: 100
```

**Parent strategies (`parent_child` only):**

- **`fixed_size`** (default) — splits the document into parent chunks of `max_chars` characters using recursive separator splitting. Good for any document type.
- **`section`** — splits parents at Markdown heading boundaries; each heading section becomes one parent. Oversized sections are sub-split with `fixed_size` logic. Documents with no headings degrade naturally to `fixed_size` behaviour. Use for structured Markdown documents (technical docs, wikis) where heading boundaries carry semantic meaning.

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
  fusion: rrf           # rrf (default) | weighted
  # weights: [0.7, 0.3] # only for fusion=weighted; [vector, keyword] order
```

`dense_top_k` and `keyword_top_k` are only used when the corresponding sub-search is active. Setting them higher than `top_k` gives the fusion step more candidates to re-rank, which generally improves result quality at a small latency cost.

**Fusion algorithms:**

- **`rrf`** (default) — Reciprocal Rank Fusion. Uses rank only (`score = Σ 1/(k+rank)`, k=60). Robust against score-range differences between vector and keyword searches. No tuning required. Recommended as the default and for most use cases.
- **`weighted`** — Min-max normalize each list's scores to `[0,1]`, then weighted sum. Preserves score strength (large gaps between top hits remain large). Configurable per-search weighting via `weights: [vector, keyword]`. Use when you want to bias toward one retrieval mode (e.g. `weights: [0.3, 0.7]` to favour keyword on table-heavy or domain-jargon corpora).

The `weights` field is retrieval-time only — changing it does **not** invalidate the index.

**Choosing a strategy:**

- **`hybrid`** — best default. Handles both exact-term queries (e.g. product codes, Japanese keywords) and semantic queries robustly.
- **`keyword`** — use when queries are expected to contain exact terms from the documents (e.g. part numbers, error codes). Also useful when Ollama / Qdrant is unavailable at query time.
- **`vector`** — use when queries are phrased differently from the source text (e.g. questions about concepts rather than exact wording). Requires Ollama to be running at query time.
- **`parent_child`** — use with `chunking.strategy: parent_child` profiles. Searches over small child chunks for precision, then returns deduplicated parent chunks for richer context. Set `dense_top_k` / `keyword_top_k` to at least `top_k × 3` (e.g. `top_k: 8` → `dense_top_k: 60`) to compensate for deduplication reducing the candidate pool.

> **Note:** The strategy is set per **profile**, not globally. You can maintain multiple profiles with different strategies and switch between them at index/query time with `--profile <name>`.

### Reranking

When `rerank.enabled: true`, mrag runs a CrossEncoder reranker after retrieval to improve result ordering. The reranker fetches `top_n` candidates, re-scores them, and returns the final result count requested by the caller (`--top-k` for CLI or `top_k` in API requests).

```yaml
rerank:
  enabled: true
  provider: sentence-transformers
  model: hotchpotch/japanese-reranker-cross-encoder-small-v1
  max_length: 512  # token truncation limit; keep at 512 for BERT-based models
  top_n: 30        # candidates fetched before reranking; final count comes from --top-k (CLI) or request body (API)
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

When enabled, mrag applies **both** Contextual Embeddings (vector) and Contextual BM25 (FTS5 keyword) — the same contextualized text (`context + chunk`) is indexed in both stores, matching Anthropic's full Contextual Retrieval recipe.

**Important notes:**

- `strategy: none` (default) — no LLM call; indexing speed is unchanged
- Changing `augmentation.strategy` invalidates the index; run `mrag reindex` to rebuild
- Indexing with `strategy: contextual` is slower: one LLM call per chunk
- **Document truncation limit (local LLM constraint):** The `{document}` placeholder in the prompt is truncated to 8000 characters before being sent to the local generation model. For documents longer than 8000 chars, chunks near the end receive context generated from only the document prefix, which can reduce contextual relevance. This is a pragmatic trade-off for local-first operation with limited-context-window models like `gemma4:e4b`. Workarounds: use a longer-context generation model, or split very long documents into multiple input files before `mrag add`.
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

Licensed under the GNU Affero General Public License v3.0.

Copyright (c) 2026 BathTimeFish KK.

---

## Acknowledgements

mrag uses [PyMuPDF](https://github.com/pymupdf/pymupdf) for PDF text extraction and table detection. PyMuPDF is developed and maintained by [Artifex Software](https://artifex.com) and licensed under AGPL-3.0.

mrag uses [sqlite-vaporetto](https://github.com/hotchpotch/sqlite-vaporetto) by [@hotchpotch](https://github.com/hotchpotch) for Japanese morphological tokenization via SQLite FTS5.

- **sqlite-vaporetto** — licensed under `MIT OR Apache-2.0`
- **bundled model** (`bccwj-suw+unidic_pos+kana.model.zst`, included in `-with-model` releases) — licensed under [BSD-3-Clause](https://opensource.org/license/BSD-3-Clause), sourced from [daac-tools/vaporetto-models](https://github.com/daac-tools/vaporetto-models/releases)

If you redistribute mrag together with the sqlite-vaporetto library or its bundled model, the BSD-3-Clause copyright notice for the model must be included in your distribution.
