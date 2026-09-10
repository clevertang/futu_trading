"""交易行为统计：低频交易者最该看的是「我是不是把好票卖早了」。"""

from __future__ import annotations

import pandas as pd


def behavior(trips: pd.DataFrame, deals: pd.DataFrame) -> dict:
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
