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
