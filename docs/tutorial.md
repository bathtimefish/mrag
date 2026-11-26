# Tutorial

This document is a simple tutorial for operating mrag.

`mrag init --force` only reinitializes an empty project with the same KB ID
and FTS5 tokenizer. It refuses projects with documents before overwriting
configuration. Use a new directory when a different tokenizer is needed.
Before starting this tutorial, complete the mrag installation described in the README.


## Running from the command line

### Step 1. Create a project

Run the following command in any working directory.

```bash
mrag init my-first-kb --non-interactive
cd my-first-kb
```

A `my-first-kb/` subdirectory is created in the current directory and the full mrag project is laid out inside it. Key files:

- `mrag.yaml` — runtime configuration for the project
- `kb_information.yaml` — metadata describing what this knowledge base contains and what it can be used for
- `profiles/default.yaml` — retrieval profile (bundles the chunking strategy and the retrieval strategy)
- `profiles/context_prompt.txt` — prompt template for contextual augmentation
- `mrag.db` — SQLite database (storage for documents and chunks)
- `data/` / `qdrant/` / `cache/` / `logs/` / `docs/` — supporting directories
- `identities/` — directory reserved by mrag (contains only a `README.md`); files placed here cannot be added as documents

During initialization, mrag auto-detects the available tokenizer. If vaporetto is found, it is enabled automatically and you will see output like this:

```
✓ vaporetto tokenizer detected (libsqlite_vaporetto.dylib)
✓ Created directory structure
✓ Generated mrag.yaml
✓ Generated profiles/default.yaml
✓ Generated profiles/context_prompt.txt
✓ Generated kb_information.yaml
✓ Generated identities/README.md
✓ Initialized mrag.db
```

A panel with the project directory, the tokenizer, and the next steps follows.

You now have an empty knowledge base.

### Step 2. Ingest documents

Use `mrag add` to register one Markdown or plain-text file.

```bash
mrag add ./sample.md
```

mrag does not convert documents. If your sources are PDF or Office files,
convert them to Markdown first with a conversion engine — docling for PDF,
MarkItDown for DOCX/PPTX/XLSX — and add the output:

```bash
docling --to md --output ./documents report.pdf
mrag add ./documents/report.md
```

To ingest a directory, opt in with `--recursive`. Preview the deterministic
selection before writing it:

```bash
mrag add ./documents --recursive --dry-run --json \
  --include '**/*.md' --include '**/*.txt'

mrag add ./documents --recursive --json \
  --include '**/*.md' --include '**/*.txt'
```

Recursive add supports repeatable `--include` / `--exclude` globs, a root
`.mragignore`, hidden-path and symlink controls, and explicit partial-failure
reporting. See [recursive directory ingestion](./recursive-add.md) for the
complete contract.

The command performs the following in one go:

1. Reads the source text
2. Saves the extracted content under `data/documents/`
3. Assigns a document ID (a random UUID) and registers the entry in `mrag.db`, together with a content hash that is used to detect duplicates

At this stage each document is *registered* but **not yet searchable**. To make
it searchable you need to build the index in Step 3. `mrag add` never starts
indexing implicitly, including in recursive mode.

### Step 3. Build the index

```bash
mrag index
```

This command does the real work of making the knowledge base searchable. Specifically:

1. Splits documents into chunks (smaller units)
2. Computes embedding vectors for each chunk via Ollama
3. Writes the vectors to Qdrant
4. Writes the chunk text to SQLite FTS5 (the keyword-search index)

A summary is shown on completion:

```
✓ Indexed: 1  Up-to-date: 0  List-skipped: 0  Excluded: 0
Log: /path/to/my-first-kb/logs/20260519101000-index.json
```

`Indexed: 1` is the number of documents (not chunks) newly indexed in this run. `Excluded` is the number of documents skipped because of an exclusion (see Step 5).

A JSON log is saved under `logs/` on every run. Use `--output-log` to change where the log is written.
If you run `add` again on a project that has already been indexed and then re-run `index`, already-indexed documents are skipped and only the newly added ones are processed.

Use `--profile` to run `index` with a specific profile configuration.


### Step 4. Search

The knowledge base is now searchable. Give it a try:

```bash
mrag search "keyword"
```

With the default profile, **hybrid search** runs (a combination of keyword search and vector search whose results are fused).

The output looks like this:

```
[1] score=0.0328  doc=sample.md  chunk=eb0495d2...
    source: sample.md  original: data/documents/7c9e6679-7425-40de-944b-e07fc1f90ae7/original.md
    section: Sample > Overview
    …the opening excerpt of the chunk body is shown here…

[2] score=0.0323  doc=sample.md  chunk=3fa12c11...
    source: sample.md  original: data/documents/7c9e6679-7425-40de-944b-e07fc1f90ae7/original.md
    section: Sample > Setup
    …excerpt from the next chunk…

Score stats:  min=0.0323  max=0.0328  mean=0.0325  σ=0.0004

Document distribution:
  sample.md                                ████████████████████ 2
```

What each line means:

- `score` — relevance score (higher = closer to the query). With the default hybrid search it is a rank-fusion (RRF) score, so the values are small (about 0.033 at most)
- `doc` — document the hit came from
- `chunk` — the first 8 characters of the chunk ID. To drill into a chunk with `mrag inspect chunk <id>` you need the full ID, which `mrag search --json` (`chunk_id`) and `mrag inspect chunks <document_id>` show
- `source` / `original` — the name of the source and the project-relative path of the stored copy of the original
- `section` — heading path of the chunk (shown only when the chunk has one)
- `Score stats` — score distribution across the top results. Lets you visually check whether scores are clustered or spread out
- `Document distribution` — a bar chart of how many hits came from which document

By passing these search results to an AI agent, you can supply the agent with knowledge.

#### Switching the retrieval strategy

mrag offers several retrieval strategies, switchable with `--strategy`.

```bash
# Keyword only (FTS5 BM25 — fast but weak against orthographic variation)
mrag search "your query" --strategy keyword

# Vector only (semantic match — picks up paraphrases and different wording)
mrag search "your query" --strategy vector

# Hybrid (default — fuses the two above)
mrag search "your query" --strategy hybrid
```

For most knowledge bases you build, `hybrid` will serve you well, but you can switch strategies as needed.

#### Reranking

Setting `rerank.enabled` to `true` in the profile activates the reranker.

```bash
# Show only the top 3 results
mrag search "your query" --top-k 3

# Disable reranking (CrossEncoder reordering) for this search
mrag search "your query" --no-rerank
```

### Step 5. Exclude outdated knowledge without deleting its source

Use a document-level exclusion when a retained document must stop contributing
to CLI, HTTP API, and MCP retrieval. Obtain its `document_id` from
`mrag search --json` or `GET /api/v1/documents`, preview the cleanup, then apply
it:

```bash
mrag exclusions add --document-id <DOCUMENT_ID>
mrag exclusions add --document-id <DOCUMENT_ID> \
  --reason "superseded specification" --force
```

The exclusion policy becomes active first, then `--force` physically cleans
derived FTS, chunk/variant, document-index, and Qdrant data while retaining the
original and extracted artifacts. Index and reindex continue to skip the
document.

To restore it, use the exclusion ID returned by `mrag exclusions list`, then
index the retained document explicitly:

```bash
mrag exclusions restore <EXCLUSION_ID> --force
mrag index --document-id <DOCUMENT_ID>
```

See [document retrieval exclusions](./document-exclusions.md) for profile scope,
degraded Qdrant cleanup, retry behavior, and the distinction from destructive
`mrag remove --force`.

---

## Operating mrag with AI agents

### SKILL.md

By having an AI agent like Claude Code read [AGENTS.md](../AGENTS.md) and [SKILL.md](../SKILL.md), you can let the agent operate mrag directly.

### Prompt examples

#### Creating a knowledge base

```
> Create a new mrag project at ./my-kb and build a default RAG knowledge base from all the Markdown files stored under ./documents/.

...

⏺ Bash(cd /home/user/tools/mrag &&           
      /home/user/tools/mrag/.venv/bin/mrag init ./my-kb
      --non-interactive --kb-info-json /tmp/my-kb-info.json)

...

⏺ Bash(cd /home/user/tools/mrag/my-kb &&
      /home/user/tools/mrag/.venv/bin/mrag add ../documents --recursive
      --include '**/*.md' --dry-run --json)

...

⏺ Bash(cd /home/user/tools/mrag/my-kb &&
      /home/user/tools/mrag/.venv/bin/mrag add ../documents --recursive
      --include '**/*.md' --strict --json)

...

⏺ Bash(/home/user/tools/mrag/.venv/bin/mrag index 2>&1 | tee          
      mrag-index-$(date +%Y%m%d-%H%M%S).log) 

...

⏺ Bash(/home/user/tools/mrag/.venv/bin/mrag search "Some keyword      
      in document" --top-k 3 --json 2>/dev/null |
      /home/user/tools/mrag/.venv/bin/python -c "                     
      import jso…)                                         
  ⎿  strategy: hybrid reranked: False count: 3                          
     score stats: {'min': 0.015873015873015872, 'max': 0.0163934426
     2295082, 'mean': 0.016131830251343737, 'stdev': 0.000260224656

...

⏺ Indexing complete. End-to-end search has been verified.

```

#### Searching

```
> Turn the reranker on and investigate the operating temperature range specifications for the temperature sensor.

...

  Read 1 file (ctrl+o to expand)                                        
                                                   
⏺ Update(profiles/default.yaml)                                         
  ⎿  Added 1 line, removed 1 line 

...

⏺ Bash(/home/user/tools/mrag/.venv/bin/mrag search "temperature sensor
      operating-temperature range usage specification" --top-k 6 --json 2>/dev/null
      > /tmp/temp-search.json; /home/user/tools/mrag/.venv/b…)
  ⎿  strategy: hybrid reranked: True count: 6                           
     score stats: {'min': 0.5065747499465942, 'max': 0.581636965274
     8108, 'mean': 0.5461344619592031, 'stdev': 0.02947577986893289

...

⏺ I enabled the reranker (rerank.enabled: true) and investigated the
  temperature-sensor specification documents within my-kb.
  The sensor reading range that can be configured is shown above; the
  product's guaranteed operating range, including the module itself, is
  -10°C to 50°C...

```

SKILL.md captures both the basic mrag commands and the operational know-how for using it, so even without mastering the mrag command set yourself you can easily ask an AI agent to build a target RAG knowledge base or run accuracy analyses for you.
