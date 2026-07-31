# mrag Native REST API — `native-api`

This document covers the **Native REST API** (`/api/v1/*`) exposed by `mrag serve`.

`mrag serve` starts a data-integration API server. The server supports two surfaces: a **Native API** and a **Dify external knowledge API**. This document covers the Native API. For the Dify API, see [the Dify API document](./dify-api.md).

The Native API is designed for programs that call mrag directly. It covers not only retrieval but also **listing documents and profiles**. Responses carry more detail than the Dify API — internal fields such as `chunk_id`, `document_id`, and the `reranked` flag are returned as-is.

> Note: the Native API is mrag-specific — it does not conform to any vendor API such as OpenAI or Pinecone. The intended usage is to generate a typed client from the OpenAPI spec (`/openapi.json`) that FastAPI auto-generates.

> Prerequisite: run `mrag serve` from inside a project directory (the one with `mrag.yaml`). Documents must already have been processed with `mrag add` → `mrag index`.


## Endpoint reference

| Method | Path | Role |
|---|---|---|
| `POST` | `/api/v1/retrieve` | Run a retrieval query and return matching chunks (`/api/v1/search` is an alias) |
| `GET` | `/api/v1/documents` | List indexed documents |
| `GET` | `/api/v1/documents/{document_id}` | Get a single document's details (including chunk count) |
| `GET` | `/api/v1/profiles` | List profiles |
| `GET` | `/api/v1/profiles/{profile_name}` | Get a single profile's details |

> Note: the auto-generated OpenAPI docs are available at `http://<host>:<port>/docs` (Swagger UI), `/redoc` (Redoc), and `/openapi.json` (raw JSON). Use them alongside this document.


## `mrag serve` setup

```bash
cd /path/to/my-kb

# (Optional) set the API key
export MRAG_API_KEY="any-long-secret-string"

mrag serve --host 0.0.0.0 --port 8000
```

`mrag serve`'s options (`--profile` / `--no-rerank`, etc.) and the authentication behavior are shared with the Dify API. See the "mrag-side setup" section of [dify-api.md](./dify-api.md) for the details.


## `POST /api/v1/retrieve` — retrieval

The endpoint that calls mrag's retrieval logic directly. `/api/v1/search` is an alias for the same handler.

### Request

```http
POST /api/v1/retrieve HTTP/1.1
Host: your-mrag-host:8000
Authorization: Bearer <MRAG_API_KEY>
Content-Type: application/json
```

```json
{
  "query": "your query",
  "profile": "default",
  "strategy": "hybrid",
  "top_k": 5
}
```

Field semantics:

- **`query`** — The search query string (required). A missing field returns 422.
- **`profile`** — Profile name. Defaults to `default_profile` in `mrag.yaml`. Specifying a non-existent profile returns 404.
- **`strategy`** — Override the retrieval strategy (`hybrid` / `vector` / `keyword` / `parent_child`). Defaults to the profile's `retrieval.strategy`. If you specify `parent_child`, the index on that profile must have been built with parent-child chunking (child chunks must exist, or the call will not behave as expected).
- **`top_k`** — Number of records to return (between `1` and `100`). Omit it to use the resolved profile's `retrieval.top_k`.

### Response

```json
{
  "query": "your query",
  "profile": "default",
  "strategy": "hybrid",
  "reranked": true,
  "results": [
    {
      "chunk_id": "...",
      "document_id": "...",
      "filename": "manual.md",
      "score": 0.823412,
      "content": "Body of the matched chunk",
      "metadata": {
        "chunk_index": 12,
        "retrieval_score": 0.42
      }
    }
  ]
}
```

Field semantics:

- **`query`** / **`profile`** / **`strategy`** — The values actually applied on the server (so omitted fields and profile defaults are resolved here).
- **`reranked`** — Whether CrossEncoder reranking was applied.
- **`results[].chunk_id`** — The chunk's DB primary key. You can pass it directly to [mrag inspect chunk](./inspect.md) for further inspection.
- **`results[].score`** — The retrieval strategy's native score (when reranking is enabled, this is replaced by the CrossEncoder output. **No `[0, 1]` normalization is applied here, unlike the Dify API**).
- **`results[].metadata.retrieval_score`** — Present only when reranking is enabled. The score before reranking (→ [reranking.md](./reranking.md)).

Active document exclusions are enforced for every strategy before results are
returned. Because an exclusion retains the source document, the same document
continues to appear in `GET /api/v1/documents` even though retrieval suppresses
all of its chunks. See [document retrieval exclusions](./document-exclusions.md).


## `GET /api/v1/documents` — document list / detail

### List

```http
GET /api/v1/documents HTTP/1.1
Authorization: Bearer <MRAG_API_KEY>
```

Response:

```json
[
  {
    "id": "abcdef0123456789",
    "filename": "manual.md",
    "file_hash": "sha256:...",
    "status": "extracted",
    "created_at": "2026-05-22T10:00:00"
  }
]
```

Field details:

- **`id`** — Document ID assigned by `mrag add`
- **`status`** — The document's **extraction** status. One of `pending` / `extracted` / `error` (note that this is *not* the indexing status — per-profile indexing state is kept in the SQLite `document_indexes` table)
- **`created_at`** — When the document was added via `mrag add`

### Detail

```http
GET /api/v1/documents/{document_id} HTTP/1.1
```

The response is a list entry with **`extracted_text_path`** and **`chunk_count`** added:

```json
{
  "id": "abcdef0123456789",
  "filename": "manual.md",
  "file_hash": "sha256:...",
  "status": "extracted",
  "created_at": "2026-05-22T10:00:00",
  "extracted_text_path": "data/extracted/abcdef0123456789.md",
  "chunk_count": 42
}
```

> Note: `chunk_count` is the total across all profiles for this document. For a per-profile breakdown, use [`mrag inspect document`](./inspect.md).

Specifying a non-existent `document_id` returns 404.


## `GET /api/v1/profiles` — profile list / detail

### List

```http
GET /api/v1/profiles HTTP/1.1
```

Response:

```json
[
  {
    "name": "default",
    "strategy": "hybrid",
    "embedding_model": "nomic-embed-text",
    "chunking_strategy": "recursive"
  }
]
```

Profiles loaded from `profiles/*.yaml` are returned. Profiles registered in `mrag.yaml` but whose YAML file cannot be found are silently dropped.

### Detail

```http
GET /api/v1/profiles/{profile_name} HTTP/1.1
```

The response is a list entry with the **main chunking and retrieval parameters** added:

```json
{
  "name": "default",
  "strategy": "hybrid",
  "embedding_model": "nomic-embed-text",
  "chunking_strategy": "recursive",
  "chunk_size": 800,
  "overlap": 120,
  "dense_top_k": 30,
  "keyword_top_k": 30,
  "fusion": "rrf"
}
```

> Note: when you need the full profile YAML, use `mrag profiles show <name>`. This endpoint returns an excerpt suitable for agents and dashboards.

Specifying a non-existent `profile_name` returns 404.


## Authentication

The `MRAG_API_KEY` environment variable controls auth the same way as the [Dify API](./dify-api.md) — the mechanism is intentionally simple in the current version. Native API endpoints also require the `Authorization: Bearer <MRAG_API_KEY>` header when the key is set.

> Important: the error format for auth failures is **different from the Dify API**.
> - Dify API (`/retrieval`): `{"error_code": 1001, "error_msg": "..."}`
> - Native API (`/api/v1/*`): `{"detail": "Unauthorized"}`
>
> Both return HTTP `401`, but the response body shape differs — branch on it on the client side.


## Error code reference

| HTTP | Trigger |
|---|---|
| 401 | `Authorization` header missing or mismatched when auth is required (returned as `{"detail": "Unauthorized"}`) |
| 404 | Profile name or document ID does not exist |
| 422 | `query` field missing, `top_k` out of range, JSON structure invalid |
| 503 | Qdrant or other internal resource is unreachable. Returns `{"detail": "<reason>"}` (retryable) |

All Native API error responses follow FastAPI's default shape (`{"detail": ...}`).


## OpenAPI documentation

While `mrag serve` is running, the following URLs are accessible from a browser:

| URL | Content |
|---|---|
| `http://<host>:<port>/docs` | Swagger UI |
| `http://<host>:<port>/redoc` | Redoc |
| `http://<host>:<port>/openapi.json` | Raw OpenAPI spec JSON |

These are **exempt from authentication** even when `MRAG_API_KEY` is set (the intent is local API inspection and health checks). For production deployments, consider restricting access at a reverse proxy.


## Tips

- **Use the `reranked` flag for branching** — In CI, the simplest way to verify "the rerank-enabled profile is actually reranking" is to assert `reranked: true` on the retrieval response.
- **Do not compare raw `score` values across strategies** — The scales differ: `0.8` under `hybrid` does not mean the same thing as `0.8` under `keyword`. Use rank ordering for cross-query comparisons, or normalize on the client side if you need absolute values.
- **Request-time strategy override** — When you want to switch strategies without swapping profiles (e.g. compare `hybrid` and `vector` against the same index), the `strategy` field in the request is the easy lever.
- **If you only need agent-shaped JSON, the CLI works too** — `mrag search "your query" --json` returns equivalent output (including `retrieval_score`) without a running server. Consider it if you cannot justify keeping the server resident.
- **If you need Dify-style `[0, 1]` normalization**, use the Dify endpoint or implement it client-side (→ the "Score normalization" section of [dify-api.md](./dify-api.md)).
