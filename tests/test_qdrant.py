from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from qdrant_client.models import Distance, VectorParams

from mrag.db.qdrant import _identity_fingerprint, collection_name, ensure_collection


# ---------------------------------------------------------------------------
# collection_name
# ---------------------------------------------------------------------------


def test_collection_name_disambiguates_normalize_name_collisions() -> None:
    # "kb-1", "kb 1", and "kb_1" all normalize to the same "kb_1" slug; the
    # collection name must still differ so unrelated knowledge bases can
    # never land in the same Qdrant collection.
    variants = ["kb-1", "kb 1", "kb_1", "kb!!1", "kb.1"]
    names = {collection_name(kb_id, "default", "bge_m3") for kb_id in variants}

    assert len(names) == len(variants)


def test_collection_name_is_stable_for_identical_inputs() -> None:
    first = collection_name("kb_example", "default", "bge_m3")
    second = collection_name("kb_example", "default", "bge_m3")

    assert first == second


def test_collection_name_keeps_readable_prefix_and_fingerprint_suffix() -> None:
    name = collection_name("kb_example", "default", "bge_m3")

    assert name.startswith("mrag_kb_example_default_bge_m3_")
    fingerprint = name.removeprefix("mrag_kb_example_default_bge_m3_")
    assert len(fingerprint) == 8
    assert all(ch in "0123456789abcdef" for ch in fingerprint)


def test_identity_fingerprint_is_boundary_safe() -> None:
    # Naive concatenation would let ("ab", "c") and ("a", "bc") collide
    # ("ab" + "c" == "a" + "bc" == "abc"); the unit-separator join must keep
    # the fingerprint distinct.
    assert _identity_fingerprint("ab", "c") != _identity_fingerprint("a", "bc")


# ---------------------------------------------------------------------------
# ensure_collection
# ---------------------------------------------------------------------------


def _fake_client(existing_names: list[str] | None = None) -> MagicMock:
    client = MagicMock()
    client.get_collections.return_value.collections = [
        SimpleNamespace(name=name) for name in (existing_names or [])
    ]
    return client


def _with_collection_info(
    client: MagicMock, vectors: VectorParams | dict[str, VectorParams]
) -> None:
    client.get_collection.return_value = SimpleNamespace(
        config=SimpleNamespace(params=SimpleNamespace(vectors=vectors))
    )


def test_ensure_collection_creates_when_missing() -> None:
    client = _fake_client(existing_names=[])

    ensure_collection(client, "mrag_kb_default_bge_m3_deadbeef", 768)

    client.create_collection.assert_called_once_with(
        collection_name="mrag_kb_default_bge_m3_deadbeef",
        vectors_config=VectorParams(size=768, distance=Distance.COSINE),
    )
    client.get_collection.assert_not_called()


def test_ensure_collection_accepts_matching_existing_collection() -> None:
    col_name = "mrag_kb_default_bge_m3_deadbeef"
    client = _fake_client(existing_names=[col_name])
    _with_collection_info(client, VectorParams(size=768, distance=Distance.COSINE))

    ensure_collection(client, col_name, 768)

    client.create_collection.assert_not_called()


def test_ensure_collection_raises_on_dimension_mismatch() -> None:
    col_name = "mrag_kb_default_bge_m3_deadbeef"
    client = _fake_client(existing_names=[col_name])
    _with_collection_info(client, VectorParams(size=384, distance=Distance.COSINE))

    with pytest.raises(ValueError, match="different vector schema"):
        ensure_collection(client, col_name, 768)


def test_ensure_collection_raises_on_distance_mismatch() -> None:
    col_name = "mrag_kb_default_bge_m3_deadbeef"
    client = _fake_client(existing_names=[col_name])
    _with_collection_info(client, VectorParams(size=768, distance=Distance.EUCLID))

    with pytest.raises(ValueError, match="different vector schema"):
        ensure_collection(client, col_name, 768, distance=Distance.COSINE)


def test_ensure_collection_raises_on_named_vector_config() -> None:
    col_name = "mrag_kb_default_bge_m3_deadbeef"
    client = _fake_client(existing_names=[col_name])
    _with_collection_info(client, {"dense": VectorParams(size=768, distance=Distance.COSINE)})

    with pytest.raises(ValueError, match="named vectors"):
        ensure_collection(client, col_name, 768)
