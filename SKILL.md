---
name: mrag
description: >
  Step-by-step procedures for operating mrag — a lightweight, local-first RAG
  knowledge base CLI. Use this skill when the user wants to build, search,
  inspect, evaluate, or serve a small-scale RAG pipeline from local documents
  (PDF / Markdown / text) with hybrid keyword+vector retrieval, contextual
  augmentation, parent-child retrieval, reranking, or Dify external knowledge
  API integration. Common user intents include: building a local RAG knowledge
  base from individual files or recursively filtered directory trees, querying
  or tuning retrieval quality on an existing knowledge base, inspecting how
  documents were chunked and augmented, reversibly excluding or permanently
  removing indexed documents, aggregating multiple knowledge bases into a
  registry for Agentic RAG workflows, and serving the knowledge base over the
  HTTP API.
license: AGPL-3.0
metadata:
  upstream: https://github.com/bathtimefish/mrag
  version: "0.23.0"
---

# SKILL.md — mrag Skill Procedures

Concrete, step-by-step procedures for AI agents operating mrag. Each skill lists preconditions, exact commands, expected output, and verification steps.

For background concepts and constraints, see [AGENTS.md](AGENTS.md).

---

## Important: mrag command path (venv setup)

When mrag is installed with `uv venv` + `uv pip install -e "."`, the `mrag` executable is located at:

```
.venv/bin/mrag
```

This path is relative to the repository root where `uv venv` was run. If the shell's `PATH` does not include `.venv/bin`, invoke mrag with the full path:

```bash
/path/to/mrag-repo/.venv/bin/mrag <command>
```

Or activate the venv first:

```bash
source .venv/bin/activate
mrag <command>
```

**Always confirm the correct mrag binary** before running any skill. Using a globally installed or mismatched mrag can silently operate on the wrong project.

---

## Skill 1 — Initialize a new project

**Preconditions:**
- `mrag` CLI is installed via `uv pip install -e "."` (binary at `.venv/bin/mrag`)
- Ollama is running and `bge-m3` is pulled
- You are in the **parent directory** (not inside the intended project directory)
- Qdrant: **not required** — `mrag init` generates `mode: local` by default (embedded, no Docker)

**Steps:**

```bash
# 1. Move to the parent directory where the project subdirectory should be created
cd /path/to/parent

# 2. Initialize (creates /path/to/parent/<name>/)
mrag init --name <name> --non-interactive
# --non-interactive skips prompts and uses defaults for all unspecified fields

# 3. Enter the project directory for all subsequent operations
cd <name>
```

**Expected output:**
```
✓ vaporetto tokenizer detected (libsqlite_vaporetto.dylib)   # if vaporetto available
✓ Created directory structure
✓ Generated mrag.yaml
✓ Generated profiles/default.yaml
✓ Generated profiles/context_prompt.txt
✓ Generated kb_information.yaml
✓ Initialized mrag.db
```

**Verify:**
```bash
mrag doctor   # should show all checks green
cat mrag.yaml # confirm fts_tokenizer and knowledge_base.id
cat kb_information.yaml  # agent-facing KB metadata
cat profiles/context_prompt.txt  # default LLM prompt for contextual augmentation
```

**Note for AI agents:** the recommended init form for AI-driven KB creation is via `--kb-info-json` so that the resulting `kb_information.yaml` is fully populated. See **Skill 1.5** below.

---

## Skill 1.5 — Initialize a project with full KB metadata (LLM agents)

**When to use this skill instead of Skill 1:** When an AI agent needs to create a KB project that will be discoverable by other agents (Agentic RAG workflows). This produces a fully populated `kb_information.yaml` — the metadata file that external agents read to decide whether to query this KB.

**Preconditions:** same as Skill 1, plus:
- The agent has enough domain knowledge to write the KB description / tags / best_for / avoid_for entries

**Steps:**

```bash
# 1. (Optional) Inspect the JSON Schema to know what fields are accepted
mrag init --print-kb-info-schema | head -40

# 2. Generate kb_info.json with full metadata
cat > /tmp/kb_info.json <<'EOF'
{
  "project": {"name": "device-kb"},
  "knowledge_base": {
    "id": "kb_device",
    "name": "Device Development Knowledge",
    "description": "Knowledge base for M5Stack, SIM7080G, MQTT, LTE, BraveJIG, and embedded device development."
  },
  "agent_usage": {
    "tags": ["m5stack", "sim7080g", "mqtt", "lte", "bravejig", "embedded"],
    "best_for": [
      "SIM7080G / LTE module troubleshooting",
      "MQTT publish stops and keepalive issues",
      "AT command investigation"
    ],
    "avoid_for": [
      "Contract review",
      "Accounting"
    ],
    "preferred_profiles": ["default"],
    "example_queries": [
      "SIM7080G MQTT publish stops after several hours",
      "BraveJIG USB router start stop command"
    ]
  }
}
EOF

# 3. Initialize the project — files are created at ./knowledges/kb-device/
mrag init ./knowledges/kb-device --non-interactive --kb-info-json /tmp/kb_info.json
```

**Verify:**

```bash
cd ./knowledges/kb-device
mrag kb-info validate   # confirms the file passes the v1 schema
mrag kb-info show       # prints the generated kb_information.yaml
```

**Required JSON fields:** `project.name`, `knowledge_base.{id, name, description}`.
**Optional JSON fields:** `agent_usage.{tags, best_for, avoid_for, preferred_profiles, example_queries}`.

Missing required fields cause `mrag init` to exit 1 with a clear validation error and no partial files are created. `knowledge_base.id` must be lowercase alphanumeric + underscore (e.g. `kb_device`, not `KB-Device`).

---

## Skill 2 — Add documents to a project

**Preconditions:**
- Inside the project directory (`mrag.yaml` exists in cwd)
- Source files are accessible (`.pdf`, `.txt`, `.md`, or `.markdown`)

### Add one file

```bash
mrag add /path/to/document.pdf

# Machine-readable result for an agent
mrag add /path/to/document.pdf --json
```

Adding does **not** index. Run `mrag index` separately. A duplicate SHA-256 is
reported as `skipped_duplicate` with exit code 0. Pass `--force` only when the
same file must be re-extracted and replaced under its existing document ID.

### Recursively add a directory

A directory is accepted only when `--recursive` is explicit. Conversely,
`--recursive` and its directory-only options are rejected for a file source.

Use a dry-run first for any unfamiliar or large tree:

```bash
# 1. Preview deterministic candidates without modifying the project
mrag add /path/to/documents --recursive --dry-run --json

# 2. Ingest the selected tree; this still does not start indexing
mrag add /path/to/documents --recursive --json

# 3. Build the retrieval index separately, following Skill 3 logging rules
mrag index 2>&1 | tee mrag-index-$(date +%Y%m%d-%H%M%S).log
```

Filter with repeatable, source-root-relative globs. Quote globs so the shell does
not expand them before mrag receives them:

```bash
# Include only Markdown and PDF, then hard-exclude drafts and generated output
mrag add /path/to/documents --recursive --dry-run --json \
  --include '**/*.md' --include '**/*.markdown' --include '**/*.pdf' \
  --exclude 'drafts/' --exclude '**/generated-*'

# After reviewing items[].source and items[].status, apply the same selection
mrag add /path/to/documents --recursive --json \
  --include '**/*.md' --include '**/*.markdown' --include '**/*.pdf' \
  --exclude 'drafts/' --exclude '**/generated-*'
```

Selection order is:

1. If any `--include` rules exist, require at least one match.
2. Reject any `--exclude` match. An ignore negation cannot override this hard
   exclusion.
3. Apply the source root's optional `.mragignore` rules in order. Use `!pattern`
   to re-include a path ignored by an earlier `.mragignore` rule.

Patterns use normalized POSIX relative paths even on Windows. A pattern without
`/` matches that name at any depth; a trailing `/` selects a directory tree;
`**` crosses directory boundaries. `.mragignore` must be a regular, non-symlink,
UTF-8 file no larger than 1 MiB.

### Control traversal and conversion

```bash
# Dot-prefixed files/directories are skipped unless explicitly enabled
mrag add /path/to/documents --recursive --hidden --dry-run --json

# Symlinks are skipped by default; opt in only for a trusted source tree
mrag add /path/to/documents --recursive --follow-symlinks --dry-run --json

# Bound concurrent PDF/extractor work (default: 2; valid range: 1..64)
mrag add /path/to/documents --recursive --converter-jobs 4 --json

# Re-extract duplicate hashes while preserving their existing document IDs
mrag add /path/to/documents --recursive --force --json
```

Keep the safe defaults unless the user requests otherwise. With
`--follow-symlinks`, mrag detects directory cycles and ingests the same canonical
file target at most once. It always refuses a recursive source inside the
project's `data/` directory and skips that directory (including aliases to it)
when scanning a broader tree, preventing self-ingestion.

Candidates and report items are emitted in stable relative-path order even when
converter jobs run concurrently.

### Handle recursive results and exit codes

Read the single JSON report rather than parsing human output:

```json
{
  "schema_version": 1,
  "command": "add",
  "status": "partial",
  "summary": {"added": 4, "skipped": 2, "failed": 1},
  "items": [
    {"source": "docs/a.md", "status": "added", "document_id": "...", "error": null},
    {"source": "docs/b.md", "status": "skipped_duplicate", "document_id": "...", "error": null},
    {"source": "docs/bad.bin", "status": "failed", "document_id": null,
     "error": {"code": "prepare_failed", "message": "..."}}
  ],
  "index_started": false,
  "recursive": true,
  "dry_run": false
}
```

Interpret exit codes as follows:

| Exit | Meaning | Agent action |
|---:|---|---|
| `0` | No failed items; additions and duplicate skips may both be present | Continue to `mrag index` when ingestion is complete |
| `3` | Partial success under the default best-effort mode | Preserve successes, inspect failed `items`, fix or omit them, then rerun |
| `1` | Every selected item failed, or at least one item failed with `--strict` | Inspect every failed item before indexing |
| `2` | Invalid CLI use, such as a directory without `--recursive` | Correct the invocation |

`--strict` changes the failure exit code; it does **not** roll back documents
already added during the run. A retry is safe because successful files become
`skipped_duplicate` unless `--force` is explicitly used.

Use `--strict` in CI when any per-file failure must fail the job:

```bash
mrag add /path/to/documents --recursive --strict --json
```

### Verify extraction

Use the `document_id` values returned in `items`. Recursive addition creates one
independent mrag document per source file.

```bash
mrag show-extracted <doc-id>
mrag inspect document <doc-id> --json
```

Extracted artifacts are stored under `data/documents/<doc-id>/` as
`extracted.txt`, `extracted.md`, and extraction metadata alongside the retained
original.

---

## Skill 3 — Build (or update) the retrieval index

**Preconditions:**
- Inside the project directory
- At least one document has been added (`mrag add` completed)
- Ollama is running and the embedding model (default: `bge-m3`) is pulled
- Qdrant: check `mrag.yaml` — `mode: local` (default) needs nothing; `mode: server` needs a running Qdrant instance
- If `augmentation.strategy: contextual` is set in the profile: the generation model must also be pulled (`ollama pull gemma4:e2b` or whichever model is configured)

**Steps (AI agents — required form):**

AI agents MUST always pipe `mrag index` / `mrag reindex` output through `tee` to a timestamped log file. This ensures the per-chunk progress (`augmenting`, `↻ retry`, `⤵ fallback`) is captured to disk so the agent (or a parallel monitor) can inspect long-running progress without waiting for the run to complete. See "Monitoring index progress in real time" below for the rationale.

```bash
# Required form for AI agents — always tee the output
mrag index 2>&1 | tee mrag-index-$(date +%Y%m%d-%H%M%S).log

# Index a specific document only
mrag index --document-id <doc-id> 2>&1 | tee mrag-index-$(date +%Y%m%d-%H%M%S).log

# Force full rebuild of the index (drops and recreates all chunks)
mrag reindex 2>&1 | tee mrag-reindex-$(date +%Y%m%d-%H%M%S).log

# Specify a custom log output path (still tee for the progress stream)
mrag index --output-log /tmp/my-index-run.json 2>&1 | tee mrag-index-$(date +%Y%m%d-%H%M%S).log

# Skip documents that failed in a previous run
mrag index --skip-list-json logs/20260514103000-index.json 2>&1 | tee mrag-index-$(date +%Y%m%d-%H%M%S).log
```

> **Note for human users:** The `tee` requirement applies to AI agents because they cannot watch a streaming terminal interactively. Human operators may omit `| tee ...` and watch the terminal directly — both forms are functionally equivalent. The JSON log (auto-written to `logs/`) is generated either way.

**Expected output:**
```
✓ Indexed: 12  Up-to-date: 0  List-skipped: 0
Log: logs/20260514103000-index.json
```

Every run writes a JSON log to `logs/` automatically (default filename: `YYYYMMDDHHmmss-index.json`). The log records the run summary and full metadata for any failed documents.

`Up-to-date` shows documents whose content and profile hash have not changed. `List-skipped` shows documents explicitly skipped via `--skip-list-json`.

If any documents failed, the summary shows:
```
✓ Indexed: 11  Up-to-date: 0  List-skipped: 0
Error (2ba41462-...): Empty context response from Ollama: ...
Log: logs/20260514103000-index.json
```

Failed documents remain in `error` status and are automatically retried on the next `mrag index` run.

**Skipping persistently failing documents:**

If a document consistently fails (e.g. oversized PDF producing too many chunks), use the run log as a skip list to proceed with the rest of the corpus while the problematic document is investigated separately:

```bash
# Run 1: index everything, note the log path
mrag index
# → logs/20260514103000-index.json  (failed_count: 1)

# Run 2: skip the failing document
mrag index --skip-list-json logs/20260514103000-index.json
# → logs/20260514104500-index.json  (List-skipped: 1)
```

The skip list JSON only requires `failed_documents[].document_id`. You can also create the file manually or have an LLM generate it:

```json
{
  "failed_documents": [
    {"document_id": "6559abe7-8cb6-45ae-817b-a42c8534179f"}
  ]
}
```

**Monitoring index progress in real time:**

The JSON log is a post-run artifact for failure analysis. It does not capture per-chunk progress lines. **AI agents must always pipe index/reindex through `tee`** so the streaming output (`augmenting`, `↻ retry`, `⤵ fallback`, per-document `[idx/n]` lines) is preserved to a file the agent can poll while the command is still running:

```bash
mrag index 2>&1 | tee mrag-index-$(date +%Y%m%d-%H%M%S).log
```

Why this matters for AI agents:

- Contextual augmentation runs make one LLM call per chunk and can take many minutes (or hours for large corpora). Without `tee`, the agent has no way to inspect progress until the command exits.
- The agent (or a parallel monitor process) can `tail -f` the log to detect stalls, retry storms, or fallback floods early — instead of waiting for the run to complete and then learning from the JSON summary that the run was unhealthy.
- The JSON log produced at the end is complementary, not redundant: it captures the structured summary and failure list, while the `tee` log captures the chunk-level narrative.

Human users may watch the terminal directly and omit `tee` if they prefer — but for any unattended or long-running invocation (and for all AI-agent invocations), the `tee` form is mandatory.

**`⚠ large chunks` warning during index:**

When a document produces one or more chunks whose embedding input (chunk content, or `context + content` if contextual augmentation is enabled) exceeds 6000 characters, mrag prints:

```
⚠ large chunks  3 chunks exceed 6000 chars (max: 8421) — may hit embedding model input limit  <filename>
```

This is informational, not an error — indexing continues. The threshold (6000 chars) is set conservatively below the input limit of typical embedding models (e.g. `bge-m3` ≈ 8192 tokens, which corresponds to roughly 12000–25000 characters depending on language and content type). When this warning fires:

- The embedding model **may silently truncate** the input on its side. Retrieval quality for that chunk's tail content can degrade without any error surfacing.
- To eliminate the warning, lower `chunking.chunk_size` (or `chunking.parent.max_chars` for `parent_child` profiles), then `mrag reindex`.
- The warning is most likely with parent-child profiles using `parent.max_chars >= 6000`, or with chunking profiles where contextual augmentation prepends substantial context to already-large chunks.
- If `chunking` defaults (`chunk_size: 800`, `parent.max_chars: 3000`) are used, this warning should never fire.

**Verify:**
```bash
mrag search "test" --strategy keyword --top-k 1
# Should return at least one result if documents contain the term
```

---

## Skill 4 — Search the knowledge base (CLI)

**Preconditions:**
- Inside the project directory
- `mrag index` has completed at least once
- For `vector` or `hybrid` strategy: Ollama must be running; Qdrant is embedded automatically for `mode: local`

**Steps:**

```bash
# Hybrid search (default — recommended)
mrag search "<query>" --top-k 5

# Keyword search (FTS5 BM25 — works without Qdrant/Ollama)
mrag search "<query>" --strategy keyword --top-k 5

# Vector search (dense — requires Qdrant + Ollama)
mrag search "<query>" --strategy vector --top-k 5

# Disable reranking for this search (even if enabled in the profile)
mrag search "<query>" --no-rerank

# JSON output for AI agents — single JSON object on stdout, warnings on stderr
mrag search "<query>" --json
mrag search "<query>" --strategy vector --top-k 10 --json | jq '.results[].chunk_id'
```

**`--json` output structure** (for AI agents and pipeline scripts):

```json
{
  "query": "...",
  "profile": "default",
  "strategy": "hybrid",
  "reranked": false,
  "result_count": 3,
  "results": [
    {
      "rank": 1,
      "chunk_id": "...",
      "document_id": "...",
      "filename": "manual.pdf",
      "score": 0.8421,
      "content": "full chunk content here",
      "metadata": {
        "heading_path_text": "H1 > H2 > H3",
        "section_id": "h1/h2/h3",
        "contains_table": false
      }
    }
  ],
  "score_stats": {"min": 0.42, "max": 0.84, "mean": 0.67, "stdev": 0.13},
  "document_distribution": {"manual.pdf": 3}
}
```

When reranking is active, each result also exposes a top-level `retrieval_score` (the first-stage score from before reranking) — useful for diagnosing reranker effects. Empty result set returns `result_count: 0` with `results: []` and `score_stats: null`; the exit code stays 0.

**Query syntax for keyword/hybrid strategy:**

| Query form | Behaviour |
|------------|-----------|
| `word1 word2` | AND — chunks containing both terms |
| `product overview` | AND — chunks containing both terms |
| `熱電対の基本仕様` (continuous Japanese, vaporetto) | Phrase — vaporetto tokenizes the run into morphemes and matches them as an adjacent sequence |

Each whitespace-separated token is wrapped as an FTS5 string literal, so characters like `%`, `*`, `:`, `-`, `/`, `(`, `)` are treated as ordinary characters rather than FTS5 operators. ASCII double quotes in the query are stripped — quoted-phrase syntax (`"exact phrase"`) is not supported; pass keyword tokens instead. Long natural-language questions (e.g. `"この論文中で人員削減効果が示されたのは何%ですか"`) are treated as a single phrase and will usually return no results — break them into keywords (`人員削減 効果 %`) for useful hits.

Note — natural-language queries on contextual KBs: for profiles with `augmentation.strategy: contextual`, the same long natural-language question often still retrieves well under `--strategy vector` or `hybrid`, because `context_text` is embedded alongside chunk content. The whitespace-tokenization advice above applies to `--strategy keyword` only. See **Skill 16 Step 3** for a per-strategy audit procedure.

**Expected output:**
```
[1] score=6.39  doc=manual.pdf  chunk=eb0495d2...
    …access control policy defines the permitted operations…

[2] score=5.81  doc=manual-b.pdf  chunk=3fa12c11...
    …

Score stats:  min=5.81  max=6.39  mean=6.10  σ=0.0412

Document distribution:
  manual.pdf    ████████████████████ 3
  manual-b.pdf  ████                 1
```

When the profile uses `strategy: block_aware`, each result that has heading metadata shows an additional `section:` line:

```
[1] score=0.8421  doc=manual.md  chunk=a3f2b1c4...
    section: SIM7080G > MQTT > KeepAlive
    MQTT keepalive settings can be configured with AT+CMQTTKEEPALIVE...
```

σ (standard deviation of scores) indicates query precision: a **low σ** means results are clustered with similar scores — the query is likely too broad. A **high σ** means a top result clearly stands out — the query is precise. Use this signal to decide whether to refine the query before synthesising an answer.

**If zero results:**
1. Confirm `mrag index` has been run
2. Check `fts_tokenizer` in `mrag.yaml` matches what was used at index time
3. Try a simpler, shorter query term first
4. Run `mrag doctor` to verify Ollama is reachable (and SQLite/FTS5 capabilities)

**Verifying keyword presence (false-negative guard):**

When `rerank.enabled: true`, results are reordered by semantic relevance and the 200-character preview may not include the matched keyword if it appears later in the chunk — easy to misread as "the corpus does not contain this keyword." To confirm literal presence in the index, run keyword-only retrieval with the reranker disabled:

```bash
mrag search "<keyword>" --strategy keyword --no-rerank --top-k 10
# --json `content` field has the full chunk body — no need for inspect chunk
mrag search "<keyword>" --strategy keyword --no-rerank --json
```

`--strategy keyword` runs FTS5 BM25 only (no vector retrieval). `--no-rerank` overrides the profile's `rerank.enabled: true` so scores stay pure BM25 (typically 0–20+ for hits). Zero results here means the keyword truly is not indexed; non-zero results means it exists and prior hybrid+rerank previews were just hiding it. Use this **fact-check mode** before concluding the corpus lacks a topic, and for vocabulary-variant audits (e.g. `ドローン` vs `UAV` vs `無人航空機`) where one variant may be indexed while another is not.

---

## Skill 5 — Search the knowledge base (HTTP API)

**Preconditions:**
- Inside the project directory
- `mrag serve` is running (see Skill 6)
- If `MRAG_API_KEY` is set: include `Authorization: Bearer <key>` in every request

**Retrieve chunks:**

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/retrieve \
  -H "Content-Type: application/json" \
  -d '{
    "query": "access control policy",
    "strategy": "hybrid",
    "top_k": 5
  }'
```

**Response shape:**
```json
{
  "query": "access control policy",
  "profile": "default",
  "strategy": "hybrid",
  "results": [
    {
      "chunk_id": "eb0495d2-...",
      "document_id": "91f28863-...",
      "filename": "manual.pdf",
      "score": 6.39,
      "content": "…access control policy defines the permitted operations…",
      "metadata": {}
    }
  ]
}
```

**List all documents:**
```bash
curl -s http://127.0.0.1:8000/api/v1/documents
```

**Get a document by ID:**
```bash
curl -s http://127.0.0.1:8000/api/v1/documents/<doc-id>
```

**List profiles:**
```bash
curl -s http://127.0.0.1:8000/api/v1/profiles
```

**With authentication:**
```bash
curl -s http://127.0.0.1:8000/api/v1/documents \
  -H "Authorization: Bearer <your-api-key>"
```

---

## Skill 6 — Start the API server

**Preconditions:**
- Inside the project directory
- `mrag index` has completed at least once
- Ollama must be running (for vector/hybrid retrieval); Qdrant is embedded automatically for `mode: local`

**Steps:**

```bash
# Start on default port 8000
mrag serve

# Custom host and port
mrag serve --host 0.0.0.0 --port 8080

# With authentication
MRAG_API_KEY=your-secret-key mrag serve

# Disable reranking for all requests (overrides profile rerank.enabled)
mrag serve --no-rerank
```

**Verify the server is ready:**
```bash
curl -s http://127.0.0.1:8000/api/v1/profiles
# Should return a JSON array with at least "default"
```

Interactive API docs: `http://127.0.0.1:8000/docs`

---

## Skill 7 — Exclude, restore, or remove a document

**Preconditions:**
- Inside the project directory
- The document ID must exist in the database

### Choose the correct operation

| Requirement | Operation | Source artifacts | Retrieval artifacts |
|---|---|---|---|
| Stop using a document as knowledge but retain it for audit or later restoration | `mrag exclusions add` | Retained | Physically cleaned when `--force` succeeds |
| Make an excluded document eligible for search again | `mrag exclusions restore`, then `mrag index` | Retained | Rebuilt only by the explicit index step |
| Delete the document itself from mrag | `mrag remove` | Deleted by `--force` | Deleted |

Prefer `exclusions` when the intent is “stop using this document as
knowledge.” Prefer `remove` only when the document record, original file, and
extracted artifacts must also be deleted.

### Find the stable document ID

Use a document ID, not a chunk ID or filename. A document can have many chunks,
and filenames are not the policy identity.

```bash
# From a known search result
mrag search "<unique query>" --json \
  | jq '.results[] | {document_id, filename}'

# From SQLite
sqlite3 mrag.db "SELECT id, filename, status FROM documents ORDER BY filename;"

# From the HTTP API when mrag serve is running
curl -s http://127.0.0.1:8000/api/v1/documents
```

### Exclude while retaining the document

```bash
# 1. Preview the affected chunks, FTS rows, and Qdrant points (default: dry-run)
mrag exclusions add --document-id <doc-id>

# 2. Apply to every current and future profile (recommended default)
mrag exclusions add --document-id <doc-id> \
  --reason "obsolete knowledge" --force

# Apply only when one profile must exclude the document
mrag exclusions add --document-id <doc-id> \
  --profile <profile-name> --reason "profile-specific reason" --force

# Machine-readable forms for agents
mrag exclusions add --document-id <doc-id> --json
mrag exclusions add --document-id <doc-id> --reason "obsolete" --force --json

# Audit active policies; --all also shows restored policies
mrag exclusions list
mrag exclusions list --all --json
```

Treat `exclusions add --force` as two coordinated actions:

1. Activate a persistent, document-level retrieval policy first. Index/reindex
   skips the document before chunking, augmentation, or embedding; CLI, HTTP,
   and MCP retrieval all filter it fail-closed.
2. Immediately clean the document's derived index artifacts: FTS rows,
   chunks, variants, per-profile index state, and Qdrant points. Keep the
   document record, original file, extracted text, and exclusion audit record.

Therefore, exclusion is not merely a logical delete. The policy provides the
durable safety barrier, while `--force` also performs application-level physical
cleanup of the derived indexes. It is not secure erasure of filesystem media,
logs, caches, or backups.

Use an all-profile exclusion unless the user explicitly requests different
knowledge visibility by profile. Do not stack overlapping all-profile and
profile-specific policies. Run `mrag exclusions list`, restore the narrower
policies if necessary, and then create the desired scope.

**Handle degraded cleanup safely:**

- Exit code `0` with JSON status `applied` means the policy and application-level
  cleanup completed.
- Exit code `3` with JSON status `degraded` means the policy is active and the
  document remains blocked from every retrieval path, but Qdrant cleanup is
  incomplete. FTS has been cleaned; chunk metadata needed to identify and retry
  Qdrant deletion is intentionally retained.
- Do not revoke the exclusion after exit code `3`. Restore Qdrant availability,
  then repeat the same `mrag exclusions add ... --force` command. Reapplying an
  active exclusion reconciles the pending cleanup safely.

### Restore an excluded document

Use the exclusion ID returned by `add` or `list`; it is distinct from the
document ID.

```bash
# 1. Preview restoration (default: dry-run)
mrag exclusions restore <exclusion-id>

# 2. Revoke the policy only after residual cleanup succeeds
mrag exclusions restore <exclusion-id> --force

# 3. Explicitly rebuild the retained document; follow Skill 3 logging rules
mrag index --document-id <doc-id> 2>&1 \
  | tee mrag-index-$(date +%Y%m%d-%H%M%S).log

# For a restored profile-specific exclusion, rebuild that profile only
mrag index --document-id <doc-id> --profile <profile-name> 2>&1 \
  | tee mrag-index-$(date +%Y%m%d-%H%M%S).log
```

Restoration never invokes the embedding provider implicitly. Until the explicit
`mrag index` succeeds, the document is eligible but not searchable. If residual
Qdrant cleanup fails, restoration exits non-zero and keeps the exclusion active;
fix Qdrant and repeat `restore --force`.

### Permanently remove the mrag document

```bash
# Preview first (default: dry-run)
mrag remove <doc-id>

# Delete the document record, source/extraction artifacts, derived indexes,
# and any exclusion history owned by that document
mrag remove --force <doc-id>
```

`remove --force` is irreversible at the mrag application level. It does not
guarantee secure erasure from storage media, external backups, copied logs, or
operator-managed caches; handle those separately when required.

### Verify the result

Check by document ID rather than assuming that a shared search term should
disappear completely from the knowledge base.

```bash
# Confirm the exclusion policy is active
mrag exclusions list --json \
  | jq --arg id "<doc-id>" '.exclusions[] | select(.document_id == $id)'

# Confirm no result from this document escapes keyword retrieval
mrag search "<term from the document>" --strategy keyword --json \
  | jq --arg id "<doc-id>" '[.results[] | select(.document_id == $id)] | length'

# Repeat with the configured vector/hybrid path when available
mrag search "<term from the document>" --strategy hybrid --json \
  | jq --arg id "<doc-id>" '[.results[] | select(.document_id == $id)] | length'
```

The two search-count checks must print `0`; the policy check must return the
active exclusion record. After `remove --force`, additionally confirm that the
document ID no longer appears in `GET /api/v1/documents` or the `documents`
table.

---

## Skill 8 — Check environment health

**Preconditions:** None — `mrag doctor` is project-independent and runs from any directory.

```bash
mrag doctor
```

Checks and reports:
- SQLite version (>= 3.35.0)
- FTS5 `trigram` tokenizer
- sqlite-vaporetto native library (optional)
- Ollama reachability at `http://localhost:11434`

**Use this skill** to verify the mrag install is healthy. Note that `mrag doctor` no longer reads `mrag.yaml` or checks Qdrant — project configuration is validated at runtime by individual commands (`mrag add`, `mrag index`, `mrag search`), and Qdrant runs embedded in-process for the default `mode: local` so a server check is unnecessary.

---

## Skill 9 — Preview or export extracted text

Use these skills to inspect what text was extracted from a document before or after indexing.

**Preview extracted text of an added document:**
```bash
mrag show-extracted <doc-id>
```

**Export to a file:**
```bash
mrag export-extracted <doc-id> --output /path/to/output.txt
```

**Dry-run extraction from a file (no storage):**
```bash
mrag extract /path/to/document.pdf
```

This is useful to verify that a PDF is readable and produces meaningful text before committing it to the knowledge base.

---

## Skill 10 — Migrate a project to another host

Use this skill to move a `mode: local` project to a different machine without re-indexing.

**Preconditions:**
- The project uses `qdrant.mode: local` (check `mrag.yaml`)
- Target host has mrag installed and Ollama running with the same embedding model

**Steps:**

```bash
# On the source host
tar -czf my-project.tar.gz my-project/
scp my-project.tar.gz user@target-host:~/

# On the target host
tar -xzf my-project.tar.gz
cd my-project
mrag search "test query"   # works immediately — no mrag reindex needed
```

**What is included in the archive:**

| Path | Role |
|------|------|
| `mrag.db` | SQLite — documents, chunks, FTS5 index |
| `qdrant/` | Pre-built vector data (local mode only) |
| `profiles/` | Retrieval profile YAML files + `context_prompt.txt` |
| `data/documents/` | Original files + extracted text |

**If using `mode: server`:** Copy everything except `qdrant/`, start the Qdrant server on the target host, then run `mrag reindex`.

**Verify after migration:**
```bash
mrag doctor          # check environment
mrag search "test"   # confirm results return
```

---

## Skill 11 — Evaluate retrieval quality

Use `mrag eval` to inspect retrieval results for a query without running a full search pipeline. It shows scores, duplicate chunks, document distribution, and can compare multiple profiles side-by-side.

**Preconditions:**
- Inside the project directory
- `mrag index` has completed at least once
- For `vector` or `hybrid` strategy: Qdrant and Ollama must be running

**Steps:**

```bash
# Basic evaluation (uses default profile and its configured strategy)
mrag eval "device configuration options"

# Force a specific strategy regardless of profile setting
mrag eval "device configuration options" --strategy keyword
mrag eval "device configuration options" --strategy vector

# Change number of results
mrag eval "device configuration options" --top-k 20

# Disable reranking for this run
mrag eval "device configuration options" --no-rerank

# Compare two profiles side-by-side
mrag eval "device configuration options" --profile default --profile second
```

**Output sections:**
- Per-result: score, document filename, chunk ID, content preview, duplicate warning if content matches another result
- **Score stats** — min / max / mean / σ across results
- **Document distribution** — bar chart of how many chunks came from each document
- **Profile Diff table** (multi-profile mode) — rank-by-rank chunk comparison with ✓ for identical placements

**Interpreting scores by strategy:**

| Strategy | Score range | σ typical | Use for |
|----------|-------------|-----------|---------|
| `keyword` | 0 – 20+ (BM25) | varies widely | Exact keyword match quality |
| `vector` | 0.0 – 1.0 (cosine) | 0.01 – 0.10 | Semantic relevance, score gaps |
| `hybrid` | 0.01 – 0.02 (RRF) | ≈ 0.001 | Rank ordering only; scores are not meaningful in absolute terms |

> When hybrid scores look flat (σ ≈ 0.001), this is normal RRF behaviour. Use `--strategy vector` to see discriminative cosine scores.

---

## Skill 12 — Enable and tune contextual augmentation

Contextual augmentation runs an Ollama LLM once per chunk during `mrag index` to generate a short context description. This context is prepended to the chunk content before embedding, improving semantic retrieval quality — especially for long documents where individual chunks may lack surrounding context.

**Preconditions:**
- Inside the project directory
- Ollama is running and the generation model is pulled: `ollama pull gemma4:e2b`

**Steps:**

```bash
# 1. Edit the profile to enable contextual augmentation
nano profiles/default.yaml
```

Add (or update) the `augmentation` section:

```yaml
augmentation:
  strategy: contextual        # was: none
  provider: ollama
  model: gemma4:e2b           # any Ollama-compatible chat/generation model
  endpoint: http://localhost:11434
  # retry:                    # optional — defaults shown below
  #   max_attempts: 3
  #   initial_delay_seconds: 2.0
  #   backoff_multiplier: 2.0
  #   max_delay_seconds: 30.0
  # failure_policy:           # optional — controls per-chunk failure behaviour
  #   mode: raw_fallback      # raw_fallback (default) | fail_document
```

```bash
# 2. Rebuild the index with augmentation applied
mrag reindex

# (or mrag index if the documents haven't been indexed under this profile yet)
mrag index
```

**Verify:**
```bash
# Check that variant_type is 'contextual' in the DB
sqlite3 mrag.db "SELECT variant_type, context_text FROM chunk_variants LIMIT 3;"
```

**Customise the prompt per project:**

```bash
# View the current prompt
cat profiles/context_prompt.txt

# Edit to tailor it to your domain (must keep {document} and {chunk} placeholders)
nano profiles/context_prompt.txt

# After editing, reindex to apply the new prompt to all chunks
mrag reindex
```

**Disable augmentation:**

Set `augmentation.strategy: none` in the profile YAML, then run `mrag reindex`. This removes all contextual variants and rebuilds raw variants only.

**What gets contextualized:**

When enabled, mrag applies both **Contextual Embeddings** (vector index) and **Contextual BM25** (FTS5 keyword index) — the same `context + chunk` text is stored in both backends, matching Anthropic's full Contextual Retrieval recipe. Hybrid search therefore benefits in both retrieval branches.

**Known constraint — document truncation (8000 chars):**

The `{document}` placeholder in the prompt is truncated to **8000 characters** before being sent to the local LLM. This is a pragmatic trade-off for local-first operation with limited-context-window models like `gemma4:e2b`.

- For documents **≤ 8000 chars**: every chunk receives context generated from the full document — no degradation.
- For documents **> 8000 chars**: chunks near the end receive context generated from only the document prefix, which can produce less relevant context text.

Workarounds:
- Use a long-context generation model (some Ollama models support 32K+ context); raise `_MAX_DOC_CHARS` in `mrag/core/indexing/augmentation.py` if needed.
- Split very long documents into multiple input files before `mrag add` (each becomes its own document with its own contextualization scope).
- Use a smaller `chunk_size` so individual chunks stay close to the document start in token order (does not fundamentally solve the issue but reduces the proportion of "distant" chunks).

**Performance and reliability notes:**

- Indexing with `strategy: contextual` is significantly slower than `strategy: none` — one LLM call per chunk. For a 100-chunk document, expect roughly 100 × (LLM generation time). Use a fast model (`gemma4:e2b`) or index overnight for large corpora.
- Transient Ollama timeouts and HTTP 5xx errors are retried automatically (default: 3 attempts, exponential backoff). Watch for `↻ retry` lines in the log.
- If a chunk still fails after all retries, the default `failure_policy.mode: raw_fallback` stores the raw chunk instead of failing the whole document. The success line shows `(N raw fallback)` and `⤵ fallback` log lines identify affected chunks. Set `mode: fail_document` to restore pre-0.7 strict behaviour.
- Documents with **300 or more chunks** trigger a `⚠ large document` warning at index time. This is informational — retry and fallback are active and the index will proceed.
- The `embedding` section supports the same `retry` block for controlling embedding call retry behaviour.

**Retrieval-side benefit:**

Because `context_text + chunk content` is written to both the vector index and the FTS5 index, contextual augmentation is not only about generating richer chunk metadata — it materially changes what retrieval can find. Table fragments, OCR-noisy spans, and mid-section chunks that are weak on their own become retrievable by natural-language questions through the semantic label in `context_text`. To verify this on a specific KB after indexing, follow **Skill 16 Step 3**.

---

## Skill 13 — Choosing between search and eval

`mrag search` and `mrag eval` both retrieve chunks and output score stats (min/max/mean/σ) and document distribution. The difference is in the **additional analytical features** `eval` provides on top.

| | `mrag search` | `mrag eval` |
|---|---|---|
| Chunk text | ✓ | ✓ |
| Score stats (min/max/mean/σ) | ✓ | ✓ |
| Document distribution | ✓ | ✓ |
| Duplicate chunk detection | — | ✓ |
| Multi-profile diff | — | ✓ |
| Typical use | Day-to-day retrieval and synthesis | Index quality analysis, profile tuning |

**Use `mrag search` for day-to-day retrieval.** The σ and document distribution in the output let you immediately judge query precision and information layout without running a separate command.

```bash
mrag search "what are the key challenges for adoption" --top-k 5
# → if σ is low, the query is too broad — try a more specific query
# → document distribution shows which files contain the most relevant chunks
```

**Use `mrag eval` for index quality analysis:**
- Detect duplicate chunks (sign of chunking issues)
- Compare two profiles side-by-side with `--profile A --profile B`
- Systematic accuracy measurement after reindexing

```bash
mrag eval "network security" --profile default --profile v2 --strategy vector
```

**Recommended workflow for unfamiliar corpora:**

```bash
# Step 1: broad query — check σ and distribution to understand the landscape
mrag search "<broad query>" --top-k 10

# Step 2: if σ is low, refine the query and search again
mrag search "<focused query>" --top-k 5
```

---

## Skill 14 — Use parent-child retrieval

Parent-child retrieval indexes small **child chunks** for precise search and returns large **parent chunks** as context. This gives better retrieval accuracy than large chunks while providing richer context than small chunks.

**Preconditions:**
- Inside the project directory
- `mrag index` has NOT been run yet (or run `mrag reindex` after adding the profile)
- Ollama is running with `bge-m3` pulled

**Create the profile:**

```bash
cat > profiles/parent_child.yaml << 'EOF'
name: parent_child

chunking:
  strategy: parent_child
  source_format: markdown      # enables block-aware wrapping at the parent level
  preserve_heading_path: true  # optional: attach section breadcrumbs to chunks
  preserve_tables: true        # optional: keep tables intact at the parent level
  preserve_code_blocks: true   # optional: keep code blocks intact at the parent level
  parent:
    strategy: fixed_size       # fixed_size | section (Markdown heading boundaries)
    max_chars: 3000
  child:
    strategy: recursive
    chunk_size: 600
    overlap: 100

embedding:
  provider: ollama
  model: bge-m3
  endpoint: http://localhost:11434

retrieval:
  strategy: parent_child
  top_k: 8
  dense_top_k: 60   # must be >= top_k * 3 (see note below)
  keyword_top_k: 60
  fusion: rrf

augmentation:
  strategy: none
EOF
```

**Index and search:**

```bash
mrag index --profile parent_child
mrag search "your query" --profile parent_child
```

**`chunking.strategy` and `retrieval.strategy` must always match:**

Setting only one to `parent_child` will cause a profile validation error at runtime:

```
Error: chunking.strategy is 'parent_child' but retrieval.strategy is 'hybrid'.
       Both must be 'parent_child' when using parent-child retrieval.
```

**Why `dense_top_k` must be ≥ `top_k × 3`:**

The `parent_child` search fetches child chunks, then resolves them to parent chunks with deduplication. Because multiple child chunks can map to the same parent, the effective number of distinct parent results after dedup may be significantly less than the raw child count. To ensure the final `top_k` parent results are available:

```
dense_top_k >= top_k * 3   (minimum — go higher for better coverage)
keyword_top_k >= top_k * 3
```

With `top_k: 8`, set `dense_top_k: 60` and `keyword_top_k: 60`. Using the defaults (`dense_top_k: 20`) with `top_k=8` will result in fewer than 8 parent results in practice.

**Storage overhead:**

Parent chunk content is stored in full in the `chunks` table, while child chunks store the same content in smaller pieces. With `parent_max_chars=3000` and 4 child chunks per parent, the `chunks` table is approximately 1.8× larger than a standard profile. Plan storage accordingly for large corpora.

**Reranking with parent-child:**

If `rerank.enabled: true`, the reranker evaluates **parent chunk content** (not child chunks). This is beneficial — the reranker scores richer context rather than fragments. Execution order:

```
hybrid_search (child level) → resolve_to_parent → reranker → final top_k
```

**Debugging child-level results:**

Use `--strategy keyword` or `--strategy vector` with a parent_child profile to see child-level results without parent resolution. Useful for verifying chunking behaviour:

```bash
mrag eval "your query" --profile parent_child --strategy keyword
```

---

## Skill 15 — Inspect documents, chunks, and sections

`mrag inspect` is a **read-only, AI-agent-first** command group for debugging chunking and indexing outcomes. Every subcommand supports `--json`; stdout is the payload, stderr carries warnings / errors. Use these instead of querying SQLite by hand.

**Preconditions:**
- Inside the project directory (`cwd` must contain `mrag.yaml` + `mrag.db`)
- At least one document has been added and indexed

### Skill 15.1 — Per-document summary

```bash
mrag inspect document <doc-id>           # human-readable rich Table
mrag inspect document <doc-id> --json    # JSON for agent consumption
```

Returns: filename / source_type / extraction_provider / per-profile chunk counts (including parent / child) / augmentation success vs raw_fallback per profile.

### Skill 15.2 — List chunks (full inventory by default)

```bash
# Returns ALL chunks (agent-first default; no pagination)
mrag inspect chunks <doc-id> --profile default --json

# Page through a very large document explicitly
mrag inspect chunks <doc-id> --profile default --limit 50 --offset 0 --json
mrag inspect chunks <doc-id> --profile default --limit 50 --offset 50 --json
```

**Paging norm:** for documents with hundreds of chunks (e.g. large PDFs ≥200 pages), prefer `--limit 50` + `--offset N` over emitting thousands of metadata rows in one go. Default-all is for ergonomics on small/medium docs; explicitly page when the run will flood the terminal.

**Multi-profile documents:** if the document is indexed under more than one profile and `--profile` is omitted, the command exits 1 with a candidate list. Always pass `--profile` when you know multiple profiles index the document.

**Optional payload expansion:**

```bash
mrag inspect chunks <doc-id> --profile default --show-content --json   # adds full body
mrag inspect chunks <doc-id> --profile default --show-context --json   # adds LLM context_text
```

### Skill 15.3 — Single-chunk deep dive

```bash
mrag inspect chunk <chunk-id> --json
```

Always returns content + context_text. `--profile` is not needed because `chunk_id` is the PRIMARY KEY. Typical pattern: agent gets `chunk_id` from `mrag search --json`, then deep-dives the suspicious chunk.

### Skill 15.4 — Heading hierarchy / parent-child tree

```bash
mrag inspect sections <doc-id> --profile default          # heading hierarchy
mrag inspect sections <doc-id> --profile parent-child     # parent → child layered view
```

If the profile has no heading_path (e.g. `recursive` strategy with `preserve_heading_path: false`), the command exits 1 and points the agent at `inspect chunks`. Use `inspect chunks` for flat listings.

### Two-stage agent workflow (recommended)

```bash
# Stage 1: lightweight metadata survey, filter for interesting chunks
mrag inspect chunks abc123 --profile default --json \
  | jq '.chunks[] | select(.metadata.contains_table or .metadata.contains_code)'

# Stage 2: pull full body + context for the candidate chunk_id
mrag inspect chunk c-014 --json
```

This minimizes token usage in agent context windows: Stage 1 is metadata-only, Stage 2 fetches body content only for the specific chunks you actually need.

---

## Skill 16 — Diagnose contextual augmentation quality

When `augmentation.strategy: contextual` is enabled, each chunk gets an LLM-generated context_text. The quality of those generations is **not automatically validated** (the `succeeded` counter only confirms the LLM call returned without falling back to raw). Use this skill to manually audit the output.

**Preconditions:**
- A profile with `augmentation.strategy: contextual`
- The document has been indexed under that profile

### Step 1 — High-level health check

```bash
mrag inspect document <doc-id> --json | jq '.profiles[] | {name, augmentation}'
```

A non-zero `raw_fallback` count means the LLM exhausted retries on some chunks (look at `augmentation_error` in the DB for details). High `raw_fallback` ratios suggest LLM instability — consider switching `augmentation.model` or raising `retry.max_attempts`.

### Step 2 — Eyeball context_text quality

```bash
# Sample every context_text in one pass
mrag inspect chunks <doc-id> --profile <name> --show-context --json \
  | jq '.chunks[] | {chunk_id, ctx: .context_text}'

# Or deep-dive one suspicious chunk
mrag inspect chunk <chunk-id> --json | jq '.context_text'
```

Look for:

- **Language mixing** (e.g. English context for a Japanese chunk) — change `augmentation.model` or edit `profiles/context_prompt.txt`
- **Generic / vacuous context** ("This is a chunk about X") — refine the prompt to demand more specific framing
- **Context that contradicts the chunk body** — model hallucination; consider a stronger model

After prompt edits, run `mrag reindex` to regenerate all variants with the new prompt.

### Step 3 — Natural-language query audit

Contextual augmentation indexes `context_text + content` into both the vector store and FTS5, so a natural-language question that would fail under plain keyword retrieval may still succeed under `vector` / `hybrid`. Use this step to confirm dense retrieval is actually doing that work on your KB — not just assume it.

```bash
# Same natural-language question, three strategies in sequence
mrag search "<natural-language question>" --strategy hybrid  --top-k 5 --json
mrag search "<natural-language question>" --strategy vector  --top-k 5 --json
mrag search "<natural-language question>" --strategy keyword --top-k 5 --json

# Then deep-dive a top chunk_id to see WHY it matched
mrag inspect chunk <chunk-id> --json | jq '{content, context_text}'

# Optional: rewrite the question as space-separated keywords
mrag search "term1 term2 term3" --strategy keyword --top-k 5 --json
```

Evaluation criteria (read together with Step 2):

1. Does `vector` (and therefore `hybrid`) return relevant chunks for the natural-language form? If `keyword` is 0-hit while `vector` succeeds, dense retrieval is carrying the query — expected on a healthy contextual KB.
2. Does the keyword-rewritten query recover hits under `--strategy keyword`? That confirms FTS5 still needs tokenized input even though `context_text` is indexed on the FTS side.
3. For each top chunk, does `context_text` give a concrete semantic label — naming the table, section, or topic — rather than restating the body? Fragmentary `content` is acceptable if `context_text` carries the meaning; a vacuous or generic `context_text` is the failure mode to flag (see Step 2 for prompt fixes).

Agent-side caveat: OCR noise and duplicate neighboring chunks are downstream synthesis concerns. When mrag returns several near-duplicate chunks from the same `document_id` with adjacent `chunk_index`, the agent should deduplicate and prefer the chunk whose `context_text` is most specific.

---

## Skill 17 — Aggregate multiple KBs into a registry for Agentic RAG

When you maintain several mrag projects and want an external agent (Claude Code, Cline, Cursor, custom Agentic RAG workflow) to discover and pick the right KB per query, generate a `knowledge_registry.yaml`.

**Preconditions:**
- A parent directory containing one or more mrag projects (each with `mrag.yaml` + `kb_information.yaml`)
- Projects sit **directly under the root** — registry generate only walks 1 level deep, not recursively

### Directory layout

```text
knowledges/                          ← <root_dir>
├── kb-device/
│   ├── mrag.yaml
│   ├── kb_information.yaml
│   └── ...
├── kb-contract/
│   └── ...
└── kb-design/
    └── ...
```

### Step 1 — Generate

```bash
# Default: writes <root>/knowledge_registry.yaml
mrag registry generate ./knowledges

# Preview without writing
mrag registry generate ./knowledges --dry-run

# Custom output location
mrag registry generate ./knowledges --output /elsewhere/registry.yaml
```

The generator:
- Walks `<root>/*/kb_information.yaml` (one level deep)
- Warns + skips directories missing `kb_information.yaml` or `mrag.yaml`
- Exits 1 if **no** KB matches (so typos and misplaced KBs are caught early)
- Detects ID collisions and refuses to write a partial registry

**Path semantics:** `knowledge_bases[].path` is stored as POSIX relative path from the registry file's directory (e.g. `./kb-device`). The tree is portable — moving or scp-ing `knowledges/` to another machine works without regeneration.

### Step 2 — Validate

```bash
mrag registry validate ./knowledges/knowledge_registry.yaml

# Machine-readable for CI / agent automation
mrag registry validate ./knowledges/knowledge_registry.yaml --json
```

Aggregates **all** issues in one run (rather than stopping on the first one). Stable issue keys for agent branching:

| `issue` key | Action |
|---|---|
| `path_not_found` | Regenerate the registry (KB was deleted / renamed) |
| `mrag_yaml_not_found` | The listed path is not an mrag project |
| `kb_information_yaml_not_found` | Pre-v0.17 project; create `kb_information.yaml` manually |
| `preferred_profile_not_found` | Profile YAML deleted; remove from preferred_profiles or recreate |
| `duplicate_id` | Two KBs share an id — must rename one |

Fatal errors (YAML parse failure, schema mismatch, missing registry file) exit immediately without aggregating.

### Step 3 — Agent consumption pattern

The agent reads `knowledge_registry.yaml` and selects a KB based on `knowledge_bases[].description / tags / best_for`, then issues:

```bash
# `path` is relative to the directory containing the registry file
cd <registry_dir>/<knowledge_bases[i].path>
mrag search "<user query>" --profile <preferred_profiles[0]> --json
```

The `agent_instructions.search_command_template` field in the registry encodes this template literally; the agent can substitute `{path}`, `{query}`, `{profile}` directly.

---

## Skill 18 — Expose one KB as an MCP server

Use `mrag mcp` when an MCP-capable client should call a single mrag project through standard MCP tools/resources instead of shelling out to `mrag search --json`.

### Step 1 — Install MCP support

```bash
uv pip install -e ".[mcp]"
```

Combine extras as needed:

```bash
uv pip install -e ".[vaporetto,reranker,mcp]"
```

### Step 2 — Start with stdio

Run from inside the project directory:

```bash
cd <mrag-project>
mrag mcp
```

Default behaviour:

- transport: `stdio`
- project_dir: current directory
- profile: `mrag.yaml.default_profile`
- strategy: profile `retrieval.strategy`
- top_k default: profile `retrieval.top_k`

`stdio` is strict: stdout is reserved for MCP JSON-RPC messages. Logs and warnings go to stderr.

### Step 3 — Use config for daemon/container operation

Create a config:

```bash
mrag mcp init-config > mrag-mcp.yaml
```

Typical HTTP config:

```yaml
project_dir: /data/kb
transport: streamable-http
http:
  host: 127.0.0.1
  port: 8001
  path: /mcp
auth:
  bearer_token_env: MRAG_MCP_API_KEY
```

Validate and start:

```bash
mrag mcp validate --config ./mrag-mcp.yaml
mrag mcp --config ./mrag-mcp.yaml
```

Environment variables override YAML values:

```bash
MRAG_MCP_CONFIG=/etc/mrag/mcp.yaml
MRAG_PROJECT_DIR=/data/kb
MRAG_MCP_TRANSPORT=streamable-http
MRAG_MCP_PORT=8001
MRAG_MCP_API_KEY=...
```

Use `mrag mcp --config ./mrag-mcp.yaml --print-effective-config` to debug resolved settings. Secret values are masked.

### Step 4 — Available surface

Current MCP support is read-only. Tools:

- `search`
- `list_documents`
- `list_profiles`
- `inspect_document`
- `inspect_chunks`
- `inspect_chunk`
- `inspect_sections`

Resources:

- `mrag://kb/info`
- `mrag://profiles`
- `mrag://profiles/{profile}`
- `mrag://documents`
- `mrag://documents/{document_id}`
- `mrag://documents/{document_id}/extracted.txt`
- `mrag://documents/{document_id}/extracted.md`
- `mrag://chunks/{chunk_id}`

Do not expect write tools. `add`, `index`, `reindex`, `remove`, and profile editing are intentionally not exposed through MCP.

---

## Quick decision guide

```
Need to search without Qdrant/Ollama?                       →  --strategy keyword
Need best Japanese retrieval?                               →  ensure fts_tokenizer: vaporetto in mrag.yaml
AI agent parsing mrag search output?                        →  always pass --json (stdout: single JSON object, stderr: warnings)
AI agent creating a new KB project?                         →  use Skill 1.5 with --kb-info-json for fully populated kb_information.yaml
Need to add a directory tree?                               →  dry-run `mrag add <dir> --recursive --dry-run --json`, review items, then rerun without `--dry-run`; index separately
Need only part of a directory tree?                         →  repeat quoted `--include` / `--exclude` relative-path globs or add root `.mragignore` rules; see Skill 2
Recursive add exited 3 / partial?                           →  successful files remain added; inspect failed JSON items, fix and rerun (duplicates skip safely)
Want to know what a KB is for?                              →  mrag kb-info show (or read kb_information.yaml directly)
Want to see how a document was chunked?                     →  mrag inspect document <doc-id> --json (per-profile chunk + augmentation summary)
Want to list every chunk's metadata for an agent?           →  mrag inspect chunks <doc-id> --profile <p> --json (default: all chunks)
Need to deep-dive one chunk's content + LLM context?        →  mrag inspect chunk <chunk-id> --json (no --profile needed)
Want to see the heading hierarchy or parent/child tree?     →  mrag inspect sections <doc-id> --profile <p> --json
Inspect chunks errors with "multiple profiles"?             →  add --profile <name>; agent should pick one from the candidate list in stderr
inspect sections exits with "no section structure"?         →  the profile has preserve_heading_path: false; use inspect chunks instead
Want to audit contextual augmentation quality?              →  mrag inspect chunks --show-context --json | jq, then mrag inspect chunk for deep-dive
Augmentation Status section is missing for a profile?       →  that profile has augmentation.strategy: none (no LLM augmentation attempted)
Want to expose multiple KBs to an Agentic RAG agent?        →  mrag registry generate <root_dir>; agent reads <root>/knowledge_registry.yaml
Want to expose one KB to an MCP-capable agent?              →  uv pip install -e ".[mcp]"; cd <project>; mrag mcp
Registry generate found nothing?                            →  check the root path; KBs must be at <root>/<kb-name>/ (1 level, no recursion)
Registry validate reports issues?                           →  branch on the stable issue key (path_not_found / preferred_profile_not_found / duplicate_id / ...)
kb_information.yaml validation error?                       →  mrag kb-info validate; check knowledge_base.id is lowercase + underscore only
Zero results from keyword search?                           →  check mrag index ran; try single-word query
Zero results from vector/hybrid?                            →  check Ollama running; run mrag doctor
Reranker hides keyword in preview — looks like a miss?      →  mrag search "<word>" --strategy keyword --no-rerank [--json]  (BM25-only fact check; --json .content has full body)
Qdrant "Collection not found" error?                        →  mode: server needs a running Qdrant; or switch to mode: local
Need to hide one document but retain its source?            →  dry-run `mrag exclusions add --document-id <id>`, then repeat with `--reason "..." --force`
Need derived index data physically cleaned but source kept? →  the same `exclusions add --force` command cleans FTS/chunks/variants/Qdrant while retaining source
`exclusions add --force` exited 3 / degraded?               →  policy is active and retrieval is blocked; restore Qdrant, then repeat the exact command to reconcile cleanup
Need to make an excluded document searchable again?        →  `mrag exclusions restore <exclusion-id> --force`, then explicitly `mrag index --document-id <doc-id>`
Need to delete the source document itself?                  →  dry-run `mrag remove <id>`, then `mrag remove --force <id>`; this is not backup/media secure erase
Need to update one document?                                →  mrag remove --force <id>; mrag add <file>; mrag index 2>&1 | tee mrag-index-$(date +%Y%m%d-%H%M%S).log
Need to rebuild everything?                                 →  mrag reindex 2>&1 | tee mrag-reindex-$(date +%Y%m%d-%H%M%S).log
AI agent running mrag index/reindex?                        →  ALWAYS pipe through `2>&1 | tee mrag-index-$(date +%Y%m%d-%H%M%S).log` (required for AI agents — see Skill 3)
Some documents failed during index?                         →  re-run mrag index (retries error-status docs automatically); check logs/ for the JSON run log
Document always fails and blocks the rest?                  →  mrag index --skip-list-json logs/<ts>-index.json  (or create minimal JSON with failed_documents[].document_id)
Need to expose retrieval over HTTP?                         →  mrag serve (from inside project dir)
Need to inspect retrieval quality?                          →  mrag eval "<query>" [--strategy vector]
Want to retrieve and synthesise content?                    →  mrag search (includes σ + document distribution)
Query results look unfocused (low σ)?                       →  refine the query and re-run mrag search
Need duplicate detection or profile diff?                   →  mrag eval
Need to migrate project to another host?                    →  tar the project dir (includes qdrant/); extract on target; no reindex needed (mode: local)
Want to improve semantic retrieval quality?                 →  enable augmentation.strategy: contextual in profile; mrag reindex (see Skill 12)
Contextual indexing too slow?                               →  use a faster/smaller model in augmentation.model; or disable with strategy: none
Want to tune the LLM augmentation prompt?                   →  edit profiles/context_prompt.txt; run mrag reindex
Reranker ImportError?                                       →  uv pip install -e ".[reranker]"
mrag not found after uv install?                            →  use .venv/bin/mrag or activate the venv
Want richer context in retrieval results?                   →  use strategy: parent_child (see Skill 14)
parent_child profile validation error?                      →  chunking.strategy and retrieval.strategy must both be 'parent_child'
parent_child returning fewer results than top_k?            →  increase dense_top_k and keyword_top_k to >= top_k * 3
WARN: rerank.enabled=true with strategy: parent_child?      →  reranking on parent chunks (~3000 chars) truncates at 512 tokens; set rerank.enabled: false for parent_child profiles
RuntimeError: index 514 out of bounds?                      →  reranker received a chunk exceeding BERT token limit; ensure rerank.max_length: 512 in profile
"⚠ large chunks" warning during mrag index?                 →  embedding input may be truncated silently by the model; lower chunk_size or parent.max_chars and mrag reindex (defaults never trigger this)
Want score-based fusion instead of rank-based?              →  retrieval.fusion: weighted + retrieval.weights: [vector, keyword]; retrieval-time only (no reindex needed)
Want to bias hybrid retrieval toward keyword (or vector)?   →  retrieval.fusion: weighted + retrieval.weights e.g. [0.3, 0.7] favours keyword; experiment per-corpus
Documents have tables or code blocks that get split?        →  verify source_format: markdown + preserve_tables/preserve_code_blocks: true (default since 0.8.0); mrag reindex if changed
Want heading breadcrumbs in search results?                 →  verify source_format: markdown + preserve_heading_path: true (default since 0.8.0); results show "section: H1 > H2 > H3"
block_aware results missing section line?                   →  chunk has no heading — only chunks under a heading carry section metadata
Want block-aware options with parent_child?                 →  add source_format: markdown + preserve_* flags; tables/code/heading-path are preserved at the parent level (children may still split mid-block but the returned parent always shows the intact block)
```
