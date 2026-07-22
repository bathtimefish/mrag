# Document retrieval exclusions

Use `mrag exclusions` when a document must stop contributing knowledge to
retrieval while its original and extracted artifacts remain available for audit
or later restoration. The target is a stable document ID, never a generated
chunk ID or filename.

## Choose exclusion or removal

| Requirement | Operation | Original/extracted artifacts | Search state |
|---|---|---|---|
| Retain the document but stop using it as knowledge | `exclusions add` | Retained | Blocked immediately |
| Make an excluded document eligible again | `exclusions restore`, then `index` | Retained | Searchable only after explicit indexing |
| Delete the mrag document itself | `remove --force` | Deleted | Document no longer exists |

An exclusion combines a persistent logical policy with physical cleanup of
derived retrieval data. It is the reversible operation. `remove --force` is the
application-level destructive operation for the document catalog and retained
source.

## Find the document ID

```bash
# From search results
mrag search "<unique query>" --json \
  | jq '.results[] | {document_id, filename}'

# From SQLite
sqlite3 mrag.db "SELECT id, filename, status FROM documents ORDER BY filename;"

# From the Native API while mrag serve is running
curl -s http://127.0.0.1:8000/api/v1/documents
```

Use the document ID shown by these commands. A chunk ID identifies only one
generated retrieval unit and is not accepted as an exclusion target.

## Preview and apply an exclusion

`add` is a dry-run unless `--force` is supplied.

```bash
# Preview affected chunks, variants, FTS rows, and Qdrant points.
mrag exclusions add --document-id <DOCUMENT_ID>

# Machine-readable preview.
mrag exclusions add --document-id <DOCUMENT_ID> --json

# Apply to every current and future profile (recommended default).
mrag exclusions add --document-id <DOCUMENT_ID> \
  --reason "obsolete knowledge" --force

# Limit the policy to one profile only.
mrag exclusions add --document-id <DOCUMENT_ID> \
  --profile contextual --reason "not valid for this profile" --force
```

The optional audit reason is limited to 1000 characters. Prefer all-profile
scope unless different profiles intentionally expose different knowledge. Do
not stack overlapping scopes: list and restore existing profile-scoped policies
before replacing them with an all-profile policy.

### What `--force` does

The command deliberately orders policy and cleanup as follows:

1. Commit the exclusion policy to SQLite. It becomes the authoritative search
   barrier immediately.
2. Delete the document's FTS rows.
3. Delete its Qdrant points.
4. After vector cleanup succeeds, delete its chunks, variants, and per-profile
   document-index state from SQLite.

The document row, original file, extracted text/Markdown, and exclusion audit
record are retained. CLI, HTTP API, and MCP retrieval enforce the same policy.
Keyword retrieval applies a SQLite anti-filter; vector retrieval applies both a
Qdrant document filter and a final SQLite hydration filter.

`mrag index` and `mrag reindex` resolve exclusions before constructing
providers, chunking, augmentation, or embedding. Reindexing therefore cannot
accidentally restore an excluded document.

### Recover from degraded cleanup

If Qdrant cleanup cannot complete, the command exits `3` and JSON output reports
`status: "degraded"`. The policy remains active and every retrieval path stays
fail-closed. FTS rows have already been removed; chunk/variant metadata is
retained so the Qdrant point deletion can be retried safely.

Restore Qdrant availability and repeat the same forced add command:

```bash
mrag exclusions add --document-id <DOCUMENT_ID> \
  --reason "obsolete knowledge" --force
```

Reapplying an active exclusion with `--force` reconciles pending cleanup. Do not
restore the exclusion merely because cleanup returned exit `3`.

## Audit exclusions

```bash
# Active policies only
mrag exclusions list

# Include restored/revoked audit records
mrag exclusions list --all
mrag exclusions list --all --json
```

Keep the exclusion ID returned by `add` or `list`. It identifies the policy and
is distinct from the document ID.

## Restore a document

`restore` is also dry-run-first. It purges any residual derived artifacts before
revoking the policy.

```bash
# Preview
mrag exclusions restore <EXCLUSION_ID>

# Revoke the policy after residual cleanup succeeds
mrag exclusions restore <EXCLUSION_ID> --force

# Explicitly rebuild the retained document
mrag index --document-id <DOCUMENT_ID>

# Rebuild the matching profile after a profile-scoped restoration
mrag index --document-id <DOCUMENT_ID> --profile contextual
```

Restoration never calls an embedding provider implicitly. Until the explicit
index succeeds, the retained document is eligible but not searchable. If
residual Qdrant cleanup fails, restoration keeps the policy active; repair
Qdrant and retry `restore --force`.

## Physical deletion and secure erasure

Use `mrag remove <DOCUMENT_ID>` to preview deletion and `mrag remove
--force <DOCUMENT_ID>` to delete the document record, its retained artifacts,
derived index records, and exclusion history.

Neither operation guarantees storage-media secure erasure. If copied logs,
operator-managed caches, filesystem snapshots, or external backups must also be
destroyed, handle those stores separately according to the deployment's data
retention policy.
