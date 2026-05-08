"""
sqlite3.Connection-compatible wrapper around apsw.Connection.

Used when the vaporetto tokenizer is enabled, because Python's stdlib sqlite3
may be compiled with OMIT_LOAD_EXTENSION and therefore cannot load the
vaporetto shared library.  apsw always supports extension loading.

The wrapper exposes:
  - execute(sql, params)  → _Cursor (with fetchall / fetchone / __iter__)
  - executescript(sql)
  - commit() / rollback() / close()
  - __enter__ / __exit__ (context manager)

Row objects returned by execute() support both dict-style (row["col"]) and
positional (row[0]) access, matching sqlite3.Row behaviour.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class _Row(dict):
    """Dict subclass that also supports positional indexing like sqlite3.Row."""

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

    def keys(self):  # type: ignore[override]
        return list(super().keys())


class _Cursor:
    """Minimal cursor-like object returned by ApswConnection.execute()."""

    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows
        self._pos = 0

    def fetchall(self) -> list[_Row]:
        return self._rows

    def fetchone(self) -> _Row | None:
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)

    def __getitem__(self, key):
        return self._rows[key]

    def __len__(self) -> int:
        return len(self._rows)


def _make_row_trace(description: list[tuple]):
    """Return a row-trace function that converts apsw tuples to _Row dicts."""
    col_names = [d[0] for d in description]

    def trace(cursor, row):  # noqa: ARG001
        return _Row(zip(col_names, row))

    return trace


class ApswConnection:
    """
    apsw.Connection wrapped to behave like sqlite3.Connection.
    Loads the given SQLite extension on construction.
    """

    def __init__(
        self,
        db_path: str | Path,
        lib_path: Path,
        entrypoint: str,
    ) -> None:
        import apsw

        self._conn = apsw.Connection(str(db_path))
        self._conn.setbusytimeout(5000)

        # Load extension
        self._conn.enableloadextension(True)
        self._conn.loadextension(str(lib_path), entrypoint)
        self._conn.enableloadextension(False)

        # WAL + FK pragmas (mirrors open_connection())
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def execute(self, sql: str, params: Any = ()) -> _Cursor:
        raw = self._conn.execute(sql, params)
        try:
            description = raw.getdescription()
        except Exception:
            # DML (INSERT/UPDATE/DELETE) has no result-set description.
            # The statement already executed; return an empty cursor.
            return _Cursor([])
        if not description:
            return _Cursor([])
        col_names = [d[0] for d in description]
        rows = [_Row(zip(col_names, row)) for row in raw]
        return _Cursor(rows)

    def executescript(self, sql: str) -> None:
        """Execute a multi-statement SQL script (apsw processes them sequentially)."""
        import apsw
        with self._conn:
            for stmt in _split_statements(sql):
                if stmt.strip():
                    try:
                        self._conn.execute(stmt)
                    except apsw.SQLError:
                        pass  # ignore "no such table" etc. from IF NOT EXISTS patterns

    def commit(self) -> None:
        self._conn.execute("COMMIT")

    def rollback(self) -> None:
        self._conn.execute("ROLLBACK")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ApswConnection":
        self._conn.execute("BEGIN")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None:
            try:
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        else:
            try:
                self._conn.execute("ROLLBACK")
            except Exception:
                pass


def _split_statements(sql: str) -> list[str]:
    """Split a SQL script into individual statements on ';'."""
    return [s.strip() for s in sql.split(";") if s.strip()]
