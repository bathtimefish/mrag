import hashlib
import math
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from numbers import Real
from pathlib import Path
from typing import Sequence

import numpy as np

from mrag.db.connection import db_connection


_CACHE_KEY_DOMAIN = b"mrag.embedding-cache.v2\x00"


class EmbeddingCacheCorruptionError(RuntimeError):
    """Raised when persisted cache metadata or vector bytes are inconsistent."""


def _frame(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(8, "big") + encoded


def _cache_key(model_id: str, content: str) -> str:
    """Return a domain-separated digest with unambiguous component boundaries."""
    digest = hashlib.sha256()
    digest.update(_CACHE_KEY_DOMAIN)
    digest.update(_frame(model_id))
    digest.update(_frame(content))
    return digest.hexdigest()


def _is_cache_key(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EmbeddingCache:
    """File-backed embedding cache stored as .npy files under cache/embeddings/."""

    def __init__(self, cache_dir: Path, db_path: Path) -> None:
        self.cache_dir = cache_dir
        self.db_path = db_path
        self.project_dir = cache_dir.parent.parent
        cache_dir.mkdir(parents=True, exist_ok=True)

    def key_for(self, model_id: str, content: str) -> str:
        return _cache_key(model_id, content)

    def get(self, cache_key: str) -> list[float] | None:
        """Return a validated cached vector or ``None`` on a metadata miss."""
        if not _is_cache_key(cache_key):
            return None
        with db_connection(self.db_path) as conn:
            return self._get(cache_key, conn)

    def _get(
        self, cache_key: str, conn: sqlite3.Connection
    ) -> list[float] | None:
        if not _is_cache_key(cache_key):
            return None
        path = self.cache_dir / f"{cache_key}.npy"
        expected_rel_path = path.relative_to(self.project_dir).as_posix()
        row = conn.execute(
            """SELECT c.embedding_model_id, c.vector_path, m.dimension
               FROM embedding_cache AS c
               LEFT JOIN embedding_models AS m ON m.id = c.embedding_model_id
               WHERE c.cache_key = ?""",
            (cache_key,),
        ).fetchone()

        if row is None:
            return None
        if row["dimension"] is None:
            raise EmbeddingCacheCorruptionError(
                "embedding cache references an unknown model"
            )
        if row["vector_path"] != expected_rel_path:
            raise EmbeddingCacheCorruptionError(
                "embedding cache vector path does not match its cache key"
            )

        try:
            array = np.load(path, allow_pickle=False)
        except (EOFError, OSError, ValueError) as exc:
            raise EmbeddingCacheCorruptionError(
                "embedding cache vector file is missing or unreadable"
            ) from exc

        return _validated_loaded_vector(array, int(row["dimension"]))

    def put(
        self, cache_key: str, model_id: str, vector: Sequence[float]
    ) -> None:
        """Atomically publish one vector and its SQLite metadata."""
        if not _is_cache_key(cache_key):
            raise ValueError("embedding cache key must be a lowercase SHA-256 digest")
        path = self.cache_dir / f"{cache_key}.npy"
        rel_path = path.relative_to(self.project_dir).as_posix()
        array = _validated_vector_for_storage(vector)
        now = _now_iso()

        with db_connection(self.db_path) as conn:
            # Serialize same-key publishers. The row remains invisible until the
            # complete vector has been atomically installed and the transaction
            # commits.
            conn.execute("BEGIN IMMEDIATE")
            model = conn.execute(
                "SELECT dimension FROM embedding_models WHERE id = ?",
                (model_id,),
            ).fetchone()
            if model is None:
                raise ValueError("embedding model must be registered before caching")
            dimension = int(model["dimension"])
            if array.size != dimension:
                raise ValueError(
                    "embedding vector dimension does not match the registered model"
                )

            existing = conn.execute(
                """SELECT embedding_model_id, vector_path
                   FROM embedding_cache WHERE cache_key = ?""",
                (cache_key,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["embedding_model_id"] != model_id
                    or existing["vector_path"] != rel_path
                ):
                    raise EmbeddingCacheCorruptionError(
                        "embedding cache key conflicts with persisted metadata"
                    )
                return

            _atomic_save(path, array)
            conn.execute(
                """INSERT INTO embedding_cache
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

        with db_connection(self.db_path) as conn:
            for i, text in enumerate(texts):
                key = self.key_for(model_id, text)
                cached = self._get(key, conn)
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

    def get_or_embed_with_failures(
        self,
        texts: list[str],
        model_id: str,
        embed_fn_with_failures,
    ) -> tuple[list[list[float] | None], dict[int, str]]:
        """Cache-aware version that tolerates per-input embedding failures.

        Only successful embeddings are cached. Failed indices have None vectors
        in the results and their error messages mapped back to original indices
        in the failures dict.

        embed_fn_with_failures(texts: list[str])
            -> tuple[list[list[float] | None], dict[int, str]]

        Returns:
          (results, failures) where:
            - results[i] is the cached or newly-embedded vector, or None on failure
            - failures[i] is the error message keyed by ORIGINAL `texts` index
        """
        results: list[list[float] | None] = [None] * len(texts)
        miss_indices: list[int] = []
        miss_texts: list[str] = []

        with db_connection(self.db_path) as conn:
            for i, text in enumerate(texts):
                key = self.key_for(model_id, text)
                cached = self._get(key, conn)
                if cached is not None:
                    results[i] = cached
                else:
                    miss_indices.append(i)
                    miss_texts.append(text)

        if not miss_texts:
            return results, {}

        miss_vectors, miss_failures = embed_fn_with_failures(miss_texts)
        failures: dict[int, str] = {}
        for sub_idx, orig_idx in enumerate(miss_indices):
            v = miss_vectors[sub_idx]
            if v is not None:
                key = self.key_for(model_id, texts[orig_idx])
                self.put(key, model_id, v)
                results[orig_idx] = v
            if sub_idx in miss_failures:
                failures[orig_idx] = miss_failures[sub_idx]

        return results, failures


def _validated_vector_for_storage(vector: Sequence[float]) -> np.ndarray:
    if len(vector) == 0:
        raise ValueError("embedding cache vector must not be empty")
    if any(
        isinstance(value, (bool, np.bool_))
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
        for value in vector
    ):
        raise ValueError("embedding cache vector must contain only finite numbers")
    array = np.asarray(vector, dtype=np.float32)
    if array.ndim != 1 or not np.isfinite(array).all():
        raise ValueError(
            "embedding cache vector must be one-dimensional and finite as float32"
        )
    return array


def _validated_loaded_vector(
    array: np.ndarray, expected_dimension: int
) -> list[float]:
    if (
        not isinstance(array, np.ndarray)
        or array.ndim != 1
        or array.dtype.kind != "f"
        or array.dtype.itemsize != 4
    ):
        raise EmbeddingCacheCorruptionError(
            "embedding cache vector has an invalid shape or data type"
        )
    if array.size != expected_dimension:
        raise EmbeddingCacheCorruptionError(
            "embedding cache vector dimension does not match its model"
        )
    if not np.isfinite(array).all():
        raise EmbeddingCacheCorruptionError(
            "embedding cache vector contains non-finite values"
        )
    return array.tolist()


def _atomic_save(path: Path, array: np.ndarray) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            np.save(temporary, array, allow_pickle=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
