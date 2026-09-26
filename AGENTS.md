# mrag agent guide

mrag is a Python, local-first retrieval runtime. One KB project contains its
own documents, indexes, and retrieval profiles. Distinguish operating a KB from
changing the mrag implementation; the source checkout is not necessarily a KB.

## Choose the relevant guidance

- For KB creation, ingestion, search, tuning, or document/server management,
  use [SKILL.md](SKILL.md). It contains operational prerequisites and links to
  task-specific instructions. Existing KBs should resume at the needed stage.
- For source changes, use the code and tests for the affected subsystem below.
  A source edit does not require initializing a KB or running model services.
- For installation or missing dependencies, see [SETUP.md](SETUP.md).
  [README.md](README.md) is the documentation index and CLI overview.

Read references when their subject matters to the task, rather than loading the
whole manual or repeating an environment audit before every edit.

## Data and compatibility boundaries

- **SQLite is authoritative.** `mrag.db` holds documents, canonical chunks, FTS5,
  profile/index state, and exclusion policies. Qdrant holds derived vectors.
  Preserve retained sources and SQLite state when changing vector storage.
- **Configuration has distinct audiences.** `mrag.yaml` controls the runtime;
  `profiles/*.yaml` controls retrieval/indexing. `kb_information.yaml` describes
  the KB for external agents and is not runtime configuration.
- **Ingestion and indexing are separate.** `add` accepts Markdown/plain text and
  stores source/extraction artifacts; `index` creates retrieval artifacts.
  Document conversion belongs outside mrag. Partial recursive failures retain
  successful additions; strict mode does not imply rollback.
- **Source identity is path-based.** A document ID stays stable when content at
  the same source changes. Project paths and registered external roots have
  distinct identities; legacy rows retain `legacy_unbound` identity rather than
  guessing their original path. Non-path identities live under the reserved
  `identities/` namespace (scheme 2), so no project file may be added from
  there; only `mrag catalog migrate-identities` rewrites a stored identity. Content identity (SHA-256) still applies across
  sources, so a re-added file matching any registered document — migrated rows
  included — is `skipped_duplicate`. Native API and MCP lists share one row contract.
- **Tokenizer choice is a schema decision.** `mrag.yaml.fts_tokenizer` must match
  the initialized FTS5 table and any explicit profile tokenizer. `reindex` does
  not migrate that table to another tokenizer. Preserve index/query
  normalization and literal query escaping when changing keyword retrieval.
- **Index identity controls rebuilds.** Changes to chunking, embedding identity,
  augmentation strategy, and effective contextual prompts must be reflected in
  differential indexing. Retrieval/rerank settings and retry/failure policies
  must not cause unnecessary rebuilds. See [profile config](mrag/config/profile.py)
  and [index decisions](mrag/core/indexing/diff.py) for the implementation.
- **Generated context is separate from source text.** Contextual augmentation
  changes embedding input; FTS5 retains the canonical chunk body. Augmentation
  success does not establish factual accuracy. Embedding fallback chunks with
  a null Qdrant point ID remain keyword-searchable and retain failure metadata.
- **Exclusions are persistent retrieval policy.** Preserve enforcement in
  indexing/reindexing and CLI/API/MCP retrieval. Exclusion retains sources;
  removal deletes them. Restoration revokes policy but requires explicit
  indexing to restore searchability. Incomplete vector cleanup must not expose
  excluded documents; see [exclusion semantics](docs/document-exclusions.md).
- **Interfaces share retrieval behavior.** Respect the profile's `top_k` when
  callers omit a count, and preserve explicit overrides and profile scoping.
  CLI JSON stdout must remain machine-readable, with diagnostics on stderr.
  MCP is read-only; its stdio stdout is reserved for JSON-RPC. Preserve existing
  API/MCP authentication behavior when changing their request paths.

## Find the implementation and its tests

Use this map to locate the affected behavior, not as a required reading order.

| Area | Implementation | Tests and details |
|---|---|---|
| CLI entry points and options | [mrag/cli](mrag/cli) | [tests](tests), especially `test_init`, `test_add`, `test_search_json`, `test_kb_info_cli` |
| Runtime/profile configuration | [mrag/config](mrag/config) | [Profile validation](tests/test_profile_validation.py), [KB metadata](docs/kb-information.md) |
| Ingestion and recursive selection | [mrag/core/ingestion](mrag/core/ingestion), [extractors](mrag/extractors) | [Recursive tests](tests/test_recursive_add.py), [selection rules](docs/recursive-add.md) |
| SQLite, FTS5, Qdrant and migration | [mrag/db](mrag/db) | `test_schema`, `test_tokenizer`, `test_db_connection`, `test_qdrant_migrate` in [tests](tests) |
| Chunking | [mrag/core/chunking](mrag/core/chunking) | [Chunking guide](docs/chunking-strategies.md); chunking/block/parent-child tests |
| Indexing, embedding and fallback | [indexing](mrag/core/indexing), [embedding](mrag/core/embedding) | [Indexing tests](tests/test_indexing.py), [contextual retrieval](docs/contextual-retrieval.md); embedding/augmentation fallback tests |
| Retrieval and reranking | [retrieval](mrag/core/retrieval), [reranking](mrag/core/reranking) | [Retrieval tests](tests/test_retrieval.py), [strategies](docs/retrieval-strategies.md), [reranking](docs/reranking.md) |
| Exclusions | [mrag/core/exclusions.py](mrag/core/exclusions.py), [DB policy](mrag/db/exclusions.py) | [Exclusion tests](tests/test_exclusions.py) |
| HTTP and Dify | [mrag/api](mrag/api) | [API tests](tests/test_api.py), [Dify tests](tests/test_dify_api.py), [API contract](docs/native-api.md) |
| MCP | [mrag/mcp](mrag/mcp) | [MCP tool tests](tests/test_mcp_tools.py), [MCP configuration](docs/mcp.md) |
| Inspection and registry | [Inspect queries](mrag/db/inspect_queries.py), [CLI](mrag/cli) | `test_inspect_*`, `test_registry_*` in [tests](tests); [inspection](docs/inspect.md), [registry](docs/registry.md) |

## Development and validation

Python >= 3.11 and dependencies/extras are defined in [pyproject.toml](pyproject.toml).
Use the repository's environment when available. With `.venv` installed, a
focused test invocation is `rtk proxy .venv/bin/python -m pytest tests/<test_file>.py`.

Select tests for the behavior changed. Existing fixtures use temporary projects
or in-memory SQLite, and many provider tests use mocks; some integration tests
contact local Ollama/Qdrant and skip when services are unavailable. Inspect the
selected tests before relying on their isolation or treating skips as coverage.
Run and repair affected isolated tests within the requested change without
pausing for approval at each iteration. A full suite is useful for broad shared
changes, not a prerequisite for every documentation edit.

For real CLI smoke tests, use a disposable KB outside existing user data. The
operating constraints (working directory, model dependencies, Qdrant modes,
indexing and restoration) are in [SKILL.md](SKILL.md). Distinguish environment
failures from implementation failures; do not change a KB's tokenizer or
retrieval semantics merely to bypass an unavailable dependency.

When changing a public command, schema, or retrieval behavior, update the
relevant maintained documentation, including its English/Japanese counterpart
where present. Keep detailed options and examples in those documents; update
this guide or SKILL.md only when a prerequisite, boundary, or routing changes.
Report what changed, the checks performed, and any unverified behavior or skips.
