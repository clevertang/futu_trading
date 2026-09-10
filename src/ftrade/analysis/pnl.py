"""基于成交流水的 FIFO 归因。

富途接口给的是「当前持仓的浮盈」，做长期复盘需要自己把每一笔买卖配对成
完整的一轮交易（round trip），才能算胜率、持有期、已实现盈亏。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime

import pandas as pd

BUY_WORDS = {"BUY", "BUY_BACK", "TrdSide.BUY"}
SELL_WORDS = {"SELL", "SELL_SHORT", "TrdSide.SELL"}


def _side(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).upper().replace("TRDSIDE.", "")
    if s.startswith("BUY"):
        return "BUY"
    if s.startswith("SELL"):
        return "SELL"
    return None


def _ts(raw: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(raw)[:26], fmt)
        except ValueError:
            continue
    return datetime.min


@dataclass
class RoundTrip:
    code: str
    stock_name: str | None
    qty: float
    open_time: str
    close_time: str
    open_price: float
    close_price: float
    pnl: float
    pnl_pct: float
    holding_days: int
    currency: str | None


def fifo_round_trips(deals: pd.DataFrame, currency_of=None) -> pd.DataFrame:
    """把成交流水按 FIFO 配对成已平仓的交易。"""
    if deals is None or deals.empty:
        return pd.DataFrame(columns=[f.name for f in RoundTrip.__dataclass_fields__.values()])

    df = deals.copy()
    df["_side"] = df["trd_side"].map(_side)
    df = df[df["_side"].notna()]
    df["_ts"] = df["create_time"].map(_ts)
    df = df.sort_values("_ts")

    trips: list[RoundTrip] = []
    for code, sub in df.groupby("code"):
        lots: deque[list] = deque()  # [qty, price, ts]
        name = sub["stock_name"].dropna().iloc[0] if sub["stock_name"].notna().any() else None
        cur = currency_of(code) if currency_of else None
        for _, r in sub.iterrows():
            qty, price, ts = float(r["qty"] or 0), float(r["price"] or 0), r["_ts"]
            if qty <= 0:
                continue
            if r["_side"] == "BUY":
                lots.append([qty, price, ts])
                continue
            remaining = qty
            while remaining > 1e-9 and lots:
                lot = lots[0]
                take = min(lot[0], remaining)
                pnl = (price - lot[1]) * take
                cost = lot[1] * take
                trips.append(
                    RoundTrip(
                        code=code,
                        stock_name=name,
                        qty=take,
                        open_time=lot[2].strftime("%Y-%m-%d"),
                        close_time=ts.strftime("%Y-%m-%d"),
                        open_price=lot[1],
                        close_price=price,
                        pnl=round(pnl, 2),
                        pnl_pct=round(pnl / cost * 100, 2) if cost else 0.0,
                        holding_days=max((ts - lot[2]).days, 0),
                        currency=cur,
                    )
                )
                lot[0] -= take
                remaining -= take
                if lot[0] <= 1e-9:
                    lots.popleft()
            # remaining > 0 说明存在融券/数据缺口，忽略以免污染统计

    return pd.DataFrame([asdict(t) for t in trips])


def open_lots(deals: pd.DataFrame) -> pd.DataFrame:
    """FIFO 剩余未平仓批次，用于核对本地推算与券商持仓是否一致。"""
    if deals is None or deals.empty:
        return pd.DataFrame(columns=["code", "qty", "avg_cost"])
    df = deals.copy()
    df["_side"] = df["trd_side"].map(_side)
    df = df[df["_side"].notna()].copy()
    df["_ts"] = df["create_time"].map(_ts)
    df = df.sort_values("_ts")

    rows = []
    for code, sub in df.groupby("code"):
        lots: deque[list] = deque()
        for _, r in sub.iterrows():
            qty, price = float(r["qty"] or 0), float(r["price"] or 0)
            if r["_side"] == "BUY":
                lots.append([qty, price])
            else:
                remaining = qty
                while remaining > 1e-9 and lots:
                    take = min(lots[0][0], remaining)
                    lots[0][0] -= take
                    remaining -= take
                    if lots[0][0] <= 1e-9:
                        lots.popleft()
        total = sum(l[0] for l in lots)
        if total > 1e-9:
            cost = sum(l[0] * l[1] for l in lots) / total
            rows.append({"code": code, "qty": round(total, 4), "avg_cost": round(cost, 4)})
    return pd.DataFrame(rows)
