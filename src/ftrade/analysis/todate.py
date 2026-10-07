"""This year's and this month's result, up to the latest snapshot.

Two figures per period, because they answer different questions:

- realised -- closed round trips less fees, plus dividends after tax. Built
  from fills, so it goes back as far as the deal history does.
- the change in net assets, deposits and withdrawals taken out. This one also
  counts what is still open, which is what "how much did I make this month"
  usually means, but it needs a snapshot from before the period begins.
  Snapshots start 2026-09-10, so for this year it is absent rather than
  measured from some later day and labelled as the year.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from . import cash as cash_mod
from . import fees as fees_mod
from . import metrics, trades
from .fx import FX

# Money moved in or out of the account from outside it. Every other flow type
# -- trade settlement, dividends, interest, fees -- is part of the result.
EXTERNAL_FLOW_TYPES = ("bank transfer deposits", "asset transfer")


def nav_change(snaps: pd.DataFrame, flows: pd.DataFrame | None, fx: FX, since: str, as_of: str):
    """Net-asset change from the last snapshot before `since` to `as_of`, with
    deposits and withdrawals in between taken out; None without such a snapshot."""
    curve = metrics.equity_curve(snaps, fx)
    if curve.empty:
        return None
    curve = curve[curve["total_assets"] > 0]
    before = curve[curve["snap_date"].astype(str) < since]
    upto = curve[curve["snap_date"].astype(str) <= as_of]
    if before.empty or upto.empty:
        return None
    start_date, start = str(before["snap_date"].iloc[-1]), float(before["total_assets"].iloc[-1])
    end_date, end = str(upto["snap_date"].iloc[-1]), float(upto["total_assets"].iloc[-1])
    external = 0.0
    if flows is not None and not flows.empty:
        day = flows["clearing_date"].astype(str).str[:10]
        moved = flows[
            (day > start_date)
            & (day <= end_date)
            & flows["cashflow_type"].astype(str).str.lower().isin(EXTERNAL_FLOW_TYPES)
        ]
        external = sum(
            fx.to_base(cash_mod._signed(a, d), c) or 0.0
            for a, d, c in zip(moved["amount"], moved["direction"], moved["currency"], strict=True)
        )
    pnl = end - start - external
    return {
        "from": start_date,
        "to": end_date,
        "start": round(start, 2),
        "end": round(end, 2),
        "external": round(external, 2),
        "pnl": round(pnl, 2),
        "pct": round(pnl / start, 4) if start else None,
    }


def to_date(trips, deals, order_fees, flows, snaps, fx: FX, as_of: str | None) -> dict:
    """{"as_of", "year": {...}, "month": {...}}, each period with its realised
    figures and its net-asset change (None where no earlier snapshot exists)."""
    as_of = as_of or date.today().isoformat()
    out: dict = {"as_of": as_of}
    for key, since in (("year", as_of[:4] + "-01-01"), ("month", as_of[:7] + "-01")):
        bh = trades.behavior(trips, deals, fx, start=since, end=as_of)
        fee = fees_mod.fee_summary(order_fees, fx, start=since, end=as_of).get("total") or 0.0
        cash = cash_mod.cash_summary(flows, fx, start=since, end=as_of)
        gross = bh.get("realized_pnl") or 0.0
        dividends = (cash.get("dividends") or 0.0) + (cash.get("dividend_tax") or 0.0)
        out[key] = {
            "since": since,
            "round_trips": bh.get("round_trips", 0),
            "realized": round(gross, 2),
            "fees": round(fee, 2),
            "dividends": round(dividends, 2),
            "net": round(gross - fee + dividends, 2),
            "nav": nav_change(snaps, flows, fx, since, as_of),
        }
    return out
