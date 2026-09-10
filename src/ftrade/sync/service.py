"""增量同步：把富途的数据落到本地 SQLite。

富途只保留有限的历史，且历史查询单次最多 90 天，
所以「自己存一份」是做长期归因分析的前提。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Iterator

import pandas as pd

from ..storage import repo
from ..storage.db import Database

log = logging.getLogger(__name__)

OVERLAP_DAYS = 3  # 回退几天重拉，容忍延迟成交/数据修正


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _windows(start: str, end: str, days: int) -> Iterator[tuple[str, str]]:
    cur = datetime.strptime(start[:10], "%Y-%m-%d").date()
    last = datetime.strptime(end[:10], "%Y-%m-%d").date()
    while cur <= last:
        stop = min(cur + timedelta(days=days - 1), last)
        yield cur.isoformat(), stop.isoformat()
        cur = stop + timedelta(days=1)


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _text(v: Any) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (list, tuple)):
        return ",".join(str(x) for x in v)
    return str(v)


class SyncService:
    def __init__(self, db: Database, gateway, cfg):
        self.db = db
        self.gw = gateway
        self.cfg = cfg

    # ---------- 账户 ----------

    def sync_accounts(self) -> list[int]:
        df = self.gw.get_accounts()
        if df.empty:
            log.warning("没有查到任何交易账户")
            return []
        rows = [
            {
                "acc_id": int(r["acc_id"]),
                "trd_env": _text(r.get("trd_env")),
                "acc_type": _text(r.get("acc_type")),
                "security_firm": _text(r.get("security_firm")),
                "card_num": _text(r.get("card_num")),
                "trdmarket_auth": _text(r.get("trdmarket_auth")),
                "acc_status": _text(r.get("acc_status")),
                "updated_at": _now(),
            }
            for _, r in df.iterrows()
        ]
        self.db.upsert("accounts", rows)
        log.info("同步账户 %d 个", len(rows))
        return [r["acc_id"] for r in rows]

    def sync_account_info(self, acc_id: int) -> None:
        cur = self.cfg.analysis.base_currency
        info = self.gw.get_account_info(acc_id, cur)
        if not info:
            return
        row = {
            "snap_date": _today(),
            "acc_id": acc_id,
            "currency": cur,
            "total_assets": _num(info.get("total_assets")),
            "securities_assets": _num(info.get("securities_assets")),
            "cash": _num(info.get("cash")),
            "frozen_cash": _num(info.get("frozen_cash")),
            "market_val": _num(info.get("market_val")) or _num(info.get("securities_assets")),
            "power": _num(info.get("power")),
            "risk_status": _text(info.get("risk_status")),
            "raw": pd.Series(info).astype(str).to_json(),
            "synced_at": _now(),
        }
        self.db.upsert("account_snapshots", [row])
        log.info("账户 %s 资金快照已更新（%s）", acc_id, cur)

    # ---------- 持仓 ----------

    def sync_positions(self, acc_id: int) -> int:
        df = self.gw.get_positions(acc_id)
        if df is None or df.empty:
            log.info("账户 %s 当前无持仓", acc_id)
            return 0
        snap = _today()
        rows = [
            {
                "snap_date": snap,
                "acc_id": acc_id,
                "code": _text(r.get("code")),
                "stock_name": _text(r.get("stock_name")),
                "position_side": _text(r.get("position_side")),
                "position_market": _text(r.get("position_market")),
                "currency": _text(r.get("currency")),
                "qty": _num(r.get("qty")),
                "can_sell_qty": _num(r.get("can_sell_qty")),
                "nominal_price": _num(r.get("nominal_price")),
                "cost_price": _num(r.get("cost_price")),
                "diluted_cost": _num(r.get("diluted_cost")),
                "market_val": _num(r.get("market_val")),
                "pl_val": _num(r.get("pl_val")),
                "pl_ratio": _num(r.get("pl_ratio")),
                "today_pl_val": _num(r.get("today_pl_val")),
                "unrealized_pl": _num(r.get("unrealized_pl")),
                "realized_pl": _num(r.get("realized_pl")),
                "synced_at": _now(),
            }
            for _, r in df.iterrows()
        ]
        n = self.db.upsert("position_snapshots", rows)
        log.info("账户 %s 持仓快照 %d 条（%s）", acc_id, n, snap)
        return n

    # ---------- 成交 / 订单 ----------

    def sync_deals(self, acc_id: int, full: bool = False) -> int:
        key = f"deals_last_end:{acc_id}"
        start = self.cfg.sync.deals_start
        if not full:
            last = self.db.get_state(key)
            if last:
                start = (
                    datetime.strptime(last, "%Y-%m-%d").date() - timedelta(days=OVERLAP_DAYS)
                ).isoformat()
        end = _today()
        total = 0
        for w_start, w_end in _windows(start, end, self.cfg.sync.window_days):
            df = self.gw.get_history_deals(acc_id, f"{w_start} 00:00:00", f"{w_end} 23:59:59")
            if df is None or df.empty:
                continue
            rows = [
                {
                    "deal_id": _text(r.get("deal_id")),
                    "acc_id": acc_id,
                    "order_id": _text(r.get("order_id")),
                    "code": _text(r.get("code")),
                    "stock_name": _text(r.get("stock_name")),
                    "trd_side": _text(r.get("trd_side")),
                    "deal_market": _text(r.get("deal_market")),
                    "qty": _num(r.get("qty")),
                    "price": _num(r.get("price")),
                    "create_time": _text(r.get("create_time")),
                    "status": _text(r.get("status")),
                    "synced_at": _now(),
                }
                for _, r in df.iterrows()
                if _text(r.get("deal_id"))
            ]
            total += self.db.upsert("deals", rows)
            log.info("成交 %s ~ %s：%d 条", w_start, w_end, len(rows))
        self.db.set_state(key, end)
        return total

    def sync_orders(self, acc_id: int, full: bool = False) -> int:
        key = f"orders_last_end:{acc_id}"
        start = self.cfg.sync.deals_start
        if not full:
            last = self.db.get_state(key)
            if last:
                start = (
                    datetime.strptime(last, "%Y-%m-%d").date() - timedelta(days=OVERLAP_DAYS)
                ).isoformat()
        end = _today()
        total = 0
        for w_start, w_end in _windows(start, end, self.cfg.sync.window_days):
            df = self.gw.get_history_orders(acc_id, f"{w_start} 00:00:00", f"{w_end} 23:59:59")
            if df is None or df.empty:
                continue
            rows = [
                {
                    "order_id": _text(r.get("order_id")),
                    "acc_id": acc_id,
                    "code": _text(r.get("code")),
                    "stock_name": _text(r.get("stock_name")),
                    "trd_side": _text(r.get("trd_side")),
                    "order_type": _text(r.get("order_type")),
                    "order_status": _text(r.get("order_status")),
                    "qty": _num(r.get("qty")),
                    "price": _num(r.get("price")),
                    "dealt_qty": _num(r.get("dealt_qty")),
                    "dealt_avg_price": _num(r.get("dealt_avg_price")),
                    "currency": _text(r.get("currency")),
                    "create_time": _text(r.get("create_time")),
                    "updated_time": _text(r.get("updated_time")),
                    "synced_at": _now(),
                }
                for _, r in df.iterrows()
                if _text(r.get("order_id"))
            ]
            total += self.db.upsert("orders", rows)
        self.db.set_state(key, end)
        return total

    # ---------- 行情 ----------

    def sync_klines(self, codes: list[str] | None = None) -> int:
        codes = codes or repo.held_codes(self.db)
        if not codes:
            return 0
        start = (date.today() - timedelta(days=self.cfg.sync.kline_lookback_days)).isoformat()
        end = _today()
        total = 0
        for code in codes:
            try:
                df = self.gw.get_klines(code, start, end)
            except Exception as exc:  # 单个标的失败不影响整体
                log.warning("拉取 %s 日线失败：%s", code, exc)
                continue
            if df is None or df.empty:
                continue
            rows = [
                {
                    "code": code,
                    "time_key": _text(r.get("time_key")),
                    "open": _num(r.get("open")),
                    "high": _num(r.get("high")),
                    "low": _num(r.get("low")),
                    "close": _num(r.get("close")),
                    "volume": _num(r.get("volume")),
                }
                for _, r in df.iterrows()
            ]
            total += self.db.upsert("klines", rows)
        log.info("日线 %d 条（%d 个标的）", total, len(codes))
        return total

    # ---------- 一键 ----------

    def _syncable_accounts(self) -> list[int]:
        """只同步与配置 trd_env 一致的账户。

        富途会把模拟盘账户一并返回，但用 REAL 的上下文去查它们必然报错；
        已销户（DISABLED）的真实账户仍要同步，历史成交是有价值的。
        """
        want = self.cfg.futu.trd_env
        df = self.db.query("select acc_id, trd_env from accounts")
        if df.empty:
            return []
        keep = [int(r["acc_id"]) for _, r in df.iterrows() if _text(r["trd_env"]) == want]
        skipped = len(df) - len(keep)
        if skipped:
            log.info("跳过 %d 个非 %s 账户", skipped, want)
        return keep

    def sync_all(self, full: bool = False, with_klines: bool = True) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        all_ids = self.sync_accounts()
        acc_ids = self._syncable_accounts() or all_ids
        stats["accounts"] = len(acc_ids)
        deals = positions = orders = 0
        failed: list[str] = []
        for acc_id in acc_ids:
            # 单个账户失败不应中断整轮同步：销户账户、权限缺失的市场都会抛错。
            try:
                self.sync_account_info(acc_id)
                positions += self.sync_positions(acc_id)
                deals += self.sync_deals(acc_id, full=full)
                orders += self.sync_orders(acc_id, full=full)
            except Exception as exc:
                log.warning("账户 %s 同步失败，已跳过：%s", acc_id, exc)
                failed.append(f"{acc_id}: {exc}")
        stats.update(positions=positions, deals=deals, orders=orders)
        if failed:
            stats["failed"] = failed
        if with_klines:
            stats["klines"] = self.sync_klines()
        self.db.set_state("last_sync_at", _now())
        return stats
