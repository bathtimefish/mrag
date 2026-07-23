import math
import re
from pathlib import Path
from typing import Any

from mrag.core.embedding.base import BaseEmbeddingProvider
from mrag.core.ollama_client import ollama_post

_DEFAULT_BATCH_SIZE = 32
_REVISION = "v1"


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class OllamaEmbeddingProvider(BaseEmbeddingProvider):
    def __init__(
        self,
        model: str,
        endpoint: str = "http://localhost:11434",
        batch_size: int = _DEFAULT_BATCH_SIZE,
        max_attempts: int = 3,
        initial_delay: float = 2.0,
        backoff_multiplier: float = 2.0,
        max_delay: float = 30.0,
    ) -> None:
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.backoff_multiplier = backoff_multiplier
        self.max_delay = max_delay
        self._dimension: int | None = None

    # ------------------------------------------------------------------
    # BaseEmbeddingProvider interface
    # ------------------------------------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        expected_dimension = self._dimension
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            batch_vectors = self._embed_batch(batch, expected_dimension)
            if expected_dimension is None:
                expected_dimension = len(batch_vectors[0])
            vectors.extend(batch_vectors)
        if self._dimension is None:
            self._dimension = expected_dimension
        return vectors

    def get_dimension(self) -> int:
        if self._dimension is None:
            raise RuntimeError(
                "Dimension unknown — call embed() with at least one text first."
            )
        return self._dimension

    def get_model_id(self) -> str:
        return f"ollama:{self.model}:{self.get_dimension()}:{_REVISION}"

    def get_normalized_name(self) -> str:
        return _normalize(self.model)

    # ------------------------------------------------------------------
    # SQLite registration
    # ------------------------------------------------------------------

    def ensure_model_registered(self, db_path: Path) -> None:
        """Upsert this model into embedding_models. Requires embed() called first."""
        from datetime import datetime, timezone
        from mrag.db.connection import db_connection

        model_id = self.get_model_id()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with db_connection(db_path) as conn:
            conn.execute(
                """INSERT OR IGNORE INTO embedding_models
                   (id, provider, model_name, dimension, model_revision, normalized_name, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    model_id,
                    "ollama",
                    self.model,
                    self.get_dimension(),
                    _REVISION,
                    self.get_normalized_name(),
                    now,
                ),
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _embed_batch(
        self,
        texts: list[str],
        expected_dimension: int | None,
    ) -> list[list[float]]:
        def _validate(data: dict) -> None:
            _validate_embed_response(
                data,
                expected_count=len(texts),
                expected_dimension=expected_dimension,
            )

        data = ollama_post(
            self.endpoint,
            "/api/embed",
            {"model": self.model, "input": texts},
            max_attempts=self.max_attempts,
            initial_delay=self.initial_delay,
            backoff_multiplier=self.backoff_multiplier,
            max_delay=self.max_delay,
            validate=_validate,
        )
        return data["embeddings"]


def _validate_embed_response(
    data: Any,
    *,
    expected_count: int,
    expected_dimension: int | None,
) -> None:
    """Reject malformed vectors before they reach cache, SQLite, or Qdrant."""
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected Ollama embed response: expected an object")

    embeddings = data.get("embeddings")
    if not isinstance(embeddings, list):
        raise RuntimeError(
            "Unexpected Ollama embed response: 'embeddings' must be a list"
        )
    if len(embeddings) != expected_count:
        raise RuntimeError(
            "Unexpected Ollama embed response: embedding count "
            f"{len(embeddings)} does not match input count {expected_count}"
        )

    batch_dimension: int | None = None
    for index, vector in enumerate(embeddings):
        if not isinstance(vector, list) or not vector:
            raise RuntimeError(
                "Unexpected Ollama embed response: "
                f"embedding {index} must be a non-empty list"
            )
        for component in vector:
            if (
                isinstance(component, bool)
                or not isinstance(component, (int, float))
                or not math.isfinite(component)
            ):
                raise RuntimeError(
                    "Unexpected Ollama embed response: "
                    f"embedding {index} contains a non-finite or non-numeric component"
                )

        dimension = len(vector)
        if batch_dimension is None:
            batch_dimension = dimension
        elif dimension != batch_dimension:
            raise RuntimeError(
                "Unexpected Ollama embed response: inconsistent dimensions "
                f"{batch_dimension} and {dimension}"
            )
        if expected_dimension is not None and dimension != expected_dimension:
            raise RuntimeError(
                "Unexpected Ollama embed response: dimension "
                f"{dimension} does not match expected dimension {expected_dimension}"
            )
