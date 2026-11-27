English / [日本語](document-sync-ja.md)

# Keeping documents in step with their files

`mrag add` records where a document came from. Files then move, change and
disappear, and the catalog does not notice: a renamed file is added again as a
second document, a deleted file keeps answering searches, an edited file keeps
its old text. `mrag documents sync` compares what the catalog says lives under a
directory with what the directory holds now, shows the difference as a plan,
and with `--apply` carries it out. `mrag documents rebind` binds one document to
one file by hand when the sync cannot decide on its own.

Both commands decide first and write second. The plan is the same whether or
not `--apply` is given; `--apply` only executes it, one item per transaction.

## Safe workflow

```bash
# Show what a sync would do. Nothing is written.
mrag documents sync ./corpus

# Apply it.
mrag documents sync ./corpus --apply

# Apply and then rebuild every profile the project has indexed before.
mrag documents sync ./corpus --apply --index

# Rebuild only the named profiles.
mrag documents sync ./corpus --apply --index --profile default --profile ja

# One machine-readable object, for automation.
mrag documents sync ./corpus --apply --json
```

The directory may be inside the project (named by its project-relative path)
or anywhere else. The project's own `data/` and the reserved `identities/`
directory are refused, as are `profiles/`, `qdrant/`, `logs/` and `cache/`; a
sync of the project root passes all of them by, together with `mrag.yaml`,
`kb_information.yaml` and `mrag.db`. `--include`, `--exclude`, `--hidden`, `--follow-symlinks`
and `.mragignore` select files exactly as they do for
[`mrag add --recursive`](recursive-add.md); a sync and a recursive add over
the same selection see the same files.

## What the plan says

Every document the catalog places under the directory, and every file the scan
finds there, becomes one item with an action:

| Action | When | What `--apply` does |
|---|---|---|
| `add` | a file with no document and content no document holds | registers it as a new document |
| `update` | a document's file holds different content | stores the new content as the document's current version; the ID stays |
| `move` | a document's file is gone and exactly one new file holds its content | points the document at the new path; the ID stays |
| `exclude` | a document's file is gone and it was not paired with a new file as a move | excludes the document from every profile (`reason: file_missing`, or `ambiguous_move` when new files hold its content but could not be paired) |
| `restore` | a file the sync had excluded is back with its content | lifts that exclusion |
| `noop` | the document's file is where the catalog says, with the same content | nothing |
| `duplicate` | a new file holds content another document already has, or several files could be the same move | nothing; the report names the document that holds the content |
| `adopt_candidate` | a new file holds the content of a document migrated from an older catalog that never recorded a path | nothing; `documents rebind` adopts it when you say so |

Moves are decided one to one. Two missing documents with the same content, or
two new files with the same content, are never paired by guessing. Each new
file is reported as `duplicate` with `reason: ambiguous_move` and is left
alone. Each missing document becomes an `exclude` with
`reason: ambiguous_move`, and `--apply` excludes it like any other `exclude`.
A rename that changes only letter case is a move, even on a case-insensitive
file system.

Files in formats mrag does not ingest — PDF, Office, HTML — are counted as
`unsupported` and leave nothing behind. A corpus directory holds files that are
not documents; the sync does not treat them as failures. (A recursive add
reports the same files as `failed`; the sync has nothing to fail because it was
not asked to add them.)

A document whose file exists but was left out by the selection — a filter, a
hidden directory, `.mragignore` — is `out_of_scope`: present, just not looked
at, so it is never excluded by a sync that did not see it.

### Exclusions the sync writes, and exclusions you write

An `exclude` item writes the same kind of document exclusion rule that
`mrag exclusions add` writes, across every profile, marked `origin: sync` with
the reason `source file missing at documents sync`. It writes the rule only: it
does not run the physical index cleanup that `mrag exclusions add` runs
(removing the document's FTS rows, chunks and Qdrant points). The rule keeps
the document out of search. The sync lifts only its own exclusions,
and only when the file comes back or the document is moved or rebound. An
exclusion a person wrote (`origin: user`) is never lifted by a sync, and a
document a person excluded from every profile is a `noop` with
`reason: excluded_by_user` when its file disappears — you already said what you
wanted. Moving such a document carries the exclusion with it. An exclusion a
person wrote for a single profile does not have this effect: when the file
disappears, the document becomes an `exclude` like any other. A document the
sync has already excluded, whose file is still gone, is a `noop` with
`reason: already_excluded_by_sync`.

`mrag exclusions list --json` shows the origin of each rule.

### Directories outside the project

A file outside the project is identified under the directory it was added
from, its *root*: `identities/external/<root key>/<path under the root>`. The
root key is derived from the directory's resolved path, so the same directory
always yields the same identities, and a sync of that directory places its
documents directly.

A sync of a *parent* of a registered root also places them, because every root
records the keys of its ancestor directories when it is registered. A sync of
some other directory cannot tell whether the root lives under it; such roots
are left alone and counted in `summary.unresolvable_roots`. Roots registered by
an older mrag before ancestors were recorded are placed as soon as a sync finds
them under the directory it walks, and their ancestors are recorded then.

## JSON report and exit codes

```json
{
  "schema_version": 1,
  "command": "documents sync",
  "status": "success",
  "applied": true,
  "root": "corpus",
  "directory_binding": "project_relative",
  "summary": {"add": 1, "update": 1, "move": 1, "exclude": 0, "restore": 0, "noop": 4,
              "duplicate": 0, "adopt_candidate": 0, "blocked": 0, "out_of_scope": 0,
              "unsupported": 2, "failed": 0, "unresolvable_roots": 0},
  "items": [
    {"action": "move", "status": "applied", "document_id": "...",
     "source_identity": "corpus/archive/old-notes.txt", "previous_identity": "corpus/notes.txt",
     "content_hash": "...", "reason": null}
  ],
  "scan_issues": [],
  "audit_log": "logs/2026-10-02T09-15-42.113204Z-documents-sync.json",
  "index": {"status": "indexed", "reason": null,
            "profiles": [{"profile": "default", "status": "indexed", "error": null}],
            "next_action": null}
}
```

Item `status` in a plan is `planned` for an item `--apply` would carry out and
`reported` for one it would not (`noop`, `duplicate`, `adopt_candidate`). After
`--apply` it is `applied`, `failed` or `cancelled`; the items with nothing to
carry out are still `reported`. A failed item carries an `error` object with one
of three codes: `ingestion_source_changed`, with `reason: changed_during_sync`
(a file's bytes changed between the plan and the write, and the next run picks
them up); `documents_sync_duplicate_appeared` (another document gained the
content between the plan and the write); or `documents_sync_item_failed` (the
file could not be read or stored). `scan_issues` lists the paths the scan could
not read, each with `path`, `code` and `message`: the directory scan's own codes
(such as `directory_walk_failed` or `directory_symlink_cycle`),
`source_identity_failed` or `native_source_read_failed`. Each scan issue counts
in `summary.failed`, in a plan as well. The
`directory_binding` is `project_relative` or `external_root`. `blocked` is
always 0: mrag ingests only Markdown and text, so nothing waits on a conversion.

The `index` object appears with `--index`. In a plan it says `planned` with the
profiles it would rebuild, or `skipped` with `reason` `no_changes` or
`no_indexed_profile`; after `--apply` it is `indexed`, `failed` (with the
`mrag index --profile` command to run under `next_action`), or `skipped` with
`reason` `no_changes`, `no_indexed_profile` (no profile has been indexed yet;
name one with `--profile`) or `cancelled`. Each rebuild writes its own log under
`logs/`. A failed rebuild makes the report `partial`.

An applied run writes the report to `logs/<timestamp>-documents-sync.json`. If
the log cannot be written the changes stand and the report says `degraded`.

| Exit code | Meaning |
|---:|---|
| `0` | The plan was shown, or applied, with no failed item and no scan issue. |
| `3` | At least one item failed, a scan issue was reported (a plan without `--apply` exits 3 for this too), a rebuild failed, or the audit log could not be written. The other items stand. |
| `2` | Invalid usage: the directory does not exist or is a file, one of the project's own directories (`data/`, `identities/`, `profiles/`, `qdrant/`, `logs/`, `cache/`), `--profile` without `--index`, an unknown profile, a scan that could not start (`sync_scan_failed`: an invalid `--include` or `--exclude` glob, an unusable `.mragignore`, or a symlinked directory without `--follow-symlinks`), or a catalog whose identities need `mrag catalog migrate-identities` first. |
| `1` | Not inside an initialised project. |
| `130` | Interrupted; see below. |

## Stopping a run

An applied sync stops at the next item boundary when it receives `SIGINT`
(Ctrl-C) or `SIGTERM` — what `timeout`, cron, systemd and service wrappers
send. The items already applied are kept, the rest are reported as `cancelled`,
the report says `status: "cancelled"`, the audit log is written and the exit
code is 130. Running the command again finishes the work. A second signal ends
the process at once, with no report.

`mrag add --recursive` follows the same contract between files, reporting the
files it did not reach as `cancelled`.

## Binding one document by hand

```bash
# Show what binding would do.
mrag documents rebind <DOCUMENT_ID> ./notes/renamed.md

# Do it.
mrag documents rebind <DOCUMENT_ID> ./notes/renamed.md --apply
```

Rebind is for the cases the sync leaves alone: a file that moved *and* changed,
an `adopt_candidate`, an `ambiguous_move` you can resolve yourself. It points
the document at the file you name and keeps the ID. A file holding different
content becomes the document's new content — no `--force`, since you named both
ends. The sync's own exclusions on the document are lifted; a person's stay.

It refuses, with exit 1, when no document has that ID (`document_not_found`),
or when another document already lives at that path (`rebind_target_taken`) or
already holds that content (`rebind_content_held_by_other_document`); for these
two the report names the document (`held_by`). The report's `action` is `rebind`
when the document's path changes, `update` when its own file holds different
content, `restore` when its own file holds the same content and only the sync's
exclusions are lifted, and `noop` when its own file holds the same content and
there is no sync exclusion to lift. Usage errors —
a missing file, a directory, a format mrag does not ingest, a file under `data/`
or `identities/` — exit 2.

An applied rebind writes `logs/<timestamp>-documents-rebind.json`. If the path
was updated but the new content could not be read afterwards, the document is
left at its new path with its previous content and the command exits 3 with
`status: "partial"`; running it again finishes the job. A run that changed
nothing — an update whose new content could not be read, with no path to move
and no exclusion to lift — exits 1 with `status: "failed"` and
`applied: false`, and writes no log. The report's `new_revision` is whether the
content was taken, not whether the plan meant to take it.

Neither command rebuilds the index on its own; `--index` on the sync, or
`mrag index`, does. A moved or rebound document counts as changed: the document
list (`GET /api/v1/documents` and the MCP `list_documents` tool) reports it
`stale` until then, and the next index rebuilds it under its new
name.
