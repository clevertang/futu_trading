"""Sync: what lands in SQLite after one or more runs against a gateway."""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from ftrade.config import load_config
from ftrade.gateway.mock import MockGateway
from ftrade.storage import repo
from ftrade.storage.db import Database
from ftrade.sync import SyncService

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.yaml"


@pytest.fixture
def cfg(tmp_path):
    c = load_config(EXAMPLE)
    c.storage.db_path = str(tmp_path / "t.db")
    return c


@pytest.fixture
def db(cfg):
    d = Database(cfg.db_path)
    yield d
    d.close()


class _Positions(MockGateway):
    """Mock whose current holdings can be replaced between syncs."""

    def __init__(self, cfg, rows):
        super().__init__(cfg)
        self.rows = rows

    def get_positions(self, acc_id):
        return pd.DataFrame(self.rows)


def _pos(code, qty):
    return {
        "code": code,
        "stock_name": code,
        "qty": qty,
        "market_val": qty * 10.0,
        "currency": "USD",
        "position_market": "US",
    }


def test_a_position_sold_between_two_same_day_syncs_is_gone(cfg, db):
    gw = _Positions(cfg, [_pos("US.A", 10), _pos("US.B", 5)])
    svc = SyncService(db, gw, cfg)
    svc.sync_positions(1)
    gw.rows = [_pos("US.A", 10)]
    svc.sync_positions(1)
    assert repo.positions(db)["code"].tolist() == ["US.A"]


def test_selling_everything_leaves_today_empty_not_yesterdays_holdings(cfg, db):
    gw = _Positions(cfg, [_pos("US.A", 10)])
    svc = SyncService(db, gw, cfg)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    svc.sync_positions(1)
    db.conn.execute("UPDATE position_snapshots SET snap_date = ?", (yesterday,))
    db.conn.commit()
    gw.rows = []
    svc.sync_positions(1)
    assert repo.latest_snapshot_date(db) == date.today().isoformat()
    assert repo.positions(db).empty


def test_a_resync_does_not_touch_another_accounts_positions(cfg, db):
    gw = _Positions(cfg, [_pos("US.A", 10)])
    svc = SyncService(db, gw, cfg)
    svc.sync_positions(1)
    svc.sync_positions(2)
    gw.rows = []
    svc.sync_positions(2)
    assert repo.positions(db)["acc_id"].tolist() == [1]


class _CountingFees(MockGateway):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.asked: list[str] = []

    def get_order_fees(self, acc_id, order_ids):
        self.asked.extend(order_ids)
        return super().get_order_fees(acc_id, order_ids)


def test_orders_that_never_filled_are_not_sent_for_fees(cfg, db):
    db.upsert(
        "orders",
        [
            {"order_id": "o1", "acc_id": 1, "dealt_qty": 10.0, "order_status": "FILLED_ALL"},
            {"order_id": "o2", "acc_id": 1, "dealt_qty": 0.0, "order_status": "CANCELLED_ALL"},
        ],
    )
    gw = _CountingFees(cfg)
    SyncService(db, gw, cfg).sync_order_fees(1)
    assert gw.asked == ["o1"]


class _Flows(MockGateway):
    def __init__(self, cfg, fail_on=None):
        super().__init__(cfg)
        self.asked: list[str] = []
        self.fail_on = fail_on

    def get_cash_flow(self, acc_id, clearing_date):
        self.asked.append(clearing_date)
        if clearing_date == self.fail_on:
            raise RuntimeError("Disconnect")
        return pd.DataFrame()


def _weekday_before(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def test_cash_flow_checkpoint_stays_behind_today(cfg, db):
    # Entries can still post against today's clearing date after the sync
    # runs; checkpointing today would skip them for good.
    gw = _Flows(cfg)
    svc = SyncService(db, gw, cfg)
    start = (date.today() - timedelta(days=10)).isoformat()
    svc.sync_cash_flows(1, start)
    assert db.get_state("cashflow_last_date:1") < date.today().isoformat()
    gw.asked.clear()
    svc.sync_cash_flows(1, start)
    if date.today().weekday() < 5:
        assert date.today().isoformat() in gw.asked


def test_cash_flow_rereads_recent_days_for_late_postings(cfg, db):
    gw = _Flows(cfg)
    svc = SyncService(db, gw, cfg)
    svc.sync_cash_flows(1, (date.today() - timedelta(days=20)).isoformat())
    gw.asked.clear()
    svc.sync_cash_flows(1, (date.today() - timedelta(days=20)).isoformat())
    assert _weekday_before(date.today()).isoformat() in gw.asked


def test_a_cash_flow_failure_is_reported_as_failed(cfg, db):
    target = _weekday_before(date.today())
    gw = _Flows(cfg, fail_on=target.isoformat())
    svc = SyncService(db, gw, cfg)
    stats = svc.sync_all(with_klines=False, with_cash_flow=True)
    assert any("Disconnect" in f for f in stats.get("failed", []))


class _FailingFees(MockGateway):
    def get_order_fees(self, acc_id, order_ids):
        raise RuntimeError("fee endpoint down")


def test_a_fee_failure_is_reported_as_failed(cfg, db):
    stats = SyncService(db, _FailingFees(cfg), cfg).sync_all(with_klines=False)
    assert any("fee endpoint down" in f for f in stats.get("failed", []))
