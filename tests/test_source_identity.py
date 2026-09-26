"""The OSS side of the source-identity contract shared with Plus."""

import json
import sqlite3
from pathlib import Path

import pytest

from mrag.core.ingestion.source_identity import (
    RESERVED_NAMESPACE,
    SCHEME_VERSION,
    ReservedPathError,
    binding_status,
    display_name,
    external_identity,
    legacy_identity,
    project_identity,
    read_scheme_one,
    root_key,
)
from mrag.db.connection import db_connection, open_connection


def test_plus_source_identity_golden_fixture():
    """MRAG Plus and OSS return the same identities for one shared fixture.

    MRAG Plus holds the canonical copy (quality/golden/source-identity.json);
    this one is byte-identical, so neither side can drift without a failing test.
    """
    fixture = json.loads((Path(__file__).parent / "fixtures" / "source_identity_golden.json").read_text())
    assert fixture["scheme_version"] == SCHEME_VERSION
    assert fixture["reserved_namespace"] == RESERVED_NAMESPACE
    for source, expected in fixture["canonical_paths"].items():
        assert project_identity(source) == expected
    for source in fixture["invalid_paths"]:
        with pytest.raises(ValueError):
            project_identity(source)
    for source in fixture["reserved_paths"]:
        with pytest.raises(ReservedPathError):
            project_identity(source)

    external = fixture["external"]
    assert root_key(Path(external["root"])) == external["root_key"]
    identity = external_identity(external["root_key"], external["relative"])
    assert identity == external["identity"]
    assert display_name(identity, {external["root_key"]: external["label"]}) == external["display_name"]
    assert display_name(identity, {}) == external["unlabelled_display_name"]
    assert binding_status(identity) == "external_root"

    legacy = fixture["legacy"]
    assert legacy_identity(legacy["document_id"]) == legacy["identity"]
    assert display_name(legacy["identity"], {}) == legacy["display_name"]
    assert binding_status(legacy["identity"]) == "legacy_unbound"

    readings = fixture["scheme_one_readings"]
    roots = {root_key(Path(root)) for root in readings["registered_roots"]}
    for case in readings["cases"]:
        reading = read_scheme_one(case["stored"], readings["document_id"], roots)
        assert (reading.reading, reading.identity, reading.binding) == (
            case["reading"], case["identity"], case["binding"]
        ), case["stored"]


def test_a_project_directory_named_external_or_legacy_is_an_ordinary_path():
    """The scheme-1 defect: these read as external/legacy and `external/notes.md` crashed."""
    for path in ["external/notes.md", "external/sub/notes.md", "legacy/v1/doc-0001"]:
        assert project_identity(path) == path
        assert binding_status(path) == "project_relative"
        assert display_name(path, {"sub": "label"}) == path


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
        assert dict(row) == {"id": "doc-0001", "source_identity": "identities/legacy/v1/doc-0001"}
        # A first assignment is made under the current scheme.
        assert conn.execute("SELECT value FROM catalog_settings").fetchone()[0] == str(SCHEME_VERSION)
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
    assert after["source_identity"].startswith("identities/external/")


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
    assert {d["source_identity"] for d in after} == {f"identities/legacy/v1/{i}" for i in ids}


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
        conn.execute("UPDATE catalog_settings SET value = '3' WHERE key = 'source_identity_scheme'")
    (project / "b.txt").write_text("beta", encoding="utf-8")
    from typer.testing import CliRunner
    from mrag.cli import app

    result = CliRunner().invoke(app, ["add", str(project / "b.txt")])
    assert result.exit_code != 0
    assert "scheme 3" in result.output
    # A newer scheme belongs to a newer release; there is no way forward to name.
    assert "migrate-identities" not in result.output
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


def _to_scheme_one(project):
    """Rewrite a catalog into the shape 1.1.0 left: scheme 1 spellings and record."""
    with sqlite3.connect(project / "mrag.db") as conn:
        conn.execute(
            "UPDATE documents SET source_identity = substr(source_identity, 12) "
            "WHERE source_identity LIKE 'identities/%'"
        )
        conn.execute("UPDATE catalog_settings SET value = '1' WHERE key = 'source_identity_scheme'")


def _catalog(*args):
    from typer.testing import CliRunner
    from mrag.cli import app

    return CliRunner().invoke(app, ["catalog", "migrate-identities", *args])


def test_init_announces_the_reserved_namespace(tmp_path, monkeypatch):
    project, _ = _project(tmp_path, monkeypatch)
    notice = (project / "identities" / "README.md").read_text(encoding="utf-8")
    assert "reserved" in notice


def test_a_source_under_identities_is_refused_before_anything_is_written(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from mrag.cli import app

    project, add = _project(tmp_path, monkeypatch)
    (project / "identities" / "mine.md").write_text("# mine", encoding="utf-8")
    runner = CliRunner()

    single = runner.invoke(app, ["add", "identities/mine.md", "--json"])
    assert single.exit_code == 1
    assert json.loads(single.output)["error"]["code"] == "source_identity_reserved_path"

    rooted = runner.invoke(app, ["add", "identities", "--recursive", "--json"])
    assert json.loads(rooted.output)["items"][0]["error"]["code"] == "directory_scan_failed"

    (project / "kept.md").write_text("# kept", encoding="utf-8")
    items = add(project, "--recursive", "--include", "**/*.md")
    assert [i["source"] for i in items] == ["kept.md"]
    assert _documents(project) and all(
        not d["source_identity"].startswith("identities/") for d in _documents(project)
    )


def test_a_project_directory_named_external_lists_without_failing(tmp_path, monkeypatch):
    """The 1.1.0 crash: `external/notes.md` inside the project broke the whole list."""
    from mrag.core.ingestion.inventory import list_document_rows

    project, add = _project(tmp_path, monkeypatch)
    (project / "external" / "sub").mkdir(parents=True)
    (project / "external" / "notes.md").write_text("# a", encoding="utf-8")
    (project / "external" / "sub" / "notes.md").write_text("# b", encoding="utf-8")
    add(project / "external", "--recursive")
    conn = open_connection(project / "mrag.db")
    try:
        rows = list_document_rows(conn)
    finally:
        conn.close()
    assert {(r["display_name"], r["source_binding_status"]) for r in rows} == {
        ("external/notes.md", "project_relative"),
        ("external/sub/notes.md", "project_relative"),
    }


def test_a_missing_identities_directory_is_restored_by_a_writing_add(tmp_path, monkeypatch):
    import shutil

    project, add = _project(tmp_path, monkeypatch)
    shutil.rmtree(project / "identities")
    (project / "a.md").write_text("# a", encoding="utf-8")
    add(project / "a.md")
    assert (project / "identities" / "README.md").is_file()


def test_an_empty_scheme_one_catalog_is_brought_to_the_current_scheme(tmp_path, monkeypatch):
    project, _ = _project(tmp_path, monkeypatch)
    with sqlite3.connect(project / "mrag.db") as conn:
        conn.execute("UPDATE catalog_settings SET value = '1'")
    with db_connection(project / "mrag.db") as conn:
        assert conn.execute("SELECT value FROM catalog_settings").fetchone()[0] == str(SCHEME_VERSION)


def test_a_scheme_one_catalog_lists_is_refused_by_add_and_migrates_explicitly(tmp_path, monkeypatch):
    from mrag.core.ingestion.inventory import list_document_rows

    project, add = _project(tmp_path, monkeypatch)
    outside = tmp_path / "corpus"
    outside.mkdir()
    (outside / "out.md").write_text("# out", encoding="utf-8")
    (project / "external").mkdir()
    (project / "external" / "in.md").write_text("# in", encoding="utf-8")
    add(outside / "out.md")
    add(project / "external" / "in.md")
    ids = {d["id"] for d in _documents(project)}
    _to_scheme_one(project)

    # Listing an unmigrated catalog works, reads it as the migration will, and
    # reports the stored value.
    conn = open_connection(project / "mrag.db")
    try:
        rows = {r["source_binding_status"]: r for r in list_document_rows(conn)}
    finally:
        conn.close()
    assert rows["external_root"]["source_identity"].startswith("external/")
    assert rows["external_root"]["display_name"] == "corpus/out.md"
    assert rows["project_relative"]["source_identity"] == "external/in.md"

    # add refuses and names the command.
    from typer.testing import CliRunner
    from mrag.cli import app

    (project / "new.md").write_text("# new", encoding="utf-8")
    refused = CliRunner().invoke(app, ["add", "new.md", "--json"])
    assert refused.exit_code == 2
    assert "mrag catalog migrate-identities" in json.loads(refused.output)["error"]["message"]

    planned = _catalog("--dry-run", "--json")
    assert planned.exit_code == 0
    plan = json.loads(planned.output)
    assert (plan["action"], plan["summary"]) == ("planned", {"respelled": 1, "unchanged": 1, "blocked": 0})
    assert {d["source_identity"] for d in _documents(project)} >= {"external/in.md"}

    applied = _catalog("--json")
    assert applied.exit_code == 0
    report = json.loads(applied.output)
    assert report["action"] == "migrated"
    assert (project / report["audit_log"]).is_file()
    after = _documents(project)
    assert {d["id"] for d in after} == ids
    assert {d["source_identity"].split("/")[0] for d in after} == {"identities", "external"}

    again = json.loads(_catalog("--json").output)
    assert (again["action"], again["summary"]["respelled"]) == ("unchanged", 0)
    add(project / "new.md")


def test_a_blocked_migration_lists_every_blocker_and_changes_nothing(tmp_path, monkeypatch):
    project, add = _project(tmp_path, monkeypatch)
    outside = tmp_path / "corpus"
    outside.mkdir()
    (outside / "out.md").write_text("# out", encoding="utf-8")
    add(outside / "out.md")
    (project / "a.md").write_text("# a", encoding="utf-8")
    (project / "b.md").write_text("# b", encoding="utf-8")
    add(project / "a.md")
    add(project / "b.md")
    _to_scheme_one(project)
    with sqlite3.connect(project / "mrag.db") as conn:
        conn.execute("UPDATE documents SET source_identity = 'identities/a.md' WHERE source_identity = 'a.md'")
        conn.execute("UPDATE documents SET source_identity = 'Identities/b.md' WHERE source_identity = 'b.md'")
    before = _documents(project)

    result = _catalog("--json")
    assert result.exit_code == 1
    report = json.loads(result.output)
    assert (report["status"], report["action"]) == ("error", "refused")
    assert sorted(b["source_identity"] for b in report["blockers"]) == ["Identities/b.md", "identities/a.md"]
    assert {b["reason"] for b in report["blockers"]} == {"reserved_path"}
    assert _documents(project) == before
    with sqlite3.connect(project / "mrag.db") as conn:
        assert conn.execute("SELECT value FROM catalog_settings").fetchone()[0] == "1"


def _relations(project):
    with sqlite3.connect(project / "mrag.db") as conn:
        return (
            sorted(conn.execute("SELECT * FROM document_indexes").fetchall()),
            sorted(conn.execute("SELECT * FROM document_exclusions").fetchall()),
            sorted(conn.execute("SELECT id FROM documents").fetchall()),
        )


def test_both_identity_migrations_keep_ids_index_records_and_exclusions(tmp_path, monkeypatch):
    """SPEC-DATA-004: a migration rewrites identities and nothing that points at a document."""
    project, add = _project(tmp_path, monkeypatch)
    outside = tmp_path / "corpus"
    outside.mkdir()
    (outside / "out.md").write_text("# out", encoding="utf-8")
    (project / "a.md").write_text("# a", encoding="utf-8")
    add(outside / "out.md")
    add(project / "a.md")
    with sqlite3.connect(project / "mrag.db") as conn:
        for number, (document_id,) in enumerate(conn.execute("SELECT id FROM documents ORDER BY id").fetchall()):
            conn.execute(
                "INSERT INTO document_indexes (id, knowledge_id, document_id, profile_name, "
                "document_file_hash, extracted_hash, profile_hash, status) "
                "VALUES (?, 'kb', ?, 'default', 'h', 'e', 'p', 'indexed')",
                (f"idx-{number}", document_id),
            )
            conn.execute(
                "INSERT INTO document_exclusions (id, document_id, profile_name, reason, created_at) "
                "VALUES (?, ?, NULL, 'kept', '2026-09-01T00:00:00Z')",
                (f"excl-{number}", document_id),
            )
    before = _relations(project)

    # Scheme 1 -> 2 through the explicit command.
    _to_scheme_one(project)
    assert _catalog("--json").exit_code == 0
    assert _relations(project) == before

    # Pre-identity -> scheme 2 through the connection-time backfill, twice.
    _strip_to_pre_identity_catalog(project)
    for _ in range(2):
        with db_connection(project / "mrag.db") as conn:
            identities = {r[0] for r in conn.execute("SELECT source_identity FROM documents")}
        assert _relations(project) == before
    assert all(identity.startswith("identities/legacy/v1/") for identity in identities)

    # The list says what is known and no more: an unrecoverable path, by ID.
    from mrag.core.ingestion.inventory import list_document_rows

    conn = open_connection(project / "mrag.db")
    try:
        rows = list_document_rows(conn)
    finally:
        conn.close()
    assert {r["source_binding_status"] for r in rows} == {"legacy_unbound"}
    assert all(r["display_name"] == r["source_identity"] == f"identities/legacy/v1/{r['document_id']}"
               for r in rows)
