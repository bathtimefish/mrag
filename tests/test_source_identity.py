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


def _project(tmp_path, monkeypatch):
    import importlib
    from typer.testing import CliRunner
    from mrag.cli import app

    init_mod = importlib.import_module("mrag.cli.init")
    monkeypatch.setattr(init_mod, "detect_best_tokenizer", lambda: ("trigram", None))
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", "--name", "kb", "--non-interactive"]).exit_code == 0
    project = tmp_path / "kb"
    monkeypatch.chdir(project)

    def add(*args):
        result = runner.invoke(app, ["add", *map(str, args), "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)["items"]

    return project, add


def _documents(project):
    conn = open_connection(project / "mrag.db")
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, filename, source_identity, file_hash FROM documents ORDER BY filename"
        )]
    finally:
        conn.close()


def _strip_to_pre_identity_catalog(project):
    """Rewrite the catalog into the shape releases before source identity left."""
    with sqlite3.connect(project / "mrag.db") as conn:
        conn.execute("DROP INDEX uq_documents_source_identity")
        conn.execute("ALTER TABLE documents DROP COLUMN source_identity")
        conn.execute("DROP TABLE source_roots")
        conn.execute("DROP TABLE catalog_settings")


def test_changed_source_keeps_id_and_equal_content_elsewhere_is_a_duplicate(tmp_path, monkeypatch):
    project, add = _project(tmp_path, monkeypatch)
    outside = tmp_path / "corpus"
    outside.mkdir()
    first = outside / "a.txt"
    second = outside / "b.txt"
    first.write_text("same", encoding="utf-8")
    second.write_text("same", encoding="utf-8")
    items = add(outside, "--recursive")
    assert [i["status"] for i in items] == ["added", "skipped_duplicate"]
    assert items[1]["document_id"] == items[0]["document_id"]
    # A single-file add of the other path is the same duplicate.
    assert add(second)[0]["status"] == "skipped_duplicate"
    [before] = _documents(project)

    first.write_text("changed", encoding="utf-8")
    assert add(first)[0]["status"] == "added"
    [after] = _documents(project)
    assert after["id"] == before["id"]
    assert after["source_identity"] == before["source_identity"]
    assert after["source_identity"].startswith("external/")


def test_readding_after_the_identity_migration_does_not_duplicate(tmp_path, monkeypatch):
    project, add = _project(tmp_path, monkeypatch)
    corpus = project / "sources"
    corpus.mkdir()
    (corpus / "a.txt").write_text("alpha", encoding="utf-8")
    (corpus / "b.md").write_text("# beta", encoding="utf-8")
    add(corpus, "--recursive")
    ids = {d["id"] for d in _documents(project)}
    _strip_to_pre_identity_catalog(project)

    recursive = add(corpus, "--recursive")
    single = add(corpus / "a.txt")
    assert {i["status"] for i in recursive + single} == {"skipped_duplicate"}
    after = _documents(project)
    assert {d["id"] for d in after} == ids
    assert {d["source_identity"] for d in after} == {f"legacy/v1/{i}" for i in ids}


def test_force_on_a_content_match_reextracts_without_rebinding(tmp_path, monkeypatch):
    project, add = _project(tmp_path, monkeypatch)
    (project / "a.txt").write_text("same", encoding="utf-8")
    (project / "copy.txt").write_text("same", encoding="utf-8")
    [original] = add(project / "a.txt")
    [forced] = add(project / "copy.txt", "--force")
    assert forced["status"] == "added"
    assert forced["document_id"] == original["document_id"]
    [row] = _documents(project)
    assert (row["filename"], row["source_identity"]) == ("a.txt", "a.txt")


def test_add_refuses_a_catalog_recorded_under_another_scheme(tmp_path, monkeypatch):
    project, add = _project(tmp_path, monkeypatch)
    (project / "a.txt").write_text("alpha", encoding="utf-8")
    add(project / "a.txt")
    with sqlite3.connect(project / "mrag.db") as conn:
        conn.execute("UPDATE catalog_settings SET value = '2' WHERE key = 'source_identity_scheme'")
    (project / "b.txt").write_text("beta", encoding="utf-8")
    from typer.testing import CliRunner
    from mrag.cli import app

    result = CliRunner().invoke(app, ["add", str(project / "b.txt")])
    assert result.exit_code != 0
    assert "scheme 2" in result.output
    assert [d["filename"] for d in _documents(project)] == ["a.txt"]


def test_list_status_is_the_stored_extraction_status(tmp_path, monkeypatch):
    from mrag.core.ingestion.inventory import list_document_rows

    project, add = _project(tmp_path, monkeypatch)
    (project / "a.txt").write_text("alpha", encoding="utf-8")
    add(project / "a.txt")
    conn = open_connection(project / "mrag.db")
    try:
        [row] = list_document_rows(conn)
    finally:
        conn.close()
    assert (row["status"], row["source_status"], row["index_status"]) == ("extracted", "ready", "not_indexed")
