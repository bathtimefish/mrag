# Contextual retrieval

This document covers mrag's contextual augmentation feature.

**Contextual retrieval** is a recipe proposed by Anthropic: for each chunk, ask an LLM to generate a short context describing the chunk's place within the whole document, prepend that text to the chunk body, and embed the combined text. The goal is to improve the quality of vector search.

In mrag, enabling `augmentation.strategy: contextual` in a profile activates this augmentation during the `mrag index` run.


## How it works

In a normal indexing run, the chunk body is passed to the embedding model as-is. With contextual augmentation enabled, `mrag index` follows this flow:

1. For each chunk, pass both the whole document (a leading slice) and the chunk to the LLM
2. The LLM returns a short text describing where the chunk sits within the document
3. That generated context is prepended to the chunk body and the combined text is passed to the embedding model
4. Vector-search precision improves by the amount of useful context the generated text adds

> Important: **the FTS5 keyword search index always uses the original chunk body (no prepended context)**. Contextual augmentation only changes the vector side; keyword search results are unaffected.


## Configuration

Set the `augmentation` section in `profiles/<profile-name>.yaml` as follows:

```yaml
augmentation:
  strategy: contextual           # none (default) | contextual
  provider: ollama
  model: gemma4:e4b              # generation LLM — separate from the embedding model
  endpoint: http://localhost:11434
  retry:                         # optional — defaults shown
    max_attempts: 3
    initial_delay_seconds: 2.0
    backoff_multiplier: 2.0
    max_delay_seconds: 30.0
  failure_policy:                # optional — behavior after all retries fail
    mode: raw_fallback           # raw_fallback (default) | fail_document
```

Field meanings:

- **`strategy`** — `none` disables contextual augmentation (fast). `contextual` enables it.
- **`model`** — the LLM used for context generation. Distinct from the search-side `embedding.model`.
- **`endpoint`** — URL of the LLM API endpoint used for context generation (default: `http://localhost:11434`).
- **`retry`** — retry settings for transient failures during context generation (timeouts, etc.).
- **`failure_policy.mode`** — how chunks that still fail after retries are handled (see below).

> Changing `augmentation.strategy` modifies the profile hash, which triggers **a full rebuild of that profile's index** on the next `mrag index` run. Changes to `retry` and `failure_policy` are excluded from the hash and do not require re-indexing.


## Context prompt (`context_prompt.txt`)

The LLM prompt is externalized as a text file at `profiles/context_prompt.txt`, so you can tune it without touching code. `mrag init` writes out the following default template:

```
<document>
{document}
</document>

Here is the chunk we want to situate within the whole document:
<chunk>
{chunk}
</chunk>

Please give a short succinct context to situate this chunk within the overall document for the purposes of improving search retrieval of the chunk. Answer only with the succinct context and nothing else. Always respond in the same language as the document and chunk. Prefer specific technical terms, parameter names, identifiers, and concrete values over general category descriptions. Do not begin your response with self-referential phrases like "This chunk" or "This section".
```

- The two placeholders `{document}` and `{chunk}` are required (removing them causes an error when the LLM is invoked)
- The document body is expanded into `{document}` up to the first 8000 characters
- Edits are picked up from the next `mrag index` run

> `context_prompt.txt` is not part of `profile_hash`. Rewriting the prompt does not trigger automatic re-indexing. **To regenerate the contexts of existing chunks with the new prompt, run `mrag reindex` explicitly.**

### Domain-specific tuning example

For example, when working with Japanese technical documents, the default prompt usually returns Japanese, but to avoid language mixing it is safer to instruct the model explicitly:

```
... Always respond in Japanese. Prefer specific module names, part numbers, communication protocol names, and concrete parameter values over general category descriptions. ...
```

Biasing the prompt toward the vocabulary that matters in your knowledge base — part numbers, protocol names, command names — can make the generated context more useful for retrieval.


## Failure behavior — `failure_policy`

LLM calls are computationally heavy and may take a long time. Transient failures such as timeouts can occur due to instability of the underlying process. mrag retries each chunk-level call with exponential backoff. The behavior for chunks that still fail after retries is switchable via `failure_policy.mode`.

| Mode | Behavior |
|------|----------|
| `raw_fallback` (default) | Save only that chunk as a **raw variant** (no prepended context) |
| `fail_document` | Treat the failure of that chunk as an error for the whole document |


> Note: `raw_fallback` mode is a safeguard that prevents one broken chunk from stopping the whole document. If fallbacks happen frequently, look at the source document quality (OCR noise, repeated text) or adjust the retry settings.


## Reading the index log

While `mrag index` runs, the log includes lines like the following:

- `↻ retry` — an LLM call failed and is being retried (informational; counts as a success if it recovers)
- `⤵ fallback` — a chunk failed even after retries and was switched to raw (worth monitoring)
- `⚠ large document` — printed at the start of augmentation for documents with 300+ chunks (informational — a heads-up that processing will take a while)
- `(N raw fallback)` — a per-document tally line showing the number of fallbacks

The log ends as usual with `✓ Indexed: ...`.


## Real-world impressions

For reference, observed values when running a local Ollama with `gemma4:e4b` on an Apple Silicon Mac:

- Roughly **tens of seconds per chunk** (varies with model and body size)
- For documents with more than 500 chunks, transient failures from VRAM pressure become common; raising `initial_delay_seconds` (e.g. 2 → 5) helps recovery
- You gain vector-search quality, but indexing time can be **several to tens of times longer than normal** — pick or skip contextual augmentation depending on document scale and use case

For quick checks or retrieval-quality evaluation, it can be worth starting with a **small model** like `gemma3:2b` to feel out the speed/quality trade-off.


## Tips

- Relationship to retrieval strategies: contextual augmentation improves the vector stage of `vector` and `hybrid`. It has no effect on `keyword` alone or on the keyword stage of `parent_child` (→ [retrieval-strategies.md](./retrieval-strategies.md)).
- Relationship to chunking strategies: augmentation is applied to each chunk after chunking, so it composes with any chunking strategy (→ [chunking-strategies.md](./chunking-strategies.md)).
- For `parent_child` profiles, `augmentation.strategy: none` is recommended — parent chunks already return broad context, so prepending more context tends to offer little benefit.
