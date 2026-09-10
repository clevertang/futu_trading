"""交易行为统计：低频交易者最该看的是「我是不是把好票卖早了」。"""

from __future__ import annotations

import pandas as pd


def behavior(trips: pd.DataFrame, deals: pd.DataFrame, fx=None) -> dict:
    """Trading-behaviour statistics.

    ``fx`` converts each round trip into the base currency before aggregating.
    Without it, summing ``pnl`` across a mixed HK/US history adds HKD to USD as
    if they were the same unit.
    """
    out: dict = {}

    if deals is not None and not deals.empty:
        d = deals.copy()
        d["date"] = d["create_time"].astype(str).str[:10]
        d["month"] = d["date"].str[:7]
        d["turnover"] = d["qty"].astype(float) * d["price"].astype(float)
        months = d["month"].nunique() or 1
        out["deal_count"] = int(len(d))
        out["trading_days"] = int(d["date"].nunique())
        out["first_deal"] = d["date"].min()
        out["last_deal"] = d["date"].max()
        out["deals_per_month"] = round(len(d) / months, 2)
        out["turnover_by_month"] = {
            k: round(float(v), 2) for k, v in d.groupby("month")["turnover"].sum().items()
        }
        out["most_traded"] = [
            {"code": k, "deals": int(v)} for k, v in d["code"].value_counts().head(5).items()
        ]

    if trips is None or trips.empty:
        out["round_trips"] = 0
        return out

    t = trips.copy()
    if fx is not None and "currency" in t:
        t["pnl"] = [fx.to_base(p, c) or 0.0 for p, c in zip(t["pnl"], t["currency"], strict=True)]
    wins = t[t["pnl"] > 0]
    losses = t[t["pnl"] < 0]
    out.update(
        round_trips=int(len(t)),
        win_rate=round(len(wins) / len(t), 4),
        avg_win=round(float(wins["pnl"].mean()), 2) if len(wins) else 0.0,
        avg_loss=round(float(losses["pnl"].mean()), 2) if len(losses) else 0.0,
        profit_factor=(
            round(float(wins["pnl"].sum() / abs(losses["pnl"].sum())), 2)
            if len(losses) and losses["pnl"].sum() != 0
            else None
        ),
        realized_pnl=round(float(t["pnl"].sum()), 2),
        avg_holding_days=round(float(t["holding_days"].mean()), 1),
        median_holding_days=float(t["holding_days"].median()),
        avg_win_holding_days=round(float(wins["holding_days"].mean()), 1) if len(wins) else None,
        avg_loss_holding_days=round(float(losses["holding_days"].mean()), 1)
        if len(losses)
        else None,
        best_trade=t.loc[t["pnl"].idxmax()].to_dict() if len(t) else None,
        worst_trade=t.loc[t["pnl"].idxmin()].to_dict() if len(t) else None,
        realized_by_code=[
            {"code": k, "pnl": round(float(v), 2)}
            for k, v in t.groupby("code")["pnl"].sum().sort_values(ascending=False).items()
        ],
    )

    # 处置效应：赚钱的拿得比亏钱的短，说明「截断利润、放任亏损」
    if out.get("avg_win_holding_days") and out.get("avg_loss_holding_days"):
        out["disposition_effect"] = out["avg_win_holding_days"] < out["avg_loss_holding_days"]
    return out


def realized_curve(trips: pd.DataFrame, fx) -> list[dict]:
    """Cumulative realized P&L in base currency, one point per closing date.

    The account-snapshot equity curve only starts the day snapshots start, so a
    fresh install has nothing to plot for months. Round trips reach back as far
    as the deal history does, which is the whole point of syncing closed
    accounts.
    """
    if trips is None or trips.empty:
        return []
    t = trips.copy()
    t["date"] = t["close_time"].astype(str).str[:10]
    t["pnl_base"] = [fx.to_base(p, c) or 0.0 for p, c in zip(t["pnl"], t["currency"], strict=True)]
    daily = t.groupby("date", as_index=False).agg(
        pnl=("pnl_base", "sum"), trades=("pnl_base", "size")
    )
    daily = daily.sort_values("date")
    daily["cum_pnl"] = daily["pnl"].cumsum()
    return [
        {
            "date": str(r.date),
            "pnl": round(float(r.pnl), 2),
            "cum_pnl": round(float(r.cum_pnl), 2),
            "trades": int(r.trades),
        }
        for r in daily.itertuples()
    ]


def pnl_distribution(trips: pd.DataFrame, bucket_pct: float = 10.0) -> list[dict]:
    """Round trips bucketed by return percentage, for a histogram.

    Buckets are scale-free (percent, not currency) so a 200-share trade and a
    20,000-share trade land in the same place when the outcome was the same.
    """
    if trips is None or trips.empty or "pnl_pct" not in trips:
        return []
    pct = pd.to_numeric(trips["pnl_pct"], errors="coerce").dropna()
    if pct.empty:
        return []
    # Clamp the tails: a handful of -100% option expiries would otherwise
    # flatten every other bucket into invisibility.
    lo, hi = -50.0, 50.0
    clamped = pct.clip(lo, hi)
    edges = [lo + i * bucket_pct for i in range(int((hi - lo) / bucket_pct) + 1)]
    out = []
    for i in range(len(edges) - 1):
        left, right = edges[i], edges[i + 1]
        last = i == len(edges) - 2
        hit = (clamped >= left) & (clamped <= right if last else clamped < right)
        out.append({"left": left, "right": right, "count": int(hit.sum())})
    return out
