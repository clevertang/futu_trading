"""This year's and this month's result, up to the latest snapshot.

Two figures per period, because they answer different questions:

- realised -- closed round trips less fees, plus dividends after tax. Built
  from fills, so it goes back as far as the deal history does.
- the change in net assets, deposits and withdrawals taken out. This one also
  counts what is still open, which is what "how much did I make this year"
  usually means. With a snapshot from before the period it is read directly.
  Without one -- snapshots start 2026-09-10, so for this year -- it is
  rebuilt from what was held when the period began:

      realised + dividends - fees + interest and other charges
        + (open P&L now - open P&L at the start)

  with both open P&Ls measured against the same FIFO cost as the realised
  figure, so no gain is counted twice. Stocks at the start are valued at the
  year-end close that actually traded (`year_end_prices`). An option open
  across the year-end has no such price and is carried at its opening
  premium, so its year-end move lands in the year it closed; the result
  names those contracts. Checked against snapshots: for 2026-09-30 ->
  10-07 the rebuild gives +3,314 against a measured +3,221.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from . import cash as cash_mod
from . import fees as fees_mod
from . import metrics, trades
from .fx import FX
from .instruments import contract_multiplier
from .pnl import open_lots

# Money moved in or out of the account from outside it. Every other flow type
# -- trade settlement, dividends, interest, fees -- is part of the result.
# Futu files IPO subscription debits under "Bank Transfer Deposits" too; those
# come back as "IPO Subscription Return" and are not money leaving.
EXTERNAL_FLOW_TYPES = ("bank transfer deposits", "asset transfer")
# Cash results that are neither a trade, a fee on an order, nor a dividend:
# margin interest ("Interest In Aug.", filed under "Others"), stock-lending
# income, and the broker's sundry charges.
OTHER_RESULT_TYPES = ("adr fee", "scrip fee", "corporate action service fee", "other")


def _external(flows: pd.DataFrame) -> pd.Series:
    kind = flows["cashflow_type"].astype(str).str.lower()
    remark = flows["remark"].astype(str) if "remark" in flows else pd.Series("", index=flows.index)
    return kind.isin(EXTERNAL_FLOW_TYPES) & ~remark.str.contains("IPO", na=False)


def _other_result(flows: pd.DataFrame) -> pd.Series:
    kind = flows["cashflow_type"].astype(str).str.lower()
    remark = flows["remark"].astype(str) if "remark" in flows else pd.Series("", index=flows.index)
    return (
        kind.str.contains("interest", na=False)
        | kind.isin(OTHER_RESULT_TYPES)
        | ((kind == "others") & remark.str.startswith("Interest"))
    )


def _flow_sum(flows, fx: FX, mask, after: str, upto: str) -> float:
    if flows is None or flows.empty:
        return 0.0
    day = flows["clearing_date"].astype(str).str[:10]
    rows = flows[mask(flows) & (day > after) & (day <= upto)]
    return sum(
        fx.to_base(cash_mod._signed(a, d), c) or 0.0
        for a, d, c in zip(rows["amount"], rows["direction"], rows["currency"], strict=True)
    )


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
    external = _flow_sum(flows, fx, _external, start_date, end_date)
    pnl = end - start - external
    return {
        "from": start_date,
        "to": end_date,
        "start": round(start, 2),
        "end": round(end, 2),
        "external": round(external, 2),
        "pnl": round(pnl, 2),
        "pct": round(pnl / start, 4) if start else None,
        "method": "snapshots",
    }


def _open_pl(lots: pd.DataFrame, value_of, currency_of, fx: FX) -> float | None:
    """Open P&L of `lots` in the base currency; None if any lot has no value."""
    total = 0.0
    for code, qty, cost in zip(lots["code"], lots["qty"], lots["avg_cost"], strict=True):
        value = value_of(code, qty)
        if value is None:
            return None
        pl = value - float(qty) * float(cost) * contract_multiplier(code)
        total += fx.to_base(pl, currency_of(code)) or 0.0
    return total


def rebuilt_change(
    deals,
    positions,
    snaps,
    flows,
    fx: FX,
    realized: float,
    fees: float,
    dividends: float,
    year_end: dict[str, float],
    since: str,
    as_of: str,
    currency_of,
) -> dict | None:
    """Net-asset change since `since`, rebuilt from fills (module docstring).

    None when the book cannot be valued: the local FIFO book disagrees with the
    broker's positions today (missing history, a corporate action), or a stock
    held at the start has no year-end price.
    """
    if deals is None or deals.empty or positions is None or positions.empty:
        return None
    curve = metrics.equity_curve(snaps, fx)
    curve = curve[(curve["total_assets"] > 0) & (curve["snap_date"].astype(str) <= as_of)]
    if curve.empty:
        return None
    end_date, end = str(curve["snap_date"].iloc[-1]), float(curve["total_assets"].iloc[-1])

    held = positions[positions["qty"].astype(float) != 0]
    broker = {
        c: (float(q), float(v or 0))
        for c, q, v in zip(held["code"], held["qty"], held["market_val"], strict=True)
    }
    now = open_lots(deals, as_of=date.fromisoformat(end_date))
    if {c: q for c, q in zip(now["code"], now["qty"], strict=True)} != {
        c: round(q, 4) for c, (q, _v) in broker.items()
    }:
        return None
    pl_now = _open_pl(now, lambda c, q: broker[c][1], currency_of, fx)

    start = deals[deals["create_time"].astype(str) < since]
    lots = open_lots(start, as_of=date.fromisoformat(since))
    cost_of = dict(zip(lots["code"], lots["avg_cost"], strict=True)) if not lots.empty else {}
    carried = []

    def value_at_start(code, qty):
        mult = contract_multiplier(code)
        if mult != 1:  # an option: no traded year-end close, carried at cost
            carried.append(code)
            return float(qty) * float(cost_of[code]) * mult
        price = year_end.get(code)
        return None if price is None else float(qty) * price

    pl_start = _open_pl(lots, value_at_start, currency_of, fx) if not lots.empty else 0.0
    if pl_now is None or pl_start is None:
        return None

    other = _flow_sum(flows, fx, _other_result, _day_before(since), end_date)
    external = _flow_sum(flows, fx, _external, _day_before(since), end_date)
    pnl = realized - fees + dividends + other + (pl_now - pl_start)
    start_value = end - pnl - external
    return {
        "from": _day_before(since),
        "to": end_date,
        "start": round(start_value, 2),
        "end": round(end, 2),
        "external": round(external, 2),
        "other": round(other, 2),
        "open_pl_change": round(pl_now - pl_start, 2),
        "pnl": round(pnl, 2),
        "pct": round(pnl / start_value, 4) if start_value > 0 else None,
        "method": "rebuilt",
        "carried_at_cost": sorted(set(carried)),
    }


def _day_before(day: str) -> str:
    return (date.fromisoformat(day) - pd.Timedelta(days=1)).isoformat()


def to_date(
    trips,
    deals,
    order_fees,
    flows,
    snaps,
    fx: FX,
    as_of: str | None,
    positions=None,
    year_end: dict[str, float] | None = None,
    currency_of=None,
) -> dict:
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
        nav = nav_change(snaps, flows, fx, since, as_of)
        # Rebuilding needs the traded close the day before `since`; only the
        # year-end one is stored, so a month without snapshots stays absent.
        if nav is None and key == "year" and positions is not None:
            nav = rebuilt_change(
                deals,
                positions,
                snaps,
                flows,
                fx,
                gross,
                fee,
                dividends,
                year_end or {},
                since,
                as_of,
                currency_of or (lambda c: "USD"),
            )
        out[key] = {
            "since": since,
            "round_trips": bh.get("round_trips", 0),
            "realized": round(gross, 2),
            "fees": round(fee, 2),
            "dividends": round(dividends, 2),
            "net": round(gross - fee + dividends, 2),
            "nav": nav,
        }
    return out
