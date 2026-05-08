import hashlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from mrag.db.connection import db_connection


def _cache_key(model_id: str, content: str) -> str:
    return hashlib.sha256((model_id + content).encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EmbeddingCache:
    """File-backed embedding cache stored as .npy files under cache/embeddings/."""

    def __init__(self, cache_dir: Path, db_path: Path) -> None:
        self.cache_dir = cache_dir
        self.db_path = db_path
        cache_dir.mkdir(parents=True, exist_ok=True)

    def key_for(self, model_id: str, content: str) -> str:
        return _cache_key(model_id, content)

    def get(self, cache_key: str) -> list[float] | None:
        """Return cached vector or None on miss."""
        path = self.cache_dir / f"{cache_key}.npy"
        if not path.exists():
            return None
        return np.load(str(path)).tolist()

    def put(self, cache_key: str, model_id: str, vector: list[float]) -> None:
        """Save vector to disk and record metadata in SQLite."""
        path = self.cache_dir / f"{cache_key}.npy"
        np.save(str(path), np.array(vector, dtype=np.float32))

        rel_path = str(path.relative_to(self.cache_dir.parent.parent))
        now = _now_iso()
        import uuid
        with db_connection(self.db_path) as conn:
            conn.execute(
                """INSERT OR IGNORE INTO embedding_cache
                   (id, cache_key, embedding_model_id, vector_path, created_at)
                   VALUES (?,?,?,?,?)""",
                (str(uuid.uuid4()), cache_key, model_id, rel_path, now),
            )

    def get_or_embed(
        self,
        texts: list[str],
        model_id: str,
        embed_fn,
    ) -> list[list[float]]:
        """
        Return embeddings for texts, using cache for hits and embed_fn for misses.
        embed_fn(texts: list[str]) -> list[list[float]]
        """
        results: list[list[float] | None] = [None] * len(texts)
        miss_indices: list[int] = []
        miss_texts: list[str] = []

        for i, text in enumerate(texts):
            key = self.key_for(model_id, text)
            cached = self.get(key)
            if cached is not None:
                results[i] = cached
            else:
                miss_indices.append(i)
                miss_texts.append(text)

        if miss_texts:
            new_vectors = embed_fn(miss_texts)
            for idx, vector in zip(miss_indices, new_vectors):
                key = self.key_for(model_id, texts[idx])
                self.put(key, model_id, vector)
                results[idx] = vector

        return results  # type: ignore[return-value]
