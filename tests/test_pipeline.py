"""用 Mock 网关跑通 同步 -> 分析 -> 报告 的完整链路。"""
from ftrade.advice import observations
from ftrade.analysis import build_report
from ftrade.config import load_config
from ftrade.gateway.mock import MockGateway
from ftrade.storage.db import Database
from ftrade.sync import SyncService


def test_end_to_end(tmp_path):
    cfg = load_config()
    db = Database(tmp_path / "t.db")
    stats = SyncService(db, MockGateway(cfg), cfg).sync_all(full=True)

    assert stats["accounts"] == 1
    assert stats["deals"] > 0
    assert stats["positions"] > 0

    rep = build_report(db, cfg)
    assert rep["portfolio"]["holdings"] > 0
    assert rep["portfolio"]["market_value"] > 0
    assert rep["behavior"]["deal_count"] == stats["deals"]
    # Weights are rounded to 4 dp per holding, so the sum can drift by up to
    # 0.00005 per holding -- assert within that bound rather than exact equality.
    detail = rep["portfolio"]["holdings_detail"]
    total_w = sum(h["weight"] or 0 for h in detail)
    assert abs(total_w - 1) < 5e-5 * len(detail) + 1e-9

    obs = observations(rep, cfg)
    assert obs and all(o.level in ("info", "warn") for o in obs)
    db.close()


def test_incremental_sync_is_idempotent(tmp_path):
    cfg = load_config()
    db = Database(tmp_path / "t.db")
    gw = MockGateway(cfg)
    svc = SyncService(db, gw, cfg)
    svc.sync_all(full=True)
    n1 = db.scalar("SELECT COUNT(*) FROM deals")
    svc.sync_all(full=False)
    assert db.scalar("SELECT COUNT(*) FROM deals") == n1
    db.close()
