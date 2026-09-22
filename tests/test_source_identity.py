"""The OSS side of the source-identity contract shared with Plus."""

import json
import sqlite3
from pathlib import Path

import pytest

from mrag.core.ingestion.source_identity import _relative, binding_status, display_name, root_key
from mrag.db.connection import db_connection, open_connection


def test_plus_source_identity_golden_fixture():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "source_identity_golden.json").read_text())
    for source, expected in fixture["canonical_paths"].items():
        assert _relative(source) == expected
    for source in fixture["invalid_paths"]:
        with pytest.raises(ValueError):
            _relative(source)
    external = fixture["external"]
    assert root_key(Path(external["root"])) == external["root_key"]
    assert f"external/{external['root_key']}/{external['relative']}" == external["identity"]
    assert display_name(external["identity"], {external["root_key"]: "corpus"}) == external["display_name"]
    assert binding_status(external["identity"]) == "external_root"
    assert binding_status(fixture["legacy"]["identity"]) == "legacy_unbound"


def test_legacy_catalog_backfill_preserves_document_id(tmp_path):
    db_path = tmp_path / "mrag.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, filename TEXT, file_hash TEXT, status TEXT, created_at TEXT, updated_at TEXT)")
        conn.execute("INSERT INTO documents VALUES ('doc-0001', 'old.md', 'hash', 'extracted', 'time', 'time')")
    # Document lists may read legacy rows without changing the catalog.
    with open_connection(db_path) as read_conn:
        assert "source_identity" not in {r[1] for r in read_conn.execute("PRAGMA table_info(documents)")}
    with db_connection(db_path) as conn:
        row = conn.execute("SELECT id, source_identity FROM documents").fetchone()
        assert dict(row) == {"id": "doc-0001", "source_identity": "legacy/v1/doc-0001"}
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='uq_documents_source_identity'").fetchone()


def test_changed_source_keeps_id_and_distinct_equal_content_stays_distinct(tmp_path, monkeypatch):
    import importlib
    from typer.testing import CliRunner
    from mrag.cli import app

    init_mod = importlib.import_module("mrag.cli.init")
    monkeypatch.setattr(init_mod, "detect_best_tokenizer", lambda: ("trigram", None))
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", "--name", "kb", "--non-interactive"]).exit_code == 0
    project = tmp_path / "kb"
    outside = tmp_path / "corpus"
    outside.mkdir()
    first = outside / "a.txt"
    second = outside / "b.txt"
    first.write_text("same", encoding="utf-8")
    second.write_text("same", encoding="utf-8")
    monkeypatch.chdir(project)
    assert runner.invoke(app, ["add", str(outside), "--recursive"]).exit_code == 0
    conn = open_connection(project / "mrag.db")
    try:
        before = {r["filename"]: r["id"] for r in conn.execute("SELECT filename, id FROM documents")}
    finally:
        conn.close()
    assert len(set(before.values())) == 2
    first.write_text("changed", encoding="utf-8")
    assert runner.invoke(app, ["add", str(first)]).exit_code == 0
    conn = open_connection(project / "mrag.db")
    try:
        after = {r["filename"]: (r["id"], r["source_identity"]) for r in conn.execute("SELECT filename, id, source_identity FROM documents")}
    finally:
        conn.close()
    assert after["a.txt"][0] == before["a.txt"]
    assert after["a.txt"][1].startswith("external/")
    assert after["b.txt"][0] == before["b.txt"]
