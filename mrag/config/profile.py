import hashlib
import json
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ChunkingConfig(BaseModel):
    strategy: str = "recursive"
    source_format: str = "text"
    chunk_size: int = 800
    overlap: int = 120


class RetrievalConfig(BaseModel):
    strategy: str = "hybrid"
    top_k: int = 8
    dense_top_k: int = 20
    keyword_top_k: int = 20
    fusion: str = "rrf"


class OllamaRetryConfig(BaseModel):
    max_attempts: int = 3
    initial_delay_seconds: float = 2.0
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 30.0


class EmbeddingCacheConfig(BaseModel):
    enabled: bool = False


class EmbeddingConfig(BaseModel):
    provider: str = "ollama"
    model: str = "bge-m3"
    endpoint: str = "http://localhost:11434"
    cache: EmbeddingCacheConfig = Field(default_factory=EmbeddingCacheConfig)
    retry: OllamaRetryConfig = Field(default_factory=OllamaRetryConfig)


class AugmentationConfig(BaseModel):
    strategy: str = "none"        # none | contextual | raptor | ...
    provider: str = "ollama"
    model: str = "gemma4:e4b"
    endpoint: str = "http://localhost:11434"
    retry: OllamaRetryConfig = Field(default_factory=OllamaRetryConfig)


class KeywordConfig(BaseModel):
    provider: str = "sqlite_fts5"
    tokenizer: str = "trigram"
    fallback_tokenizer: str = "trigram"


class RerankConfig(BaseModel):
    enabled: bool = False
    provider: str = "sentence-transformers"
    model: str = "hotchpotch/japanese-reranker-cross-encoder-small-v1"
    top_n: int = 30
    top_k: int = 8


class ProfileExtractionPdfConfig(BaseModel):
    provider: str = "pymupdf"
    output_format: str = "text"


class ProfileExtractionConfig(BaseModel):
    pdf: ProfileExtractionPdfConfig = Field(default_factory=ProfileExtractionPdfConfig)


class ProfileConfig(BaseModel):
    name: str
    extraction: ProfileExtractionConfig = Field(default_factory=ProfileExtractionConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    augmentation: AugmentationConfig = Field(default_factory=AugmentationConfig)
    keyword: KeywordConfig = Field(default_factory=KeywordConfig)
    rerank: RerankConfig = Field(default_factory=RerankConfig)

    def compute_hash(self) -> str:
        """SHA256 of index-affecting fields, used for differential indexing.

        rerank is intentionally excluded: it is applied at retrieval time and
        does not affect stored chunks, embeddings, or FTS5 data.
        """
        relevant = {
            "chunking": self.chunking.model_dump(),
            "embedding": self.embedding.model_dump(exclude={"retry"}),
            "retrieval": self.retrieval.model_dump(),
            "augmentation": self.augmentation.model_dump(exclude={"retry"}),
            "keyword": self.keyword.model_dump(),
        }
        canonical = json.dumps(relevant, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()


def load_profile(profile_name: str, project_dir: Path | None = None) -> ProfileConfig:
    if project_dir is None:
        project_dir = Path.cwd()

    profile_path = project_dir / "profiles" / f"{profile_name}.yaml"
    if not profile_path.exists():
        raise FileNotFoundError(
            f"Profile '{profile_name}' not found at {profile_path}"
        )

    data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    return ProfileConfig(**data)
