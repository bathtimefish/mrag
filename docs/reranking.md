# Reranking

This document covers mrag's reranking — re-ordering of search results via a CrossEncoder.

**Reranking** is a process that takes the top N candidates from retrieval and uses a separate model (a CrossEncoder) to re-score them against the query more precisely, then re-orders them. It is useful when you want to cast a wide net at the retrieval stage and end up with a higher-precision final ordering.

In mrag, setting `rerank.enabled: true` in a profile turns reranking on. It can be layered on top of any retrieval strategy — `keyword`, `vector`, `hybrid`, or `parent_child`. The default is `false` (no reranking).


## How it works

When reranking is enabled, the search flow becomes:

1. Retrieve up to `rerank.top_n` candidates using the regular retrieval strategy (`keyword` / `vector` / `hybrid`, etc.)
2. Pass `(query, candidate chunk body)` pairs to the CrossEncoder model and compute a relevance score for each
3. Sort by the CrossEncoder score, descending
4. Trim down to the final count requested by the caller (`--top-k` from the CLI or `top_k` from the API) and return

> Important: reranking is a **post-processing step that only runs at retrieval time**. It does not touch the stored index, so changes to `rerank.*` do not require re-indexing — the next search picks up the new settings.


## Configuration

Set the `rerank` section in `profiles/<profile-name>.yaml` as follows:

```yaml
rerank:
  enabled: true                                              # false (default) | true
  provider: sentence-transformers
  model: hotchpotch/japanese-reranker-cross-encoder-small-v1 # default — Japanese-focused
  max_length: 512                                            # CrossEncoder token limit (do not raise)
  top_n: 30                                                  # number of candidates to rerank
```

Field meanings:

- **`enabled`** — `true` enables reranking. `false` skips it and returns the retrieval results as-is.
- **`provider`** — implementation of the reranker (currently only `sentence-transformers`)
- **`model`** — CrossEncoder model name (a HuggingFace model ID). The default is a small Japanese-focused model.
- **`max_length`** — maximum token length passed to the model per item. Default: 512 (see the warning below).
- **`top_n`** — the number of candidates retrieved for reranking. **Set this comfortably larger than the final `top_k`.**


## Installation

Reranking is an optional dependency. Install the extras before using it:

```bash
uv pip install -e ".[reranker]"
```

This pulls in `sentence-transformers`. The configured model is downloaded from HuggingFace automatically on the first search.


## `max_length` and BERT token limits

BERT-based CrossEncoders — including the default `hotchpotch/japanese-reranker-cross-encoder-small-v1` — have a hard ceiling of **514 position embeddings**. `rerank.max_length: 512` (the default) tells the tokenizer to truncate inputs to fit within that limit.

> Note: with BERT-based models, **do not raise `max_length` above 512**. If you exceed the limit, inference will crash with an out-of-bounds error. To handle longer text, switch to a model that natively supports longer contexts rather than raising `max_length`.


## Compatibility with `parent_child` profiles

When you combine a `retrieval.strategy: parent_child` profile with `rerank.enabled: true`, keep the following in mind:

- `parent_child` retrieves child chunks but substitutes the **~3000-character parent chunk** for display
- The CrossEncoder receives the parent chunk, but `max_length: 512` means it only scores against **roughly the first 512 tokens**
- As a result, information in the later half of the parent chunk does not contribute to scoring, and rerank scores become less reliable

When using `parent_child`, leaving `rerank.enabled: false` is the safer choice.


## Disabling at runtime (CLI / API)

Even if a profile sets `enabled: true`, you can disable reranking per invocation via command-line options. Useful when you want to compare retrieval quality with and without reranking, or when latency takes priority.

```bash
# Disable reranking for a single search
mrag search "your query" --no-rerank

# Disable reranking for an evaluation run
mrag eval "your query" --no-rerank

# Disable reranking for the entire API server session
mrag serve --no-rerank
```


## The `retrieval_score` in search results

When reranking is applied, each result also carries a **`retrieval_score`**. This is the score from **before** reranking (the native relevance score of the retrieval strategy) — useful for inspecting how much the CrossEncoder reordered things.

Where it lives depends on the interface:

- **`mrag search --json`** — emitted as a **top-level field** alongside `score` (the same value is also kept inside `metadata`)
- **`mrag serve` API responses** — present under each result's **`metadata.retrieval_score`** (not lifted to the top level)
- **`mrag eval`** and other human-readable output — shown in parentheses next to `score`, e.g. `score=0.81  (retrieval=0.42)`

Example of inspecting it from CLI `--json` output:

```bash
mrag search "your query" --json | jq '.results[] | {score, retrieval_score: .retrieval_score}'
```

- `score` — the **post-rerank** score (CrossEncoder output)
- `retrieval_score` — the **pre-rerank** score (RRF for `hybrid`, BM25 for `keyword`, etc.)

This lets you observe how a candidate that ranked 1st at retrieval time moved after reranking — handy for tuning the reranker and verifying its effect.


## Choosing `top_n`

`rerank.top_n` is the number of candidates fetched for reranking. Default: **30**. It must be larger than the final result count (`retrieval.top_k`, default 8).

Rules of thumb:

- Start around **`top_n = top_k × 3 to 5`**
- Larger `top_n` makes the reranker's effect stand out more, but CrossEncoder inference cost grows linearly
- Too small and you risk losing a candidate that was, say, 10th at retrieval but actually deserves to be 1st

> Reranker inference has a non-trivial per-item cost. Blindly growing `top_n` visibly increases search latency, so pick the value with your use case and latency budget in mind.


## Model selection

The default `hotchpotch/japanese-reranker-cross-encoder-small-v1` is a small Japanese-focused model. You can swap it depending on your needs:

- **Japanese-focused / lightweight**: `hotchpotch/japanese-reranker-cross-encoder-small-v1` (default)
- **Japanese-focused / mid accuracy**: `hotchpotch/japanese-reranker-cross-encoder-base-v1`, etc.
- **Multilingual**: `BAAI/bge-reranker-base` / `BAAI/bge-reranker-v2-m3`, etc.

Changing the model does **not** require re-indexing — `rerank.*` is excluded from `profile_hash`. The new model is loaded on the next search (downloaded on the first load).


## Tips

- Reranking improves search quality but **adds latency**. When integrating it into an interactive tool, check that the latency budget can absorb it before enabling.
- For A/B comparison with `--no-rerank`, use **the same query** and read `score` against `retrieval_score` side by side.
- For strategies other than `parent_child` (`hybrid` / `vector` / `keyword`), the reranker's effect tends to be clearly visible.
- The reranker's model cache lives in the default HuggingFace cache directory (`~/.cache/huggingface/`). If disk space is a concern, clean up unused models by hand.
