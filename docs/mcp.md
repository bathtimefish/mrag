# MCP server — `mrag mcp`

`mrag mcp` exposes a single mrag project as a read-only Model Context Protocol server.

`list_documents` returns exactly what Native API `GET /api/v1/documents`
returns — the same envelope (`total`, `returned`, `page`), rows, statuses and
ordering — and takes the same arguments: `profile`, `all`, `status` (a list of
aggregate statuses), `limit` (1–500, default 100) and `offset`. Without `all`
only extracted documents are listed. Legacy rows show `legacy_unbound` rather
than guessing their original path. See the [Native API](./native-api.md#list)
for every field.

Use it when an MCP-capable client should call mrag as tools/resources instead of shelling out to `mrag search --json`.

## Install

The MCP server uses the official Python MCP SDK, provided as an optional dependency:

```bash
uv pip install -e ".[mcp]"
```

Combine it with other extras as needed:

```bash
uv pip install -e ".[vaporetto,reranker,mcp]"
```

## Quick start

Run from inside an indexed mrag project:

```bash
mrag mcp
```

With no options, mrag starts a `stdio` MCP server using:

- `project_dir: .`
- `profile: <mrag.yaml default_profile>`
- `retrieval.strategy: <profile retrieval.strategy>`
- `retrieval.top_k_default: <profile retrieval.top_k>`

`stdio` writes MCP JSON-RPC messages to stdout. Logs and warnings go to stderr.

## Config file

For daemon or container use, put options in YAML:

```yaml
version: 1

project_dir: /data/kb
profile: default
transport: streamable-http

retrieval:
  strategy: hybrid
  top_k_default: 5
  top_k_max: 50
  no_rerank: true

http:
  host: 127.0.0.1
  port: 8001
  path: /mcp
  allowed_origins: []

auth:
  bearer_token_env: MRAG_MCP_API_KEY
  bearer_token_file_env: MRAG_MCP_API_KEY_FILE
```

Start with:

```bash
mrag mcp --config ./mrag-mcp.yaml
```

Relative `project_dir` values in a config file are resolved from the directory containing that config file.

Three more sections are optional; `mrag mcp init-config` prints them with their defaults:

| Key | Default | Meaning |
|---|---|---|
| `features.tools` | `true` | Expose the tools |
| `features.resources` | `true` | Expose the resources |
| `features.management_tools` | `false` | Must stay `false`; `true` is rejected as a config error |
| `limits.content_max_chars` | `20000` | Chunk content in tool results, and the YAML, extracted-text and text-original resources, are cut to this many characters; `0` turns the cut off. A cut tool result says `truncated: true` with `content_chars` and `returned_chars` |
| `limits.artifact_max_bytes` | `1048576` | Largest stored original `.../original` serves |
| `limits.request_timeout_seconds` | `60` | Accepted and validated (1 or more); the server does not apply it |
| `logging.level` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`, in upper case |

## Environment overrides

Environment variables override YAML values:

```bash
MRAG_MCP_CONFIG=/etc/mrag/mcp.yaml \
MRAG_PROJECT_DIR=/data/kb \
MRAG_MCP_TRANSPORT=streamable-http \
MRAG_MCP_PORT=8001 \
MRAG_MCP_API_KEY=secret \
mrag mcp
```

Common variables:

| Variable | Meaning |
|---|---|
| `MRAG_MCP_CONFIG` | Config file path |
| `MRAG_PROJECT_DIR` | Project directory |
| `MRAG_MCP_TRANSPORT` | `stdio` or `streamable-http` |
| `MRAG_MCP_PROFILE` | Default profile |
| `MRAG_MCP_STRATEGY` | Default retrieval strategy |
| `MRAG_MCP_TOP_K_DEFAULT` | Default result count |
| `MRAG_MCP_TOP_K_MAX` | Maximum accepted `top_k` |
| `MRAG_MCP_NO_RERANK` | Disable reranking |
| `MRAG_MCP_HOST` / `MRAG_MCP_PORT` / `MRAG_MCP_PATH` | HTTP bind settings |
| `MRAG_MCP_ALLOWED_ORIGINS` | Comma-separated list for `http.allowed_origins` |
| `MRAG_MCP_LOG_LEVEL` | `logging.level` |
| `MRAG_MCP_API_KEY` | HTTP bearer token |
| `MRAG_MCP_API_KEY_FILE` | File containing the HTTP bearer token |

The token file is read before `MRAG_MCP_API_KEY`. An empty file supplies no token,
so the variables decide; a file that holds only whitespace stops the HTTP server
and `mrag mcp validate` with an error rather than serving without authentication.

## Helper commands

```bash
mrag mcp init-config > mrag-mcp.yaml
mrag mcp schema > mrag-mcp.schema.json
mrag mcp validate --config ./mrag-mcp.yaml
mrag mcp --config ./mrag-mcp.yaml --print-effective-config
```

`--print-effective-config` masks resolved secret values.

## Tools

The MVP server exposes read-only tools:

| Tool | Purpose |
|---|---|
| `search` | Retrieve chunks from the KB |
| `list_documents` | List documents with source/index/retrieval state for one profile |
| `list_profiles` | List retrieval profiles |
| `inspect_document` | Inspect per-document indexing state |
| `inspect_chunks` | List chunk metadata |
| `inspect_chunk` | Inspect one chunk with content/context |
| `inspect_sections` | Inspect heading or parent-child structure |

Parameters (only the first one listed for a tool is required):

| Tool | Parameters |
|---|---|
| `search` | `query`; `profile` (default: the server's profile); `strategy` (default: `retrieval.strategy` of the MCP config if set, otherwise the selected profile's); `top_k` (default: `retrieval.top_k_default` if set, otherwise the selected profile's `retrieval.top_k`); `no_rerank` (default: `retrieval.no_rerank`) |
| `list_documents` | `profile`, `all`, `status`, `limit`, `offset` — all optional, described above |
| `list_profiles` | none |
| `inspect_document` | `document_id`; `profile` (limit the answer to one profile) |
| `inspect_chunks` | `document_id`; `profile`; `limit` (default: every chunk); `offset` (default `0`); `show_content` and `show_context` (default `false`) |
| `inspect_chunk` | `chunk_id` |
| `inspect_sections` | `document_id`; `profile` |

For `inspect_chunks` and `inspect_sections`, `profile` may be omitted only when
the document is indexed under exactly one profile.

`search` does not clamp `top_k`. A value above `retrieval.top_k_max` — given in
the call or resolved from the defaults — fails as a tool error (`isError: true`):
`top_k must be <= retrieval.top_k_max (50)`, with the limit in effect in the
parentheses.

Each `search` result carries a `reference` — the document's name, its stored
original and extraction (project-relative paths), and the
`mrag://documents/{document_id}/original` resource — so an agent can cite what
it read. It is the object `mrag search --json` and `POST /api/v1/retrieve`
return; see [the native API](./native-api.md#post-apiv1retrieve--retrieval) for
every field.

The `search` tool honors active document exclusions. `list_documents` still
lists an excluded document because its source is retained. Exclusion management
is intentionally CLI-only; see [document retrieval exclusions](./document-exclusions.md).

Write/management tools such as add, index, reindex, remove, exclusions, and
profile editing are intentionally not exposed.

## Resources

The server exposes these read-only resources:

```text
mrag://kb/info
mrag://profiles
mrag://profiles/{profile}
mrag://documents
mrag://documents/{document_id}
mrag://documents/{document_id}/extracted.txt
mrag://documents/{document_id}/extracted.md
mrag://documents/{document_id}/original
mrag://chunks/{chunk_id}
```

Large content is truncated according to `limits.content_max_chars`.

`mrag://documents` returns the first page only: what `list_documents` returns
with no arguments (the server's profile, extracted documents, the first 100
rows). `page.next_offset` in the answer tells whether more exist; page through
them with the `list_documents` tool.

`.../original` serves the stored original in the type recorded when it was
added: a Markdown or text file as text, a PDF added before 1.0.0 as a blob.
Text or blob follows from that type, not from whether the bytes happen to
decode. An original larger than `limits.artifact_max_bytes` (1 MiB by default)
is refused rather than cut — read it from the project instead.

## Streamable HTTP

Set `transport: streamable-http` to run an HTTP MCP endpoint.

```yaml
transport: streamable-http
http:
  host: 127.0.0.1
  port: 8001
  path: /mcp
auth:
  bearer_token_env: MRAG_MCP_API_KEY
```

The default host is `127.0.0.1`. If you bind to `0.0.0.0` without a bearer token, mrag prints a warning.

When a token is configured, a request whose `Authorization` header is not
`Bearer <token>` is answered `401` with the plain-text body `Unauthorized`.
When `http.allowed_origins` is not empty, a request whose `Origin` header is
not in the list is answered `403` with the plain-text body `Forbidden origin`
(a request without an `Origin` header passes; the origin is checked before the
token). Both answers are `text/plain`, not JSON-RPC errors.
