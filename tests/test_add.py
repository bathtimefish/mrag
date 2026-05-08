import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mrag.cli import app
from mrag.db.connection import find_db, open_connection

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_project(parent_dir: Path, name: str = "test-kb") -> Path:
    """Run mrag init and return the project subdirectory."""
    result = runner.invoke(app, ["init", "--name", name, "--yes"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return parent_dir / name


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# mrag add — happy path
# ---------------------------------------------------------------------------

def test_add_txt_creates_document_record(tmp_path: Path, sample_txt: Path):
    """mrag add should insert a row in documents with status='extracted'."""
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)

        result = runner.invoke(app, ["add", str(sample_txt)], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "Added" in result.output or "document_id" in result.output

        db = find_db(project_dir)
        conn = open_connection(db)
        rows = conn.execute("SELECT * FROM documents").fetchall()
        conn.close()

        assert len(rows) == 1
        row = dict(rows[0])
        assert row["status"] == "extracted"
        assert row["source_type"] == "txt"
        assert row["file_hash"] == _sha256(sample_txt.read_bytes())


def test_add_md_creates_document_record(tmp_path: Path, sample_md: Path):
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)

        result = runner.invoke(app, ["add", str(sample_md)], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        db = find_db(project_dir)
        conn = open_connection(db)
        row = dict(conn.execute("SELECT * FROM documents").fetchone())
        conn.close()

        assert row["status"] == "extracted"
        assert row["source_type"] == "md"


def test_add_creates_file_structure(tmp_path: Path, sample_txt: Path):
    """After mrag add, original, extracted.md, extracted.txt, extraction_meta.json must exist."""
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)

        runner.invoke(app, ["add", str(sample_txt)], catch_exceptions=False)

        db = find_db(project_dir)
        conn = open_connection(db)
        row = dict(conn.execute("SELECT * FROM documents").fetchone())
        conn.close()

        doc_id = row["id"]
        doc_dir = project_dir / "data" / "documents" / doc_id

        assert (doc_dir / "original.txt").exists()
        assert (doc_dir / "extracted.txt").exists()
        assert (doc_dir / "extracted.md").exists()
        assert (doc_dir / "extraction_meta.json").exists()


def test_add_extracted_paths_recorded_in_db(tmp_path: Path, sample_txt: Path):
    """extracted_markdown_path and extracted_text_path must be recorded in DB."""
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)

        runner.invoke(app, ["add", str(sample_txt)], catch_exceptions=False)

        db = find_db(project_dir)
        conn = open_connection(db)
        row = dict(conn.execute("SELECT * FROM documents").fetchone())
        conn.close()

        assert row["extracted_text_path"] is not None
        assert row["extracted_markdown_path"] is not None
        assert "extracted.txt" in row["extracted_text_path"]
        assert "extracted.md" in row["extracted_markdown_path"]


def test_add_extraction_meta_json_structure(tmp_path: Path, sample_txt: Path):
    """extraction_meta.json must contain expected keys."""
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)

        runner.invoke(app, ["add", str(sample_txt)], catch_exceptions=False)

        db = find_db(project_dir)
        conn = open_connection(db)
        row = dict(conn.execute("SELECT * FROM documents").fetchone())
        conn.close()

        meta_path = project_dir / row["extraction_meta_path"]
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        assert "provider" in meta
        assert "source_type" in meta
        assert "created_at" in meta


# ---------------------------------------------------------------------------
# mrag add — duplicate detection
# ---------------------------------------------------------------------------

def test_add_duplicate_skips_by_default(tmp_path: Path, sample_txt: Path):
    """Adding the same file twice without --force should print Skipped and not add a second row."""
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)

        runner.invoke(app, ["add", str(sample_txt)], catch_exceptions=False)
        result2 = runner.invoke(app, ["add", str(sample_txt)], catch_exceptions=False)

        assert result2.exit_code == 0
        assert "Skipped" in result2.output

        db = find_db(project_dir)
        conn = open_connection(db)
        count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        conn.close()
        assert count == 1


def test_add_force_replaces_existing(tmp_path: Path, sample_txt: Path):
    """--force should replace the existing record and still result in exactly one row."""
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)

        runner.invoke(app, ["add", str(sample_txt)], catch_exceptions=False)
        result2 = runner.invoke(app, ["add", "--force", str(sample_txt)], catch_exceptions=False)

        assert result2.exit_code == 0, result2.output

        db = find_db(project_dir)
        conn = open_connection(db)
        count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        conn.close()
        assert count == 1


# ---------------------------------------------------------------------------
# mrag add — error cases
# ---------------------------------------------------------------------------

def test_add_missing_file_exits_nonzero(tmp_path: Path):
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)

        result = runner.invoke(app, ["add", "nonexistent.txt"])
        assert result.exit_code != 0


def test_add_without_init_exits_nonzero(tmp_path: Path, sample_txt: Path):
    """Running mrag add before mrag init should fail gracefully."""
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)

        result = runner.invoke(app, ["add", str(sample_txt)])
        assert result.exit_code != 0
        assert "Error" in result.output


# ---------------------------------------------------------------------------
# mrag extract — dry-run
# ---------------------------------------------------------------------------

def test_extract_outputs_content(tmp_path: Path, sample_txt: Path):
    """mrag extract should print content to stdout without touching any DB."""
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)

        result = runner.invoke(app, ["extract", str(sample_txt)], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Hello, world!" in result.output


def test_extract_does_not_write_db(tmp_path: Path, sample_txt: Path):
    """mrag extract must not create or modify mrag.db."""
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)

        runner.invoke(app, ["extract", str(sample_txt)], catch_exceptions=False)
        assert not (tmp_path / "mrag.db").exists()


def test_extract_saves_to_file(tmp_path: Path, sample_txt: Path):
    """mrag extract --out should write the extracted content to a file."""
    out_file = tmp_path / "out.txt"
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)

        result = runner.invoke(
            app,
            ["extract", str(sample_txt), "--out", str(out_file)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert out_file.exists()
        assert "Hello, world!" in out_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# mrag show-extracted / export-extracted
# ---------------------------------------------------------------------------

def test_show_extracted_prints_content(tmp_path: Path, sample_txt: Path):
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)

        add_result = runner.invoke(app, ["add", str(sample_txt)], catch_exceptions=False)
        doc_id = None
        for line in add_result.output.splitlines():
            if "document_id:" in line:
                doc_id = line.split("document_id:")[-1].strip()
                break
        assert doc_id is not None, f"document_id not found in output: {add_result.output}"

        result = runner.invoke(app, ["show-extracted", doc_id], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Hello, world!" in result.output


def test_export_extracted_writes_file(tmp_path: Path, sample_txt: Path):
    out_file = tmp_path / "exported.txt"
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)

        add_result = runner.invoke(app, ["add", str(sample_txt)], catch_exceptions=False)
        doc_id = None
        for line in add_result.output.splitlines():
            if "document_id:" in line:
                doc_id = line.split("document_id:")[-1].strip()
                break
        assert doc_id is not None

        result = runner.invoke(
            app,
            ["export-extracted", doc_id, "--out", str(out_file)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert out_file.exists()
        assert "Hello, world!" in out_file.read_text(encoding="utf-8")


def test_show_extracted_bad_id_exits_nonzero(tmp_path: Path):
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)

        result = runner.invoke(app, ["show-extracted", "nonexistent-id"])
        assert result.exit_code != 0
