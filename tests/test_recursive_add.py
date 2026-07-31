import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mrag.cli import app
from mrag.core.ingestion.directory import scan_directory
from mrag.db.connection import find_db, open_connection

runner = CliRunner()


def _init_project(tmp_path: Path) -> Path:
    result = runner.invoke(app, ["init", "--name", "recursive-kb", "--non-interactive"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return tmp_path / "recursive-kb"


def _document_count(project: Path) -> int:
    connection = open_connection(find_db(project))
    try:
        return connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    finally:
        connection.close()


def test_scan_filters_hidden_ignore_unicode_and_windows_separator(tmp_path: Path):
    project = tmp_path / "project"
    source = project / "source"
    (project / "data").mkdir(parents=True)
    (source / "docs").mkdir(parents=True)
    (source / ".hidden").mkdir()
    (source / "日本語").mkdir()
    (source / "docs" / "keep.md").write_text("keep", encoding="utf-8")
    (source / "docs" / "drop.md").write_text("drop", encoding="utf-8")
    (source / ".hidden" / "secret.md").write_text("secret", encoding="utf-8")
    (source / "日本語" / "資料.md").write_text("資料", encoding="utf-8")
    (source / ".mragignore").write_text("docs/*.md\n!docs/keep.md\n", encoding="utf-8")

    scan = scan_directory(
        project,
        source,
        include=[r"**\*.md"],
        exclude=["docs/drop.md"],
    )

    assert [candidate.relative_path for candidate in scan.candidates] == [
        "docs/keep.md",
        "日本語/資料.md",
    ]
    assert scan.issues == []


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires platform privileges on Windows")
def test_scan_symlink_policy_deduplicates_targets_and_reports_cycle(tmp_path: Path):
    project = tmp_path / "project"
    source = project / "source"
    (project / "data").mkdir(parents=True)
    (source / "real").mkdir(parents=True)
    (source / "real" / "notes.txt").write_text("notes", encoding="utf-8")
    (source / "alias.txt").symlink_to(source / "real" / "notes.txt")
    (source / "real" / "cycle").symlink_to(source, target_is_directory=True)

    without_follow = scan_directory(project, source)
    assert [candidate.relative_path for candidate in without_follow.candidates] == ["real/notes.txt"]

    with_follow = scan_directory(project, source, follow_symlinks=True)
    assert len(with_follow.candidates) == 1
    assert any(issue.code == "directory_symlink_cycle" for issue in with_follow.issues)


def test_recursive_dry_run_requires_opt_in_and_does_not_mutate_catalog(tmp_path: Path):
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.chdir(tmp_path)
        project = _init_project(tmp_path)
        source = project / "source"
        source.mkdir()
        (source / "zeta.txt").write_text("zeta", encoding="utf-8")
        (source / "alpha.md").write_text("alpha", encoding="utf-8")
        monkeypatch.chdir(project)

        rejected = runner.invoke(app, ["add", "source", "--json"])
        assert rejected.exit_code == 2
        assert json.loads(rejected.stdout)["error"]["code"] == "directory_requires_recursive"

        result = runner.invoke(
            app,
            ["add", "source", "--recursive", "--dry-run", "--include", "**/*.md", "--json"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, result.output
        report = json.loads(result.stdout)
        assert report["dry_run"] is True
        assert report["summary"] == {"added": 0, "skipped": 0, "failed": 0}
        assert [(item["source"], item["status"]) for item in report["items"]] == [("alpha.md", "planned")]
        assert _document_count(project) == 0


def test_recursive_partial_and_strict_exit_codes_preserve_successes(tmp_path: Path):
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.chdir(tmp_path)
        project = _init_project(tmp_path)
        source = project / "source"
        source.mkdir()
        (source / "good.txt").write_text("good", encoding="utf-8")
        (source / "unsupported.bin").write_bytes(b"bad")
        monkeypatch.chdir(project)

        partial = runner.invoke(app, ["add", "source", "--recursive", "--json"], catch_exceptions=False)
        assert partial.exit_code == 3
        report = json.loads(partial.stdout)
        assert report["status"] == "partial"
        assert report["summary"] == {"added": 1, "skipped": 0, "failed": 1}
        assert [item["source"] for item in report["items"]] == ["good.txt", "unsupported.bin"]
        assert _document_count(project) == 1

        strict = runner.invoke(
            app,
            ["add", "source", "--recursive", "--strict", "--json"],
            catch_exceptions=False,
        )
        assert strict.exit_code == 1
        strict_report = json.loads(strict.stdout)
        assert strict_report["summary"] == {"added": 0, "skipped": 1, "failed": 1}
        assert _document_count(project) == 1


def test_recursive_project_root_never_reingests_data(tmp_path: Path):
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.chdir(tmp_path)
        project = _init_project(tmp_path)
        (project / "notes.txt").write_text("notes", encoding="utf-8")
        data_fixture = project / "data" / "preexisting"
        data_fixture.mkdir()
        (data_fixture / "copied.txt").write_text("skip", encoding="utf-8")
        monkeypatch.chdir(project)

        result = runner.invoke(
            app,
            [
                "add",
                ".",
                "--recursive",
                "--include",
                "**/*.txt",
                "--exclude",
                "profiles/",
                "--json",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, result.output
        report = json.loads(result.stdout)
        assert [item["source"] for item in report["items"]] == ["notes.txt"]
        assert _document_count(project) == 1


def test_recursive_rejects_conversion_required_sources_without_blocking_plain_text(tmp_path: Path):
    """A tree mixing PDF with Markdown ingests the Markdown and reports the PDF."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.chdir(tmp_path)
        project = _init_project(tmp_path)
        source = project / "source"
        source.mkdir()
        (source / "notes.md").write_text("# notes", encoding="utf-8")
        (source / "manual.pdf").write_bytes(b"%PDF-1.4 not really a pdf")
        monkeypatch.chdir(project)

        result = runner.invoke(app, ["add", "source", "--recursive", "--json"], catch_exceptions=False)

        assert result.exit_code == 3, result.output
        report = json.loads(result.stdout)
        assert report["summary"] == {"added": 1, "skipped": 0, "failed": 1}
        items = {item["source"]: item for item in report["items"]}
        assert items["notes.md"]["status"] == "added"
        assert items["manual.pdf"]["status"] == "failed"
        assert "requires external conversion to Markdown" in items["manual.pdf"]["error"]["message"]
        assert _document_count(project) == 1


def test_recursive_dry_run_reports_conversion_required_sources(tmp_path: Path):
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.chdir(tmp_path)
        project = _init_project(tmp_path)
        source = project / "source"
        source.mkdir()
        (source / "manual.pdf").write_bytes(b"%PDF-1.4 not really a pdf")
        monkeypatch.chdir(project)

        result = runner.invoke(
            app,
            ["add", "source", "--recursive", "--dry-run", "--json"],
            catch_exceptions=False,
        )

        assert result.exit_code == 1, result.output
        report = json.loads(result.stdout)
        item = report["items"][0]
        assert item["status"] == "failed"
        assert item["error"]["code"] == "unsupported_source"
        assert "requires external conversion to Markdown" in item["error"]["message"]
