"""Tests for pipeline helper functions — _resolve_parent_ids."""
import uuid

from mrag.core.chunking.base import ChunkData
from mrag.core.indexing.pipeline import _resolve_parent_ids


def _child(hint: str) -> ChunkData:
    return ChunkData(content="child", chunk_index=0, chunk_type="child", parent_chunk_id=hint)


def _parent(hint: str) -> ChunkData:
    return ChunkData(
        content="parent", chunk_index=0, chunk_type="parent",
        metadata={"_parent_id_hint": hint},
    )


def _regular() -> ChunkData:
    return ChunkData(content="chunk", chunk_index=0, chunk_type="chunk")


class TestResolveParentIds:
    def test_resolves_child_parent_chunk_id(self):
        hint = str(uuid.uuid4())
        real_id = str(uuid.uuid4())
        parent = _parent(hint)
        child = _child(hint)
        _resolve_parent_ids([parent, child], [real_id, str(uuid.uuid4())])
        assert child.parent_chunk_id == real_id

    def test_removes_hint_from_parent_metadata(self):
        hint = str(uuid.uuid4())
        parent = _parent(hint)
        child = _child(hint)
        _resolve_parent_ids([parent, child], [str(uuid.uuid4()), str(uuid.uuid4())])
        assert "_parent_id_hint" not in parent.metadata

    def test_regular_chunks_unaffected(self):
        chunk = _regular()
        real_id = str(uuid.uuid4())
        _resolve_parent_ids([chunk], [real_id])
        assert chunk.chunk_type == "chunk"
        assert chunk.parent_chunk_id is None

    def test_multiple_parents_resolve_independently(self):
        hint_a = str(uuid.uuid4())
        hint_b = str(uuid.uuid4())
        real_a = str(uuid.uuid4())
        real_b = str(uuid.uuid4())

        pa = _parent(hint_a)
        ca = _child(hint_a)
        pb = _parent(hint_b)
        cb = _child(hint_b)

        id_list = [real_a, str(uuid.uuid4()), real_b, str(uuid.uuid4())]
        _resolve_parent_ids([pa, ca, pb, cb], id_list)

        assert ca.parent_chunk_id == real_a
        assert cb.parent_chunk_id == real_b
