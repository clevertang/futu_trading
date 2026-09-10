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
    for col in SNAPSHOT_COLUMNS:
        if col not in df:
            continue
        raw = pd.to_numeric(df[col], errors="coerce").fillna(0.0).sum()
        out[col] = round(float(fx.to_base(float(raw), stored_currency) or 0.0), 2)

    # Only accounts holding something report a meaningful risk level; the rest
    # come back as "N/A" and would otherwise mask the real one.
    statuses = [s for s in df.get("risk_status", pd.Series(dtype=str)) if s and s != "N/A"]
    out["risk_status"] = statuses[0] if statuses else None

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
    return out


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
