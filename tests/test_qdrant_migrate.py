"""Tests for the migration of Qdrant collections named under the pre-0.24.0 scheme.

These run against a real local-mode QdrantClient: the bug they guard against
(0.24.0 said local mode was unaffected by the collection rename; it was not)
only shows up with real collections on disk.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from typer.testing import CliRunner

from mrag.cli import app
from mrag.config.profile import load_profile
from mrag.config.project import load_project_config
from mrag.core.indexing.pipeline import run_index
from mrag.core.retrieval.runner import run_retrieval
from mrag.db.connection import find_db, open_connection
from mrag.db.qdrant import collection_name, legacy_collection_name, normalize_name
from mrag.db.qdrant_migrate import migrate_legacy_collection, resolve_collection
from tests.test_indexing import FakeEmbeddingProvider

runner = CliRunner()

# Retrieval derives the collection name from the profile's `embedding.model`,
# while indexing derives it from the provider; the fake must agree with the
# profile `mrag init` writes (bge-m3) for search to look where index wrote.
MODEL = normalize_name("bge-m3")


class ProfileModelFake(FakeEmbeddingProvider):
    def get_normalized_name(self) -> str:
        return MODEL


def _collections(client: QdrantClient) -> set[str]:
    return {c.name for c in client.get_collections().collections}


def _collection_column(db_path: Path) -> set[str]:
    conn = open_connection(db_path)
    try:
        rows = conn.execute("SELECT DISTINCT qdrant_collection FROM chunk_variants").fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def _demote_to_legacy(client: QdrantClient, db_path: Path, target: str, legacy: str) -> int:
    """Rewrite a freshly built index so it looks like one built before 0.24.0."""
    info = client.get_collection(target)
    client.create_collection(legacy, vectors_config=info.config.params.vectors)
    points, _ = client.scroll(target, limit=10_000, with_payload=True, with_vectors=True)
    client.upsert(
        legacy,
        points=[PointStruct(id=p.id, vector=p.vector, payload=p.payload) for p in points],
    )
    client.delete_collection(target)
    conn = open_connection(db_path)
    try:
        conn.execute(
            "UPDATE chunk_variants SET qdrant_collection=? WHERE qdrant_collection=?",
            (legacy, target),
        )
        conn.commit()
    finally:
        conn.close()
    return len(points)


@pytest.fixture
def legacy_project(tmp_path: Path, sample_md: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--name", "legacy-kb", "--non-interactive"], catch_exceptions=False)
    project_dir = tmp_path / "legacy-kb"
    monkeypatch.chdir(project_dir)
    runner.invoke(app, ["add", str(sample_md)], catch_exceptions=False)

    config = load_project_config(project_dir)
    profile = load_profile("default", project_dir)
    assert normalize_name(profile.embedding.model) == MODEL
    provider = ProfileModelFake()
    client = QdrantClient(path=str(project_dir / "qdrant"))
    run_index(
        project_dir=project_dir,
        config=config,
        profile_name="default",
        embedding_provider=provider,
        qdrant_client=client,
    )
    db_path = find_db(project_dir)
    target = collection_name(config.knowledge_id, "default", MODEL)
    legacy = legacy_collection_name(config.knowledge_id, "default", MODEL)
    point_count = _demote_to_legacy(client, db_path, target, legacy)
    assert point_count > 0
    assert _collections(client) == {legacy}
    assert _collection_column(db_path) == {legacy}

    yield SimpleNamespace(
        project_dir=project_dir,
        config=config,
        provider=provider,
        client=client,
        db_path=db_path,
        target=target,
        legacy=legacy,
        point_count=point_count,
    )
    client.close()


def _migrate(p, **kwargs):
    return migrate_legacy_collection(
        p.client, p.db_path, p.config.knowledge_id, "default", MODEL, **kwargs
    )


def test_moves_points_repoints_rows_and_drops_legacy(legacy_project):
    p = legacy_project

    report = _migrate(p)

    assert report is not None
    assert (report.legacy, report.target) == (p.legacy, p.target)
    assert report.moved == p.point_count
    assert report.orphaned == 0
    assert report.dropped is True
    assert _collections(p.client) == {p.target}
    assert p.client.count(p.target, exact=True).count == p.point_count
    assert _collection_column(p.db_path) == {p.target}


def test_nothing_to_do_when_no_legacy_collection_exists(legacy_project):
    p = legacy_project
    _migrate(p)

    assert _migrate(p) is None
    assert _collections(p.client) == {p.target}


def test_merges_into_a_target_created_after_the_rename(legacy_project):
    # A post-0.24.0 `mrag index` on a legacy project created the fingerprinted
    # collection for whatever it indexed next, leaving older documents behind
    # in the legacy collection. The migration must merge, not create afresh.
    p = legacy_project
    info = p.client.get_collection(p.legacy)
    p.client.create_collection(p.target, vectors_config=info.config.params.vectors)

    report = _migrate(p)

    assert report is not None and report.moved == p.point_count and report.dropped
    assert _collections(p.client) == {p.target}
    assert p.client.count(p.target, exact=True).count == p.point_count


def test_leaves_points_the_project_does_not_reference(legacy_project):
    # On a shared Qdrant server the legacy collection can hold another
    # knowledge base's points (that collision is why 0.24.0 renamed
    # collections). Those must survive, and so must the collection.
    p = legacy_project
    dim = p.provider.get_dimension()
    p.client.upsert(
        p.legacy,
        points=[PointStruct(id="00000000-0000-0000-0000-00000000beef", vector=[0.5] * dim,
                            payload={"knowledge_id": "kb_other", "profile_name": "default"})],
    )

    report = _migrate(p, drop_unreferenced=False)

    assert report is not None
    assert report.moved == p.point_count
    assert report.orphaned == 1
    assert report.dropped is False
    assert _collections(p.client) == {p.legacy, p.target}
    assert p.client.count(p.legacy, exact=True).count == 1


def test_drops_an_unreferenced_legacy_collection_in_local_mode(legacy_project):
    # Local mode: the directory belongs to exactly one project, so a legacy
    # collection no row references is dead weight (e.g. `mrag reindex` already
    # rebuilt under the new name) and is removed.
    p = legacy_project
    _migrate(p)
    dim = p.provider.get_dimension()
    p.client.create_collection(
        p.legacy, vectors_config=p.client.get_collection(p.target).config.params.vectors
    )
    p.client.upsert(
        p.legacy,
        points=[PointStruct(id="00000000-0000-0000-0000-00000000dead", vector=[0.5] * dim, payload={})],
    )

    assert _migrate(p, drop_unreferenced=False) is None
    assert p.legacy in _collections(p.client)

    report = _migrate(p, drop_unreferenced=True)

    assert report is not None and report.moved == 0 and report.orphaned == 1 and report.dropped
    assert _collections(p.client) == {p.target}


def test_resolve_collection_reports_once_and_returns_current_name(legacy_project):
    p = legacy_project
    messages: list[str] = []

    first = resolve_collection(
        p.client, db_path=p.db_path, config=p.config, profile_name="default",
        model_normalized=MODEL, notify=messages.append,
    )
    second = resolve_collection(
        p.client, db_path=p.db_path, config=p.config, profile_name="default",
        model_normalized=MODEL, notify=messages.append,
    )

    assert first == second == p.target
    assert len(messages) == 1
    assert "pre-0.24.0" in messages[0] and p.legacy in messages[0] and p.target in messages[0]


def test_vector_search_on_a_pre_0_24_index_works_without_reindex(legacy_project):
    # The user-facing promise: a project indexed before 0.24.0 searches after
    # upgrading. Before this migration existed, this raised
    # "Collection ... not found".
    p = legacy_project
    warnings: list[str] = []

    run = run_retrieval(
        query="markdown document",
        project_dir=p.project_dir,
        config=p.config,
        strategy="vector",
        top_k=3,
        embedding_provider=p.provider,
        qdrant_client=p.client,
        warn=warnings.append,
    )

    assert run.results, "vector search returned nothing from the migrated collection"
    assert any("Migrated" in w for w in warnings)
    assert _collections(p.client) == {p.target}
    assert _collection_column(p.db_path) == {p.target}
