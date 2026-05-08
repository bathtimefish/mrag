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


def collection_name(kb_id: str, profile_name: str, model_normalized: str) -> str:
    kb = normalize_name(kb_id)
    profile = normalize_name(profile_name)
    model = normalize_name(model_normalized)
    return f"mrag_{kb}_{profile}_{model}"


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
) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if col_name not in existing:
        client.create_collection(
            collection_name=col_name,
            vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
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
) -> list[dict[str, Any]]:
    result = client.query_points(
        collection_name=col_name,
        query=vector,
        limit=top_k,
        with_payload=True,
    )
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
