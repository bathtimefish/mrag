English / [日本語](CHANGELOG-ja.md)

# Changelog

All notable changes to mrag are documented here. Entries before 0.24.0 were not
kept; the repository history is the record for those releases.

---

## 1.0.1 — 2026-09-03

### Fixed

- **Index time grew with the size of the index.** `_cleanup_document` cleared a
  document's FTS rows before re-indexing it, and ran for every document on every
  index run — including a first build, where there is nothing to clear. Every
  column `fts_chunks` can be filtered on is `UNINDEXED`, so that delete has no
  index to use and scans the whole table: measured against this schema, 0.65 ms
  per document at 2,000 rows against 4.61 ms at 20,000, so total cost grew
  roughly with the square of the corpus. The delete is now skipped when the
  document has no chunk rows, which is an indexed lookup and is exactly the case
  a first build is in. Re-indexing still clears the rows it replaces.
- **A knowledge base indexed before 0.24.0 could not be searched in `local`
  mode.** 0.24.0 renamed Qdrant collections and said only `qdrant.mode: server`
  was affected. It was not: the name is derived the same way in `local` mode, so
  every vector or hybrid search on a project indexed before 0.24.0 failed with
  `Collection mrag_…_<fingerprint> not found`, and a `mrag index` run after
  upgrading created the new collection for newly added documents only, leaving
  everything indexed earlier unreachable. The vectors are now migrated on first
  use: `mrag search`, `mrag eval`, `mrag serve`, `mrag mcp`, `mrag index` and
  `mrag reindex` move the points that `chunk_variants` still attributes to the
  old collection into the new one, repoint those rows, and drop the old
  collection once nothing references it. No re-embedding; one notice on stderr
  (`Migrated N vector point(s) from pre-0.24.0 Qdrant collection …`); about
  20 seconds per 30,000 points in local mode. Only points the database names
  are moved, so on a shared Qdrant server a collection that also holds another
  knowledge base's points keeps them and is left in place, and a legacy
  collection that `mrag reindex` already rebuilt under the new name is treated
  as an orphan — removed in local mode, left alone on a server — rather than
  merged back. The 0.24.0 entry below carries a correction.
- **`mrag eval` missed near-duplicate results.** Its duplicate check compared
  chunk text byte for byte after stripping outer whitespace, so the copies that
  actually flood results — the same paragraph re-published in every edition of
  an annual survey, differing only in chunk boundary, heading level or spacing —
  were never flagged. On one such corpus six of the eight results for a query
  were copies of one paragraph and none was marked. Results are now compared on
  normalized character shingles (Unicode NFKC, case, whitespace and punctuation
  removed) with an overlap threshold of 0.85, and the flag names the earlier
  result: `⚠ duplicate of [1]`, `⚠ near-duplicate (0.93) of [1]`, and
  `⚠ duplicated by [3], [4]` on the first occurrence. Texts shorter than 40
  normalized characters are still flagged only on an exact match.

### Changed

- **`mrag doctor` reports apsw on its own line.** A project whose
  `fts_tokenizer` is `vaporetto` fails at search time with `APSW is not
  installed` when the `vaporetto` extra is missing, but doctor only implied that
  through a failed library load — and skipped even that when it found more than
  one sqlite-vaporetto library. It now prints the apsw version, or `not
  installed` with the install command, and when the library is found but apsw
  is absent says so instead of "failed to load".

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

  **Correction:** the claim that `local` mode was unaffected was wrong. The
  rename applied to both modes, and local-mode projects indexed before 0.24.0
  failed with `Collection … not found` until the automatic migration described
  under 1.0.1 was added.
