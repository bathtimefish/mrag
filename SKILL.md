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
mrag init --name <name> --yes
# --yes skips interactive prompts and accepts all defaults

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
✓ Initialized mrag.db
```

**Verify:**
```bash
mrag doctor   # should show all checks green
cat mrag.yaml # confirm fts_tokenizer and knowledge_base.id
cat profiles/context_prompt.txt  # default LLM prompt for contextual augmentation
```

---

## Skill 2 — Add documents to a project

**Preconditions:**
- Inside the project directory (`mrag.yaml` exists in cwd)
- Source files are accessible (PDF, .txt, or .md)

**Steps:**

```bash
# Add one file
mrag add /path/to/document.pdf

# Add multiple files — loop (mrag add accepts one file per invocation)
for f in /path/to/docs/*.pdf; do mrag add "$f"; done
```

**Expected output per file:**
```
✓ Added: document.pdf  (id=91f28863-...)
```

**Notes:**
- Adding does **not** index. Run `mrag index` separately.
- Re-adding the same file (same SHA-256 hash) is rejected unless `--force` is passed.
- The extracted text is stored in `data/documents/<doc-id>/extracted.txt`.

**Verify extraction:**
```bash
mrag show-extracted <doc-id>   # preview extracted text
```

---

## Skill 3 — Build (or update) the retrieval index

**Preconditions:**
- Inside the project directory
- At least one document has been added (`mrag add` completed)
- Ollama is running and the embedding model (default: `bge-m3`) is pulled
- Qdrant: check `mrag.yaml` — `mode: local` (default) needs nothing; `mode: server` needs a running Qdrant instance
- If `augmentation.strategy: contextual` is set in the profile: the generation model must also be pulled (`ollama pull gemma4:e4b` or whichever model is configured)

**Steps:**

```bash
# Index all un-indexed documents (differential)
mrag index

# Index a specific document only
mrag index --document-id <doc-id>

# Force full rebuild of the index (drops and recreates all chunks)
mrag reindex
```

**Expected output:**
```
✓ Indexed: 12  Skipped: 0
```

`Skipped` count shows documents whose content and profile hash have not changed since last indexing.

If any documents failed (e.g. Ollama timeout during contextual augmentation), the summary line shows:
```
✓ Indexed: 11  Skipped: 0
Error (2ba41462-...): Empty context response from Ollama: ...
```

Failed documents remain in `error` status and are automatically retried on the next `mrag index` run. No manual intervention is needed other than re-running the command.

**Log output to file (recommended for large corpora):**

When indexing many documents, redirect output to a log file so that errors are preserved for later review:

```bash
mrag index 2>&1 | tee mrag-index-$(date +%Y%m%d-%H%M%S).log
```

This streams output to the terminal in real time and simultaneously writes to a timestamped log file. After the run, check for errors with:

```bash
grep "Error" mrag-index-*.log
```

Re-run `mrag index` to retry only the failed documents — successfully indexed documents are skipped automatically.

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
```

**Query syntax for keyword/hybrid strategy:**

| Query form | Behaviour |
|------------|-----------|
| `word1 word2` | AND — chunks containing both terms |
| `"exact phrase"` | Phrase match |
| `product overview` | AND — chunks containing both terms |
| `"product overview"` | Phrase match |

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
4. Run `mrag doctor` to check Qdrant and Ollama connectivity

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

## Skill 7 — Remove a document

**Preconditions:**
- Inside the project directory
- The document ID must exist in the database

**Steps:**

```bash
# Step 1: dry-run — shows what would be deleted, nothing is changed
mrag remove <doc-id>

# Step 2: actual deletion (removes from SQLite, FTS5, and Qdrant)
mrag remove --force <doc-id>
```

**Find a document ID:**
```bash
# From CLI (after indexing)
mrag profiles list

# From SQLite directly
sqlite3 mrag.db "SELECT id, filename FROM documents;"

# From API
curl -s http://127.0.0.1:8000/api/v1/documents
```

**Verify deletion:**
```bash
mrag search "<term from that document>" --strategy keyword
# Should return no results from the deleted document
```

---

## Skill 8 — Check environment health

**Preconditions:** None (works without a project directory)

```bash
mrag doctor
```

Checks and reports:
- SQLite version and FTS5 availability
- `trigram` tokenizer availability
- vaporetto extension load (if library found)
- Qdrant reachability (`localhost:6333`)
- Ollama reachability (`localhost:11434`)
- `mrag.yaml` validity (if in a project directory)

**Use this skill** before starting any indexing or serving task to confirm the environment is ready.

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
- Ollama is running and the generation model is pulled: `ollama pull gemma4:e4b`

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
  model: gemma4:e4b           # any Ollama-compatible chat/generation model
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

**Performance and reliability notes:**

- Indexing with `strategy: contextual` is significantly slower than `strategy: none` — one LLM call per chunk. For a 100-chunk document, expect roughly 100 × (LLM generation time). Use a fast model (`gemma4:e4b`) or index overnight for large corpora.
- Transient Ollama timeouts and HTTP 5xx errors are retried automatically (default: 3 attempts, exponential backoff). Watch for `↻ retry` lines in the log.
- If a chunk still fails after all retries, the default `failure_policy.mode: raw_fallback` stores the raw chunk instead of failing the whole document. The success line shows `(N raw fallback)` and `⤵ fallback` log lines identify affected chunks. Set `mode: fail_document` to restore pre-0.7 strict behaviour.
- Documents with **300 or more chunks** trigger a `⚠ large document` warning at index time. This is informational — retry and fallback are active and the index will proceed.
- The `embedding` section supports the same `retry` block for controlling embedding call retry behaviour.

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
  source_format: markdown      # enables block-aware wrapping for child chunks
  preserve_heading_path: true  # optional: attach section breadcrumbs
  preserve_tables: true        # optional: keep tables intact in child chunks
  preserve_code_blocks: true   # optional: keep code blocks intact in child chunks
  parent:
    strategy: fixed_size
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

## Quick decision guide

```
Need to search without Qdrant/Ollama?                       →  --strategy keyword
Need best Japanese retrieval?                               →  ensure fts_tokenizer: vaporetto in mrag.yaml
Zero results from keyword search?                           →  check mrag index ran; try single-word query
Zero results from vector/hybrid?                            →  check Ollama running; run mrag doctor
Qdrant "Collection not found" error?                        →  mode: server needs a running Qdrant; or switch to mode: local
Need to update one document?                                →  mrag remove --force <id>; mrag add <file>; mrag index
Need to rebuild everything?                                 →  mrag reindex
Some documents failed during index?                         →  re-run mrag index (retries error-status docs only); check logs for details
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
Documents have tables or code blocks that get split?        →  verify source_format: markdown + preserve_tables/preserve_code_blocks: true (default since 0.8.0); mrag reindex if changed
Want heading breadcrumbs in search results?                 →  verify source_format: markdown + preserve_heading_path: true (default since 0.8.0); results show "section: H1 > H2 > H3"
block_aware results missing section line?                   →  chunk has no heading — only chunks under a heading carry section metadata
Want block-aware options with parent_child?                 →  add source_format: markdown + preserve_* flags to a parent_child profile; child chunks get block-aware preprocessing
```
