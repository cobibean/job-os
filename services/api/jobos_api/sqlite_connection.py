import sqlite3
from pathlib import Path


def connect_sqlite(
    database: str | Path, *, uri: bool = False, timeout: float = 5.0
) -> sqlite3.Connection:
    """Open a JobOS SQLite connection with relational constraints enforced."""
    connection = sqlite3.connect(database, uri=uri, timeout=timeout)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
