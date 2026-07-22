# Dify External Knowledge API endpoint — `dify-api`

This document covers the **Dify External Knowledge API compatible endpoint** exposed by mrag.

`mrag serve` starts a data-integration API server. The server supports two surfaces: a **Native API** and a **Dify external knowledge API**. This document covers the Dify API. For the Native API, see [the Native API document](./native-api.md).

Once you register the **endpoint URL and API key** in Dify's external-knowledge settings, your Dify chat flows and RAG nodes can search the mrag knowledge base directly.

> Prerequisite: run `mrag serve` from inside a project directory (the one with `mrag.yaml`). Documents must already have been processed with `mrag add` → `mrag index`.


## Endpoint

mrag exposes the following Dify-compatible endpoint:

| Method | Path | Role |
|---|---|---|
| `POST` | `/retrieval` | Receives a retrieval request from Dify and returns matching chunks |

> Note: `/retrieval` is mounted on the **root path**. The mrag Native REST API lives under `/api/v1/*`, but this single endpoint sits at the root to comply with the Dify spec.


## mrag-side setup

```bash
# 1. Move into the project directory
cd /path/to/my-kb

# 2. (Optional) set the API key via an environment variable
export MRAG_API_KEY="any-long-secret-string"

# 3. Start the server
mrag serve --host 0.0.0.0 --port 8000
```

- **`--host`** — Bind to an address reachable from Dify. For a local Dify instance, `127.0.0.1` is fine.
- **`--port`** — Defaults to `8000`.
- **`--profile`** — Profile used for retrieval. Defaults to `default_profile` in `mrag.yaml`.
- **`--no-rerank`** — Disables reranking for this server session even if the profile has `rerank.enabled: true` (useful when latency matters).

The startup banner prints `Knowledge ID: <id>`. If a Dify request comes in with a `knowledge_id` that doesn't match this value, the response is 404 (see below).


## Request format

```http
POST /retrieval HTTP/1.1
Host: your-mrag-host:8000
Authorization: Bearer <MRAG_API_KEY>
Content-Type: application/json
```

```json
{
  "knowledge_id": "kb_device",
  "query": "your query",
  "retrieval_setting": {
    "top_k": 5,
    "score_threshold": 0.3
  },
  "metadata_condition": null
}
```

Field semantics:

- **`knowledge_id`** — Must match `knowledge_base.id` in `mrag.yaml` (i.e. the project's knowledge ID). A mismatch returns 404 + `error_code: 2001`.
- **`query`** — The search query string. A missing field returns 422. An empty string is accepted at the schema layer, but tends to yield empty results in practice — filter it on the caller side.
- **`retrieval_setting.top_k`** — Maximum number of records to return (between `1` and `100`).
- **`retrieval_setting.score_threshold`** — Lower bound on the normalized score (between `0.0` and `1.0`). **Records below this value are filtered out**.
- **`metadata_condition`** — Accepted for Dify spec compatibility but **ignored in the current version**. The endpoint still returns 200 if you send it.


## Response format

```json
{
  "records": [
    {
      "content": "Body of the matched chunk",
      "score": 0.823412,
      "title": "manual.pdf",
      "metadata": {
        "chunk_id": "...",
        "document_id": "..."
      }
    }
  ]
}
```

Field semantics:

- **`content`** — Chunk body (for a `parent_child` profile, the **parent chunk's** body)
- **`score`** — Score normalized into the `[0.0, 1.0]` range (see below)
- **`title`** — Filename of the document the chunk belongs to. Falls back to the first 8 characters of `document_id` if the filename cannot be resolved
- **`metadata`** — Internal metadata attached to the chunk (`chunk_id` / `document_id`, etc.)

Active document exclusions are enforced before this response is built, for
every retrieval strategy. See [document retrieval
exclusions](./document-exclusions.md).


## Score normalization

Different retrieval strategies produce scores with different characteristics, so the Dify endpoint maps them all into `[0.0, 1.0]`:

| Retrieval strategy | Raw score | Normalization |
|---|---|---|
| `keyword` | BM25 | Compressed into `(0, 1)` via `score / (1 + score)` |
| `vector` | Cosine similarity (`[0, 1]`) | Pass-through, clamped to `[0, 1]` |
| `hybrid` | RRF fusion score (`[0, 1]`) | Pass-through, clamped to `[0, 1]` |
| `parent_child` | Hybrid + parent-resolution score | Pass-through, clamped to `[0, 1]` |

> Important: `retrieval_setting.score_threshold` is checked against this **normalized value**. For example, a BM25 score of `5.0` under the `keyword` strategy compresses to roughly `0.83`, so the raw and normalized scales are different. Tune the threshold against a live server.

> Note: when a profile has `rerank.enabled: true`, `score` is replaced by the CrossEncoder output. CrossEncoder score ranges are model-dependent and not guaranteed to land in `[0, 1]` — for `vector` / `hybrid` / `parent_child` the clamp still applies, and for `keyword` the `score/(1+score)` compression still applies on top. **Lower the threshold by a notch when reranking is on** (→ [reranking.md](./reranking.md)).


## Authentication

Authentication is configured via the `MRAG_API_KEY` environment variable. The mechanism is intentionally simple in the current version.

| Setting | Behavior |
|---|---|
| Not set | All requests are accepted without auth (suitable for local development) |
| Set | An `Authorization: Bearer <MRAG_API_KEY>` header is required |

Auth failures return Dify-compatible error codes:

| Situation | HTTP | error_code | error_msg |
|---|---|---|---|
| `Authorization` header missing or malformed | 401 | `1001` | `Invalid Authorization header format.` |
| Bearer token mismatch | 401 | `1002` | `Authorization failed. Please check your API key.` |

> Note: the four endpoints `/`, `/docs`, `/openapi.json`, and `/redoc` are exempt from auth (so you can view the OpenAPI docs and run health checks).


## Error code reference

| HTTP | error_code | Trigger |
|---|---|---|
| 401 | 1001 | `Authorization` header missing or malformed |
| 401 | 1002 | Bearer token mismatch |
| 404 | 2001 | `knowledge_id` does not match the value in `mrag.yaml` |
| 422 | — | `knowledge_id` or `query` missing, `top_k` out of range, JSON structure invalid |
| 500 | — | Internal server exceptions, e.g. Qdrant unreachable |

Errors with `error_code: 1001 / 1002 / 2001` are returned in the `{"error_code": ..., "error_msg": ...}` shape. `422` / `500` use FastAPI's default shape (`{"detail": ...}`).


## Setting up the Dify side

These are the key steps for registering mrag as an **External Knowledge** source in Dify (see the official Dify documentation for the UI details):

1. From the "Knowledge" section in Dify Studio, choose "Add External Knowledge"
2. Enter `http(s)://<mrag-host>:<port>/retrieval` as the **endpoint URL**
3. Enter the same value as `MRAG_API_KEY` for the **API key** (leave empty if `MRAG_API_KEY` is not set on the mrag side)
4. Enter `knowledge_base.id` from `mrag.yaml` as the **Knowledge ID** (this value is printed on the mrag console right after `mrag init`)

Once the connection test passes, you can reference this knowledge from Dify's chat flow via a Knowledge Retrieval node.


## Tips

- **Picking `score_threshold`** — Start at `0.0` and walk it up in 0.05 increments while watching the Dify-side retrieval results. Raw BM25 varies widely under the `keyword` strategy, so the reasonable threshold range is dataset-dependent.
- **Debug with curl** — Hitting the endpoint directly with `curl -X POST http://localhost:8000/retrieval -H 'Authorization: Bearer ...' -H 'Content-Type: application/json' -d '{...}'` helps separate Dify Studio misconfiguration from mrag-side issues.
- **Latency in production** — With reranking enabled, CrossEncoder inference adds noticeable cost. Consider lowering `rerank.top_n`, or starting the server with `--no-rerank`, while keeping Dify's request timeout in mind.
