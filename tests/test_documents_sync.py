"""`mrag documents sync` and `mrag documents rebind` through the CLI.

The planner's decisions are checked table by table in ``test_sync_plan.py``;
these tests follow a project through the situations the planner describes and
check what the commands write, report and exit with.
"""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mrag.cli import app
from mrag.core.ingestion.source_identity import root_key
from mrag.db.connection import find_db, open_connection

runner = CliRunner()


def _init_project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--name", "sync-kb", "--non-interactive"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    project = tmp_path / "sync-kb"
    monkeypatch.chdir(project)
    return project


def _invoke(args: list[str]) -> tuple[int, dict]:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    lines = [line for line in result.output.splitlines() if line.startswith("{")]
    assert lines, result.output
    return result.exit_code, json.loads(lines[-1])


def _documents(project: Path) -> dict[str, dict]:
    connection = open_connection(find_db(project))
    try:
        rows = connection.execute("SELECT id, source_identity, filename, file_hash FROM documents").fetchall()
        return {row["id"]: dict(row) for row in rows}
    finally:
        connection.close()


def _query(project: Path, sql: str, *params) -> list[tuple]:
    connection = open_connection(find_db(project))
    try:
        return [tuple(row) for row in connection.execute(sql, params).fetchall()]
    finally:
        connection.close()


def _by_action(report: dict, action: str) -> list[dict]:
    return [item for item in report["items"] if item["action"] == action]


def _write_corpus(root: Path) -> None:
    (root / "guides").mkdir(parents=True)
    (root / "guides" / "install.md").write_text("# Install\n\nRun the installer.\n", encoding="utf-8")
    (root / "guides" / "usage.md").write_text("# Usage\n\nUse it.\n", encoding="utf-8")
    (root / "notes.txt").write_text("plain notes\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# sync: plan and apply
# ---------------------------------------------------------------------------


def test_plan_of_an_untracked_directory_adds_everything_and_writes_nothing(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")
    (project / "corpus" / "photo.png").write_bytes(b"\x89PNG")

    code, report = _invoke(["documents", "sync", "corpus"])

    assert code == 0, report
    assert report["command"] == "documents sync"
    assert report["status"] == "success"
    assert report["applied"] is False
    assert report["directory_binding"] == "project_relative"
    assert report["root"] == "corpus"
    assert report["summary"]["add"] == 3
    assert report["summary"]["unsupported"] == 1
    assert {item["status"] for item in _by_action(report, "add")} == {"planned"}
    assert sorted(item["source_identity"] for item in _by_action(report, "add")) == [
        "corpus/guides/install.md",
        "corpus/guides/usage.md",
        "corpus/notes.txt",
    ]
    assert report["audit_log"] is None
    assert _documents(project) == {}
    assert not (project / "logs").exists() or not list((project / "logs").glob("*documents-sync*"))


def test_apply_adds_then_a_second_run_is_all_noop(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")

    code, report = _invoke(["documents", "sync", "corpus", "--apply"])

    assert code == 0, report
    assert report["applied"] is True
    assert report["summary"]["add"] == 3
    assert {item["status"] for item in _by_action(report, "add")} == {"applied"}
    assert all(item["document_id"] for item in _by_action(report, "add"))
    assert report["audit_log"].startswith("logs/") and report["audit_log"].endswith("-documents-sync.json")
    logged = json.loads((project / report["audit_log"]).read_text(encoding="utf-8"))
    assert logged["summary"] == report["summary"]
    assert len(_documents(project)) == 3

    code, again = _invoke(["documents", "sync", "corpus"])

    assert code == 0, again
    assert again["summary"]["noop"] == 3
    assert again["summary"]["add"] == 0


def test_a_changed_file_is_an_update_that_keeps_the_document_id(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")
    _invoke(["documents", "sync", "corpus", "--apply"])
    before = _documents(project)
    (project / "corpus" / "notes.txt").write_text("plain notes, revised\n", encoding="utf-8")

    code, report = _invoke(["documents", "sync", "corpus", "--apply"])

    assert code == 0, report
    updates = _by_action(report, "update")
    assert [item["source_identity"] for item in updates] == ["corpus/notes.txt"]
    assert updates[0]["status"] == "applied"
    after = _documents(project)
    assert set(after) == set(before)
    notes_id = updates[0]["document_id"]
    assert after[notes_id]["file_hash"] != before[notes_id]["file_hash"]
    assert report["summary"]["noop"] == 2


def test_a_file_moved_within_the_directory_keeps_its_document(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")
    _invoke(["documents", "sync", "corpus", "--apply"])
    before = _documents(project)
    (project / "corpus" / "archive").mkdir()
    (project / "corpus" / "notes.txt").rename(project / "corpus" / "archive" / "old-notes.txt")

    code, plan = _invoke(["documents", "sync", "corpus"])
    assert code == 0, plan
    moves = _by_action(plan, "move")
    assert len(moves) == 1
    assert moves[0]["previous_identity"] == "corpus/notes.txt"
    assert moves[0]["source_identity"] == "corpus/archive/old-notes.txt"
    assert plan["summary"]["exclude"] == 0

    code, report = _invoke(["documents", "sync", "corpus", "--apply"])

    assert code == 0, report
    after = _documents(project)
    assert set(after) == set(before)
    moved_id = moves[0]["document_id"]
    assert after[moved_id]["source_identity"] == "corpus/archive/old-notes.txt"
    assert after[moved_id]["filename"] == "old-notes.txt"


def test_a_missing_file_is_excluded_by_the_sync_and_restored_when_it_returns(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")
    _invoke(["documents", "sync", "corpus", "--apply"])
    notes = project / "corpus" / "notes.txt"
    content = notes.read_text(encoding="utf-8")
    notes.unlink()

    code, report = _invoke(["documents", "sync", "corpus", "--apply"])

    assert code == 0, report
    excluded = _by_action(report, "exclude")
    assert [item["source_identity"] for item in excluded] == ["corpus/notes.txt"]
    assert excluded[0]["reason"] == "file_missing"
    rules = _query(project, "SELECT document_id, profile_name, origin, reason, revoked_at FROM document_exclusions")
    assert rules == [(excluded[0]["document_id"], None, "sync", "source file missing at documents sync", None)]
    # A document the sync excluded is not found again as missing: nothing to do.
    code, settled = _invoke(["documents", "sync", "corpus"])
    assert settled["summary"]["exclude"] == 0
    assert settled["summary"]["noop"] == 3

    notes.write_text(content, encoding="utf-8")
    code, restored = _invoke(["documents", "sync", "corpus", "--apply"])

    assert code == 0, restored
    assert [item["source_identity"] for item in _by_action(restored, "restore")] == ["corpus/notes.txt"]
    assert _query(project, "SELECT COUNT(*) FROM document_exclusions WHERE revoked_at IS NULL") == [(0,)]


def test_a_persons_exclusion_is_never_lifted_by_the_sync(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")
    _, added = _invoke(["documents", "sync", "corpus", "--apply"])
    notes_id = next(item["document_id"] for item in added["items"] if item["source_identity"] == "corpus/notes.txt")
    result = runner.invoke(app, ["exclusions", "add", "--document-id", notes_id, "--reason", "not for this kb", "--force"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    code, report = _invoke(["documents", "sync", "corpus", "--apply"])

    assert code == 0, report
    assert report["summary"]["restore"] == 0
    assert report["summary"]["noop"] == 3
    assert _query(project, "SELECT origin FROM document_exclusions WHERE revoked_at IS NULL") == [("user",)]


def test_a_persons_exclusion_travels_with_a_moved_document(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")
    _, added = _invoke(["documents", "sync", "corpus", "--apply"])
    notes_id = next(item["document_id"] for item in added["items"] if item["source_identity"] == "corpus/notes.txt")
    result = runner.invoke(app, ["exclusions", "add", "--document-id", notes_id, "--force"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    (project / "corpus" / "notes.txt").rename(project / "corpus" / "renamed.txt")

    code, report = _invoke(["documents", "sync", "corpus", "--apply"])

    # The person excluded the document, not its path: the one-to-one move
    # carries the document to the new path and the exclusion stays as it is.
    assert code == 0, report
    moves = _by_action(report, "move")
    assert len(moves) == 1 and moves[0]["document_id"] == notes_id
    assert _documents(project)[notes_id]["source_identity"] == "corpus/renamed.txt"
    assert _query(project, "SELECT origin FROM document_exclusions WHERE revoked_at IS NULL") == [("user",)]


def test_two_files_with_the_same_content_report_a_duplicate(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")
    _invoke(["documents", "sync", "corpus", "--apply"])
    (project / "corpus" / "copy.txt").write_text("plain notes\n", encoding="utf-8")

    code, report = _invoke(["documents", "sync", "corpus", "--apply"])

    assert code == 0, report
    duplicates = _by_action(report, "duplicate")
    assert [item["source_identity"] for item in duplicates] == ["corpus/copy.txt"]
    assert duplicates[0]["status"] == "reported"
    assert duplicates[0]["reason"] == "matches_document"
    assert len(_documents(project)) == 3


def test_a_file_that_moved_out_of_the_scanned_directory_is_out_of_scope(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")
    _invoke(["documents", "sync", "corpus", "--apply"])

    code, report = _invoke(["documents", "sync", "corpus", "--exclude", "notes.txt"])

    assert code == 0, report
    assert report["summary"]["out_of_scope"] == 1
    assert report["summary"]["exclude"] == 0
    assert report["summary"]["noop"] == 2


def test_the_data_and_identities_directories_are_refused(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    (project / "identities").mkdir(exist_ok=True)

    code, report = _invoke(["documents", "sync", "data"])
    assert code == 2
    assert report["status"] == "error"
    assert report["error"]["code"] == "sync_directory_is_project_data"

    code, report = _invoke(["documents", "sync", "identities"])
    assert code == 2
    assert report["error"]["code"] == "sync_directory_is_reserved"

    code, report = _invoke(["documents", "sync", "nowhere"])
    assert code == 2
    assert report["error"]["code"] == "sync_directory_not_found"

    (project / "one.md").write_text("one\n", encoding="utf-8")
    code, report = _invoke(["documents", "sync", "one.md"])
    assert code == 2
    assert report["error"]["code"] == "sync_directory_not_a_directory"


def test_profile_without_index_is_a_usage_error(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    (project / "corpus").mkdir()

    code, report = _invoke(["documents", "sync", "corpus", "--profile", "default"])

    assert code == 2
    assert report["error"]["code"] == "profile_requires_index"


# ---------------------------------------------------------------------------
# sync: external directories and root ancestors
# ---------------------------------------------------------------------------


def test_an_external_directory_binds_under_its_root_and_records_the_ancestors(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    external = tmp_path / "shared" / "corpus"
    _write_corpus(external)

    code, report = _invoke(["documents", "sync", str(external), "--apply"])

    assert code == 0, report
    assert report["directory_binding"] == "external_root"
    assert report["root"] == "corpus"
    key = root_key(external.resolve())
    identities = sorted(item["source_identity"] for item in _by_action(report, "add"))
    assert identities == [
        f"identities/external/{key}/guides/install.md",
        f"identities/external/{key}/guides/usage.md",
        f"identities/external/{key}/notes.txt",
    ]
    assert _query(project, "SELECT root_key FROM source_roots") == [(key,)]
    recorded = {ancestor for (ancestor,) in _query(project, "SELECT ancestor_key FROM source_root_ancestors WHERE root_key = ?", key)}
    assert root_key(external.resolve().parent) in recorded
    assert root_key(tmp_path.resolve()) in recorded


def test_syncing_a_parent_directory_places_a_registered_root_by_its_ancestors(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    external = tmp_path / "shared" / "corpus"
    _write_corpus(external)
    _invoke(["documents", "sync", str(external), "--apply"])
    (external / "notes.txt").unlink()

    code, report = _invoke(["documents", "sync", str(tmp_path / "shared")])

    assert code == 0, report
    assert report["summary"]["unresolvable_roots"] == 0
    assert report["summary"]["exclude"] == 1
    assert report["summary"]["noop"] == 2


def test_a_root_registered_without_ancestors_is_backfilled_when_found_and_unresolvable_until_then(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    external = tmp_path / "shared" / "corpus"
    _write_corpus(external)
    _invoke(["documents", "sync", str(external), "--apply"])
    key = root_key(external.resolve())
    connection = open_connection(find_db(project))
    try:
        connection.execute("DELETE FROM source_root_ancestors WHERE root_key = ?", (key,))
        connection.commit()
    finally:
        connection.close()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    # A directory holding none of the roots cannot say whether the root lives
    # under it: the root's documents are left alone and the plan says so.
    code, report = _invoke(["documents", "sync", str(elsewhere)])
    assert code == 0, report
    assert report["summary"]["unresolvable_roots"] == 1
    assert report["summary"]["exclude"] == 0

    # Found under its parent, the root is placed and its ancestors recorded.
    code, report = _invoke(["documents", "sync", str(tmp_path / "shared"), "--apply"])
    assert code == 0, report
    assert report["summary"]["unresolvable_roots"] == 0
    assert report["summary"]["noop"] == 3
    assert _query(project, "SELECT COUNT(*) FROM source_root_ancestors WHERE root_key = ?", key)[0][0] >= 2


# ---------------------------------------------------------------------------
# sync: --index and the stop contract
# ---------------------------------------------------------------------------


def test_index_is_skipped_when_no_profile_was_indexed_and_when_nothing_changed(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")

    code, plan = _invoke(["documents", "sync", "corpus", "--index"])
    assert code == 0, plan
    assert plan["index"] == {"status": "skipped", "reason": "no_indexed_profile", "profiles": [], "next_action": None}

    code, report = _invoke(["documents", "sync", "corpus", "--apply", "--index"])
    assert code == 0, report
    assert report["index"]["status"] == "skipped"
    assert report["index"]["reason"] == "no_indexed_profile"

    code, settled = _invoke(["documents", "sync", "corpus", "--apply", "--index", "--profile", "default"])
    assert code == 0, settled
    assert settled["index"] == {"status": "skipped", "reason": "no_changes", "profiles": [], "next_action": None}


def test_index_with_an_unknown_profile_is_a_usage_error(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    (project / "corpus").mkdir()

    code, report = _invoke(["documents", "sync", "corpus", "--index", "--profile", "missing"])

    assert code == 2
    assert report["error"]["code"] == "profile_not_found"


def test_a_stop_signal_ends_the_apply_at_an_item_boundary(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")
    import sys

    documents = sys.modules["mrag.cli.documents"]

    real_apply_one = documents._apply_one
    stopped = {"sent": False}

    def apply_then_signal(item, *args, **kwargs):
        report = real_apply_one(item, *args, **kwargs)
        if not stopped["sent"]:
            stopped["sent"] = True
            os.kill(os.getpid(), signal.SIGTERM)
        return report

    monkeypatch.setattr(documents, "_apply_one", apply_then_signal)

    code, report = _invoke(["documents", "sync", "corpus", "--apply"])

    assert code == 130, report
    assert report["status"] == "cancelled"
    statuses = sorted(item["status"] for item in report["items"])
    assert statuses == ["applied", "cancelled", "cancelled"]
    assert len(_documents(project)) == 1
    assert (project / report["audit_log"]).exists()
    # The handler is gone with the command; the next run finishes the work.
    monkeypatch.setattr(documents, "_apply_one", real_apply_one)
    code, rest = _invoke(["documents", "sync", "corpus", "--apply"])
    assert code == 0, rest
    assert rest["summary"]["add"] == 2
    assert len(_documents(project)) == 3


def test_add_recursive_stops_at_a_file_boundary(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")
    import sys

    add = sys.modules["mrag.cli.add"]

    real_persist = add._persist_candidate
    stopped = {"sent": False}

    def persist_then_signal(*args, **kwargs):
        real_persist(*args, **kwargs)
        if not stopped["sent"]:
            stopped["sent"] = True
            os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(add, "_persist_candidate", persist_then_signal)

    code, report = _invoke(["add", "corpus", "--recursive"])

    assert code == 130, report
    assert report["status"] == "cancelled"
    assert report["summary"] == {"added": 1, "skipped": 0, "failed": 0, "cancelled": 2}
    assert sorted(item["status"] for item in report["items"]) == ["added", "cancelled", "cancelled"]
    assert len(_documents(project)) == 1


# ---------------------------------------------------------------------------
# rebind
# ---------------------------------------------------------------------------


def test_rebind_moves_a_document_to_the_named_file_and_lifts_the_syncs_exclusion(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")
    _, added = _invoke(["documents", "sync", "corpus", "--apply"])
    notes_id = next(item["document_id"] for item in added["items"] if item["source_identity"] == "corpus/notes.txt")
    (project / "elsewhere").mkdir()
    (project / "corpus" / "notes.txt").rename(project / "elsewhere" / "notes-v2.txt")
    (project / "elsewhere" / "notes-v2.txt").write_text("plain notes, rewritten\n", encoding="utf-8")
    _invoke(["documents", "sync", "corpus", "--apply"])  # excludes the missing file
    assert _query(project, "SELECT origin FROM document_exclusions WHERE revoked_at IS NULL") == [("sync",)]

    code, plan = _invoke(["documents", "rebind", notes_id, "elsewhere/notes-v2.txt"])
    assert code == 0, plan
    assert plan["command"] == "documents rebind"
    assert plan["status"] == "planned"
    assert plan["action"] == "rebind"
    assert plan["new_revision"] is True
    assert plan["moves_identity"] is True
    assert plan["revoke_exclusions"] == 1
    assert _documents(project)[notes_id]["source_identity"] == "corpus/notes.txt"

    code, report = _invoke(["documents", "rebind", notes_id, "elsewhere/notes-v2.txt", "--apply"])

    assert code == 0, report
    assert report["status"] == "applied"
    document = _documents(project)[notes_id]
    assert document["source_identity"] == "elsewhere/notes-v2.txt"
    assert document["filename"] == "notes-v2.txt"
    assert document["file_hash"] == report["content_hash"]
    assert _query(project, "SELECT COUNT(*) FROM document_exclusions WHERE revoked_at IS NULL") == [(0,)]
    assert report["audit_log"].endswith("-documents-rebind.json")
    assert (project / report["audit_log"]).exists()


def test_rebind_refuses_a_taken_path_and_content_another_document_holds(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")
    _, added = _invoke(["documents", "sync", "corpus", "--apply"])
    ids = {item["source_identity"]: item["document_id"] for item in added["items"]}

    code, report = _invoke(["documents", "rebind", ids["corpus/notes.txt"], "corpus/guides/usage.md"])
    assert code == 1
    assert report["error"]["code"] == "rebind_target_taken"
    assert report["error"]["held_by"] == ids["corpus/guides/usage.md"]

    (project / "corpus" / "usage-copy.md").write_text("# Usage\n\nUse it.\n", encoding="utf-8")
    code, report = _invoke(["documents", "rebind", ids["corpus/notes.txt"], "corpus/usage-copy.md"])
    assert code == 1
    assert report["error"]["code"] == "rebind_content_held_by_other_document"
    assert report["error"]["held_by"] == ids["corpus/guides/usage.md"]

    code, report = _invoke(["documents", "rebind", "no-such-document", "corpus/notes.txt"])
    assert code == 1
    assert report["error"]["code"] == "document_not_found"


def test_rebind_usage_errors_exit_2(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")
    _, added = _invoke(["documents", "sync", "corpus", "--apply"])
    notes_id = next(item["document_id"] for item in added["items"] if item["source_identity"] == "corpus/notes.txt")
    (project / "corpus" / "photo.png").write_bytes(b"\x89PNG")

    code, report = _invoke(["documents", "rebind", notes_id, "corpus/missing.txt"])
    assert code == 2 and report["error"]["code"] == "rebind_file_not_found"

    code, report = _invoke(["documents", "rebind", notes_id, "corpus"])
    assert code == 2 and report["error"]["code"] == "rebind_source_not_a_file"

    code, report = _invoke(["documents", "rebind", notes_id, "corpus/photo.png"])
    assert code == 2 and report["error"]["code"] == "rebind_source_unsupported"

    (project / "data" / "inside.md").write_text("inside\n", encoding="utf-8")
    code, report = _invoke(["documents", "rebind", notes_id, "data/inside.md"])
    assert code == 2 and report["error"]["code"] == "rebind_file_is_project_data"


def test_rebind_to_the_documents_own_file_is_a_noop_and_to_same_content_elsewhere_an_identity_move(tmp_path: Path, monkeypatch):
    project = _init_project(tmp_path, monkeypatch)
    _write_corpus(project / "corpus")
    _, added = _invoke(["documents", "sync", "corpus", "--apply"])
    notes_id = next(item["document_id"] for item in added["items"] if item["source_identity"] == "corpus/notes.txt")

    code, plan = _invoke(["documents", "rebind", notes_id, "corpus/notes.txt"])
    assert code == 0 and plan["action"] == "noop"

    code, report = _invoke(["documents", "rebind", notes_id, "corpus/notes.txt", "--apply"])
    assert code == 0 and report["status"] == "unchanged"

    (project / "corpus" / "notes.txt").rename(project / "corpus" / "moved.txt")
    code, report = _invoke(["documents", "rebind", notes_id, "corpus/moved.txt", "--apply"])
    assert code == 0, report
    assert report["action"] == "rebind"
    assert report["new_revision"] is False
    assert _documents(project)[notes_id]["source_identity"] == "corpus/moved.txt"
