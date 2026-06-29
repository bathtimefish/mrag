# MCP server — `mrag mcp`

`mrag mcp` exposes a single mrag project as a read-only Model Context Protocol server.

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
| `MRAG_MCP_API_KEY` | HTTP bearer token |
| `MRAG_MCP_API_KEY_FILE` | File containing the HTTP bearer token |

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
| `list_documents` | List registered documents |
| `list_profiles` | List retrieval profiles |
| `inspect_document` | Inspect per-document indexing state |
| `inspect_chunks` | List chunk metadata |
| `inspect_chunk` | Inspect one chunk with content/context |
| `inspect_sections` | Inspect heading or parent-child structure |

Write/management tools such as add, index, reindex, remove, and profile editing are intentionally not exposed.

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
mrag://chunks/{chunk_id}
```

Large content is truncated according to `limits.content_max_chars`.

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

