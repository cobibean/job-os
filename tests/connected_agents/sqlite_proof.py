"""Exact SQLite snapshots and integrity checks for migration acceptance."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class SQLiteIntegrityError(AssertionError):
    pass


class SQLiteSnapshotMismatch(AssertionError):
    """Reports structural coordinates, never potentially sensitive values."""

    def __init__(self, coordinates: tuple[str, ...]) -> None:
        self.coordinates = coordinates
        super().__init__("sqlite_snapshot_mismatch:" + ",".join(coordinates))


@dataclass(frozen=True)
class SQLiteSnapshot:
    schema_version: int
    integrity: tuple[str, ...]
    schema: tuple[tuple[str, str, str, str], ...]
    rows: tuple[tuple[str, tuple[str, ...], tuple[tuple[object, ...], ...]], ...]

    def canonical_json(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "integrity": self.integrity,
                "schema": self.schema,
                "rows": self.rows,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


def _value(value: object) -> object:
    if isinstance(value, bytes):
        return {"blob_sha256": hashlib.sha256(value).hexdigest(), "blob_hex": value.hex()}
    if isinstance(value, (str, int, float)) or value is None:
        return value
    raise TypeError(f"unsupported SQLite value type: {type(value).__name__}")


def snapshot_sqlite(path: Path) -> SQLiteSnapshot:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
        if integrity != ("ok",):
            raise SQLiteIntegrityError("sqlite_integrity_check_failed")
        schema_rows = connection.execute(
            """
            SELECT type, name, tbl_name, COALESCE(sql, '')
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()
        schema = tuple(tuple(str(value) for value in row) for row in schema_rows)
        table_names = [
            str(row[1]) for row in schema_rows if row[0] == "table" and row[1] != "sqlite_sequence"
        ]
        tables: list[tuple[str, tuple[str, ...], tuple[tuple[object, ...], ...]]] = []
        for table_name in sorted(table_names):
            quoted = '"' + table_name.replace('"', '""') + '"'
            columns = tuple(
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({quoted})")
            )
            raw_rows = connection.execute(f"SELECT * FROM {quoted}").fetchall()
            canonical_rows = [tuple(_value(value) for value in row) for row in raw_rows]
            canonical_rows.sort(
                key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":"))
            )
            tables.append((table_name, columns, tuple(canonical_rows)))
        version_row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        return SQLiteSnapshot(
            schema_version=int(version_row[0]),
            integrity=integrity,
            schema=schema,
            rows=tuple(tables),
        )
    finally:
        connection.close()


def assert_exact_snapshot(expected: SQLiteSnapshot, actual: SQLiteSnapshot) -> None:
    coordinates: list[str] = []
    if expected.schema_version != actual.schema_version:
        coordinates.append("schema_version")
    if expected.integrity != actual.integrity:
        coordinates.append("integrity")
    if expected.schema != actual.schema:
        coordinates.append("schema")
    if expected.rows != actual.rows:
        expected_tables = {table[0]: table for table in expected.rows}
        actual_tables = {table[0]: table for table in actual.rows}
        for table in sorted(expected_tables.keys() | actual_tables.keys()):
            if expected_tables.get(table) != actual_tables.get(table):
                coordinates.append(f"rows:{table}")
    if coordinates:
        raise SQLiteSnapshotMismatch(tuple(coordinates))


def restore_sql_fixture(sql_path: Path, database_path: Path) -> SQLiteSnapshot:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(sql_path.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()
    return snapshot_sqlite(database_path)
