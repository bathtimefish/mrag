English / [日本語](README-ja.md)

# mrag — Micro RAG

**A lightweight, local-first retrieval runtime for building RAG pipelines.**

mrag is a CLI for building and operating small-scale RAG knowledge bases. It provides everything from document indexing to search, with a variety of strategies for building custom RAG pipelines to fit your needs. Skills for AI agents let you expose your knowledge base to any AI agent.

mrag ingests Markdown and plain text. Converting other formats is the job of a document conversion engine such as [docling](https://github.com/docling-project/docling) or [MarkItDown](https://github.com/microsoft/markitdown); run one of those first and feed mrag the Markdown it produces.

---

Release notes, including breaking changes and upgrade steps, live in [CHANGELOG.md](./CHANGELOG.md).

---

## Requirements

| Component | Notes |
|---|---|
| Python 3.11+ | |
| [Ollama](https://ollama.com) | `ollama serve` must be running. Defaults use `bge-m3` for embeddings and `gemma4:e2b` for contextual augmentation |
| [Qdrant](https://qdrant.tech) | The default `local` mode runs Qdrant in-process. Docker Qdrant is required only when `qdrant.mode: server` is set |


## Installation

```bash
git clone https://github.com/bathtimefish/mrag.git
cd mrag
```

You can run an agent such as Claude Code in the cloned directory and have it read [SETUP.md](./SETUP.md) to complete the setup automatically.

The manual setup steps are below.

We recommend [uv](https://docs.astral.sh/uv/) for installing Python modules:

```bash
uv venv
uv pip install -e ".[vaporetto,reranker]"
```

This installs the standard configuration that includes Japanese morphological tokenization (`vaporetto`) and CrossEncoder reranking (`reranker`).

### Vaporetto native library

The `vaporetto` extra installs `apsw` (for SQLite extension loading), but the native shared library must be placed separately.

1. Download the latest **`-with-model.tar.gz`** that matches your OS / architecture from [sqlite-vaporetto releases](https://github.com/hotchpotch/sqlite-vaporetto/releases) (use the model-bundled variant).
2. Extract the archive and place the shared library under `~/.mrag/extensions/`:

   ```bash
   mkdir -p ~/.mrag/extensions
   cp libsqlite_vaporetto.dylib ~/.mrag/extensions/   # macOS
   # cp libsqlite_vaporetto.so ~/.mrag/extensions/    # Linux
   ```

To use a custom path, set the environment variable:

```bash
export MRAG_VAPORETTO_LIB=/path/to/libsqlite_vaporetto.dylib
```

If vaporetto is not detected when you run `mrag init`, mrag falls back to the trigram tokenizer automatically. Run `mrag doctor` to verify the detection state.

### Pull the embedding model

```bash
ollama pull bge-m3
```

`bge-m3` is a multilingual model that supports Japanese and English (1024 dimensions). You can swap it for any Ollama-compatible model by editing `profiles/default.yaml`.


## Quick Start

Four mrag commands are all it takes to create a KB from a directory and search it:

```bash
mrag init my-kb --non-interactive
cd my-kb
mrag add /path/to/documents --recursive --include '**/*.md'
mrag index
mrag search "your query"
```

Pass a single file instead of a directory when bulk ingestion is unnecessary.
Adding and indexing are intentionally separate operations.

For step-by-step details and the agent-integration workflow, see [docs/tutorial.md](./docs/tutorial.md).


## CLI commands

| Command | Role |
|---|---|
| `mrag init [PROJECT_DIR]` | Initialize a project |
| `mrag add <path>` | Add one document, or a directory with `--recursive` |
| `mrag index` | Build the index |
| `mrag reindex` | Rebuild the index |
| `mrag search <query>` | Run a search |
| `mrag eval <query>` | Evaluate retrieval quality |
| `mrag serve` | Start the HTTP API server |
| `mrag mcp` | Expose the project as a read-only MCP server |
| `mrag remove <doc-id>` | Remove a document |
| `mrag exclusions add \| list \| restore` | Retain a document while excluding it from retrieval |
| `mrag profiles list \| show <name>` | List or show profile details |
| `mrag kb-info show \| validate \| schema` | Manage the knowledge-base self-description |
| `mrag inspect document \| chunks \| chunk \| sections` | Inspect the index internals |
| `mrag registry generate \| validate` | Manage the multi-KB registry |
| `mrag extract <file>` | Run text extraction only |
| `mrag show-extracted <doc-id>` | Show the extracted text |
| `mrag export-extracted <doc-id>` | Export the extracted text to a file |
| `mrag doctor` | Check the environment |

Run `mrag <command> --help` for the full set of options.

For directory ingestion, preview with `mrag add <dir> --recursive --dry-run
--json`, then apply the same selection without `--dry-run`. See [recursive
directory ingestion](./docs/recursive-add.md) for filtering, symlink, duplicate,
concurrency, and partial-success behavior.

To stop a retained document from contributing knowledge, use the dry-run-first
`mrag exclusions` workflow instead of `mrag remove`. See [document retrieval
exclusions](./docs/document-exclusions.md) for cleanup, restoration, and failure
semantics.


## Documentation

Per-feature details live under `./docs/`.

### Getting Started

- [tutorial.md](./docs/tutorial.md) — Your first mrag session (init → add → index → search)
- [recursive-add.md](./docs/recursive-add.md) — Safe bulk ingestion with filters and deterministic reporting

### Retrieval

- [chunking-strategies.md](./docs/chunking-strategies.md) — Four chunking strategies
- [retrieval-strategies.md](./docs/retrieval-strategies.md) — Four retrieval strategies and fusion methods
- [contextual-retrieval.md](./docs/contextual-retrieval.md) — Anthropic-style contextual retrieval
- [reranking.md](./docs/reranking.md) — CrossEncoder reranking

### Operations

- [document-exclusions.md](./docs/document-exclusions.md) — Reversibly exclude retained documents from retrieval
- [inspect.md](./docs/inspect.md) — Inspecting the index
- [kb-information.md](./docs/kb-information.md) — Knowledge-base self-description (`kb_information.yaml`)
- [registry.md](./docs/registry.md) — Aggregating multiple KBs (`knowledge_registry.yaml`)

### API

- [mcp.md](./docs/mcp.md) — Model Context Protocol server (`mrag mcp`)
- [dify-api.md](./docs/dify-api.md) — Dify External Knowledge API compatible endpoint
- [native-api.md](./docs/native-api.md) — mrag Native REST API

### Deployment

- [packaging.md](./docs/packaging.md) — Single-binary packaging with PyInstaller (optional distribution method)


## License

Copyright (c) 2026 BathTimeFish KK.

Licensed under the [MIT License](./LICENSE).

Releases up to and including 0.27.0 were licensed under AGPL-3.0, a condition of the PyMuPDF dependency that 1.0.0 removed.

Third-party dependency licenses are listed in [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).


## Acknowledgements

mrag uses [sqlite-vaporetto](https://github.com/hotchpotch/sqlite-vaporetto) by [@hotchpotch](https://github.com/hotchpotch) for Japanese morphological tokenization via SQLite FTS5.

- **sqlite-vaporetto** — licensed under `MIT OR Apache-2.0`
- **bundled model** (`bccwj-suw+unidic_pos+kana.model.zst`, included in `-with-model` releases) — licensed under [BSD-3-Clause](https://opensource.org/license/BSD-3-Clause), sourced from [daac-tools/vaporetto-models](https://github.com/daac-tools/vaporetto-models/releases)

If you redistribute mrag together with the sqlite-vaporetto library or its bundled model, the BSD-3-Clause copyright notice for the model must be included in your distribution.
