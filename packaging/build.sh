#!/usr/bin/env bash
#
# Build a single-file `mrag` executable with PyInstaller.
#
# This is an OPTIONAL distribution method. The standard install path
# (`uv pip install -e .` / `pip install mrag`) is unaffected by this script and
# remains the recommended way to run mrag. Nothing here modifies mrag's runtime
# source — it only freezes the already-installed package into one binary.
#
# Usage:
#   packaging/build.sh                 # lean onefile build (NO reranker / torch)  ← default
#   packaging/build.sh --with-reranker # full build (bundles sentence-transformers + torch)
#   packaging/build.sh --onedir        # folder build — near-instant startup (see note)
#   packaging/build.sh --help
#
# Output:
#   onefile (default): dist/mrag        — one self-contained executable
#   onedir  (--onedir): dist/mrag/mrag  — a folder; launch the inner `mrag`
#
# onefile vs onedir: onefile is a single file but RE-EXTRACTS itself to a temp
# dir on every launch (~7 s cold start on macOS for this build). onedir starts
# almost instantly (~0.5 s, same as a venv install) because nothing is extracted
# — at the cost of distributing a folder instead of one file. For a CLI invoked
# repeatedly, prefer --onedir.
#
# Notes:
#   * PyInstaller cannot cross-compile. Run this on each target OS/arch
#     (macOS arm64, macOS x86_64, Linux, ...). Windows users: see the
#     PowerShell notes at the bottom of this file.
#   * The lean build is ~60-200 MB. --with-reranker bundles PyTorch: ~230 MB on
#     macOS arm64 (CPU/MPS), but can exceed 1 GB with CUDA-enabled torch on
#     Linux — with a noticeably slower onefile startup either way.
#   * The vaporetto FTS tokenizer is loaded at runtime from
#     ~/.mrag/extensions and is intentionally NOT bundled — it stays an
#     external, swappable plugin regardless of how mrag is packaged.
set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
WITH_RERANKER=0
ONEFILE_FLAG="--onefile"
PACKAGE_MODE="onefile"
for arg in "$@"; do
  case "$arg" in
    --with-reranker) WITH_RERANKER=1 ;;
    --onedir) ONEFILE_FLAG="--onedir"; PACKAGE_MODE="onedir" ;;
    --onefile) ONEFILE_FLAG="--onefile"; PACKAGE_MODE="onefile" ;;
    --help|-h)
      sed -n '2,34p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $arg (use --with-reranker, --onedir, or --help)" >&2
      exit 2
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Locate project root (this script lives in packaging/)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# Prefer the project venv's tools if present.
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; else PY="python3"; fi
fi

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
if ! "$PY" -c "import PyInstaller" 2>/dev/null; then
  echo "PyInstaller is not installed in the target environment." >&2
  echo "Install it with:  $PY -m pip install pyinstaller   (or: uv pip install pyinstaller)" >&2
  exit 1
fi

if ! "$PY" -c "import mrag" 2>/dev/null; then
  echo "mrag is not importable by $PY. Install it first:  uv pip install -e ." >&2
  exit 1
fi

# Add `--collect-all <pkg>` only when the package is actually importable, so an
# absent optional dependency never breaks the build.
COLLECT_ARGS=()
add_collect_if_present() {
  if "$PY" -c "import $1" 2>/dev/null; then
    COLLECT_ARGS+=("--collect-all" "$1")
  else
    echo "  (skip) optional package not present: $1"
  fi
}

echo "==> Resolving bundled packages"
# Core data/binary-bearing deps that PyInstaller's static analysis can miss.
add_collect_if_present fitz          # PyMuPDF (PDF extraction; ships MuPDF binary)
add_collect_if_present pymupdf       # modern import alias of fitz
add_collect_if_present qdrant_client # vector store client (grpc/httpx)
add_collect_if_present uvicorn       # uvicorn[standard]: protocols, ws, httptools
add_collect_if_present fastapi
add_collect_if_present pydantic      # pydantic v2 (compiled pydantic_core)
add_collect_if_present apsw          # optional: needed only for vaporetto tokenizer

# ---------------------------------------------------------------------------
# Reranker mode toggle
# ---------------------------------------------------------------------------
RERANKER_ARGS=()
if [ "$WITH_RERANKER" -eq 1 ]; then
  echo "==> Mode: FULL (bundling reranker — sentence-transformers + torch)"
  if ! "$PY" -c "import sentence_transformers" 2>/dev/null; then
    echo "--with-reranker requested but sentence-transformers is not installed." >&2
    echo "Install it first:  uv pip install -e \".[reranker]\"" >&2
    exit 1
  fi
  for pkg in sentence_transformers transformers torch tokenizers safetensors; do
    add_collect_if_present "$pkg"
  done
  echo "    NOTE: large binary and slower startup. Size depends on the torch build:"
  echo "          ~230 MB on macOS arm64 (CPU/MPS); >1 GB with CUDA-enabled torch on Linux."
else
  echo "==> Mode: LEAN (reranker excluded)"
  RERANKER_ARGS+=(
    --exclude-module torch
    --exclude-module sentence_transformers
    --exclude-module transformers
  )
fi

# ---------------------------------------------------------------------------
# Clean previous artifacts
# ---------------------------------------------------------------------------
echo "==> Cleaning build/ dist/ *.spec(generated)"
rm -rf build dist
rm -f mrag.spec mrag_entry.spec

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
echo "==> Running PyInstaller ($PACKAGE_MODE)"
"$PY" -m PyInstaller \
  "$ONEFILE_FLAG" \
  --name mrag \
  --clean \
  --noconfirm \
  --copy-metadata mrag \
  --collect-submodules mrag \
  --collect-data mrag \
  ${COLLECT_ARGS[@]+"${COLLECT_ARGS[@]}"} \
  ${RERANKER_ARGS[@]+"${RERANKER_ARGS[@]}"} \
  packaging/mrag_entry.py

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
if [ "$PACKAGE_MODE" = "onedir" ]; then
  BIN="dist/mrag/mrag"
else
  BIN="dist/mrag"
fi
if [ -f "$BIN" ]; then
  if [ "$PACKAGE_MODE" = "onedir" ]; then
    SIZE="$(du -sh dist/mrag | cut -f1)"
    echo ""
    echo "==> Build complete (onedir): dist/mrag/  ($SIZE total)"
    echo "    Launch the inner executable:  $BIN --version"
  else
    SIZE="$(du -h "$BIN" | cut -f1)"
    echo ""
    echo "==> Build complete (onefile): $BIN  ($SIZE)"
    echo "    Smoke test:  $BIN --version"
  fi
else
  echo "Build finished but $BIN was not found — check PyInstaller output above." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Windows (PowerShell) equivalent — run inside an activated venv:
#
#   $collect = @('fitz','pymupdf','qdrant_client','uvicorn','fastapi','pydantic','apsw') |
#       ForEach-Object { '--collect-all', $_ }
#   pyinstaller --onefile --name mrag --clean --noconfirm `
#     --copy-metadata mrag --collect-submodules mrag --collect-data mrag `
#     @collect `
#     --exclude-module torch --exclude-module sentence_transformers `
#     packaging\mrag_entry.py
#
# (Drop the --exclude-module flags and add the reranker packages to $collect
#  for a full build. On Windows the --add-data separator is ';' not ':'.)
# ---------------------------------------------------------------------------
