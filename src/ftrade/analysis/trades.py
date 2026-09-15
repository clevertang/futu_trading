"""交易行为统计：低频交易者最该看的是「我是不是把好票卖早了」。"""

from __future__ import annotations

import pandas as pd

from .instruments import market_of, underlying_of


def _in_range(dates: pd.Series, start: str | None, end: str | None) -> pd.Series:
    """Boolean mask: date string (YYYY-MM-DD, or longer with a timestamp) falls
    within [start, end]. Both bounds are inclusive; either or both may be None
    for an open-ended range. Plain string comparison is safe here because
    every date column in this codebase is stored ISO-formatted, which sorts
    lexicographically the same as chronologically.
    """
    mask = pd.Series(True, index=dates.index)
    if start:
        mask &= dates >= start
    if end:
        # A bare "2026-09-08" must still include timestamped rows on that day
        # ("2026-09-08 19:59:05"), which sort *after* the bare date string.
        mask &= dates.str[:10] <= end[:10]
    return mask


def _by_market(trips: pd.DataFrame) -> list[dict]:
    """Realised P&L per market, counting stock and options together.

    A covered call and the shares it is written against are one position taken
    in one market; separating them would report two halves of the same trade.
    """
    if trips is None or trips.empty:
        return []
    t = trips.copy()
    t["_market"] = t["code"].map(market_of)
    t = t[t["_market"].notna()]
    if t.empty:
        return []
    grouped = t.groupby("_market")["pnl"].agg(["size", "sum"])
    rows = [
        {"market": str(market), "trips": int(r["size"]), "pnl": round(float(r["sum"]), 2)}
        for market, r in grouped.iterrows()
    ]
    return sorted(rows, key=lambda r: r["pnl"], reverse=True)


def behavior(
    trips: pd.DataFrame,
    deals: pd.DataFrame,
    fx=None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Trading-behaviour statistics, optionally scoped to a date range.

    ``fx`` converts each round trip into the base currency before aggregating.
    Without it, summing ``pnl`` across a mixed HK/US history adds HKD to USD as
    if they were the same unit.

    ``start``/``end`` filter deals by *when they happened* and round trips by
    *when they closed* -- not by when they opened. A trip opened before the
    window and closed inside it is a realised result of the window; a trip
    opened inside the window but still open contributes nothing to realised
    P&L yet regardless. This matches how a broker's own "period P&L" report
    works, and is why the filter is applied here rather than upstream on the
    deals feed FIFO pairing consumes -- pairing needs the full history to
    match a close against the right open, which can predate the window.
    """
    out: dict = {}

    if deals is not None and not deals.empty:
        d = deals.copy()
        if start or end:
            d = d[_in_range(d["create_time"].astype(str), start, end)]
        d["date"] = d["create_time"].astype(str).str[:10]
        d["month"] = d["date"].str[:7]
        d["turnover"] = d["qty"].astype(float) * d["price"].astype(float)
        # deals carries no currency column, so a mixed HK/US history would
        # otherwise add HKD notional straight onto USD as if they were the
        # same unit -- exactly the bug already fixed once for round-trip P&L
        # (pnl.py), just not yet here. currency_of mirrors that resolution:
        # a currently-held symbol's own currency, else the market prefix.
        if fx is not None and "currency" in d:
            d["turnover"] = [
                fx.to_base(v, c) or 0.0 for v, c in zip(d["turnover"], d["currency"], strict=True)
            ]
        months = d["month"].nunique() or 1
        out["deal_count"] = int(len(d))
        out["trading_days"] = int(d["date"].nunique())
        out["first_deal"] = d["date"].min() if not d.empty else None
        out["last_deal"] = d["date"].max() if not d.empty else None
        out["deals_per_month"] = round(len(d) / months, 2) if len(d) else 0.0
        out["turnover_by_month"] = {
            k: round(float(v), 2) for k, v in d.groupby("month")["turnover"].sum().items()
        }
        out["most_traded"] = [
            {"code": str(k), "deals": int(v)}
            for k, v in d["code"].map(underlying_of).value_counts().head(5).items()
        ]

    if trips is None or trips.empty:
        out["round_trips"] = 0
        return out

    t = trips.copy()
    if (start or end) and "close_time" in t:
        t = t[_in_range(t["close_time"].astype(str), start, end)]
    if t.empty:
        out["round_trips"] = 0
        return out
    if fx is not None and "currency" in t:
        t["pnl"] = [fx.to_base(p, c) or 0.0 for p, c in zip(t["pnl"], t["currency"], strict=True)]
    t["_close_date"] = t["close_time"].astype(str).str[:10] if "close_time" in t else ""
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
            for k, v in t.assign(_u=t["code"].map(underlying_of))
            .groupby("_u")["pnl"]
            .sum()
            .sort_values(ascending=False)
            .items()
        ],
        realized_by_market=_by_market(t),
        realized_by_month={
            k: round(float(v), 2)
            for k, v in t.groupby(t["_close_date"].str[:7])["pnl"].sum().sort_index().items()
        },
        realized_by_year={
            k: round(float(v), 2)
            for k, v in t.groupby(t["_close_date"].str[:4])["pnl"].sum().sort_index().items()
        },
    )

    # 处置效应：赚钱的拿得比亏钱的短，说明「截断利润、放任亏损」
    if out.get("avg_win_holding_days") and out.get("avg_loss_holding_days"):
        out["disposition_effect"] = out["avg_win_holding_days"] < out["avg_loss_holding_days"]
    return out


def realized_curve(
    trips: pd.DataFrame, fx, start: str | None = None, end: str | None = None
) -> list[dict]:
    """Cumulative realized P&L in base currency, one point per closing date.

    The account-snapshot equity curve only starts the day snapshots start, so a
    fresh install has nothing to plot for months. Round trips reach back as far
    as the deal history does, which is the whole point of syncing closed
    accounts.

    ``start``/``end`` filter by ``close_time``, same as ``behavior()`` -- a
    trip realised inside the window counts regardless of when it was opened.
    """
    if trips is None or trips.empty:
        return []
    t = trips.copy()
    if (start or end) and "close_time" in t:
        t = t[_in_range(t["close_time"].astype(str), start, end)]
    if t.empty:
        return []
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


def pnl_distribution(
    trips: pd.DataFrame,
    bucket_pct: float = 20.0,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """Round trips bucketed by return percentage, split by how they closed.

    Buckets are scale-free (percent, not currency) so a 200-share and a
    20,000-share trade land in the same place when the outcome was the same.

    The split matters more than it looks. A short option carried to expiry
    returns exactly 100% of its premium *by construction* -- there is no
    distribution to observe. Here that is 521 of 1,439 trips, and lumping them
    in with traded closes buries every other shape under one bar. Keeping the
    counts separate lets the pile be read as what it is rather than as an
    artefact.

    ``start``/``end`` filter by ``close_time``, same as ``behavior()``.
    """
    if trips is None or trips.empty or "pnl_pct" not in trips:
        return []
    df = trips.copy()
    if (start or end) and "close_time" in df:
        df = df[_in_range(df["close_time"].astype(str), start, end)]
    df["pnl_pct"] = pd.to_numeric(df["pnl_pct"], errors="coerce")
    df = df[df["pnl_pct"].notna()]
    if df.empty:
        return []

    # Clamp at the full loss / full premium boundaries rather than halfway, so
    # +100% gets an honestly labelled bucket instead of sharing one with +40%.
    lo, hi = -100.0, 100.0
    df["clamped"] = df["pnl_pct"].clip(lo, hi)
    reason = df.get("close_reason", pd.Series("TRADE", index=df.index)).fillna("TRADE")

    steps = int(round((hi - lo) / bucket_pct))
    out = []
    for i in range(steps):
        left = lo + i * bucket_pct
        right = left + bucket_pct
        last = i == steps - 1
        hit = (df["clamped"] >= left) & (df["clamped"] <= right if last else df["clamped"] < right)
        out.append(
            {
                "left": left,
                "right": right,
                "count": int(hit.sum()),
                "expiry": int((hit & (reason == "EXPIRY")).sum()),
                "trade": int((hit & (reason != "EXPIRY")).sum()),
            }
        )
    return out
