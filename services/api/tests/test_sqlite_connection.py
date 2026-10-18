import sqlite3
import subprocess
import sys

import pytest
from jobos_api.sqlite_connection import connect_sqlite


def test_scoped_connection_commits_and_closes(tmp_path):
    database = tmp_path / "scoped.sqlite3"
    with connect_sqlite(database) as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES ('(FAKE) committed')")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with connect_sqlite(database) as reopened:
        assert reopened.execute("SELECT value FROM sample").fetchone()[0] == "(FAKE) committed"


def test_scoped_connection_rolls_back_and_closes_on_body_error(tmp_path):
    database = tmp_path / "rollback.sqlite3"
    with connect_sqlite(database) as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
    with (
        pytest.raises(RuntimeError, match="synthetic failure"),
        connect_sqlite(database) as connection,
    ):
        connection.execute("INSERT INTO sample VALUES ('(FAKE) rolled back')")
        raise RuntimeError("synthetic failure")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with connect_sqlite(database) as reopened:
        assert reopened.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 0


def test_foreign_keys_are_enforced_and_failed_commit_closes(tmp_path):
    database = tmp_path / "foreign-keys.sqlite3"
    with connect_sqlite(database) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id) "
            "DEFERRABLE INITIALLY DEFERRED)"
        )
    with (
        pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"),
        connect_sqlite(database) as connection,
    ):
        connection.execute("INSERT INTO child VALUES (42)")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with connect_sqlite(database) as reopened:
        assert reopened.execute("SELECT COUNT(*) FROM child").fetchone()[0] == 0


def test_connection_closes_when_foreign_key_setup_fails(tmp_path, monkeypatch):
    opened = sqlite3.connect(tmp_path / "setup-failure.sqlite3")
    opened.set_authorizer(
        lambda action, *_: (
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_PRAGMA else sqlite3.SQLITE_OK
        )
    )
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: opened)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"), connect_sqlite("unused"):
        pytest.fail("Connection setup should not yield an unsafe connection")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened.execute("SELECT 1")


def test_inner_native_transaction_keeps_outer_scoped_connection_open(tmp_path):
    with connect_sqlite(tmp_path / "nested.sqlite3") as connection:
        with connection:
            connection.execute("CREATE TABLE sample (id INTEGER)")
            connection.execute("INSERT INTO sample VALUES (1)")
        # The migration ledger uses a native transaction within the owned
        # connection. Its inner exit must not close the outer connection.
        connection.execute("INSERT INTO sample VALUES (2)")
        assert connection.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 2
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_readonly_uri_remains_readonly_and_closes(tmp_path):
    database = tmp_path / "readonly.sqlite3"
    with connect_sqlite(database) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER)")
    with (
        pytest.raises(sqlite3.OperationalError, match="readonly"),
        connect_sqlite(f"file:{database}?mode=ro", uri=True) as connection,
    ):
        connection.execute("INSERT INTO sample VALUES (1)")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


@pytest.mark.skipif(sys.platform == "win32", reason="Requires POSIX file descriptor limits")
def test_repeated_actual_event_polling_does_not_depend_on_garbage_collection(tmp_path):
    # Keep resource limits and disabled GC inside a child so this regression
    # cannot change the test runner or other tests' descriptor budget.
    script = """
import gc
import resource
import sys
from pathlib import Path
from jobos_api.state_store import JobOsStateStore

soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
limit = 256 if hard == resource.RLIM_INFINITY else min(256, hard)
resource.setrlimit(resource.RLIMIT_NOFILE, (limit, hard))
store = JobOsStateStore(Path(sys.argv[1]))
store.initialize()
gc.collect()
gc.disable()
for _ in range(1024):
    assert store.all_conversation_events_after(0) == []
print('1024 synthetic event polls completed without garbage collection')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "polling.sqlite3")],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "1024 synthetic event polls completed" in result.stdout
