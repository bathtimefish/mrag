"""A profile the project does not store gets one answer from every command (1.7.4).

Until 1.7.3 the commands that take ``--profile`` disagreed: ``inspect document``
and ``inspect chunks`` took an unknown name as a profile that had indexed
nothing and exited 0, ``inspect sections`` said the profile kept no heading
path, and ``search``, ``index`` and ``reindex`` exited 1 naming the file that
was not there. They now refuse it alike — ``profile_not_found``, exit 2, with
the profiles the project does store — before anything is read or built.

The catalog still answers first in ``inspect``: a profile that indexed the
document is read even after its file is removed, and a stored profile that has
indexed nothing is answered as before.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mrag.cli import app
from tests.inspect_fixtures import (
    init_inspect_project,
    open_seed_conn,
    seed_basic_indexed_document,
    seed_chunk,
    seed_index,
    seed_profile,
)

runner = CliRunner()

REFUSAL = "profile_not_found: no profile named 'absent' is stored in the project"


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    project_dir, db_path = init_inspect_project(tmp_path, monkeypatch)
    seed_basic_indexed_document(db_path, n_chunks=2)
    return project_dir, db_path


def _said(result) -> str:
    # `index` and `reindex` print their errors to stdout, the others to stderr.
    return " ".join((result.stdout + result.stderr).split())


@pytest.mark.parametrize(
    "arguments",
    [
        ["inspect", "document", "d1"],
        ["inspect", "chunks", "d1"],
        ["inspect", "sections", "d1"],
        ["search", "Hello"],
        ["index"],
        ["reindex"],
    ],
    ids=["inspect-document", "inspect-chunks", "inspect-sections", "search", "index", "reindex"],
)
def test_a_profile_the_project_does_not_store_is_refused_by_name(project, arguments):
    result = runner.invoke(app, [*arguments, "--profile", "absent"])

    assert result.exit_code == 2, _said(result)
    said = _said(result)
    assert REFUSAL in said
    assert "(available: default)" in said
    # Refused before anything was read or built.
    assert "Indexing profile" not in said
    assert "no section structure" not in said
    assert "total chunks" not in said


def test_the_refusal_names_every_stored_profile(project):
    project_dir, _ = project
    default = (project_dir / "profiles" / "default.yaml").read_text(encoding="utf-8")
    (project_dir / "profiles" / "other.yaml").write_text(
        default.replace("name: default", "name: other", 1), encoding="utf-8"
    )

    result = runner.invoke(app, ["inspect", "chunks", "d1", "--profile", "absent"])

    assert result.exit_code == 2
    assert "(available: default, other)" in _said(result)


def test_the_catalog_answers_for_a_profile_whose_file_was_removed(project):
    _, db_path = project
    conn = open_seed_conn(db_path)
    with conn:
        seed_profile(conn, "retired")
        seed_index(conn, "idx_d1_retired", profile_name="retired")
        seed_chunk(
            conn, "c-retired", profile_name="retired",
            metadata={"heading_path": ["Ch1"]},
        )
    conn.close()

    document = runner.invoke(
        app, ["inspect", "document", "d1", "--profile", "retired", "--json"],
        catch_exceptions=False,
    )
    assert document.exit_code == 0, document.stderr
    assert [p["name"] for p in json.loads(document.stdout)["profiles"]] == ["retired"]

    chunks = runner.invoke(
        app, ["inspect", "chunks", "d1", "--profile", "retired", "--json"],
        catch_exceptions=False,
    )
    assert chunks.exit_code == 0, chunks.stderr
    assert json.loads(chunks.stdout)["total"] == 1

    sections = runner.invoke(
        app, ["inspect", "sections", "d1", "--profile", "retired"],
        catch_exceptions=False,
    )
    assert sections.exit_code == 0, sections.stderr


def test_a_stored_profile_that_indexed_nothing_is_answered_as_before(project):
    project_dir, _ = project
    default = (project_dir / "profiles" / "default.yaml").read_text(encoding="utf-8")
    (project_dir / "profiles" / "other.yaml").write_text(
        default.replace("name: default", "name: other", 1), encoding="utf-8"
    )

    document = runner.invoke(
        app, ["inspect", "document", "d1", "--profile", "other"],
        catch_exceptions=False,
    )
    assert document.exit_code == 0
    assert "No profiles have indexed this document." in document.stdout

    chunks = runner.invoke(
        app, ["inspect", "chunks", "d1", "--profile", "other", "--json"],
        catch_exceptions=False,
    )
    assert chunks.exit_code == 0
    assert json.loads(chunks.stdout)["total"] == 0
