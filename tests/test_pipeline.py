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


def test_big_ints_survive_the_json_boundary():
    from ftrade.web.app import _clean

    # Real Futu account ids are 18 digits -- past JavaScript's 2**53-1, where
    # JSON.parse silently rewrites the trailing digits.
    acc = 281756480175496435
    out = _clean({"accounts": [{"acc_id": acc, "qty": 1200}], "nested": [[acc]]})

    assert out["accounts"][0]["acc_id"] == "281756480175496435"
    assert out["nested"][0][0] == "281756480175496435"
    assert out["accounts"][0]["qty"] == 1200  # small ints stay numeric


def _snapshot(**over):
    row = {
        "snap_date": "2026-09-10",
        "acc_id": 1,
        "currency": "HKD",
        "total_assets": 269701.08,
        "securities_assets": 269697.92,
        "cash": -195614.90,
        "market_val": 465312.82,
        "power": 12837.50,
        "risk_status": "LEVEL5",
    }
    row.update(over)
    return row


def test_account_equity_reports_leverage_in_the_requested_currency():
    import pandas as pd

    from ftrade.analysis.equity import account_equity
    from ftrade.analysis.fx import FX

    # Snapshots are stored in HKD; ask for the same figures in USD.
    fx = FX({"HKD": 1.0, "USD": 7.8}, "HKD").rebase("USD")
    eq = account_equity(pd.DataFrame([_snapshot()]), fx, "HKD")

    assert eq["total_assets"] == 34577.06
    assert eq["cash"] == -25078.83  # negative cash is a margin loan
    # Exposure is gross market value over net assets, NOT securities_assets --
    # Futu reports that as net securities equity, ~= total assets on margin.
    assert eq["gross_exposure"] == 1.7253
    assert eq["cash_ratio"] == -0.7253
    assert eq["is_leveraged"] is True
    assert eq["risk_status"] == "LEVEL5"


def test_account_equity_ignores_placeholder_risk_status():
    import pandas as pd

    from ftrade.analysis.equity import account_equity
    from ftrade.analysis.fx import FX

    rows = pd.DataFrame(
        [
            _snapshot(
                acc_id=2,
                total_assets=0,
                securities_assets=0,
                cash=0,
                market_val=0,
                power=0,
                risk_status="N/A",
            ),
            _snapshot(acc_id=1),
        ]
    )
    eq = account_equity(rows, FX({"HKD": 1.0}, "HKD"), "HKD")

    assert eq["risk_status"] == "LEVEL5"  # the empty account must not mask it
    assert eq["total_assets"] == 269701.08  # summed across accounts


def test_unleveraged_account_is_not_flagged():
    import pandas as pd

    from ftrade.analysis.equity import account_equity
    from ftrade.analysis.fx import FX

    eq = account_equity(
        pd.DataFrame([_snapshot(total_assets=1000.0, cash=200.0, market_val=800.0)]),
        FX({"HKD": 1.0}, "HKD"),
        "HKD",
    )
    assert eq["gross_exposure"] == 0.8
    assert eq["is_leveraged"] is False


def test_net_asset_weights_diverge_from_securities_weights_under_margin():
    from ftrade.analysis.equity import net_asset_weights

    holdings = [{"code": "US.TQQQ", "market_val_base": 51164.0}]
    out = net_asset_weights(holdings, net_assets=34577.06)

    assert out[0]["weight_of_net"] == 1.4797  # 148% of the money actually owned


def test_fee_summary_converts_and_breaks_down():
    import pandas as pd

    from ftrade.analysis.fees import fee_summary
    from ftrade.analysis.fx import FX

    rows = pd.DataFrame(
        [
            {
                "fee_amount": 3.04,
                "currency": "USD",
                "code": "US.TQQQ260911C75000",
                "create_time": "2026-09-08 10:00:00",
            },
            {
                "fee_amount": 2.05,
                "currency": "USD",
                "code": "US.TQQQ",
                "create_time": "2026-09-08 11:00:00",
            },
            {
                "fee_amount": 78.00,
                "currency": "HKD",
                "code": "HK.00700",
                "create_time": "2024-05-02 10:00:00",
            },
        ]
    )
    out = fee_summary(rows, FX({"HKD": 1.0, "USD": 7.8}, "HKD").rebase("USD"))

    assert out["base_currency"] == "USD"
    assert out["total"] == 15.09  # 3.04 + 2.05 + 78/7.8
    assert out["by_currency"] == {"HKD": 78.0, "USD": 5.09}  # raw, not converted
    assert out["by_year"] == {"2024": 10.0, "2026": 5.09}
    # Sorted by converted amount, so the HKD row leads once converted.
    assert out["top_symbols"][0] == {"code": "HK.00700", "fees": 10.0}
    # Option fees roll up to their underlying rather than to each contract:
    # the two US rows are one contract and its underlying, and they merge.
    assert out["top_symbols"][1] == {"code": "US.TQQQ", "fees": 5.09}


def test_fee_summary_is_safe_when_no_fees_are_synced():
    import pandas as pd

    from ftrade.analysis.fees import fee_summary
    from ftrade.analysis.fx import FX

    out = fee_summary(pd.DataFrame(), FX({"USD": 1.0}, "USD"))
    assert out["total"] == 0.0 and out["count"] == 0


def test_realized_net_of_fees_is_reported_separately():
    from ftrade.analysis.report import _with_net_of_fees

    bh = _with_net_of_fees({"realized_pnl": 9806.11}, {"total": 4806.13})

    assert bh["realized_pnl"] == 9806.11  # gross stays untouched
    assert bh["fees_total"] == 4806.13
    assert bh["realized_pnl_net"] == 4999.98
