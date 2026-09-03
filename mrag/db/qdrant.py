import hashlib
import re
import uuid
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
)


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _identity_fingerprint(*parts: str) -> str:
    """Deterministic 8-hex-char suffix distinguishing raw identities that
    ``normalize_name`` can collapse onto the same slug (e.g. "kb-1", "kb 1",
    and "kb_1" all normalize to "kb_1", as does "kb!!1" or "kb.1"). Joins
    parts with the unit-separator control character (0x1F, never valid in
    these identifiers) before hashing so distinct part boundaries can't
    themselves collide, e.g. ("a", "bc") vs ("ab", "c").
    """
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:8]


def legacy_collection_name(kb_id: str, profile_name: str, model_normalized: str) -> str:
    """The pre-0.24.0 collection name: the normalized slugs with no fingerprint.

    Kept only so ``mrag.db.qdrant_migrate`` can find collections that a project
    indexed before 0.24.0 still holds its vectors in. Never write to this name.
    """
    kb = normalize_name(kb_id)
    profile = normalize_name(profile_name)
    model = normalize_name(model_normalized)
    return f"mrag_{kb}_{profile}_{model}"


def collection_name(kb_id: str, profile_name: str, model_normalized: str) -> str:
    """Derives a stable, collision-resistant Qdrant collection name.

    ``normalize_name`` alone is lossy: distinct knowledge-base IDs or profile
    names can normalize to the same slug (see ``_identity_fingerprint``), and
    a name collision would silently mix unrelated vector data into one
    collection. The trailing fingerprint, derived from the raw (pre-
    normalization) inputs, makes that practically impossible while keeping
    the human-readable prefix for debugging.

    Changing this format is a breaking change for every existing deployment,
    ``local`` mode included: previously created collections no longer match.
    Collections named under the pre-0.24.0 scheme are migrated on first use by
    ``mrag.db.qdrant_migrate.resolve_collection``; any future rename needs the
    same treatment.
    """
    kb = normalize_name(kb_id)
    profile = normalize_name(profile_name)
    model = normalize_name(model_normalized)
    fingerprint = _identity_fingerprint(kb_id, profile_name, model_normalized)
    return f"mrag_{kb}_{profile}_{model}_{fingerprint}"


def make_client(
    mode: str = "server",
    host: str = "localhost",
    port: int = 6333,
    path: Path | None = None,
) -> QdrantClient:
    if mode == "local":
        qdrant_path = path or Path("./qdrant")
        return QdrantClient(path=str(qdrant_path))
    try:
        client = QdrantClient(host=host, port=port)
        client.get_collections()
        return client
    except Exception as e:
        raise ConnectionError(
            f"Cannot connect to Qdrant at {host}:{port}. "
            "Is Qdrant running? (docker run -p 6333:6333 qdrant/qdrant)"
        ) from e


def ensure_collection(
    client: QdrantClient,
    col_name: str,
    dimension: int,
    distance: Distance = Distance.COSINE,
) -> None:
    """Creates the collection if missing; otherwise verifies its existing
    vector schema matches what this profile/model expects.

    A pre-existing collection under this name is not automatically safe to
    reuse: it may have been built for a different embedding model or
    distance metric (for example after a `col_name` collision, or a
    manually reused name). Silently reusing it would mix incompatible
    vectors into the same collection and corrupt search results, so a
    mismatch raises ValueError instead.
    """
    existing = {c.name for c in client.get_collections().collections}
    if col_name not in existing:
        client.create_collection(
            collection_name=col_name,
            vectors_config=VectorParams(size=dimension, distance=distance),
        )
        return

    info = client.get_collection(col_name)
    vectors_config = info.config.params.vectors
    if not isinstance(vectors_config, VectorParams):
        raise ValueError(
            f"Qdrant collection '{col_name}' already exists with an incompatible "
            "vector configuration (expected a single unnamed vector; found named "
            "vectors instead). It was likely created by different tooling. Remove "
            "it intentionally, or use a different knowledge base/profile."
        )
    if vectors_config.size != dimension or vectors_config.distance != distance:
        raise ValueError(
            f"Qdrant collection '{col_name}' already exists with a different "
            f"vector schema (size={vectors_config.size}, distance={vectors_config.distance}) "
            f"than requested (size={dimension}, distance={distance}). This usually means "
            "a different embedding model or distance metric is now configured for this "
            "knowledge base/profile. Remove the collection intentionally before "
            "reindexing, or use a different knowledge base/profile."
        )


def upsert_points(
    client: QdrantClient,
    col_name: str,
    points: list[dict[str, Any]],
) -> None:
    """
    points: list of {"id": str (UUID), "vector": list[float], "payload": dict}
    """
    qdrant_points = [
        PointStruct(
            id=str(p["id"]),
            vector=p["vector"],
            payload=p.get("payload", {}),
        )
        for p in points
    ]
    client.upsert(collection_name=col_name, points=qdrant_points)


def search(
    client: QdrantClient,
    col_name: str,
    vector: list[float],
    top_k: int = 10,
    excluded_document_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    query_filter = None
    if excluded_document_ids:
        from qdrant_client.models import FieldCondition, Filter, MatchAny

        query_filter = Filter(
            must_not=[
                FieldCondition(
                    key="document_id",
                    match=MatchAny(any=sorted(excluded_document_ids)),
                )
            ]
        )
    kwargs: dict[str, Any] = {
        "collection_name": col_name,
        "query": vector,
        "limit": top_k,
        "with_payload": True,
    }
    if query_filter is not None:
        kwargs["query_filter"] = query_filter
    result = client.query_points(**kwargs)
    return [
        {"id": str(h.id), "score": h.score, "payload": h.payload}
        for h in result.points
    ]


def delete_points(
    client: QdrantClient,
    col_name: str,
    point_ids: list[str],
) -> None:
    from qdrant_client.models import PointIdsList
    client.delete(
        collection_name=col_name,
        points_selector=PointIdsList(points=point_ids),
    )


def list_point_ids(
    client: QdrantClient,
    col_name: str,
    batch_size: int = 1000,
) -> set[str]:
    """Return every point ID currently stored in a collection.

    Point IDs are random UUIDs rather than content-derived, so a point whose
    chunk row has been deleted cannot be located from SQLite at all — the only
    way to find it is to enumerate what the collection actually holds and
    subtract the live mapping. `reconcile_profile_vectors` uses this to reclaim
    points orphaned by earlier runs.

    A missing collection yields an empty set rather than raising: nothing is
    stored, so nothing can be orphaned.
    """
    existing = {c.name for c in client.get_collections().collections}
    if col_name not in existing:
        return set()

    ids: set[str] = set()
    offset: Any = None
    while True:
        points, offset = client.scroll(
            collection_name=col_name,
            limit=batch_size,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        ids.update(str(point.id) for point in points)
        if offset is None:
            return ids
