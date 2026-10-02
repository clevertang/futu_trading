"""Account-level capital: what is actually owned versus what is borrowed.

Position market value alone describes a portfolio's *composition*, not its
*size*. An account holding 59,655 of securities against 25,079 of margin debt
owns 34,577 -- and a position that is 87% of the securities is 150% of the
money at stake. Reporting only the first number understates concentration and
hides leverage entirely.
"""

from __future__ import annotations

import pandas as pd

from .fx import FX

# Account snapshots are stored in whatever currency the sync ran under.
SNAPSHOT_COLUMNS = ("total_assets", "securities_assets", "cash", "market_val", "power")
# Kept apart from SNAPSHOT_COLUMNS: snapshots synced before these were recorded
# have no value, and a missing requirement must read as unknown, not as zero.
MARGIN_COLUMNS = ("initial_margin", "maintenance_margin", "margin_call_margin")


def account_equity(
    snapshots: pd.DataFrame, fx: FX, stored_currency: str, snap_date: str | None = None
) -> dict:
    """Aggregate the latest capital snapshot across accounts, in fx's base."""
    if snapshots is None or snapshots.empty:
        return {}
    df = snapshots.copy()
    date = snap_date or df["snap_date"].max()
    df = df[df["snap_date"] == date]
    if df.empty:
        return {}

    out: dict = {"snap_date": str(date), "base_currency": fx.base}
    # Convert per row before summing rather than summing then converting once:
    # every account currently syncs under the same configured currency, but a
    # config change between syncs would leave older rows carrying the old
    # currency string in their own `currency` column, and summing first would
    # silently misconvert them. Same fix already made to metrics.equity_curve.
    currencies = df.get("currency")
    for col in SNAPSHOT_COLUMNS:
        if col not in df:
            continue
        values = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        if currencies is not None:
            total = sum(fx.to_base(v, c) or 0.0 for v, c in zip(values, currencies, strict=True))
        else:
            total = fx.to_base(float(values.sum()), stored_currency) or 0.0
        out[col] = round(float(total), 2)

    # Only accounts holding something report a meaningful risk level; the rest
    # come back as "N/A" and would otherwise mask the real one.
    statuses = [s for s in df.get("risk_status", pd.Series(dtype=str)) if s and s != "N/A"]
    out["risk_status"] = statuses[0] if statuses else None

    for col in MARGIN_COLUMNS:
        values = pd.to_numeric(df.get(col, pd.Series(dtype=float)), errors="coerce")
        if values.isna().all():
            continue
        rows = zip(
            values.fillna(0.0),
            currencies if currencies is not None else [stored_currency] * len(values),
            strict=True,
        )
        out[col] = round(float(sum(fx.to_base(v, c) or 0.0 for v, c in rows)), 2)
    levels = [s for s in df.get("exposure_level", pd.Series(dtype=str)) if s and s != "N/A"]
    out["exposure_level"] = levels[0] if levels else None

    total = out.get("total_assets") or 0.0
    # Futu's `securities_assets` is net securities *equity*, not gross market
    # value -- on a margin account it comes back roughly equal to total assets.
    # `market_val` is the exposure that actually matters here.
    securities = out.get("market_val") or 0.0
    if total:
        out["gross_exposure"] = round(securities / total, 4)
        out["cash_ratio"] = round((out.get("cash") or 0.0) / total, 4)
        # Borrowing to hold more than the account owns.
        out["is_leveraged"] = securities > total * 1.005
        out["drop_to_margin_call"] = drop_to_margin_call(
            securities, total, out.get("margin_call_margin")
        )
    return out


def drop_to_margin_call(
    market_val: float, net_assets: float, margin_call: float | None
) -> float | None:
    """How far holdings can fall, all together, before the broker calls for margin.

    A call comes when net assets drop below `margin_call`. Assuming that
    requirement stays the same share of market value as prices move, and
    the debt (market value minus net assets) stays put:

        MV' - debt = k * MV',  k = margin_call / MV   ->   MV' = debt / (1 - k)

    Returns the fall as a fraction (0.42 = a 42% fall), 0.0 if the account is
    already below the line, or None with no margin debt or no requirement on
    record. It is a single-move estimate: a slide met with partial forced
    sales and re-buying follows a different path.
    """
    if not margin_call or not market_val or market_val <= 0:
        return None
    debt = market_val - net_assets
    if debt <= 0:
        return None
    k = margin_call / market_val
    if k >= 1:
        return 0.0
    return round(max(0.0, 1 - debt / (1 - k) / market_val), 4)


def milestone_progress(net_assets: float | None, milestone: dict | None, fx: FX) -> dict | None:
    """Progress toward a personal net-asset target the user set for themselves.

    Purely descriptive -- how far along a plan already made, not a suggestion
    that the plan is right or that now is the time to act on it. `milestone`
    is read straight from config (`{"amount": 50000, "currency": "USD",
    "label": "..."}`), converted into whatever currency the report is in.
    """
    if not milestone or net_assets is None:
        return None
    amount = milestone.get("amount")
    currency = milestone.get("currency") or fx.base
    if not amount:
        return None
    target = fx.to_base(float(amount), currency)
    if not target:
        return None
    return {
        "target": round(target, 2),
        "current": round(net_assets, 2),
        "progress_pct": round(min(net_assets / target, 1.0) * 100, 1),
        "remaining": round(max(target - net_assets, 0.0), 2),
        "reached": net_assets >= target,
        "label": milestone.get("label"),
    }


def net_asset_weights(holdings: list[dict], net_assets: float | None) -> list[dict]:
    """Add each holding's share of net assets alongside its share of securities.

    With no leverage the two are the same. With margin they diverge sharply, and
    the net-asset figure is the one that describes risk.
    """
    if not net_assets:
        return holdings
    for h in holdings:
        mv = h.get("market_val_base")
        h["weight_of_net"] = None if mv is None else round(float(mv) / net_assets, 4)
    return holdings
