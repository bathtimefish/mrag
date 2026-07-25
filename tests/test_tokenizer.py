from pathlib import Path

import pytest

from mrag.cli.doctor import _check_vaporetto
from mrag.db import tokenizer


def _write_library(directory: Path, name: str = "libsqlite_vaporetto.so") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    library = directory / name
    library.write_bytes(b"fixture")
    return library


def test_explicit_library_wins_over_ambiguous_standard_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit = _write_library(tmp_path / "explicit")
    standard = tmp_path / "standard"
    _write_library(standard / "sqlite-vaporetto-v1")
    _write_library(standard / "sqlite-vaporetto-v2")
    monkeypatch.setenv("MRAG_VAPORETTO_LIB", str(explicit))
    monkeypatch.setattr(tokenizer, "_DEFAULT_SEARCH_DIRS", [standard])

    assert tokenizer.find_vaporetto_lib() == explicit


def test_single_direct_or_versioned_candidate_is_discovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first"
    direct = _write_library(first)
    monkeypatch.delenv("MRAG_VAPORETTO_LIB", raising=False)
    monkeypatch.setattr(tokenizer, "_DEFAULT_SEARCH_DIRS", [first])
    assert tokenizer.find_vaporetto_lib() == direct

    versioned_root = tmp_path / "versioned"
    versioned = _write_library(versioned_root / "sqlite-vaporetto-v1")
    monkeypatch.setattr(tokenizer, "_DEFAULT_SEARCH_DIRS", [versioned_root])
    assert tokenizer.find_vaporetto_lib() == versioned


def test_standard_directory_precedence_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _write_library(tmp_path / "first")
    _write_library(tmp_path / "second")
    monkeypatch.delenv("MRAG_VAPORETTO_LIB", raising=False)
    monkeypatch.setattr(
        tokenizer,
        "_DEFAULT_SEARCH_DIRS",
        [tmp_path / "first", tmp_path / "second"],
    )

    assert tokenizer.find_vaporetto_lib() == first


def test_multiple_candidates_fail_with_deterministic_explicit_path_guidance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    standard = tmp_path / "standard"
    later = _write_library(standard / "sqlite-vaporetto-v2")
    earlier = _write_library(standard / "sqlite-vaporetto-v1")
    monkeypatch.delenv("MRAG_VAPORETTO_LIB", raising=False)
    monkeypatch.setattr(tokenizer, "_DEFAULT_SEARCH_DIRS", [standard])

    with pytest.raises(tokenizer.VaporettoLibraryAmbiguityError) as captured:
        tokenizer.find_vaporetto_lib()

    message = str(captured.value)
    assert message.index(str(earlier)) < message.index(str(later))
    assert "Set MRAG_VAPORETTO_LIB" in message


def test_doctor_reports_ambiguous_discovery_without_probing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def ambiguous() -> Path:
        raise tokenizer.VaporettoLibraryAmbiguityError("ambiguous fixture")

    monkeypatch.setattr(tokenizer, "find_vaporetto_lib", ambiguous)
    monkeypatch.setattr(
        tokenizer,
        "probe_vaporetto",
        lambda _path: pytest.fail("ambiguous discovery must not be probed"),
    )

    assert _check_vaporetto() == (False, "ambiguous fixture")
