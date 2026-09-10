"""离线 Mock 网关：无需 OpenD 也能跑通整条链路（开发/自测/demo）。

数据由固定随机种子生成，结果可复现。
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

import pandas as pd

ACC_ID = 900000001

UNIVERSE = [
    # code, name, market, currency, 起始价
    ("HK.00700", "腾讯控股", "HK", "HKD", 330.0),
    ("HK.09988", "阿里巴巴-W", "HK", "HKD", 78.0),
    ("US.AAPL", "苹果", "US", "USD", 175.0),
    ("US.MSFT", "微软", "US", "USD", 330.0),
    ("US.VOO", "标普500ETF", "US", "USD", 400.0),
]


def _seeded(code: str) -> random.Random:
    return random.Random(hash(code) & 0xFFFF)


def _price_series(code: str, base: float, days: int) -> dict[str, float]:
    rnd = _seeded(code)
    out: dict[str, float] = {}
    px = base
    start = date.today() - timedelta(days=days)
    for i in range(days + 1):
        d = start + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        px = max(1.0, px * (1 + rnd.gauss(0.0004, 0.016)))
        out[d.isoformat()] = round(px, 3)
    return out


class MockGateway:
    def __init__(self, cfg, days: int = 500):
        self.cfg = cfg
        self.days = days
        self.prices = {c: _price_series(c, base, days) for c, _n, _m, _cur, base in UNIVERSE}
        self._deals = self._make_deals()

    def __enter__(self) -> MockGateway:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def close(self) -> None:
        return None

    # ---------- 造数 ----------

    def _make_deals(self) -> pd.DataFrame:
        rows = []
        n = 0
        for code, name, market, _cur, _base in UNIVERSE:
            rnd = _seeded(code + "deals")
            dates = sorted(self.prices[code])
            # 低频：每个标的 6~12 笔
            picks = sorted(rnd.sample(dates[10:-5], rnd.randint(6, 12)))
            held = 0
            for d in picks:
                px = self.prices[code][d]
                lot = 100 if market == "HK" else 10
                if held <= 0 or rnd.random() < 0.6:
                    side, qty = "BUY", lot * rnd.randint(1, 5)
                    held += qty
                else:
                    qty = min(held, lot * rnd.randint(1, 3))
                    if qty <= 0:
                        continue
                    side, held = "SELL", held - qty
                n += 1
                rows.append(
                    {
                        "deal_id": f"MOCK{n:06d}",
                        "order_id": f"ORD{n:06d}",
                        "code": code,
                        "stock_name": name,
                        "trd_side": side,
                        "deal_market": market,
                        "qty": float(qty),
                        "price": px,
                        "create_time": f"{d} 10:{rnd.randint(10, 59):02d}:00",
                        "status": "OK",
                    }
                )
        return pd.DataFrame(rows).sort_values("create_time").reset_index(drop=True)

    # ---------- Gateway 接口 ----------

    def get_accounts(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "acc_id": ACC_ID,
                    "trd_env": "SIMULATE",
                    "acc_type": "MARGIN",
                    "security_firm": "MOCK",
                    "card_num": "MOCK-0001",
                    "trdmarket_auth": "HK,US",
                    "acc_status": "ACTIVE",
                }
            ]
        )

    def get_positions(self, acc_id: int) -> pd.DataFrame:
        deals = self._deals
        rows = []
        for code, name, market, cur, _base in UNIVERSE:
            sub = deals[deals["code"] == code]
            signed = sub.apply(
                lambda r: r["qty"] if r["trd_side"] == "BUY" else -r["qty"], axis=1
            )
            qty = float(signed.sum()) if len(sub) else 0.0
            if qty <= 0:
                continue
            buys = sub[sub["trd_side"] == "BUY"]
            cost = float((buys["qty"] * buys["price"]).sum() / max(buys["qty"].sum(), 1))
            last = self.prices[code][max(self.prices[code])]
            mv = qty * last
            rows.append(
                {
                    "code": code,
                    "stock_name": name,
                    "position_side": "LONG",
                    "position_market": market,
                    "currency": cur,
                    "qty": qty,
                    "can_sell_qty": qty,
                    "nominal_price": last,
                    "cost_price": round(cost, 3),
                    "diluted_cost": round(cost, 3),
                    "market_val": round(mv, 2),
                    "pl_val": round(mv - qty * cost, 2),
                    "pl_ratio": round((last / cost - 1) * 100, 2),
                    "today_pl_val": 0.0,
                    "unrealized_pl": round(mv - qty * cost, 2),
                    "realized_pl": 0.0,
                }
            )
        return pd.DataFrame(rows)

    def get_account_info(self, acc_id: int, currency: str) -> dict[str, Any]:
        pos = self.get_positions(acc_id)
        fx = self.cfg.analysis.fx_rates or {}
        mv = sum(
            float(r["market_val"]) * float(fx.get(r["currency"], 1.0))
            for _, r in pos.iterrows()
        )
        cash = 250_000.0
        return {
            "total_assets": round(mv + cash, 2),
            "securities_assets": round(mv, 2),
            "cash": cash,
            "frozen_cash": 0.0,
            "market_val": round(mv, 2),
            "power": cash * 2,
            "risk_status": "LEVEL1",
        }

    def get_history_deals(self, acc_id: int, start: str, end: str) -> pd.DataFrame:
        d = self._deals
        return d[(d["create_time"] >= start[:10]) & (d["create_time"] <= end[:10] + " 23:59:59")].copy()

    def get_history_orders(self, acc_id: int, start: str, end: str) -> pd.DataFrame:
        d = self.get_history_deals(acc_id, start, end)
        if d.empty:
            return pd.DataFrame()
        out = d.rename(columns={"deal_market": "market"}).copy()
        out["order_type"] = "NORMAL"
        out["order_status"] = "FILLED_ALL"
        out["dealt_qty"] = out["qty"]
        out["dealt_avg_price"] = out["price"]
        out["currency"] = out["code"].str.startswith("HK.").map({True: "HKD", False: "USD"})
        out["updated_time"] = out["create_time"]
        return out[
            [
                "order_id", "code", "stock_name", "trd_side", "order_type", "order_status",
                "qty", "price", "dealt_qty", "dealt_avg_price", "currency",
                "create_time", "updated_time",
            ]
        ]

    def get_klines(self, code: str, start: str, end: str) -> pd.DataFrame:
        series = self.prices.get(code, {})
        rows = []
        for d, px in series.items():
            if start[:10] <= d <= end[:10]:
                rows.append(
                    {
                        "time_key": f"{d} 00:00:00",
                        "open": px,
                        "high": round(px * 1.01, 3),
                        "low": round(px * 0.99, 3),
                        "close": px,
                        "volume": 1_000_000,
                    }
                )
        return pd.DataFrame(rows)
