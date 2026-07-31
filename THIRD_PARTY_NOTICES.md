# Third-Party Notices

mrag is distributed under the [MIT License](./LICENSE). It depends on the
third-party software listed below, each under its own license. This file is
informational; the authoritative terms are those shipped with each package.

The exact resolved dependency set, with versions, is recorded in `uv.lock`.

**No dependency of mrag is licensed under the GPL, LGPL or AGPL.** PyMuPDF
(AGPL-3.0 / Artifex commercial) was removed in 1.0.0; see
[CHANGELOG.md](./CHANGELOG.md). Releases up to and including 0.27.0 bundled it
and were distributed under AGPL-3.0.

---

## Direct dependencies

Installed by `pip install mrag` / `uv pip install -e .`:

| Package | License |
|---|---|
| [typer](https://github.com/fastapi/typer) | MIT |
| [rich](https://github.com/Textualize/rich) | MIT |
| [PyYAML](https://github.com/yaml/pyyaml) | MIT |
| [pydantic](https://github.com/pydantic/pydantic) | MIT |
| [qdrant-client](https://github.com/qdrant/qdrant-client) | Apache-2.0 |
| [FastAPI](https://github.com/fastapi/fastapi) | MIT |
| [uvicorn](https://github.com/encode/uvicorn) (with `standard` extras) | BSD-3-Clause |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause |

## Optional dependencies

Installed only when the corresponding extra is requested:

| Extra | Package | License |
|---|---|---|
| `vaporetto` | [apsw](https://github.com/rogerbinns/apsw) | zlib-style; may alternatively be used under any OSI-approved license, at the recipient's option. Copyright (c) 2004-2026 Roger Binns |
| `reranker` | [sentence-transformers](https://github.com/UKPLab/sentence-transformers) | Apache-2.0 |
| `mcp` | [mcp](https://github.com/modelcontextprotocol/python-sdk) | MIT |
| `dev` | [pytest](https://github.com/pytest-dev/pytest) | MIT |
| `dev` | [pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio) | Apache-2.0 |

---

## Runtime plugin — sqlite-vaporetto

mrag can use [sqlite-vaporetto](https://github.com/hotchpotch/sqlite-vaporetto)
by [@hotchpotch](https://github.com/hotchpotch) for Japanese morphological
tokenization via SQLite FTS5. It is a native shared library that the user
installs separately into `~/.mrag/extensions/`; **mrag does not bundle or
redistribute it.**

- **sqlite-vaporetto** — `MIT OR Apache-2.0`
- **bundled model** (`bccwj-suw+unidic_pos+kana.model.zst`, included in
  `-with-model` releases) — [BSD-3-Clause](https://opensource.org/license/BSD-3-Clause),
  sourced from [daac-tools/vaporetto-models](https://github.com/daac-tools/vaporetto-models/releases)

> If you redistribute mrag **together with** the sqlite-vaporetto library or its
> bundled model, you must include the BSD-3-Clause copyright notice for the
> model in your distribution.

---

## External services

mrag communicates with these over the network at runtime. They are separate
programs, neither bundled nor redistributed with mrag, and their licenses do not
extend to it:

- [Ollama](https://ollama.com) — embeddings and contextual augmentation
- [Qdrant](https://qdrant.tech) — vector storage (Apache-2.0). The default
  `local` mode runs through the Apache-2.0 `qdrant-client` library rather than a
  separate server process.

---

## Single-binary distributions

`packaging/build.sh` freezes mrag and its installed dependencies into one
executable with PyInstaller. Such a binary **embeds the transitive dependency
tree**, so its distribution carries those licenses. All of them are permissive,
with two weak-copyleft components worth noting:

- **certifi** — MPL-2.0
- **tqdm** — MPL-2.0 AND MIT (pulled in only by the `reranker` extra)

MPL-2.0 is file-level copyleft: it does not affect mrag's own MIT licensing, but
it requires that the source of those specific files remain available, and that
their notices be preserved. Unmodified upstream packages satisfy this by
pointing to their published sources.

Other licenses present in a full `[vaporetto,reranker]` build include Apache-2.0
(qdrant-client, grpcio, sentence-transformers, transformers, tokenizers,
safetensors, huggingface-hub, regex), BSD-2/3-Clause (uvicorn, starlette, httpx,
click, idna, numpy, scikit-learn, scipy, torch, protobuf, websockets,
python-dotenv, pygments), MIT (typer, rich, PyYAML, pydantic, FastAPI, h11,
anyio, urllib3, httptools, uvloop, watchfiles, setuptools), ISC (shellingham),
PSF-2.0 (typing-extensions) and Apache-2.0 OR BSD-2-Clause (packaging).

To reproduce the exact inventory for a given build:

```bash
python -c "
from importlib.metadata import distributions
rows = []
for d in distributions():
    m = d.metadata
    lic = (m.get('License-Expression') or m.get('License') or
           next((c.split('::')[-1].strip()
                 for c in m.get_all('Classifier', []) or []
                 if c.startswith('License')), '?'))
    rows.append((m['Name'], d.version, lic.splitlines()[0]))
for r in sorted(rows):
    print('%-30s %-12s %s' % r)
"
```

Run it with the interpreter of the environment you are packaging.
