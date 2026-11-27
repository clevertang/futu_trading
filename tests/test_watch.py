"""margin-watch: market hours, no writes to the daily snapshots, one mail per band."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from ftrade import watch
from ftrade.config import load_config
from ftrade.gateway.mock import MockGateway
from ftrade.storage.db import Database
from ftrade.sync import SyncService

NY = ZoneInfo("America/New_York")
OPEN = datetime(2026, 12, 2, 11, 0, tzinfo=NY)  # a Wednesday


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (datetime(2026, 12, 5, 11, 0, tzinfo=NY), False),  # Saturday
        (datetime(2026, 12, 7, 9, 29, tzinfo=NY), False),
        (datetime(2026, 12, 7, 9, 30, tzinfo=NY), True),
        (datetime(2026, 12, 7, 15, 59, tzinfo=NY), True),
        (datetime(2026, 12, 7, 16, 0, tzinfo=NY), False),
        # The launchd job runs on Tokyo time: 23:30 JST is 09:30 in New York in winter.
        (datetime(2026, 12, 7, 23, 30, tzinfo=ZoneInfo("Asia/Tokyo")), True),
    ],
)
def test_market_open(when, expected):
    assert watch.market_open(when) is expected


class ThinGateway(MockGateway):
    """The mock account, but with equity just `room` above maintenance (rate 0.4, beta 1)."""

    room = 0.015  # cushion as a share of market value -> distance = room / 0.6

    def get_account_info(self, acc_id, currency):
        info = super().get_account_info(acc_id, currency)
        mv = info["market_val"]
        total = mv * (0.4 + self.room)
        return {**info, "total_assets": round(total, 2), "cash": round(total - mv, 2)}


def _cfg(alert=True):
    cfg = load_config()
    codes = MockGateway(cfg).get_positions(1)["code"].tolist()
    cfg.analysis.margin_rates = {c: {"initial": 0.5, "maintenance": 0.4} for c in codes}
    cfg.analysis.margin_alert = (
        {"trigger": 0.03, "target": 0.05, "email_to": "me@example.com", "smtp_user": "me@example.com"}
        if alert
        else {}
    )
    return cfg


class Outbox:
    def __init__(self, fail=False):
        self.mails, self.fail = [], fail

    def __call__(self, to, user, subject, body, **kw):
        if self.fail:
            raise OSError("smtp down")
        self.mails.append((to, subject, body))


def _main_db(tmp_path, cfg):
    db = Database(tmp_path / "main.db")
    SyncService(db, MockGateway(cfg), cfg).sync_all(full=True)
    return db


def _count(db, table):
    return int(db.scalar(f"SELECT COUNT(*) FROM {table}"))


def _dump(db, table):
    return db.query(f"SELECT * FROM {table} ORDER BY 1, 2").to_dict("records")


def test_watch_writes_no_snapshots_to_main_db(tmp_path):
    # Row counts cannot catch this: INSERT OR REPLACE keeps them. Compare content.
    cfg = _cfg()
    db = _main_db(tmp_path, cfg)
    before = (_dump(db, "position_snapshots"), _dump(db, "account_snapshots"))
    watch.run_watch(cfg, db, ThinGateway(cfg), OPEN, sender=Outbox(), notifier=lambda *a: None,
                    log_path=tmp_path / "w.log")
    assert (_dump(db, "position_snapshots"), _dump(db, "account_snapshots")) == before


def test_watch_mails_once_per_band(tmp_path):
    cfg = _cfg()
    db = _main_db(tmp_path, cfg)
    out = Outbox()
    kw = {"sender": out, "notifier": lambda *a: None, "log_path": tmp_path / "w.log"}
    gw = ThinGateway(cfg)
    assert watch.run_watch(cfg, db, gw, OPEN, **kw) == 0
    assert watch.run_watch(cfg, db, gw, OPEN, **kw) == 0
    assert len(out.mails) == 1
    assert out.mails[0][0] == "me@example.com" and "离追保" in out.mails[0][1]
    gw.room = 0.011  # distance 2.5% -> 1.8%, worse by more than 0.5 points
    watch.run_watch(cfg, db, gw, OPEN, **kw)
    assert len(out.mails) == 2
    assert (tmp_path / "w.log").read_text(encoding="utf-8").count("\n") == 3


def test_watch_without_alert_config_sends_nothing(tmp_path):
    cfg = _cfg(alert=False)
    db = _main_db(tmp_path, cfg)
    out = Outbox()
    assert watch.run_watch(cfg, db, ThinGateway(cfg), OPEN, sender=out,
                           notifier=lambda *a: None, log_path=tmp_path / "w.log") == 0
    assert out.mails == []


def test_watch_outside_hours_does_nothing(tmp_path):
    cfg = _cfg()
    db = _main_db(tmp_path, cfg)
    out = Outbox()
    closed = datetime(2026, 12, 5, 11, 0, tzinfo=NY)
    assert watch.run_watch(cfg, db, ThinGateway(cfg), closed, sender=out,
                           notifier=lambda *a: None, log_path=tmp_path / "w.log") == 0
    assert out.mails == []


def test_send_failure_returns_1_and_records_nothing(tmp_path):
    cfg = _cfg()
    db = _main_db(tmp_path, cfg)
    kw = {"notifier": lambda *a: None, "log_path": tmp_path / "w.log"}
    assert watch.run_watch(cfg, db, ThinGateway(cfg), OPEN, sender=Outbox(fail=True), **kw) == 1
    ok = Outbox()
    watch.run_watch(cfg, db, ThinGateway(cfg), OPEN, sender=ok, **kw)
    assert len(ok.mails) == 1  # the failed attempt did not mark the band as alerted


class ExpiringCallGateway(ThinGateway):
    """Thin account that also holds a long call expiring on the New York session date.

    A long option has no loan value, so it must stay out of equity with loan.
    """

    opt, opt_val = "US.AAPL261202C300000", 50_000.0

    def get_positions(self, acc_id):
        df = MockGateway.get_positions(self, acc_id)
        row = {**df.iloc[1].to_dict(), "code": self.opt, "stock_name": "AAPL call",
               "qty": 1.0, "can_sell_qty": 1.0, "nominal_price": 500.0,
               "cost_price": 400.0, "diluted_cost": 400.0, "market_val": self.opt_val}
        return pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    def get_account_info(self, acc_id, currency):
        info = MockGateway.get_account_info(self, acc_id, currency)
        opt = self.opt_val * float((self.cfg.analysis.fx_rates or {}).get("USD", 1.0))
        stock = info["market_val"] - opt
        total = stock * (0.4 + self.room) + opt
        return {**info, "total_assets": round(total, 2), "cash": round(total - info["market_val"], 2),
                "initial_margin": stock * 0.5, "maintenance_margin": stock * 0.4,
                "margin_call_margin": stock * 0.4}


def test_option_expiring_on_the_ny_session_date_still_counts_as_held(tmp_path, monkeypatch):
    # From 10:00 EST to the close it is already the next day in Tokyo; stamped
    # with that date, a call expiring today looked expired and its value was
    # counted as loan value, hiding a real alert.
    from ftrade.sync import service

    cfg = _cfg()
    db = _main_db(tmp_path, cfg)
    monkeypatch.setattr(service, "_today", lambda: "2026-12-03")
    out = Outbox()
    watch.run_watch(cfg, db, ExpiringCallGateway(cfg), OPEN, sender=out,
                    notifier=lambda *a: None, log_path=tmp_path / "w.log")
    assert len(out.mails) == 1 and "离追保" in out.mails[0][1]


class BlindGateway(ThinGateway):
    def get_account_info(self, acc_id, currency):
        return {}


def test_blind_watch_mails_once_per_day(tmp_path):
    cfg = _cfg()
    db = _main_db(tmp_path, cfg)
    out = Outbox()
    kw = {"sender": out, "notifier": lambda *a: None, "log_path": tmp_path / "w.log"}
    watch.run_watch(cfg, db, BlindGateway(cfg), OPEN, **kw)
    watch.run_watch(cfg, db, BlindGateway(cfg), OPEN, **kw)
    assert len(out.mails) == 1 and "盯盘失效" in out.mails[0][1]


def test_opend_down_mails_once_per_day_during_hours_only(tmp_path):
    cfg = _cfg()
    db = _main_db(tmp_path, cfg)
    out = Outbox()
    closed = datetime(2026, 12, 5, 11, 0, tzinfo=NY)
    watch.report_blind(cfg, db, closed, "OpenD 未连接", sender=out, log_path=tmp_path / "w.log")
    watch.report_blind(cfg, db, OPEN, "OpenD 未连接", sender=out, log_path=tmp_path / "w.log")
    watch.report_blind(cfg, db, OPEN, "OpenD 未连接", sender=out, log_path=tmp_path / "w.log")
    assert len(out.mails) == 1 and "OpenD" in out.mails[0][2]


def test_crossing_into_a_call_mails_again(tmp_path):
    cfg = _cfg()
    db = _main_db(tmp_path, cfg)
    db.set_state("margin_alert_last:2026-12-02", "0.0040")
    gw = ThinGateway(cfg)
    gw.room = -0.01  # under maintenance: distance 0
    out = Outbox()
    watch.run_watch(cfg, db, gw, OPEN, sender=out, notifier=lambda *a: None,
                    log_path=tmp_path / "w.log")
    assert len(out.mails) == 1
