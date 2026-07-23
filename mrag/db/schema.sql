-- MRAG SQLite Schema
-- One Project = One Knowledge Base
-- SQLite is Source of Truth; Qdrant is rebuildable index

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ================================================================
-- documents
-- Populated by: mrag add
-- ================================================================
CREATE TABLE IF NOT EXISTS documents (
  id                       TEXT PRIMARY KEY,
  knowledge_id             TEXT NOT NULL,
  filename                 TEXT NOT NULL,
  original_path            TEXT NOT NULL,         -- relative: data/documents/<id>/original.*
  file_hash                TEXT NOT NULL,         -- SHA256 of original file
  source_type              TEXT NOT NULL,         -- pdf | md | txt | html
  extraction_provider      TEXT,                  -- pymupdf | marker | markitdown | docling | unstructured
  extraction_output_format TEXT,                 -- markdown | text
  extracted_markdown_path  TEXT,                 -- relative: data/documents/<id>/extracted.md
  extracted_text_path      TEXT,                 -- relative: data/documents/<id>/extracted.txt
  extraction_meta_path     TEXT,                 -- relative: data/documents/<id>/extraction_meta.json
  extracted_hash           TEXT,                 -- SHA256 of extracted content (used for diff detection)
  status                   TEXT NOT NULL DEFAULT 'pending'
                             CHECK(status IN ('pending', 'extracted', 'error')),
  error_message            TEXT,
  created_at               TEXT NOT NULL,
  updated_at               TEXT NOT NULL
);

-- ================================================================
-- embedding_models
-- Populated by: mrag index (first use of a model)
-- Version key format: provider:model_name:dimension:revision
-- e.g. "ollama:nomic-embed-text:768:v1"
-- ================================================================
CREATE TABLE IF NOT EXISTS embedding_models (
  id              TEXT PRIMARY KEY,   -- version key (above)
  provider        TEXT NOT NULL,      -- ollama | openai | azure | sentence-transformers
  model_name      TEXT NOT NULL,
  dimension       INTEGER NOT NULL,
  model_revision  TEXT,
  normalized_name TEXT,               -- slug for Qdrant collection name component
  created_at      TEXT NOT NULL
);

-- ================================================================
-- profiles
-- Populated by: mrag index (on first use or config change)
-- config_json: canonical JSON of chunking/embedding/retrieval/contextual/rerank settings
-- profile_hash: versioned SHA256 index identity (used for differential indexing)
-- ================================================================
CREATE TABLE IF NOT EXISTS profiles (
  name          TEXT PRIMARY KEY,
  knowledge_id  TEXT NOT NULL,
  config_json   TEXT NOT NULL,
  profile_hash  TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

-- ================================================================
-- chunks (canonical chunks, profile-scoped)
-- Populated by: mrag index
-- chunk_type: chunk | parent | child
--   chunk  = standard chunk (non-parent_child strategies)
--   parent = large context block (parent_child strategy)
--   child  = search-unit block (parent_child strategy)
-- source_format: markdown | text (which extracted file was used)
--
-- metadata_json schema (JSON object, all fields optional):
--   heading_path        : list[str]  — H1 > H2 > H3 hierarchy ("preserve_heading_path")
--   heading_path_text   : str        — joined breadcrumb "H1 > H2 > H3"
--   section_id          : str        — slug of heading_path ("h1/h2/h3")
--   section_title       : str        — leaf heading name
--   section_start_line  : int        — block-aware: source line range start
--   section_end_line    : int        — block-aware: source line range end
--   block_types         : list[str]  — block kinds present (heading/paragraph/table/code_block/...)
--   contains_table      : bool       — at least one BLOCK_TABLE present
--   contains_code       : bool       — at least one BLOCK_CODE present
--   language            : str        — code block language hint (when contains_code)
--   table_count         : int        — number of tables in the chunk
--   table_columns       : list[str]  — column headers of the (first) table
--   table_split         : bool       — true when this chunk is a slice of an oversized table
--   table_id            : str        — group identifier shared by all parts of a split table
--   table_part          : int        — 1-based part index when table_split=true
--   table_parts         : int        — total parts when table_split=true
--   table_header_repeated : bool     — true when the table header was repeated in this part
-- ================================================================
CREATE TABLE IF NOT EXISTS chunks (
  id              TEXT PRIMARY KEY,
  knowledge_id    TEXT NOT NULL,
  document_id     TEXT NOT NULL REFERENCES documents(id),
  profile_name    TEXT NOT NULL REFERENCES profiles(name),
  parent_chunk_id TEXT REFERENCES chunks(id),
  chunk_type      TEXT NOT NULL DEFAULT 'chunk'
                    CHECK(chunk_type IN ('chunk', 'parent', 'child')),
  chunk_index     INTEGER NOT NULL,
  content         TEXT NOT NULL,
  source_format   TEXT NOT NULL,
  token_count     INTEGER,
  char_count      INTEGER,
  metadata_json   TEXT,        -- see comment above for the schema of this JSON column
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

-- ================================================================
-- chunk_variants (retrieval-optimized, profile-scoped)
-- Populated by: mrag index
-- variant_type: raw | contextual
--   raw        = content == chunks.content (direct embedding)
--   contextual = content prepended with LLM-generated context_text
-- qdrant_collection: full Qdrant collection name for this variant
--
-- metadata_json schema (JSON object, all fields optional):
--   augmentation_status : str  — "fallback_raw" when augmentation retries were
--                                exhausted and the chunk degraded to a raw variant
--   augmentation_error  : str  — truncated error message from the failing augmentation
--                                attempt (paired with augmentation_status="fallback_raw")
--   embedding_status    : str  — "fallback_no_vector" (v0.21.0+) when embedding
--                                retries + bisection failed for this chunk.
--                                qdrant_point_id is NULL for these variants;
--                                vector search cannot return them but FTS5
--                                keyword search still does.
--   embedding_error     : str  — truncated error message (max 500 chars) from
--                                the failing embedding attempt (paired with
--                                embedding_status="fallback_no_vector")
-- When both stages succeed normally, metadata_json is NULL.
-- ================================================================
CREATE TABLE IF NOT EXISTS chunk_variants (
  id                    TEXT PRIMARY KEY,
  knowledge_id          TEXT NOT NULL,
  document_id           TEXT NOT NULL REFERENCES documents(id),
  chunk_id              TEXT NOT NULL REFERENCES chunks(id),
  profile_name          TEXT NOT NULL REFERENCES profiles(name),
  variant_type          TEXT NOT NULL     -- raw | contextual
                          CHECK(variant_type IN ('raw', 'contextual')),
  content_for_embedding TEXT NOT NULL,
  context_text          TEXT,            -- LLM-generated context (contextual only)
  embedding_model_id    TEXT REFERENCES embedding_models(id),
  qdrant_point_id       TEXT,
  qdrant_collection     TEXT,
  metadata_json         TEXT,            -- see comment above for the schema of this JSON column
  created_at            TEXT NOT NULL
);

-- ================================================================
-- embedding_cache (optional; enabled per profile config)
-- Populated by: mrag index (when embedding.cache.enabled = true)
-- vector_path: relative to cache/embeddings/ e.g. "<cache_key>.npy"
-- ================================================================
CREATE TABLE IF NOT EXISTS embedding_cache (
  id                 TEXT PRIMARY KEY,
  cache_key          TEXT NOT NULL UNIQUE,  -- v2 framed SHA256(model_id, content)
  embedding_model_id TEXT NOT NULL REFERENCES embedding_models(id),
  vector_path        TEXT NOT NULL,
  created_at         TEXT NOT NULL
);

-- ================================================================
-- document_indexes (differential indexing state)
-- Populated by: mrag index / mrag reindex
-- status: pending | indexing | indexed | error
-- profile_hash index-identity snapshot at index time (for change detection)
-- ================================================================
CREATE TABLE IF NOT EXISTS document_indexes (
  id                  TEXT PRIMARY KEY,
  knowledge_id        TEXT NOT NULL,
  document_id         TEXT NOT NULL REFERENCES documents(id),
  profile_name        TEXT NOT NULL,
  document_file_hash  TEXT NOT NULL,
  extracted_hash      TEXT NOT NULL,
  profile_hash        TEXT NOT NULL,
  status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'indexing', 'indexed', 'error')),
  indexed_at          TEXT,
  error_message       TEXT,
  UNIQUE(document_id, profile_name)
);

-- ================================================================
-- document_exclusions
-- Persistent retrieval policy. NULL profile_name means every current and
-- future profile. document_id intentionally has no FK: OSS force re-add uses
-- row replacement while retaining the stable document ID and policy.
-- ================================================================
CREATE TABLE IF NOT EXISTS document_exclusions (
  id           TEXT PRIMARY KEY,
  document_id  TEXT NOT NULL,
  profile_name TEXT,
  reason       TEXT CHECK(reason IS NULL OR length(reason) <= 1000),
  created_at   TEXT NOT NULL,
  revoked_at   TEXT
);

-- ================================================================
-- fts_chunks (FTS5 virtual table for keyword search)
-- Populated by: mrag index
-- Default tokenizer: trigram (no extra dependencies)
-- Optional tokenizer: vaporetto (requires sqlite-vaporetto extension)
-- content = chunks.content (canonical chunk raw text, NOT variant content)
-- Hybrid retrieval fuses results from fts_chunks and Qdrant on chunk_id
-- ================================================================
CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(
  content,
  knowledge_id  UNINDEXED,
  profile_name  UNINDEXED,
  chunk_id      UNINDEXED,
  document_id   UNINDEXED,
  tokenize = 'trigram'
);

-- ================================================================
-- Indexes
-- ================================================================
CREATE INDEX IF NOT EXISTS idx_documents_knowledge_id    ON documents(knowledge_id);
CREATE INDEX IF NOT EXISTS idx_documents_file_hash       ON documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_documents_status          ON documents(status);

CREATE INDEX IF NOT EXISTS idx_chunks_document_profile  ON chunks(document_id, profile_name);
CREATE INDEX IF NOT EXISTS idx_chunks_knowledge_profile ON chunks(knowledge_id, profile_name);
CREATE INDEX IF NOT EXISTS idx_chunks_parent_chunk_id   ON chunks(parent_chunk_id);

CREATE INDEX IF NOT EXISTS idx_chunk_variants_chunk_id  ON chunk_variants(chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunk_variants_profile   ON chunk_variants(knowledge_id, profile_name);
CREATE INDEX IF NOT EXISTS idx_chunk_variants_qdrant    ON chunk_variants(qdrant_point_id);

CREATE INDEX IF NOT EXISTS idx_document_indexes_lookup  ON document_indexes(document_id, profile_name);
CREATE INDEX IF NOT EXISTS idx_document_indexes_status  ON document_indexes(knowledge_id, status);
CREATE INDEX IF NOT EXISTS idx_document_exclusions_document
  ON document_exclusions(document_id);
CREATE INDEX IF NOT EXISTS idx_document_exclusions_active_profile
  ON document_exclusions(profile_name, document_id) WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_document_exclusions_active_global
  ON document_exclusions(document_id)
  WHERE profile_name IS NULL AND revoked_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_document_exclusions_active_profile
  ON document_exclusions(document_id, profile_name)
  WHERE profile_name IS NOT NULL AND revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_embedding_cache_key      ON embedding_cache(cache_key);
