# Retrieval strategies

This document covers the four retrieval strategies implemented in mrag.

A retrieval strategy is the setting that decides *how* searches are performed against the indexed chunks. Depending on the kind of document and the shape of queries (natural-language sentences vs. keyword lists, presence of orthographic variation, and so on), mrag lets you apply different retrieval strategies suited to the nature of each knowledge base.

In mrag, the strategy is selected via the `retrieval.strategy` field in `profiles/<profile-name>.yaml`.


## The four strategies

| Strategy | Description | Requires |
|----------|-------------|----------|
| `keyword` | Lexical full-text search via SQLite FTS5 BM25 | SQLite only |
| `vector` | Vector similarity search via Qdrant cosine similarity | Qdrant + Embedding Model |
| `hybrid` | Fuses the results of `keyword` and `vector` (default) | Qdrant + Embedding Model |
| `parent_child` | Small-to-Big approach — search at the child level and return parent chunks | Qdrant + Embedding Model; must be paired with `chunking.strategy: parent_child` |

Quick selection guide:

- **You want to surface specific terms or jargon that match the query exactly** → `keyword`
- **You want robustness against orthographic variation and paraphrasing, or want to use natural-language queries** → `vector`
- **You want both strengths — also the right place to start when unsure** → `hybrid`
- **You want hits in long documents to come back with broad surrounding context** → `parent_child`

> When you choose `parent_child`, `chunking.strategy` must also be set to `parent_child` (configuring only one of them triggers a profile validation error).


## `keyword` — BM25 keyword search

Lexical full-text search using SQLite FTS5's BM25 scoring. It needs neither Qdrant nor an embedding model, so it runs lightly.

```yaml
retrieval:
  strategy: keyword
  top_k: 8                 # final number of results to return
```

How it works:

1. NFKC-normalize the query text (the same normalization applied at index time)
2. Execute a `MATCH` query through the FTS5 tokenizer (vaporetto or trigram)
3. Return up to `top_k` results in descending order of BM25 score

Strengths and weaknesses:

- **Strong at**: lookups against vocabulary with fixed spelling — proper nouns, part numbers, command names, and the like
- **Weak at**: paraphrases and synonyms (e.g. "memory" vs. "RAM"). If the literal token does not appear in the chunk, it cannot be hit.

> When the vaporetto tokenizer is active, a run of Japanese text containing no whitespace (e.g. `温度センサの基本仕様`) is interpreted as a single phrase match over the morpheme sequence. Throwing a long natural-language sentence at it makes the whole thing one phrase, and almost nothing will hit. The trick is to **separate your tokens with whitespace** — for example `温度 センサ 仕様`. In most cases, AI agents already produce well-formed queries based on `AGENTS.md` and `SKILL.md`.


## `vector` — semantic search via embedding vectors

Searches Qdrant by cosine similarity between embedding vectors computed by the embedding model.

```yaml
retrieval:
  strategy: vector
  top_k: 8
```

How it works:

1. Vectorize the query text (with the same model used at index time)
2. Query Qdrant by cosine similarity and retrieve the top `top_k` matches
3. Look up each hit's chunk body from SQLite by `chunk_id` and return the results

Strengths and weaknesses:

- **Strong at**: orthographic variation, synonyms, and paraphrasing. Natural-language queries still pick up semantically close chunks.
- **Weak at**: searches where you want a literal string match — proper nouns, part numbers, and so on. Candidates that are semantically near but actually refer to a different entity tend to slip in.

> Vector search does **not** NFKC-normalize the query side. Multilingual models such as bge-m3 are largely insensitive to halfwidth/fullwidth differences and variant glyphs, but queries containing truly unusual characters can still drift slightly in the embedding space.


## `hybrid` — fusion of keyword + vector (default)

Runs `keyword` and `vector` independently and **fuses** their results into a single ranking. For most knowledge bases, this is the strategy to start with.

```yaml
retrieval:
  strategy: hybrid
  fusion: rrf              # rrf | weighted
  top_k: 8                 # final result count after fusion
  dense_top_k: 20          # candidate count at the vector stage
  keyword_top_k: 20        # candidate count at the keyword stage
```

How it works:

1. Retrieve `dense_top_k` candidates with `vector_search()`
2. Retrieve `keyword_top_k` candidates with `keyword_search()`
3. Combine the results using the configured fusion method
4. Return the top `top_k` results after fusion

### Fusion method — `rrf` (default)

**Reciprocal Rank Fusion**. A simple, robust method that uses only rank (not raw score). It sums the reciprocals of each item's rank across the lists, which makes it well-suited to combining retrieval methods with different score scales.

- No weight tuning required (rank-based, so the absolute score values don't matter)
- Less prone to surprises than `weighted` in most cases — when in doubt, pick this

> RRF compresses scores into a structurally small range (the maximum is around 0.03). Small numbers under `Score stats` are by design and have no bearing on retrieval quality.

### Fusion method — `weighted`

Normalizes the `vector` and `keyword` scores into [0, 1] each, then sums them with weights. Use this when you want to express a bias such as "weight `vector` more" or "make `keyword` count more".

```yaml
retrieval:
  strategy: hybrid
  fusion: weighted
  weights: [0.3, 0.7]      # in order [vector, keyword]. Any positive sum is treated as a relative ratio
  top_k: 8
  dense_top_k: 20
  keyword_top_k: 20
```

- `weights` must have exactly two elements in the order `[vector, keyword]`
- A sum of zero or less is a validation error
- `weights` is a retrieval-time parameter, so changing it does not require re-indexing


## `parent_child` — score on the child, return the parent

A Small-to-Big strategy that scores hits precisely against small child chunks while returning the larger parent chunk — which carries the surrounding context — for display. **Always pair it with `chunking.strategy: parent_child`**.

```yaml
chunking:
  strategy: parent_child
  parent:
    strategy: fixed_size
    max_chars: 3000
  child:
    strategy: recursive
    chunk_size: 600
    overlap: 100

retrieval:
  strategy: parent_child
  top_k: 8
  dense_top_k: 60          # leave headroom — multiple children collapse into one parent
  keyword_top_k: 60
```

How it works:

1. The retrieval phase internally runs `hybrid_search()`, scoring against the child chunks
2. For each hit child chunk, look up its `parent_chunk_id` and substitute the body of the parent chunk it belongs to
3. When multiple children under the same parent are hit, deduplicate down to a single entry (the highest-scoring child's hit is kept as the representative)
4. Return the top `top_k` parent chunks

Strengths and weaknesses:

- **Strong at**: long documents where you want "precise queries to land on the right spot, but the returned context to be wide"
- **Weak at**: short documents or setups with small parent chunks — the structural benefit of `parent_child` fades

> Important: `dense_top_k` / `keyword_top_k` are the candidate counts at the child-chunk stage. Because parents are deduplicated during aggregation, they need to be set comfortably larger than the final `top_k` (a rule of thumb is `top_k × 3` or more), or the result count will not reach `top_k`.

> Note: combining a `parent_child` profile with `rerank.enabled: true` truncates the ~3000-character parent chunk down to 512 tokens at the reranker, which degrades the reliability of rerank scores. Keep this in mind when using `parent_child`.

> For the chunking-side details of `parent_child`, see [chunking-strategies.md](./chunking-strategies.md#parent_child--precise-retrieval-with-rich-context).


## Defaults reference

Values used when a setting is omitted.

| Field | Default | Meaning |
|-------|---------|---------|
| `retrieval.top_k` | `8` | Final number of results to return |
| `retrieval.dense_top_k` | `20` | Candidate count at the vector stage for `hybrid` / `parent_child` |
| `retrieval.keyword_top_k` | `20` | Candidate count at the keyword stage for `hybrid` / `parent_child` |
| `retrieval.fusion` | `rrf` | Fusion method for `hybrid` |
| `retrieval.weights` | `null` (uniform) | Weights when `fusion: weighted` |


## Switching strategies from the CLI

Without editing the profile, you can switch the strategy and the result count via options on the search command.

```bash
# Switch strategy
mrag search "your query" --strategy keyword
mrag search "your query" --strategy vector
mrag search "your query" --strategy hybrid

# Limit the result count
mrag search "your query" --top-k 3

# Disable reranking
mrag search "your query" --no-rerank
```

This lets you experiment with a different strategy on the spot — no profile edit, no re-indexing required.
