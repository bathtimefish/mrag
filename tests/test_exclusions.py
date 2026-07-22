"""Document exclusion policy, CLI lifecycle, and retrieval safety tests."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from mrag.cli import app
from mrag.config.project import load_project_config
from mrag.core.indexing.pipeline import run_index, write_index_log
from mrag.core.retrieval.keyword import keyword_search
from mrag.core.retrieval.vector import vector_search
from mrag.db.connection import find_db, open_connection
from mrag.db.exclusions import (
    active_document_ids,
    create_exclusion,
    list_exclusions,
)
from tests.test_indexing import FakeEmbeddingProvider, _fake_qdrant_client


runner = CliRunner()


@pytest.fixture
def indexed_project(tmp_path: Path, sample_txt: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(
        app,
        ["init", "--name", "exclusion-kb", "--non-interactive"],
        catch_exceptions=False,
    )
    assert init_result.exit_code == 0, init_result.output
    project_dir = tmp_path / "exclusion-kb"
    monkeypatch.chdir(project_dir)
    add_result = runner.invoke(app, ["add", str(sample_txt)], catch_exceptions=False)
    assert add_result.exit_code == 0, add_result.output

    config = load_project_config(project_dir)
    qdrant = _fake_qdrant_client()
    result = run_index(
        project_dir=project_dir,
        config=config,
        profile_name="default",
        embedding_provider=FakeEmbeddingProvider(),
        qdrant_client=qdrant,
    )
    assert result.indexed == 1

    db_path = find_db(project_dir)
    conn = open_connection(db_path)
    try:
        document = conn.execute(
            "SELECT id, filename, original_path FROM documents LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return SimpleNamespace(
        project_dir=project_dir,
        db_path=db_path,
        config=config,
        qdrant=qdrant,
        document_id=document["id"],
        filename=document["filename"],
        original_path=document["original_path"],
    )


def _patch_qdrant(monkeypatch, client) -> None:
    import mrag.core.exclusions as exclusions_core

    monkeypatch.setattr(exclusions_core, "make_client", lambda **_: client)


def _force_exclude(indexed_project, monkeypatch):
    _patch_qdrant(monkeypatch, indexed_project.qdrant)
    result = runner.invoke(
        app,
        [
            "exclusions",
            "add",
            "--document-id",
            indexed_project.document_id,
            "--reason",
            "obsolete knowledge",
            "--force",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    return list_exclusions(indexed_project.db_path)[0]


def test_exclusion_dry_run_is_non_mutating(indexed_project):
    before = open_connection(indexed_project.db_path)
    try:
        chunk_count = before.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        before.close()

    result = runner.invoke(
        app,
        ["exclusions", "add", "--document-id", indexed_project.document_id],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Dry run" in result.output
    assert "Source files   : retained" in result.output
    assert list_exclusions(indexed_project.db_path) == []
    conn = open_connection(indexed_project.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == chunk_count
    finally:
        conn.close()


def test_force_exclusion_removes_derived_index_but_retains_document(
    indexed_project,
    monkeypatch,
):
    exclusion = _force_exclude(indexed_project, monkeypatch)

    assert exclusion.document_id == indexed_project.document_id
    assert exclusion.profile_name is None
    assert exclusion.reason == "obsolete knowledge"
    assert (indexed_project.project_dir / indexed_project.original_path).is_file()

    conn = open_connection(indexed_project.db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM documents WHERE id = ?",
            (indexed_project.document_id,),
        ).fetchone()[0] == 1
        for table in ("chunks", "chunk_variants", "document_indexes", "fts_chunks"):
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE document_id = ?",
                (indexed_project.document_id,),
            ).fetchone()[0] == 0
    finally:
        conn.close()
    indexed_project.qdrant.delete.assert_called()

    results = keyword_search(
        query_text="Hello",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        top_k=10,
        tokenizer=indexed_project.config.fts_tokenizer,
    )
    assert results == []


def test_excluded_document_is_not_reindexed_or_sent_to_embedding(
    indexed_project,
    monkeypatch,
):
    _force_exclude(indexed_project, monkeypatch)
    provider = FakeEmbeddingProvider()

    result = run_index(
        project_dir=indexed_project.project_dir,
        config=indexed_project.config,
        profile_name="default",
        document_ids=[indexed_project.document_id],
        embedding_provider=provider,
        qdrant_client=_fake_qdrant_client(),
    )

    assert result.indexed == 0
    assert result.excluded == 1
    assert result.excluded_document_ids == [indexed_project.document_id]
    assert provider._call_count == 0
    log_path = indexed_project.project_dir / "logs" / "excluded-index.json"
    write_index_log(result, log_path, command="index", profile_name="default")
    log = json.loads(log_path.read_text(encoding="utf-8"))
    assert log["excluded_count"] == 1
    assert log["excluded_document_ids"] == [indexed_project.document_id]
    conn = open_connection(indexed_project.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    finally:
        conn.close()


def test_restore_requires_explicit_reindex_and_then_document_returns(
    indexed_project,
    monkeypatch,
):
    exclusion = _force_exclude(indexed_project, monkeypatch)

    restore = runner.invoke(
        app,
        ["exclusions", "restore", exclusion.id, "--force"],
        catch_exceptions=False,
    )
    assert restore.exit_code == 0, restore.output
    assert f"mrag index --document-id {indexed_project.document_id}" in restore.output
    assert active_document_ids(indexed_project.db_path, "default") == set()

    conn = open_connection(indexed_project.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    finally:
        conn.close()

    result = run_index(
        project_dir=indexed_project.project_dir,
        config=indexed_project.config,
        profile_name="default",
        document_ids=[indexed_project.document_id],
        embedding_provider=FakeEmbeddingProvider(),
        qdrant_client=_fake_qdrant_client(),
    )
    assert result.indexed == 1
    assert keyword_search(
        query_text="Hello",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        tokenizer=indexed_project.config.fts_tokenizer,
    )


def test_policy_filters_keyword_and_vector_before_physical_cleanup(indexed_project):
    conn = open_connection(indexed_project.db_path)
    try:
        chunk = conn.execute(
            "SELECT id FROM chunks WHERE document_id = ? LIMIT 1",
            (indexed_project.document_id,),
        ).fetchone()
    finally:
        conn.close()
    create_exclusion(
        indexed_project.db_path,
        indexed_project.document_id,
        None,
        "policy-first test",
    )

    assert keyword_search(
        query_text="Hello",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        tokenizer=indexed_project.config.fts_tokenizer,
    ) == []

    hit = MagicMock()
    hit.id = "00000000-0000-0000-0000-000000000001"
    hit.score = 0.9
    hit.payload = {
        "chunk_id": chunk["id"],
        "document_id": indexed_project.document_id,
    }
    qdrant = MagicMock()
    qdrant.query_points.return_value.points = [hit]
    results = vector_search(
        query_text="Hello",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        embedding_provider=FakeEmbeddingProvider(),
        qdrant_client=qdrant,
        col_name="mrag_test",
    )
    assert results == []
    query_filter = qdrant.query_points.call_args.kwargs["query_filter"]
    assert indexed_project.document_id in query_filter.must_not[0].match.any


def test_qdrant_cleanup_failure_is_degraded_but_fail_closed(
    indexed_project,
    monkeypatch,
):
    import mrag.core.exclusions as exclusions_core

    def unavailable(**_):
        raise ConnectionError("offline")

    monkeypatch.setattr(exclusions_core, "make_client", unavailable)
    result = runner.invoke(
        app,
        [
            "exclusions",
            "add",
            "--document-id",
            indexed_project.document_id,
            "--force",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 3
    assert active_document_ids(indexed_project.db_path, "default") == {
        indexed_project.document_id
    }
    conn = open_connection(indexed_project.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM fts_chunks").fetchone()[0] == 0
    finally:
        conn.close()

    exclusion = list_exclusions(indexed_project.db_path)[0]
    blocked_restore = runner.invoke(
        app,
        ["exclusions", "restore", exclusion.id, "--force"],
        catch_exceptions=False,
    )
    assert blocked_restore.exit_code == 3
    assert list_exclusions(indexed_project.db_path)[0].active

    _patch_qdrant(monkeypatch, indexed_project.qdrant)
    retried = runner.invoke(
        app,
        [
            "exclusions",
            "add",
            "--document-id",
            indexed_project.document_id,
            "--force",
        ],
        catch_exceptions=False,
    )
    assert retried.exit_code == 0
    conn = open_connection(indexed_project.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    finally:
        conn.close()


def test_list_json_and_physical_remove_clear_policy(indexed_project, monkeypatch):
    exclusion = _force_exclude(indexed_project, monkeypatch)
    listed = runner.invoke(
        app,
        ["exclusions", "list", "--json"],
        catch_exceptions=False,
    )
    payload = json.loads(listed.stdout)
    assert payload["status"] == "success"
    assert payload["exclusions"][0]["id"] == exclusion.id
    assert payload["exclusions"][0]["filename"] == indexed_project.filename

    removed = runner.invoke(
        app,
        ["remove", indexed_project.document_id, "--force"],
        catch_exceptions=False,
    )
    assert removed.exit_code == 0
    assert list_exclusions(indexed_project.db_path, include_revoked=True) == []


def test_existing_database_gets_additive_exclusion_schema(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sentinel (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    exclusion = create_exclusion(db_path, "legacy-document", None, None)
    assert exclusion.document_id == "legacy-document"
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM document_exclusions"
        ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sentinel").fetchone()[0] == 0
    finally:
        conn.close()


def test_profile_scoped_policy_does_not_hide_other_profiles(indexed_project):
    create_exclusion(
        indexed_project.db_path,
        indexed_project.document_id,
        "other-profile",
        None,
    )
    assert indexed_project.document_id not in active_document_ids(
        indexed_project.db_path, "default"
    )
    assert indexed_project.document_id in active_document_ids(
        indexed_project.db_path, "other-profile"
    )
    assert keyword_search(
        query_text="Hello",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        tokenizer=indexed_project.config.fts_tokenizer,
    )
