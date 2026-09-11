"""Trading costs.

Futu's deal feed carries no cost data, so realised P&L computed from deals
alone is a gross figure. On this account the gap is not a rounding detail:
fees over the full history come to roughly half the gross realised result.
"""

from __future__ import annotations

import pandas as pd

from .fx import FX
from .instruments import underlying_of


def fee_summary(fees: pd.DataFrame, fx: FX) -> dict:
    """Total fees paid, converted into the base currency and broken down."""
    if fees is None or fees.empty:
        return {"total": 0.0, "count": 0, "base_currency": fx.base}

    df = fees.copy()
    df["amount"] = pd.to_numeric(df["fee_amount"], errors="coerce").fillna(0.0)
    df["base"] = [
        fx.to_base(a, c) or 0.0 for a, c in zip(df["amount"], df.get("currency"), strict=True)
    ]
    df["year"] = df.get("create_time", pd.Series(dtype=str)).astype(str).str[:4]

    by_year = df.groupby("year")["base"].sum().sort_index()
    by_symbol = (
        df.assign(u=df.get("code").map(underlying_of))
        .groupby("u")["base"]
        .sum()
        .sort_values(ascending=False)
    )
    return {
        "base_currency": fx.base,
        "count": int(len(df)),
        "total": round(float(df["base"].sum()), 2),
        "by_year": {str(k): round(float(v), 2) for k, v in by_year.items() if k},
        "by_currency": {
            str(k): round(float(v), 2)
            for k, v in df.groupby("currency")["amount"].sum().items()
            if k
        },
        "top_symbols": [
            {"code": str(k), "fees": round(float(v), 2)} for k, v in by_symbol.head(8).items() if k
        ],
    }
