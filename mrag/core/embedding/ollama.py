import re
from pathlib import Path

import httpx

from mrag.core.embedding.base import BaseEmbeddingProvider

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
    ) -> None:
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.batch_size = batch_size
        self._dimension: int | None = None

    # ------------------------------------------------------------------
    # BaseEmbeddingProvider interface
    # ------------------------------------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            vectors.extend(self._embed_batch(batch))
        if self._dimension is None and vectors:
            self._dimension = len(vectors[0])
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

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = httpx.post(
                f"{self.endpoint}/api/embed",
                json={"model": self.model, "input": texts},
                timeout=120.0,
            )
            resp.raise_for_status()
        except httpx.ConnectError as e:
            raise ConnectionError(
                f"Cannot connect to Ollama at {self.endpoint}. "
                "Is Ollama running? (ollama serve)"
            ) from e
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Ollama returned HTTP {e.response.status_code}: {e.response.text}"
            ) from e

        data = resp.json()
        embeddings = data.get("embeddings")
        if not embeddings:
            raise RuntimeError(f"Unexpected Ollama response: {data}")
        return embeddings
