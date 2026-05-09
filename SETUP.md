# SETUP.md — mrag Setup Guide for LLM Agents

This document covers installation of mrag up to the point where the `mrag` command is available.
Project initialization and beyond are left to the user — see Quick Start in README.md.

Follow each section in order. Verification commands are provided after each step — do not proceed until they pass.

---

## 1. Prerequisites

### git

```bash
git --version   # must be available
```

If missing, install via your system package manager (e.g. `brew install git` on macOS, `apt install git` on Ubuntu).

### Python

```bash
python --version   # must be 3.11 or later
```

If Python 3.11+ is not available, install it via pyenv or your system package manager before continuing.

### uv (package manager)

```bash
uv --version
```

If missing:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

After installing, restart your shell or run `source ~/.local/bin/env` to put `uv` in PATH.

### Ollama

```bash
ollama --version
```

If missing, download and install from https://ollama.com.

Confirm the server is running:

```bash
ollama list   # returns a list (possibly empty) without error
```

If Ollama is not running, start it:

- **macOS**: launch the Ollama desktop app from Applications
- **Linux**: `ollama serve &`

---

## 2. Clone and Install

```bash
git clone https://github.com/bathtimefish/mrag.git
cd mrag
uv venv --python 3.11
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

```bash
uv pip install -e ".[vaporetto,reranker]"
```

- `vaporetto`: Japanese morphological tokenization. Installs `apsw`, which provides SQLite extension loading on macOS (the system `sqlite3` does not support it). The native shared library must also be placed separately — see §3.
- `reranker`: CrossEncoder reranking via `sentence-transformers`.

**Verification:**

```bash
mrag --help   # must print the command list without error
```

> **Note:** All subsequent `mrag` commands must be run with this virtual environment activated. The activate command is `source .venv/bin/activate` from the `mrag` repository root.

---

## 3. Vaporetto Tokenizer (optional, recommended for Japanese)

Vaporetto requires a native shared library (`libsqlite_vaporetto.dylib` on macOS, `.so` on Linux) in addition to the Python package installed in §2.

### Automated download via GitHub API

The following commands detect your OS and architecture, fetch the latest release URL, and download the correct `-with-model` package.

> Always use the **`-with-model`** variant. It includes the tokenization model data (`bccwj-suw+unidic_pos+kana.model.zst`) required for Japanese morphological analysis. The plain variant does not include it and will not work.

**macOS:**

```bash
ARCH=$(uname -m)
[ "$ARCH" = "arm64" ] && ARCH="aarch64"
DOWNLOAD_URL=$(curl -s "https://api.github.com/repos/hotchpotch/sqlite-vaporetto/releases/latest" \
  | grep "browser_download_url" \
  | grep "macos-${ARCH}-with-model" \
  | cut -d '"' -f 4)
echo "Downloading: $DOWNLOAD_URL"
curl -L -o sqlite-vaporetto-with-model.tar.gz "$DOWNLOAD_URL"
```

**Linux:**

```bash
ARCH=$(uname -m)   # x86_64 or aarch64
DOWNLOAD_URL=$(curl -s "https://api.github.com/repos/hotchpotch/sqlite-vaporetto/releases/latest" \
  | grep "browser_download_url" \
  | grep "linux-${ARCH}-with-model" \
  | cut -d '"' -f 4)
echo "Downloading: $DOWNLOAD_URL"
curl -L -o sqlite-vaporetto-with-model.tar.gz "$DOWNLOAD_URL"
```

**Verification:** Confirm `$DOWNLOAD_URL` is non-empty and the download completes without error before continuing.

### Extract and place the library

The archive extracts to a single top-level directory (e.g. `sqlite-vaporetto-v0.4.0-macos-aarch64-with-model/`). The shared library is directly inside that directory.

```bash
tar -xzf sqlite-vaporetto-with-model.tar.gz
EXTRACTED_DIR=$(tar -tzf sqlite-vaporetto-with-model.tar.gz | head -1 | cut -d/ -f1)
mkdir -p ~/.mrag/extensions

# macOS
cp "${EXTRACTED_DIR}/libsqlite_vaporetto.dylib" ~/.mrag/extensions/

# Linux
# cp "${EXTRACTED_DIR}/libsqlite_vaporetto.so" ~/.mrag/extensions/
```

**Verification:**

```bash
# macOS
ls -la ~/.mrag/extensions/libsqlite_vaporetto.dylib   # file must exist

# Linux
# ls -la ~/.mrag/extensions/libsqlite_vaporetto.so
```

### Custom path (alternative to ~/.mrag/extensions/)

```bash
export MRAG_VAPORETTO_LIB=/path/to/libsqlite_vaporetto.dylib
```

---

## 4. Pull Ollama Models

### Embedding model (required for all projects)

```bash
ollama pull bge-m3
```

`bge-m3` is a multilingual 1024-dim model (~1.2 GB). The download may take several minutes depending on network speed.

**Verification:**

```bash
ollama list | grep bge-m3   # must appear in the list
```

### Generation model (required only for contextual augmentation)

Skip this if you do not plan to use `augmentation.strategy: contextual`.

If the project profile uses contextual augmentation, the generation model must also be pulled before `mrag index`:

```bash
ollama pull gemma4:e4b   # default model; check profiles/default.yaml → augmentation.model
```

The generation model is separate from the embedding model — it generates a short context text per chunk at index time only.

---

## Setup Complete

The `mrag` command is now available. Run `mrag --help` to confirm.

For next steps — initializing a project, adding documents, and building the index — refer to **Quick Start** in [README.md](./README.md).

Key points to keep in mind when using `mrag init`:

- `mrag init` creates a **subdirectory** inside the current working directory. Run it from the **parent** directory of where you want the project to live — not from inside the intended project directory.
- The vaporetto library (§3) must be in place **before** running `mrag init`. The tokenizer is auto-detected at init time and stored in `mrag.yaml`. Changing it after init requires deleting the project and re-running `mrag init`.
- Only one `mrag index` process can run at a time (Qdrant `mode: local` holds a file lock on the `qdrant/` directory).
