import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect_sqlite(
    database: str | Path, *, uri: bool = False, timeout: float = 5.0
) -> Iterator[sqlite3.Connection]:
    """Own a connection, retain SQLite transaction semantics, and always close it."""
    connection = sqlite3.connect(database, uri=uri, timeout=timeout)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            yield connection
    finally:
        connection.close()
