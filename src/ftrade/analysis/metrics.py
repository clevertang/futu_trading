"""净值曲线与风险指标。"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .trades import _in_range

TRADING_DAYS = 252


def equity_curve(
    account_snapshots: pd.DataFrame,
    fx=None,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """按日汇总所有账户的总资产，换算到 fx.base。

    Snapshots are stored in whatever `analysis.base_currency` was configured at
    sync time -- per row, via its own `currency` column, so a config change
    between syncs is handled correctly rather than assumed constant. Without
    `fx` this returns the stored figures unconverted, which is only correct
    when the caller's requested base happens to match the sync-time currency;
    every caller in this codebase should pass `fx`.

    ``start``/``end`` filter by ``snap_date``. Note this re-bases `cum_ret` and
    `drawdown` to the first snapshot *inside* the window, not the account's
    inception -- the point of a period view is "how did this stretch go",
    not a fragment of the all-time curve with the early part clipped off.
    """
    if account_snapshots is None or account_snapshots.empty:
        return pd.DataFrame(columns=["snap_date", "total_assets"])
    snaps = account_snapshots.copy()
    if start or end:
        snaps = snaps[_in_range(snaps["snap_date"].astype(str), start, end)]
        if snaps.empty:
            return pd.DataFrame(columns=["snap_date", "total_assets"])
    if fx is not None:
        currencies = snaps["currency"] if "currency" in snaps else [None] * len(snaps)
        snaps["total_assets"] = [
            fx.to_base(v, c) or 0.0 for v, c in zip(snaps["total_assets"], currencies, strict=True)
        ]
    if "acc_id" in snaps:
        # One account missing a day (its sync failed) must not read as that
        # account's assets vanishing: fill the gap from its previous value.
        # Only gaps *between* an account's snapshots are filled -- before its
        # first one it contributes nothing, and once it stops reporting (closed
        # or migrated) it is not carried forward forever.
        wide = snaps.pivot_table(
            index="snap_date", columns="acc_id", values="total_assets", aggfunc="sum"
        ).sort_index()
        total = wide.ffill(limit_area="inside").sum(axis=1, min_count=1)
        df = total.rename("total_assets").reset_index()
    else:
        df = (
            snaps.groupby("snap_date", as_index=False)["total_assets"]
            .sum()
            .sort_values("snap_date")
            .reset_index(drop=True)
        )
    df["ret"] = df["total_assets"].pct_change()
    df["cum_ret"] = df["total_assets"] / df["total_assets"].iloc[0] - 1
    df["peak"] = df["total_assets"].cummax()
    df["drawdown"] = df["total_assets"] / df["peak"] - 1
    return df


def risk_metrics(curve: pd.DataFrame, risk_free_rate: float = 0.03) -> dict:
    """注意：资金快照包含出入金，这里的收益率仅供参考，不等同于时间加权收益率(TWR)。"""
    if curve is None or len(curve) < 3:
        return {"note": "净值样本不足（需要至少 3 天快照），先跑几天 sync 再看"}
    rets = curve["ret"].dropna()
    if rets.empty:
        return {"note": "净值样本不足"}

    days = len(curve)
    first, last = curve["snap_date"].iloc[0], curve["snap_date"].iloc[-1]
    # Snapshots are taken whenever a sync runs -- gaps and weekends are
    # normal -- so annualise by elapsed calendar time, and scale volatility by
    # how many returns per year were actually observed.
    years = (pd.Timestamp(last) - pd.Timestamp(first)).days / 365.25
    periods_per_year = len(rets) / years if years > 0 else TRADING_DAYS
    total_ret = float(curve["total_assets"].iloc[-1] / curve["total_assets"].iloc[0] - 1)
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if (total_ret > -1 and years > 0) else None
    scale = math.sqrt(periods_per_year)
    vol = float(rets.std(ddof=1) * scale) if len(rets) > 1 else None
    sharpe = (ann_ret - risk_free_rate) / vol if (ann_ret is not None and vol) else None
    # Downside deviation: shortfall below zero, averaged over *all* returns.
    shortfall = np.minimum(rets.to_numpy(dtype=float), 0.0)
    dvol = float(math.sqrt(np.mean(shortfall**2)) * scale) if shortfall.any() else None
    sortino = (ann_ret - risk_free_rate) / dvol if (ann_ret is not None and dvol) else None

    return {
        "days": days,
        "start_date": first,
        "end_date": last,
        "start_assets": round(float(curve["total_assets"].iloc[0]), 2),
        "end_assets": round(float(curve["total_assets"].iloc[-1]), 2),
        "total_return": round(total_ret, 4),
        "annualized_return": round(ann_ret, 4) if ann_ret is not None else None,
        "volatility": round(vol, 4) if vol is not None else None,
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "sortino": round(sortino, 2) if sortino is not None else None,
        "max_drawdown": round(float(curve["drawdown"].min()), 4),
        "current_drawdown": round(float(curve["drawdown"].iloc[-1]), 4),
        "caveat": "快照口径含出入金，非 TWR；出入金频繁时收益率会失真",
    }


def position_volatility(klines: pd.DataFrame, window: int = 60) -> dict:
    """单标的年化波动与近期回撤，用来判断持仓的风险贡献。"""
    if klines is None or len(klines) < 10:
        return {}
    close = klines["close"].astype(float)
    rets = close.pct_change().dropna().tail(window)
    if rets.empty:
        return {}
    peak = close.cummax()
    return {
        "annual_vol": round(float(rets.std(ddof=1) * np.sqrt(TRADING_DAYS)), 4),
        "drawdown_from_peak": round(float(close.iloc[-1] / peak.iloc[-1] - 1), 4),
        "ret_60d": round(float(close.iloc[-1] / close.iloc[-min(len(close), 61)] - 1), 4),
    }
