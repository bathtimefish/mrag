English / [日本語](README-ja.md)

# mrag — Micro RAG

**A lightweight, local-first retrieval runtime for building RAG pipelines.**

mrag is a CLI for building and operating small-scale RAG knowledge bases. It provides everything from document indexing to search, with a variety of strategies for building custom RAG pipelines to fit your needs. Skills for AI agents let you expose your knowledge base to any AI agent.

---

## Requirements

| Component | Notes |
|---|---|
| Python 3.11+ | |
| [Ollama](https://ollama.com) | `ollama serve` must be running. Defaults use `bge-m3` for embeddings and `gemma4:e4b` for contextual augmentation |
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

Four commands are all it takes to create a KB and search it:

```bash
mrag init my-kb --non-interactive
cd my-kb
mrag add /path/to/report.pdf
mrag index
mrag search "your query"
```

For step-by-step details and the agent-integration workflow, see [docs/tutorial.md](./docs/tutorial.md).


## CLI commands

| Command | Role |
|---|---|
| `mrag init [PROJECT_DIR]` | Initialize a project |
| `mrag add <file>` | Add a document (PDF / Markdown / text) |
| `mrag index` | Build the index |
| `mrag reindex` | Rebuild the index |
| `mrag search <query>` | Run a search |
| `mrag eval <query>` | Evaluate retrieval quality |
| `mrag serve` | Start the HTTP API server |
| `mrag remove <doc-id>` | Remove a document |
| `mrag profiles list \| show <name>` | List or show profile details |
| `mrag kb-info show \| validate \| schema` | Manage the knowledge-base self-description |
| `mrag inspect document \| chunks \| chunk \| sections` | Inspect the index internals |
| `mrag registry generate \| validate` | Manage the multi-KB registry |
| `mrag extract <file>` | Run text extraction only |
| `mrag show-extracted <doc-id>` | Show the extracted text |
| `mrag export-extracted <doc-id>` | Export the extracted text to a file |
| `mrag doctor` | Check the environment |

Run `mrag <command> --help` for the full set of options.


## Documentation

Per-feature details live under `./docs/`.

### Getting Started

- [tutorial.md](./docs/tutorial.md) — Your first mrag session (init → add → index → search)

### Retrieval

- [chunking-strategies.md](./docs/chunking-strategies.md) — Four chunking strategies
- [retrieval-strategies.md](./docs/retrieval-strategies.md) — Four retrieval strategies and fusion methods
- [contextual-retrieval.md](./docs/contextual-retrieval.md) — Anthropic-style contextual retrieval
- [reranking.md](./docs/reranking.md) — CrossEncoder reranking

### Operations

- [inspect.md](./docs/inspect.md) — Inspecting the index
- [kb-information.md](./docs/kb-information.md) — Knowledge-base self-description (`kb_information.yaml`)
- [registry.md](./docs/registry.md) — Aggregating multiple KBs (`knowledge_registry.yaml`)

### API

- [dify-api.md](./docs/dify-api.md) — Dify External Knowledge API compatible endpoint
- [native-api.md](./docs/native-api.md) — mrag Native REST API

### Deployment

- [packaging.md](./docs/packaging.md) — Single-binary packaging with PyInstaller (optional distribution method)


## License

Copyright (c) 2026 BathTimeFish KK.

Licensed under [GNU Affero General Public License v3.0](./LICENSE).


## Acknowledgements

mrag uses [PyMuPDF](https://github.com/pymupdf/pymupdf) for PDF text extraction and table detection. PyMuPDF is developed and maintained by [Artifex Software](https://artifex.com) and licensed under AGPL-3.0.

mrag uses [sqlite-vaporetto](https://github.com/hotchpotch/sqlite-vaporetto) by [@hotchpotch](https://github.com/hotchpotch) for Japanese morphological tokenization via SQLite FTS5.

- **sqlite-vaporetto** — licensed under `MIT OR Apache-2.0`
- **bundled model** (`bccwj-suw+unidic_pos+kana.model.zst`, included in `-with-model` releases) — licensed under [BSD-3-Clause](https://opensource.org/license/BSD-3-Clause), sourced from [daac-tools/vaporetto-models](https://github.com/daac-tools/vaporetto-models/releases)

If you redistribute mrag together with the sqlite-vaporetto library or its bundled model, the BSD-3-Clause copyright notice for the model must be included in your distribution.
