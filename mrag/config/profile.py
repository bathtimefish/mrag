import hashlib
import json
import math
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


# Bump whenever index-producing semantics change without a corresponding
# profile field change.  Version 2 separates index identity from query-time
# tuning and includes the effective contextual prompt and tokenizer.
# Version 3 restores the raw-only FTS contract for contextual profiles, forcing
# existing indexes that may contain generated context to be rebuilt.
INDEX_IDENTITY_VERSION = 3
TEXT_NORMALIZATION_IDENTITY = "NFKC-v1"


class ParentConfig(BaseModel):
    strategy: Literal["fixed_size", "section"] = "fixed_size"
    max_chars: int = 3000


class ChildConfig(BaseModel):
    strategy: str = "recursive"
    chunk_size: int = 600
    overlap: int = 100


class ChunkingConfig(BaseModel):
    strategy: str = "recursive"
    source_format: str = "text"
    chunk_size: int = 800
    overlap: int = 120
    preserve_heading_path: bool = True
    preserve_tables: bool = True
    preserve_code_blocks: bool = True
    # v0.3 追加 — parent_child strategy でのみ使用
    parent: ParentConfig = Field(default_factory=ParentConfig)
    child: ChildConfig = Field(default_factory=ChildConfig)

    @model_validator(mode="after")
    def _validate_parent_child_sizes(self) -> "ChunkingConfig":
        # Only enforce when parent_child is actually in use — for other
        # strategies parent/child fields are unused so size relationships
        # don't matter.
        if self.strategy != "parent_child":
            return self
        if self.child.chunk_size >= self.parent.max_chars:
            raise ValueError(
                f"chunking.child.chunk_size ({self.child.chunk_size}) must be smaller "
                f"than chunking.parent.max_chars ({self.parent.max_chars}). "
                "parent_child retrieval requires children to be substantially smaller "
                "than parents — otherwise the small-to-big benefit is lost."
            )
        return self


class RetrievalConfig(BaseModel):
    strategy: str = "hybrid"
    top_k: int = Field(default=8, ge=1)
    dense_top_k: int = Field(default=20, ge=1)
    keyword_top_k: int = Field(default=20, ge=1)
    fusion: Literal["rrf", "weighted"] = "rrf"
    weights: list[float] | None = None
    """When ``fusion='weighted'``, the fusion weight for each retrieval list in
    the order [vector, keyword]. ``None`` (default) means uniform weights.
    Ignored when ``fusion='rrf'`` (RRF uses rank only, not score)."""

    @model_validator(mode="after")
    def _validate_weights(self) -> "RetrievalConfig":
        if self.weights is None:
            return self
        if len(self.weights) != 2:
            raise ValueError(
                "retrieval.weights must have exactly 2 elements [vector, keyword]; "
                f"got {len(self.weights)}"
            )
        if any(not math.isfinite(weight) or weight < 0 for weight in self.weights):
            raise ValueError("retrieval.weights must be finite and non-negative")
        if sum(self.weights) <= 0:
            raise ValueError("retrieval.weights must sum to a positive value")
        return self

    @model_validator(mode="after")
    def _validate_candidate_limits(self) -> "RetrievalConfig":
        if self.strategy in {"vector", "hybrid", "parent_child"}:
            if self.dense_top_k < self.top_k:
                raise ValueError("retrieval.dense_top_k must be >= retrieval.top_k")
        if self.strategy in {"keyword", "hybrid", "parent_child"}:
            if self.keyword_top_k < self.top_k:
                raise ValueError("retrieval.keyword_top_k must be >= retrieval.top_k")
        return self


class OllamaRetryConfig(BaseModel):
    max_attempts: int = 3
    initial_delay_seconds: float = 2.0
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 30.0


class EmbeddingCacheConfig(BaseModel):
    enabled: bool = False


class EmbeddingFailurePolicyConfig(BaseModel):
    mode: str = "fallback_no_vector"    # "fallback_no_vector" | "fail_document"


class EmbeddingConfig(BaseModel):
    provider: str = "ollama"
    model: str = "bge-m3"
    endpoint: str = "http://localhost:11434"
    cache: EmbeddingCacheConfig = Field(default_factory=EmbeddingCacheConfig)
    retry: OllamaRetryConfig = Field(default_factory=OllamaRetryConfig)
    failure_policy: EmbeddingFailurePolicyConfig = Field(default_factory=EmbeddingFailurePolicyConfig)


class AugmentationFailurePolicyConfig(BaseModel):
    mode: str = "raw_fallback"    # "raw_fallback" | "fail_document"


class AugmentationConfig(BaseModel):
    strategy: str = "none"        # none | contextual | raptor | ...
    provider: str = "ollama"
    model: str = "gemma4:e2b"
    endpoint: str = "http://localhost:11434"
    retry: OllamaRetryConfig = Field(default_factory=OllamaRetryConfig)
    failure_policy: AugmentationFailurePolicyConfig = Field(default_factory=AugmentationFailurePolicyConfig)


class KeywordConfig(BaseModel):
    provider: str = "sqlite_fts5"
    tokenizer: str = "trigram"
    fallback_tokenizer: str = "trigram"


class RerankConfig(BaseModel):
    enabled: bool = False
    provider: str = "sentence-transformers"
    model: str = "hotchpotch/japanese-reranker-cross-encoder-small-v1"
    max_length: int = 512
    top_n: int = 30


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

    @model_validator(mode="after")
    def _validate_parent_child_consistency(self) -> "ProfileConfig":
        c_is_pc = self.chunking.strategy == "parent_child"
        r_is_pc = self.retrieval.strategy == "parent_child"
        if c_is_pc and not r_is_pc:
            raise ValueError(
                "chunking.strategy is 'parent_child' but retrieval.strategy is "
                f"'{self.retrieval.strategy}'. "
                "Both must be 'parent_child' when using parent-child retrieval."
            )
        if r_is_pc and not c_is_pc:
            raise ValueError(
                "retrieval.strategy is 'parent_child' but chunking.strategy is "
                f"'{self.chunking.strategy}'. "
                "Both must be 'parent_child' when using parent-child retrieval."
            )
        return self

    def compute_hash(
        self,
        *,
        context_prompt: str | None = None,
        effective_tokenizer: str | None = None,
    ) -> str:
        """Return the versioned index identity used for differential indexing.

        Query-time retrieval and rerank settings are intentionally excluded.
        The endpoint remains included as a conservative proxy until Ollama's
        immutable model digest is recorded.  For contextual augmentation, the
        exact effective prompt content participates in the identity.
        """
        tokenizer = effective_tokenizer or self.keyword.tokenizer
        augmentation: dict = {"strategy": self.augmentation.strategy}
        if self.augmentation.strategy == "contextual":
            if context_prompt is None:
                from mrag.core.indexing.context_prompt_template import (
                    DEFAULT_CONTEXT_PROMPT_TEMPLATE,
                )

                context_prompt = DEFAULT_CONTEXT_PROMPT_TEMPLATE
            augmentation.update(
                {
                    "provider": self.augmentation.provider,
                    "model": self.augmentation.model,
                    "endpoint": self.augmentation.endpoint,
                    "context_prompt_hash": hashlib.sha256(
                        context_prompt.encode("utf-8")
                    ).hexdigest(),
                }
            )

        relevant = {
            "index_identity_version": INDEX_IDENTITY_VERSION,
            "chunking": self.chunking.model_dump(),
            "embedding": self.embedding.model_dump(
                exclude={"cache", "retry", "failure_policy"}
            ),
            "augmentation": augmentation,
            "keyword": {
                "provider": self.keyword.provider,
                "tokenizer": tokenizer,
            },
            "text_normalization": TEXT_NORMALIZATION_IDENTITY,
        }
        canonical = json.dumps(relevant, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def compute_config_hash(self) -> str:
        """Return a deterministic hash of all non-name profile settings.

        This audit hash is deliberately separate from :meth:`compute_hash`;
        changing it does not imply that stored index artifacts are stale.
        """
        canonical = json.dumps(
            self.model_dump(exclude={"name"}),
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


def validate_effective_tokenizer(
    profile: ProfileConfig,
    project_tokenizer: str,
) -> str:
    """Return the project tokenizer after rejecting misleading profile drift.

    The current OSS schema has one FTS5 table whose tokenizer is fixed by
    ``mrag init``.  Per-profile tokenizers therefore cannot be honored safely
    without a database migration.
    """
    if (
        "tokenizer" in profile.keyword.model_fields_set
        and profile.keyword.tokenizer != project_tokenizer
    ):
        raise ValueError(
            "Tokenizer mismatch: mrag.yaml fts_tokenizer "
            f"({project_tokenizer!r}) must match profile keyword.tokenizer "
            f"({profile.keyword.tokenizer!r}). The project tokenizer is fixed "
            "when the FTS5 table is created; restore matching values before "
            "indexing or searching."
        )
    return project_tokenizer


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
