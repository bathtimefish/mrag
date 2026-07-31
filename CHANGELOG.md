English / [日本語](CHANGELOG-ja.md)

# Changelog

All notable changes to mrag are documented here. Entries before 0.24.0 were not
kept; the repository history is the record for those releases.

---

## 1.0.0 — 2026-07-31

First major release. **Breaking:** PDF ingestion removed, project relicensed to MIT.

### Removed

- **In-process PDF extraction.** `mrag add` accepts `.md`, `.markdown` and `.txt`
  only. `.pdf`, `.html`, `.htm`, `.docx`, `.pptx` and `.xlsx` are rejected with
  `Unsupported file type: <ext> (requires external conversion to Markdown)`.
  Document conversion is now the job of a dedicated engine — use
  [docling](https://github.com/docling-project/docling) for PDF and
  [MarkItDown](https://github.com/microsoft/markitdown) for Office formats, then
  add the Markdown they produce.
- `--extractor` / `-e` on `mrag add` and `mrag extract`.
- `--converter-jobs` and the bounded converter pool behind it, which existed
  only to parallelise PDF extraction.
- `default_extraction` in `mrag.yaml` and `extraction` in profiles. Leaving
  these keys in an existing config is harmless — they are ignored, not rejected.
- The PyMuPDF dependency, and its collection in the PyInstaller build scripts.

### Changed

- **License: AGPL-3.0 → MIT.** PyMuPDF (AGPL-3.0 / Artifex commercial dual
  license) was the project's only copyleft dependency, and the PyInstaller build
  bundled it into the distributed binary, which placed the whole distribution
  under AGPL. With it gone, every remaining dependency is MIT, BSD, Apache-2.0,
  ISC or MPL-2.0. Releases up to and including 0.27.0 remain AGPL-3.0.
- Single-file binaries built from this release are distributable under MIT.
- `extraction_provider` is recorded as `plain` for newly added documents.

### Compatibility

**Existing knowledge bases are unaffected.** `mrag index`, `mrag reindex` and
every search path read the extracted text already stored under
`data/documents/`, never the original file, so documents ingested from PDF by an
earlier version stay searchable and re-indexable. Catalog rows keep their
`source_type='pdf'` and `extraction_provider='pymupdf'` values. This change does
not alter the profile hash, so it forces no reindex of its own — though
upgrading across 0.24.0–0.27.0 may still require one for the reasons below.

Only ingesting a *new* PDF is affected.

---

## 0.27.0 — 2026-07-30

### Fixed

- **`mrag reindex` no longer empties the profile before rebuilding it.** Two
  defects are fixed. **(1)** It deleted a profile's chunk rows without ever
  deleting the matching Qdrant points, so every reindex left a full generation
  of vectors behind. Because vector search silently discards hits whose chunk
  row is gone, those orphans consumed `top_k` slots and returned fewer results
  than requested — worsening once per reindex, with no warning. **(2)** The
  deletion ran to completion *before* the rebuild, so any failure (an
  unreachable Ollama, for example) left the profile with no index at all.

  Reindex now forces a per-document rebuild: each document's old chunks, FTS
  rows and vector points are replaced only after its new ones have been embedded
  successfully, so a failed run leaves the previous index searchable and exits 1.
  It also reconciles the collection against the database afterwards and reports
  `Reclaimed N orphaned vector point(s)` — **one `mrag reindex` per profile
  reclaims the orphans an earlier version accumulated**, with no manual Qdrant
  cleanup needed.

  Points left in a collection built under a *different embedding model* cannot
  be attributed to a profile and are not touched; remove those collections
  manually if you have switched models.

### Changed

- `cleanup_profile_index()` remains available for callers that genuinely want to
  empty a profile, and now builds its own Qdrant client rather than skipping the
  vector deletion when none is passed.

---

## 0.26.0 — 2026-07-26

### Added

- `augmentation.think`, defaulting to `false`.

### Changed

- **Contextual augmentation no longer sends thinking tokens by default.**
  Reasoning models such as the default `gemma4:e2b` were spending most of their
  generation budget on tokens Ollama strips from the response — one measured
  call produced 390 tokens to return a 54-character note. Disabling thinking cut
  that call from 6.5 s to 3.4 s and returned a *longer*, more specific note.
  mrag probes `/api/show` once per model and only sends the parameter to models
  that report the `thinking` capability, so models without it are unaffected.

  **`think` participates in the profile hash**, so a profile using
  `augmentation.strategy: contextual` fully rebuilds its index on the next
  `mrag index` — the generated context genuinely changes, and leaving the old
  index in place would leave it inconsistent with the configuration. Set
  `augmentation.think: true` to keep the previous behaviour.

---

## 0.25.0 — 2026-07-26

### Fixed

- **`mrag search` and `POST /api/v1/retrieve` now honour the profile's
  `retrieval.top_k`.** Previously both substituted a fixed default of 5 whenever
  the caller did not pass `--top-k` / `top_k`, so a profile setting such as
  `top_k: 20` was silently ignored.

  After upgrading, searches that do not specify a count return the profile's
  value instead of 5 — that is 8 results for a profile generated by `mrag init`.
  Pass `--top-k 5` (or `"top_k": 5` in the request body) to keep the old count,
  or set `retrieval.top_k` in the profile.

  The MCP server already resolved the profile value and is unchanged, as is the
  Dify endpoint, whose protocol always supplies `top_k` explicitly.

---

## 0.24.0 — 2026-07-24

### Changed

- **Breaking (Qdrant Server backend only): the Qdrant collection naming scheme
  changed** to prevent different knowledge bases/profiles from silently
  colliding into the same collection. The old scheme could normalize distinct
  IDs — e.g. `"kb-1"` and `"kb 1"` — to an identical collection name.

  This only affects `qdrant.mode: server`; the default `local` mode is
  unaffected. If you use `qdrant.mode: server`, previously created collections
  are no longer found after upgrading — run `mrag reindex` (or `mrag index`) for
  each profile once to rebuild them under the new name. Until you do, searches
  on that profile return empty results rather than mixed or wrong ones. No data
  is deleted; the old collection is simply orphaned in Qdrant and can be removed
  manually once you have confirmed the new one is populated.
