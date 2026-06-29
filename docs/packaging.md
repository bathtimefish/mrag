# Packaging into a single binary (PyInstaller)

This document explains how to **package mrag into a single executable** with PyInstaller for distribution.

This is an **optional** distribution method. The standard way to use mrag remains `uv pip install -e .` (or `pip install`); this procedure does not replace it. Packaging exists for cases where you want to ship or run mrag without setting up a Python environment. Nothing here modifies mrag's runtime source — it simply **freezes the already-installed package into one binary**.

> Note: mrag uses PyMuPDF (AGPL) for PDF extraction, so the resulting binary is governed by AGPL-3.0. Mind the license terms if you redistribute the binary.


## Prerequisites

- mrag is installed on the target OS (e.g. via `uv pip install -e .`)
- PyInstaller is installed

```bash
uv pip install pyinstaller
```

> **Important: PyInstaller cannot cross-compile.** A macOS arm64 binary must be built on macOS arm64, a Linux binary on Linux, and so on. **Build on real hardware (or an equivalent CI runner) for each platform** you want to distribute to.


## Building

Use the bundled build script `packaging/build.sh`.

```bash
packaging/build.sh                 # LEAN onefile (no reranker, default)
packaging/build.sh --with-reranker # full (bundles sentence-transformers + torch)
packaging/build.sh --onedir        # folder build — faster startup (see below)
packaging/build.sh --help
```

When the build finishes, the artifact is produced depending on the chosen mode:

- onefile (default): `dist/mrag` — a single executable
- onedir (`--onedir`): `dist/mrag/` — a folder; launch the inner `dist/mrag/mrag`

Internally the script auto-collects dependencies that PyInstaller's static analysis tends to miss (PyMuPDF / qdrant-client / uvicorn / FastAPI / pydantic / apsw) and always adds the two flags mrag requires:

- `--copy-metadata mrag` — bundles version metadata (`importlib.metadata`). Without it the binary crashes immediately on startup.
- `--collect-data mrag` — collects packaged data such as `schema.sql`. Without it `mrag init` fails.

Combined with mrag's source-level hardening for freezing (v0.21.2), these work with no extra manual steps.


## Choosing a mode

### With or without the reranker

| Mode | Bundled | Approx. size | Use case |
|---|---|---|---|
| LEAN (default) | **Excludes** torch / sentence-transformers | ~60–200 MB | Normal use without reranking |
| Full (`--with-reranker`) | Includes the CrossEncoder reranker stack | Platform-dependent (see below) | Self-contained binary including reranking |

Reranking (`rerank.enabled: true`) is an optional feature. If you don't use it, choose LEAN. The full build embeds all of PyTorch, so size, build time, and startup time all grow. Before using `--with-reranker`, the target environment must have the reranker extras installed (`uv pip install -e ".[reranker]"`).

The full build's size **depends heavily on the bundled torch build**. Measured at **~231 MB** for an onefile build on macOS arm64 (CPU/MPS torch, no CUDA). On **Linux with CUDA-enabled torch**, it can **exceed 1 GB** because the entire CUDA runtime is pulled in. The reranker model weights themselves are not bundled — they are downloaded from HuggingFace on first search.

### onefile vs onedir

This is a trade-off between "single file" and "startup speed". onefile is literally one file, but it **re-extracts itself to a temp directory on every launch**, which makes startup slow. onedir skips extraction and starts almost instantly.

Measured values for this repository's LEAN build on macOS arm64:

| Mode | Size | First launch | Subsequent launches |
|---|---|---|---|
| onefile | 58 MB (single file) | ~7 s | **~7 s every time** |
| onedir | 136 MB (folder) | ~7 s | **~0.6 s** |
| (reference) venv run | — | — | ~0.55 s |

onefile pays the extraction cost on every run, so the slow startup is noticeable for a CLI you invoke repeatedly. onedir's subsequent launches are on par with a venv install.

> **Guidance: prefer `--onedir` if you run mrag repeatedly.** Choose onefile if you simply want a single file to ship, or if invocation is infrequent.


## Smoke test

Always smoke-test after building.

```bash
# onefile
./dist/mrag --version          # → 0.22.0
./dist/mrag --help

# onedir
./dist/mrag/mrag --version
```

For a stronger check, run init in a temp directory with no Python environment:

```bash
mkdir /tmp/kb-test && cd /tmp/kb-test
/path/to/dist/mrag init        # success if mrag.db and the project files are created
```


## Notes

- **No cross-compilation**: build per target OS / architecture.
- **The vaporetto tokenizer is not included in the binary**: the Japanese morphological tokenizer's shared library is an external plugin loaded at runtime from `~/.mrag/extensions`, independent of packaging. This is intentional — it stays swappable even after freezing. To use vaporetto, place the extension on the target machine as usual (`apsw` must also be present; the script includes it automatically when available).
- **External services still required**: mrag uses Ollama for embeddings and Qdrant for vector search. These are not embedded in the binary and are still needed at runtime (packaging freezes mrag itself, not its service dependencies).
- **Version sync**: keep `version` in `pyproject.toml` and `_FALLBACK_VERSION` in `mrag/__init__.py` in agreement. The binary's reported version is based on this value.
- **AGPL-3.0**: mrag is AGPL-3.0 due to PyMuPDF. Mind license compliance when redistributing the binary.


## Building on Windows

`build.sh` assumes a Unix shell. On Windows, run the equivalent from PowerShell (note the `--add-data` separator becomes `;`).

```powershell
$collect = @('fitz','pymupdf','qdrant_client','uvicorn','fastapi','pydantic','apsw') |
    ForEach-Object { '--collect-all', $_ }
pyinstaller --onefile --name mrag --clean --noconfirm `
  --copy-metadata mrag --collect-submodules mrag --collect-data mrag `
  @collect `
  --exclude-module torch --exclude-module sentence_transformers `
  packaging\mrag_entry.py
```

For a full build, drop the `--exclude-module` flags and add the reranker packages to `$collect`.


## Troubleshooting

- **`PackageNotFoundError: mrag` on startup** → `--copy-metadata mrag` is missing. The script adds it automatically.
- **`mrag init` cannot find schema.sql** → `--collect-data mrag` is missing. Same as above.
- **`ModuleNotFoundError` (fitz / uvicorn protocols / etc.)** → the package was not collected. Add it to `add_collect_if_present` in the script and rebuild.
- **Binary is huge / slow to start** → confirm the reranker is excluded (use LEAN), and choose `--onedir` for repeated use.
