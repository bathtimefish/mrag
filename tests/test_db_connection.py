import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import mrag.db.connection as connection
from mrag.db import apsw_compat
from mrag.db.tokenizer import TOKENIZER_TRIGRAM, TOKENIZER_VAPORETTO


def test_vaporetto_connection_fails_before_opening_db_when_library_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "mrag.db"
    monkeypatch.setattr(connection, "find_vaporetto_lib", lambda: None)

    with pytest.raises(
        connection.VaporettoDependencyError,
        match="cannot fall back to trigram",
    ):
        connection.open_fts_connection(db_path, TOKENIZER_VAPORETTO)

    assert not db_path.exists()


def test_vaporetto_connection_reports_missing_apsw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib_path = tmp_path / "libsqlite_vaporetto.so"
    monkeypatch.setattr(connection, "find_vaporetto_lib", lambda: lib_path)

    def missing_apsw(*_args: object) -> None:
        raise ModuleNotFoundError("No module named 'apsw'", name="apsw")

    monkeypatch.setattr(apsw_compat, "ApswConnection", missing_apsw)

    with pytest.raises(
        connection.VaporettoDependencyError,
        match="APSW is not installed",
    ):
        connection.open_fts_connection(
            tmp_path / "mrag.db", TOKENIZER_VAPORETTO
        )


def test_trigram_connection_does_not_probe_for_vaporetto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_probe() -> None:
        raise AssertionError("trigram must not probe for sqlite-vaporetto")

    monkeypatch.setattr(connection, "find_vaporetto_lib", unexpected_probe)

    conn = connection.open_fts_connection(
        tmp_path / "mrag.db", TOKENIZER_TRIGRAM
    )
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


def test_apsw_loader_disables_extension_loading_after_load_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable_calls: list[bool] = []

    class FailingConnection:
        def __init__(self, _db_path: str) -> None:
            pass

        def setbusytimeout(self, _milliseconds: int) -> None:
            pass

        def enableloadextension(self, enabled: bool) -> None:
            enable_calls.append(enabled)

        def loadextension(self, _lib_path: str, _entrypoint: str) -> None:
            raise RuntimeError("simulated extension load failure")

    monkeypatch.setitem(
        sys.modules,
        "apsw",
        SimpleNamespace(Connection=FailingConnection),
    )

    with pytest.raises(RuntimeError, match="simulated extension load failure"):
        apsw_compat.ApswConnection(
            tmp_path / "mrag.db",
            tmp_path / "libsqlite_vaporetto.so",
            "sqlite3_vaporetto_init",
        )

    assert enable_calls == [True, False]
