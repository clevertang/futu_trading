"""净值曲线与风险指标。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def equity_curve(account_snapshots: pd.DataFrame) -> pd.DataFrame:
    """按日汇总所有账户的总资产。"""
    if account_snapshots is None or account_snapshots.empty:
        return pd.DataFrame(columns=["snap_date", "total_assets"])
    df = (
        account_snapshots.groupby("snap_date", as_index=False)["total_assets"]
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
    total_ret = float(curve["total_assets"].iloc[-1] / curve["total_assets"].iloc[0] - 1)
    ann_ret = (1 + total_ret) ** (TRADING_DAYS / max(days, 1)) - 1 if total_ret > -1 else None
    vol = float(rets.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(rets) > 1 else None
    sharpe = (ann_ret - risk_free_rate) / vol if (ann_ret is not None and vol) else None
    downside = rets[rets < 0]
    dvol = float(downside.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(downside) > 1 else None
    sortino = (ann_ret - risk_free_rate) / dvol if (ann_ret is not None and dvol) else None

    return {
        "days": days,
        "start_date": curve["snap_date"].iloc[0],
        "end_date": curve["snap_date"].iloc[-1],
        "start_assets": round(float(curve["total_assets"].iloc[0]), 2),
        "end_assets": round(float(curve["total_assets"].iloc[-1]), 2),
        "total_return": round(total_ret, 4),
        "annualized_return": round(ann_ret, 4) if ann_ret is not None else None,
        "volatility": round(vol, 4) if vol else None,
        "sharpe": round(sharpe, 2) if sharpe else None,
        "sortino": round(sortino, 2) if sortino else None,
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
        "ret_60d": round(float(close.iloc[-1] / close.iloc[-min(len(close), 60)] - 1), 4),
    }
