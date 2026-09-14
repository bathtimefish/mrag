---
name: mrag
description: Operate mrag knowledge bases when creating, ingesting, searching, tuning retrieval, managing documents, or exposing an existing KB through HTTP or MCP.
license: MIT
metadata:
  upstream: https://github.com/bathtimefish/mrag
  version: "1.0.2"
---

# mrag knowledge base operations

Use mrag for project-scoped, local-first retrieval. SQLite (`mrag.db`) is the
source of truth; Qdrant is a rebuildable vector index. Keep unrelated knowledge
domains in separate projects. This skill covers KB operation; ordinary changes
to mrag's source code do not require following a KB lifecycle.

## Essential constraints

- Run project commands from the target KB directory containing `mrag.yaml`.
  The source checkout and the KB can be different directories. If using a
  checkout's virtual environment, resolve `.venv/bin/mrag` to an absolute path
  before changing directories. `doctor` is project-independent; `init` and
  `registry` operate on the paths supplied to them.
- `mrag init --name <name> --non-interactive` creates a subdirectory. Run it
  from the intended parent to avoid double nesting.
- Ingestion follows `init → add → index → search`. Resume at the needed stage
  for an existing KB. `add` stores documents but does not index them.
- `add` accepts `.md`, `.markdown`, and `.txt`. Convert other formats first;
  prefer Docling for PDF structure and MarkItDown for Office documents.
- Preserve the initialized FTS5 tokenizer. Editing YAML or running `reindex`
  does not migrate the FTS5 schema. An explicit profile tokenizer must match.
- Before indexing or vector retrieval, check the selected profile's embedding
  model/endpoint and `qdrant.mode`. Local mode needs no external Qdrant process;
  server mode does. A missing mode defaults to server. Contextual indexing also
  needs the configured generation model. Keyword retrieval needs neither
  Qdrant nor Ollama when reranking is disabled.
- Use stable document IDs for document operations; chunk IDs, filenames, and
  exclusion IDs are not interchangeable. `remove --force` deletes retained
  sources as well as indexes. Exclusion retains sources; restoration requires
  a subsequent explicit `index` to make them searchable again.

## Read details for the current operation

Read the relevant section of the linked document when needed; these are not a
checklist to read in full before every task. Use `mrag <command> --help` for
exact flags. [AGENTS.md](AGENTS.md) describes shared invariants and development
guidance; it is not a prerequisite to reread before each command.

| Task | Reference |
|---|---|
| Install mrag or diagnose missing runtime dependencies | [SETUP.md](SETUP.md); `mrag doctor` |
| Create a KB, add files, index, or export extracted text | [Tutorial](docs/tutorial.md); `mrag show-extracted` / `mrag export-extracted --help` |
| Add a directory with filters or recover partial ingestion | [Recursive ingestion](docs/recursive-add.md) |
| Populate or validate agent-facing KB metadata | [KB information](docs/kb-information.md) |
| Select search strategy, compare results, or diagnose misses | [Retrieval strategies](docs/retrieval-strategies.md) |
| Change chunk boundaries, preserve tables/code, or use parent-child | [Chunking strategies](docs/chunking-strategies.md) |
| Configure or audit contextual generation and embedding fallbacks | [Contextual retrieval](docs/contextual-retrieval.md) |
| Enable or tune reranking | [Reranking](docs/reranking.md) |
| Inspect chunk bodies, context, or section hierarchy | [Inspection](docs/inspect.md) |
| Exclude, restore, or permanently remove a document | [Document exclusions](docs/document-exclusions.md) |
| Discover or aggregate multiple KBs | [Registry](docs/registry.md) |
| Serve HTTP retrieval or integrate Dify | [Native API](docs/native-api.md), [Dify API](docs/dify-api.md) |
| Expose a KB through MCP | [MCP](docs/mcp.md) |

## Choose settings for the goal

Use these as starting points, not guaranteed performance rankings. Distinguish
missing retrieval candidates from poor ordering of candidates already found.

| Goal or symptom | Candidate approach | Tradeoff or condition |
|---|---|---|
| Fragments lack context and semantic search misses relevant content | Consider contextual augmentation | Adds a generation call per chunk during indexing; inspect generated context for accuracy. |
| Relevant content is retrieved but ranks too low | Consider reranking | Adds query-time work and cannot recover content absent from its candidate set. Keep it disabled for parent-child retrieval because parent truncation can distort scores. |
| Faster index construction | Start with `augmentation.strategy: none` | Avoids per-chunk generation, at the cost of contextual assistance. Embedding model, chunk count, and hardware also affect build time. |
| Simple plain-text sources | Use `recursive` as a baseline | It is not a guarantee of the fastest build; choose chunk size and overlap for the content. |
| Markdown with important headings, tables, or code | Consider Markdown-aware chunking and the relevant `preserve_*` options | Preserve useful structure; large atomic blocks can increase embedding input size. |

Combine augmentation and reranking when both context loss and ranking quality
justify their costs, rather than treating the combination as a universal
accuracy preset. Use the linked chunking, contextual retrieval, and reranking
guides above for configuration details.

For tuning, compare the current configuration with a candidate on representative
questions and known source passages. Check whether relevant passages are found,
where they rank, and the build/query time that matters to the user. Describe
untested benefits as hypotheses; use `eval` for result/profile inspection, not
as an automatic ground-truth accuracy measurement.

## Indexing and profile changes

Ordinary `index` is differential. Chunking, embedding identity, augmentation
strategy, and the effective contextual prompt can invalidate stored indexes;
use ordinary `index` to apply detected changes. Query-time retrieval/rerank
settings and retry/failure policies do not require rebuilding. Reserve
`reindex` for an intentional rebuild or retrying stored embedding fallbacks.

For long or unattended runs, retain an inspectable progress stream (for example,
a timestamped `tee` log) and preserve the indexing command's exit status if
using a pipeline. The automatic JSON log in `logs/` records the completed run;
it does not replace live progress. Inspect failures and fallback counts before
reporting completion. Successful recursive additions remain stored after a
partial failure; retrying without `--force` skips duplicate sources.

When configuring parent-child retrieval, both chunking and retrieval strategies
must be `parent_child`. Allow enough child candidates for parent deduplication.
Leave parent-child reranking disabled because truncating large parents can make
scores unreliable; BERT-based rerankers must not exceed `max_length: 512`.
Context prompt templates must retain `{document}` and `{chunk}` placeholders.

## Retrieval and evidence

Use `mrag search "<query>" --json` for machine-readable results. Omit `--top-k`
to respect the profile's configured count. Use `eval` when duplicate detection
or profile comparison is needed, and `inspect` for selected chunks rather than
loading every chunk body. Multi-profile chunk/section inspection needs an
explicit `--profile`.

For vaporetto keyword search, whitespace-separated terms are ANDed; continuous
Japanese text becomes a phrase. Rewrite natural-language questions as keywords
for that branch, or use vector/hybrid retrieval for semantic matching. To
investigate literal matches, use `--strategy keyword --no-rerank --json` and
inspect full content. Zero hits do not alone prove a topic is absent: check
index state, query form, profile, and exclusions as relevant.

RRF scores are compressed ranking scores; low variance does not establish poor
relevance. Judge quality against the query and source chunks. Contextual text
is generated retrieval assistance, not source evidence; a successful generation
status does not establish its accuracy. FTS5 retains the original chunk body.

## Integration and portability

MCP is read-only and its stdio stdout is reserved for JSON-RPC. HTTP POST requests
need JSON content type; when `MRAG_API_KEY` is configured, supply bearer auth.
Registry KB paths resolve relative to the registry file's directory, and the
generator discovers projects one level below its root.

When moving a local-mode KB, stop writers and copy a consistent complete project,
including `mrag.db`, `qdrant/`, profiles, configuration, and retained documents.
The destination needs compatible runtime dependencies and the same embedding
model for vector queries. Server-mode vectors are external to the project;
plan their transfer or rebuild separately.

Report the requested outcome with relevant evidence: selected KB/profile,
retrieval sources, or indexing results and unresolved failures. Check the behavior
changed by the task without imposing a full environment audit on every operation.
