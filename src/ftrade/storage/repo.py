"""面向业务的查询封装。"""

from __future__ import annotations

import pandas as pd

from .db import Database


def latest_snapshot_date(db: Database, acc_id: int | None = None) -> str | None:
    if acc_id is None:
        return db.scalar("SELECT MAX(snap_date) FROM position_snapshots")
    return db.scalar("SELECT MAX(snap_date) FROM position_snapshots WHERE acc_id = ?", (acc_id,))


def positions(
    db: Database, snap_date: str | None = None, acc_id: int | None = None
) -> pd.DataFrame:
    snap_date = snap_date or latest_snapshot_date(db, acc_id)
    if not snap_date:
        return pd.DataFrame()
    sql = "SELECT * FROM position_snapshots WHERE snap_date = ?"
    params: list = [snap_date]
    if acc_id is not None:
        sql += " AND acc_id = ?"
        params.append(acc_id)
    return db.query(sql + " ORDER BY market_val DESC", params)


def account_snapshots(db: Database, acc_id: int | None = None) -> pd.DataFrame:
    sql = "SELECT * FROM account_snapshots"
    params: list = []
    if acc_id is not None:
        sql += " WHERE acc_id = ?"
        params.append(acc_id)
    return db.query(sql + " ORDER BY snap_date", params)


def deals(
    db: Database,
    start: str | None = None,
    end: str | None = None,
    code: str | None = None,
    acc_id: int | None = None,
) -> pd.DataFrame:
    sql = "SELECT * FROM deals WHERE 1=1"
    params: list = []
    if start:
        sql += " AND create_time >= ?"
        params.append(start)
    if end:
        sql += " AND create_time <= ?"
        params.append(end + " 23:59:59" if len(end) == 10 else end)
    if code:
        sql += " AND code = ?"
        params.append(code)
    if acc_id is not None:
        sql += " AND acc_id = ?"
        params.append(acc_id)
    return db.query(sql + " ORDER BY create_time", params)


def accounts(db: Database) -> pd.DataFrame:
    return db.query("SELECT * FROM accounts ORDER BY acc_id")


def klines(db: Database, code: str, start: str | None = None) -> pd.DataFrame:
    sql = "SELECT * FROM klines WHERE code = ?"
    params: list = [code]
    if start:
        sql += " AND time_key >= ?"
        params.append(start)
    return db.query(sql + " ORDER BY time_key", params)


def held_codes(db: Database) -> list[str]:
    """曾经出现在持仓快照或成交里的所有标的。"""
    df = db.query(
        "SELECT DISTINCT code FROM position_snapshots UNION SELECT DISTINCT code FROM deals"
    )
    return sorted(c for c in df["code"].dropna().tolist() if c)
