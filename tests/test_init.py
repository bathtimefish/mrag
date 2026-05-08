"""mrag init command tests."""
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mrag.cli import app
from mrag.config.project import load_project_config
from mrag.config.profile import load_profile

runner = CliRunner()


def test_init_from_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--name", "my-kb", "--yes"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    # project is created as a subdirectory
    project_dir = tmp_path / "my-kb"

    assert (project_dir / "mrag.yaml").exists()
    cfg = load_project_config(project_dir)
    assert cfg.project.name == "my-kb"
    assert cfg.knowledge_id == "kb_my_kb"

    assert (project_dir / "profiles" / "default.yaml").exists()
    profile = load_profile("default", project_dir)
    assert profile.name == "default"
    assert profile.chunking.strategy == "recursive"

    assert (project_dir / "mrag.db").exists()

    assert (project_dir / "data" / "documents").is_dir()
    assert (project_dir / "profiles").is_dir()
    assert (project_dir / "cache" / "embeddings").is_dir()
    assert (project_dir / "qdrant").is_dir()
    assert (project_dir / "logs").is_dir()


def test_init_fails_if_already_initialized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--name", "my-kb", "--yes"])
    result = runner.invoke(app, ["init", "--name", "my-kb", "--yes"])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_init_force_reinitializes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--name", "my-kb", "--yes"])
    result = runner.invoke(app, ["init", "--name", "my-kb", "--yes", "--force"])
    assert result.exit_code == 0


def test_init_default_name_from_dirname(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / "my-project"
    parent.mkdir()
    monkeypatch.chdir(parent)
    result = runner.invoke(app, ["init", "--yes"], catch_exceptions=False)
    assert result.exit_code == 0
    # cwd name is "my-project", so project created at parent / "my-project"
    actual_dir = parent / "my-project"
    cfg = load_project_config(actual_dir)
    assert cfg.project.name == "my-project"


def test_profile_hash_is_stable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--name", "my-kb", "--yes"])
    project_dir = tmp_path / "my-kb"
    profile = load_profile("default", project_dir)
    h1 = profile.compute_hash()
    h2 = profile.compute_hash()
    assert h1 == h2, "profile_hash must be deterministic"
    assert len(h1) == 64, "expected SHA256 hex digest"


def test_profile_hash_changes_on_config_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--name", "my-kb", "--yes"])
    project_dir = tmp_path / "my-kb"
    profile = load_profile("default", project_dir)
    h1 = profile.compute_hash()

    profile.chunking.chunk_size = 1600
    h2 = profile.compute_hash()
    assert h1 != h2, "profile_hash must change when config changes"
