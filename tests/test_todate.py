"""This year's and this month's result, as the holdings image shows them."""

import pandas as pd

from ftrade.analysis import todate
from ftrade.analysis.fx import FX


def _snaps(rows):
    return pd.DataFrame(
        [{"snap_date": d, "acc_id": 1, "total_assets": v, "currency": "USD"} for d, v in rows]
    )


def _flows(rows):
    return pd.DataFrame(
        [
            {
                "clearing_date": d,
                "cashflow_type": t,
                "amount": a,
                "direction": "IN" if a > 0 else "OUT",
                "currency": "USD",
            }
            for d, t, a in rows
        ]
    )


def test_month_change_starts_from_the_last_snapshot_before_the_month_and_drops_deposits():
    snaps = _snaps([("2026-09-29", 37000), ("2026-09-30", 38000), ("2026-10-07", 42000)])
    flows = _flows(
        [
            ("2026-09-30", "Bank Transfer Deposits", 5000),  # before the window
            ("2026-10-03", "Bank Transfer Deposits", 1000),
            ("2026-10-05", "Others", -6000),  # a trade settling: part of the result
        ]
    )
    nav = todate.nav_change(snaps, flows, FX({}, "USD"), "2026-10-01", "2026-10-07")
    assert (nav["from"], nav["to"]) == ("2026-09-30", "2026-10-07")
    assert nav["external"] == 1000
    assert nav["pnl"] == 3000
    assert nav["pct"] == round(3000 / 38000, 4)


def test_no_change_is_reported_without_a_snapshot_before_the_period():
    snaps = _snaps([("2026-09-10", 30000), ("2026-10-07", 42000)])
    assert todate.nav_change(snaps, None, FX({}, "USD"), "2026-01-01", "2026-10-07") is None


def _deals(rows):
    return pd.DataFrame(
        [
            {"code": c, "trd_side": s, "qty": q, "price": p, "create_time": t}
            for c, s, q, p, t in rows
        ]
    )


def _rebuild(deals, positions, year_end, end_nav=2000.0):
    snaps = _snaps([("2026-10-07", end_nav)])
    return todate.rebuilt_change(
        deals,
        pd.DataFrame(positions, columns=["code", "qty", "market_val"]),
        snaps,
        None,
        FX({}, "USD"),
        realized=100.0,
        fees=0.0,
        dividends=0.0,
        year_end=year_end,
        since="2026-01-01",
        as_of="2026-10-07",
        currency_of=lambda c: "USD",
    )


def test_year_is_rebuilt_from_the_traded_year_end_close():
    # 10 bought at 100 last year; 110 at the year-end close; 5 sold at 120
    # this year (+100 realised against FIFO cost); the other 5 now worth 130.
    deals = _deals(
        [
            ("US.X", "BUY", 10, 100.0, "2025-06-01 10:00:00"),
            ("US.X", "SELL", 5, 120.0, "2026-03-01 10:00:00"),
        ]
    )
    out = _rebuild(deals, [("US.X", 5, 650.0)], {"US.X": 110.0})
    # Worth 1,100 on Jan 1; now 600 in cash plus 650 in stock: +150.
    assert out["pnl"] == 150.0
    assert out["start"] == 2000.0 - 150.0
    assert out["method"] == "rebuilt" and out["carried_at_cost"] == []


def test_an_option_across_the_year_end_is_carried_at_cost_and_named():
    deals = _deals(
        [
            ("US.X", "BUY", 100, 100.0, "2025-06-01 10:00:00"),
            ("US.X260109C120000", "SELL_SHORT", 1, 2.0, "2025-12-20 10:00:00"),
            ("US.X260109C120000", "BUY_BACK", 1, 0.0, "2026-01-10 01:00:00"),
        ]
    )
    out = _rebuild(deals, [("US.X", 100, 11000.0)], {"US.X": 105.0})
    # 100 + (1,000 open now - 500 open at the start); the call adds nothing
    # beyond the realised figure passed in.
    assert out["pnl"] == 600.0
    assert out["carried_at_cost"] == ["US.X260109C120000"]


def test_no_rebuild_without_the_year_end_price_or_when_the_book_disagrees():
    deals = _deals([("US.X", "BUY", 10, 100.0, "2025-06-01 10:00:00")])
    assert _rebuild(deals, [("US.X", 10, 1300.0)], {}) is None
    assert _rebuild(deals, [("US.X", 12, 1560.0)], {"US.X": 110.0}) is None
