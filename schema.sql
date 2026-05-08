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
-- profile_hash: SHA256 of config_json (used for differential indexing)
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
  metadata_json   TEXT,
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
  metadata_json         TEXT,
  created_at            TEXT NOT NULL
);

-- ================================================================
-- embedding_cache (optional; enabled per profile config)
-- Populated by: mrag index (when embedding.cache.enabled = true)
-- vector_path: relative to cache/embeddings/ e.g. "<cache_key>.npy"
-- ================================================================
CREATE TABLE IF NOT EXISTS embedding_cache (
  id                 TEXT PRIMARY KEY,
  cache_key          TEXT NOT NULL UNIQUE,  -- SHA256 of (model_id + content)
  embedding_model_id TEXT NOT NULL REFERENCES embedding_models(id),
  vector_path        TEXT NOT NULL,
  created_at         TEXT NOT NULL
);

-- ================================================================
-- document_indexes (differential indexing state)
-- Populated by: mrag index / mrag reindex
-- status: pending | indexing | indexed | error
-- profile_hash snapshot at index time (for change detection)
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

CREATE INDEX IF NOT EXISTS idx_embedding_cache_key      ON embedding_cache(cache_key);
