"""Concurrency safety of the SQLite connection.

This project's own database was corrupted once by exactly the pattern tested
here: a long-running writer (a multi-hour cash-flow backfill) held a
transaction open while a second, short-lived connection opened and ran
`migrate()` -- which executes DDL (CREATE TABLE/INDEX IF NOT EXISTS) via
`executescript`, taking a schema lock. Under SQLite's default rollback-journal
mode that collision produced real, unrecoverable-without-`.recover` page
corruption. WAL mode is the fix: readers (and a second connection's DDL) do
not contend with a writer's in-progress transaction.
"""

from __future__ import annotations

from ftrade.storage.db import Database, connect


def test_connect_enables_wal_mode(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_concurrent_writer_and_migrate_do_not_corrupt_the_database(tmp_path):
    db_path = tmp_path / "t.db"

    # Connection A: simulate the long-running sync holding an open write
    # transaction (an INSERT that has not committed yet).
    writer = Database(db_path)
    writer.conn.execute("BEGIN")
    writer.conn.execute(
        "INSERT INTO sync_state (key, value, updated_at) VALUES ('probe', '1', datetime('now'))"
    )

    # Connection B: a second process opening a fresh connection mid-write --
    # this is what every ad-hoc read in this incident actually did, since
    # Database.__init__ always calls migrate().
    reader = Database(db_path)
    assert reader.get_state("probe") in (None, "1")  # sees committed state only

    writer.conn.commit()
    writer.close()
    reader.close()

    # The real assertion: no corruption, from either connection's view.
    verify = connect(db_path)
    assert verify.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert verify.execute("SELECT value FROM sync_state WHERE key='probe'").fetchone()[0] == "1"
    verify.close()
