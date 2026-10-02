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


def test_an_old_database_gains_margin_columns_filled_from_raw(tmp_path):
    """Snapshots synced before the columns existed already hold the values in `raw`."""
    import json
    import sqlite3

    import pandas as pd

    from ftrade.storage.db import Database

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE account_snapshots (snap_date TEXT NOT NULL, acc_id INTEGER NOT NULL, "
        "currency TEXT NOT NULL, total_assets REAL, securities_assets REAL, cash REAL, "
        "frozen_cash REAL, market_val REAL, power REAL, risk_status TEXT, raw TEXT, "
        "synced_at TEXT, PRIMARY KEY (snap_date, acc_id, currency))"
    )
    raw = {
        "maintenance_margin": "182036.12",
        "margin_call_margin": "182039.28",
        "initial_margin": "202262.52",
        "exposure_level": "WARNING",
    }
    con.execute(
        "INSERT INTO account_snapshots (snap_date, acc_id, currency, raw) VALUES (?, ?, ?, ?)",
        ("2026-09-14", 1, "HKD", json.dumps(raw)),
    )
    con.execute(
        "INSERT INTO account_snapshots (snap_date, acc_id, currency, raw) VALUES (?, ?, ?, ?)",
        ("2026-09-15", 2, "HKD", json.dumps({"maintenance_margin": "N/A"})),
    )
    con.commit()
    con.close()

    db = Database(path)
    rows = db.query("SELECT * FROM account_snapshots ORDER BY snap_date")
    db.close()

    assert rows.loc[0, "maintenance_margin"] == 182036.12
    assert rows.loc[0, "margin_call_margin"] == 182039.28
    assert rows.loc[0, "exposure_level"] == "WARNING"
    assert pd.isna(rows.loc[1, "maintenance_margin"])  # "N/A" stays unknown, not 0


def test_migration_is_idempotent(tmp_path):
    from ftrade.storage.db import Database

    Database(tmp_path / "t.db").close()
    db = Database(tmp_path / "t.db")  # second open must not try to re-add columns
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(account_snapshots)")}
    db.close()
    assert {"maintenance_margin", "margin_call_margin", "exposure_level"} <= cols
