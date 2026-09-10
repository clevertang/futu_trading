"""持仓结构分析：权重、集中度、市场/币种暴露。"""
from __future__ import annotations

import pandas as pd

from .fx import FX


def enrich(positions: pd.DataFrame, fx: FX) -> pd.DataFrame:
    if positions is None or positions.empty:
        return pd.DataFrame()
    df = positions.copy()
    df["market_val_base"] = [
        fx.to_base(mv, cur) for mv, cur in zip(df["market_val"], df["currency"])
    ]
    df["pl_val_base"] = [fx.to_base(v, cur) for v, cur in zip(df["pl_val"], df["currency"])]
    total = df["market_val_base"].dropna().sum()
    df["weight"] = df["market_val_base"] / total if total else 0.0
    return df.sort_values("market_val_base", ascending=False).reset_index(drop=True)


def summary(positions: pd.DataFrame, fx: FX, warn_threshold: float = 0.25) -> dict:
    df = enrich(positions, fx)
    if df.empty:
        return {"holdings": 0, "market_value": 0.0, "base_currency": fx.base}

    total = float(df["market_val_base"].dropna().sum())
    weights = df["weight"].fillna(0.0)
    hhi = float((weights**2).sum())

    by_market = (
        df.groupby("position_market")["market_val_base"].sum().sort_values(ascending=False)
    )
    by_currency = df.groupby("currency")["market_val_base"].sum().sort_values(ascending=False)

    unrealized = float(df["pl_val_base"].dropna().sum())
    cost = total - unrealized

    return {
        "base_currency": fx.base,
        "holdings": int(len(df)),
        "market_value": round(total, 2),
        "unrealized_pl": round(unrealized, 2),
        "unrealized_pl_pct": round(unrealized / cost * 100, 2) if cost else None,
        "hhi": round(hhi, 4),
        "effective_positions": round(1 / hhi, 2) if hhi else None,
        "top1_weight": round(float(weights.iloc[0]), 4) if len(weights) else None,
        "top5_weight": round(float(weights.head(5).sum()), 4),
        "concentrated": [
            {"code": r["code"], "name": r["stock_name"], "weight": round(float(r["weight"]), 4)}
            for _, r in df.iterrows()
            if float(r["weight"] or 0) >= warn_threshold
        ],
        "by_market": {k: round(float(v), 2) for k, v in by_market.items()},
        "by_currency": {k: round(float(v), 2) for k, v in by_currency.items()},
        "holdings_detail": [
            {
                "code": r["code"],
                "name": r["stock_name"],
                "market": r["position_market"],
                "currency": r["currency"],
                "qty": r["qty"],
                "cost_price": r["cost_price"],
                "last_price": r["nominal_price"],
                "market_val": r["market_val"],
                "market_val_base": None if pd.isna(r["market_val_base"]) else round(float(r["market_val_base"]), 2),
                "weight": None if pd.isna(r["weight"]) else round(float(r["weight"]), 4),
                "pl_val": r["pl_val"],
                "pl_ratio": r["pl_ratio"],
            }
            for _, r in df.iterrows()
        ],
    }
