# Recursive directory ingestion

Use `mrag add <DIRECTORY> --recursive` to register a directory tree as
independent mrag documents. Recursive add performs extraction and catalog
registration only; it never starts `mrag index` implicitly.

Supported source files are plain text (`.txt`), Markdown (`.md`) and
`.markdown`. Use include rules when a source tree also contains unrelated file
types.

mrag does not convert documents. Formats that need a conversion engine — `.pdf`,
`.html`, `.htm`, `.docx`, `.pptx`, `.xlsx` — are reported as `failed` items with
`requires external conversion to Markdown`. Convert them first (docling for PDF,
MarkItDown for Office formats) and add the resulting Markdown. A single
unconvertible file does not stop the run: the remaining sources are still
ingested and the command exits 3.

## Safe workflow

Preview the exact selection first. Globs must be quoted so the shell passes them
to mrag unchanged.

```bash
# Preview without changing mrag.db or data/documents/.
mrag add /path/to/documents --recursive --dry-run --json \
  --include '**/*.md' --include '**/*.markdown' --include '**/*.txt' \
  --exclude 'drafts/'

# Apply the reviewed selection.
mrag add /path/to/documents --recursive --json \
  --include '**/*.md' --include '**/*.markdown' --include '**/*.txt' \
  --exclude 'drafts/'

# Index is a separate, explicit stage.
mrag index
```

A directory argument without `--recursive` is rejected. Directory-only options
such as `--include`, `--exclude`, `--hidden`, `--follow-symlinks`, `--dry-run`,
and `--strict` are likewise rejected for a single-file source.

## Path selection

All matching uses normalized POSIX paths relative to the supplied source root.
Candidates and report items are sorted by that relative path, so the same tree
produces deterministic reporting even when extraction runs concurrently.

Selection is evaluated in this order:

1. When one or more `--include` rules are present, require a match against at
   least one of them.
2. Reject every `--exclude` match. This is a hard exclusion.
3. Apply rules from `.mragignore` at the source root in file order. A later
   `!pattern` can re-include a path ignored by an earlier `.mragignore` rule,
   but cannot override `--exclude`.

Both `--include` and `--exclude` are repeatable. `*` stays within one path
component, `**` crosses directories, `?` matches one character, and a trailing
`/` covers a directory subtree. A pattern without `/` matches that name at any
depth.

Example `.mragignore`:

```gitignore
# Ignore generated and draft trees.
generated/
drafts/

# Retain one reviewed draft.
!drafts/approved.md
```

`.mragignore` must be a regular, non-symlink UTF-8 file no larger than 1 MiB.
The ignore file itself is never ingested.

## Traversal safeguards

- Dot-prefixed path components are skipped by default. Pass `--hidden` to
  include them.
- Symbolic links are skipped by default. Pass `--follow-symlinks` only for a
  trusted tree. mrag detects directory cycles and ingests each canonical file
  target at most once.
- The project's `data/` subtree is always skipped, including symlink aliases.
  A source root inside that subtree is rejected. This prevents mrag from
  ingesting its own retained artifacts.
- Non-regular files are ignored.

## Duplicates and replacement

Content identity uses SHA-256. An already registered file is reported as
`skipped_duplicate` and is not an error. Pass `--force` only when duplicate
content must be extracted again; mrag preserves the existing document ID while
replacing its retained extraction record.

Prepared documents are persisted through a serialized write boundary, and the
final report remains in stable relative-path order.

## JSON report and exit codes

Use `--json` for automation. It emits one object with a summary and one item per
candidate or scan issue:

```json
{
  "schema_version": 1,
  "command": "add",
  "status": "partial",
  "summary": {"added": 4, "skipped": 2, "failed": 1},
  "items": [
    {"source": "manuals/a.md", "status": "added", "document_id": "...", "error": null},
    {"source": "manuals/b.md", "status": "skipped_duplicate", "document_id": "...", "error": null},
    {"source": "manuals/c.pdf", "status": "failed", "document_id": null,
     "error": {"code": "prepare_failed",
               "message": "Unsupported file type: .pdf (requires external conversion to Markdown)"}}
  ],
  "index_started": false,
  "recursive": true,
  "dry_run": false
}
```

| Exit code | Meaning |
|---:|---|
| `0` | No failed items. Added and duplicate-skipped items may both be present. |
| `3` | Partial success in the default best-effort mode. Successful documents remain registered. |
| `1` | Every item failed, or at least one item failed with `--strict`. |
| `2` | Invalid CLI usage. |

`--strict` changes the exit code; it does not make ingestion atomic and does not
roll back earlier successes. After correcting failed items, rerun the command:
previous successes safely become `skipped_duplicate` unless `--force` is used.

Run `mrag index` only after the ingestion report is acceptable. See the
[tutorial](./tutorial.md) for the full add → index → search lifecycle.
