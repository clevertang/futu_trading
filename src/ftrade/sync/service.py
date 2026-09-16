"""增量同步：把富途的数据落到本地 SQLite。

富途只保留有限的历史，且历史查询单次最多 90 天，
所以「自己存一份」是做长期归因分析的前提。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from ..analysis.instruments import option_expiry, underlying_of
from ..storage import repo
from ..storage.db import Database

log = logging.getLogger(__name__)

OVERLAP_DAYS = 3  # 回退几天重拉，容忍延迟成交/数据修正

# A symbol is treated as no longer quoting after this many *consecutive*
# failures, which a transient OpenD hiccup or a rate-limit retry never reaches.
# It is tried again once the cooldown lapses, so a re-listing or a broker-side
# backfill is eventually picked up rather than written off permanently.
DEAD_CODE_FAILURES = 3
DEAD_CODE_RETRY_DAYS = 30


def quotable_codes(codes: list[str], as_of: date | None = None) -> list[str]:
    """Narrow a traded-code list to what a K-line fetch can actually use.

    Two corrections, both of which cost real time on this account:

    An expired option contract returns "Unknown stock" from the quote API --
    documented, unavoidable, and 674 of the 763 codes ever traded here. Each
    one was still requested individually against a 60-per-30s limit, close to
    six minutes of guaranteed failures that a daily cron paid every morning.

    Conversely a name only ever traded through options never appears in the
    deal feed under its own symbol, so its underlying was never requested at
    all -- ten of them here, including QQQ and AAPL. Those are exactly the
    prices needed to say where a strike sat relative to spot, so implied
    underlyings are pulled in even though they were never traded directly.
    """
    as_of = as_of or date.today()
    keep: set[str] = set()
    for code in codes:
        expiry = option_expiry(code)
        if expiry is None:
            keep.add(code)  # a plain stock or ETF
            continue
        if expiry >= as_of:
            keep.add(code)  # a live contract still has quotes
        underlying = underlying_of(code)
        if underlying and underlying != code:
            keep.add(underlying)
    return sorted(keep)


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

    def sync_order_fees(self, acc_id: int, batch: int = 300) -> int:
        """Fetch fees for orders that do not have them yet.

        Fees only exist per order, and only for orders that actually filled, so
        this walks the local orders table rather than a date window. Already
        fetched orders are skipped, which makes repeated runs cheap.
        """
        df = self.db.query(
            "SELECT o.order_id FROM orders o "
            "LEFT JOIN order_fees f ON f.order_id = o.order_id "
            "WHERE o.acc_id = ? AND f.order_id IS NULL",
            [acc_id],
        )
        if df.empty:
            return 0
        ids = [str(i) for i in df["order_id"].tolist()]
        total = 0
        for i in range(0, len(ids), batch):
            chunk = ids[i : i + batch]
            try:
                fees = self.gw.get_order_fees(acc_id, chunk)
            except Exception as exc:
                log.warning("账户 %s 订单费用查询失败：%s", acc_id, exc)
                break
            if fees is None or fees.empty:
                continue
            rows = [
                {
                    "order_id": _text(r.get("order_id")),
                    "acc_id": acc_id,
                    "fee_amount": _num(r.get("fee_amount")),
                    "currency": None,
                    "details": _text(r.get("fee_details")),
                    "synced_at": _now(),
                }
                for _, r in fees.iterrows()
                if _text(r.get("order_id"))
            ]
            total += self.db.upsert("order_fees", rows)
        log.info("账户 %s 订单费用 %d 条", acc_id, total)
        return total

    def sync_cash_flows(self, acc_id: int, start: str, end: str | None = None) -> int:
        """Walk clearing dates pulling non-trade cash movements.

        Dividends, withholding tax, interest and transfers never appear in the
        deal feed, so P&L built from deals alone omits all of them. Futu only
        answers one clearing date at a time for this account -- a range is
        rejected -- so this is inherently a day-by-day walk, rate limited to 20
        requests per 30s.

        Progress is checkpointed per account so an interrupted backfill resumes
        instead of starting over, and weekends are skipped since they settle
        nothing.
        """
        key = f"cashflow_last_date:{acc_id}"
        last = self.db.get_state(key)
        begin = date.fromisoformat(last) + timedelta(days=1) if last else date.fromisoformat(start)
        finish = date.fromisoformat(end) if end else date.fromisoformat(_today())
        total = 0
        day = begin
        while day <= finish:
            if day.weekday() >= 5:  # settlement does not happen at weekends
                day += timedelta(days=1)
                continue
            iso = day.isoformat()
            try:
                df = self.gw.get_cash_flow(acc_id, iso)
            except Exception as exc:
                log.warning(
                    "账户 %s 资金流水 %s 失败，已中断（下次从此处续）：%s", acc_id, iso, exc
                )
                break
            if df is not None and not df.empty:
                rows = [
                    {
                        "cashflow_id": _text(r.get("cashflow_id")),
                        "acc_id": acc_id,
                        "clearing_date": _text(r.get("clearing_date")),
                        "settlement_date": _text(r.get("settlement_date")),
                        "currency": _text(r.get("currency")),
                        "cashflow_type": _text(r.get("cashflow_type")),
                        "direction": _text(r.get("cashflow_direction")),
                        "amount": _num(r.get("cashflow_amount")),
                        "remark": _text(r.get("cashflow_remark")),
                        "synced_at": _now(),
                    }
                    for _, r in df.iterrows()
                    if _text(r.get("cashflow_id"))
                ]
                total += self.db.upsert("cash_flows", rows)
            self.db.set_state(key, iso)
            day += timedelta(days=1)
        if total:
            log.info("账户 %s 资金流水 %d 条（至 %s）", acc_id, total, self.db.get_state(key))
        return total

    # ---------- 行情 ----------

    def _live_codes(self, codes: list[str]) -> tuple[list[str], int]:
        """Drop symbols that have failed often enough to look permanently dead.

        Retried once the cooldown lapses, so a re-listing or a broker-side
        backfill is picked up eventually rather than never.
        """
        status = repo.quote_status(self.db)
        if status.empty:
            return codes, 0
        cutoff = (date.today() - timedelta(days=DEAD_CODE_RETRY_DAYS)).isoformat()
        dead = {
            str(r["code"])
            for _, r in status.iterrows()
            if int(r["fail_count"] or 0) >= DEAD_CODE_FAILURES
            and str(r["last_failed_at"] or "")[:10] > cutoff
        }
        keep = [c for c in codes if c not in dead]
        return keep, len(codes) - len(keep)

    def _record_quote(self, code: str, error: str | None) -> None:
        """Note one fetch outcome. Success clears the streak; failure extends it.

        The whole prior row is read and carried forward because `upsert` is an
        INSERT OR REPLACE: writing a partial row would blank the columns left
        out, so a failure would erase the last success and vice versa --
        exactly the history that makes this table worth reading.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prior = self.db.query(
            "SELECT fail_count, last_error, last_failed_at, last_ok_at "
            "FROM quote_status WHERE code = ?",
            [code],
        )
        row = {} if prior.empty else prior.iloc[0].to_dict()
        merged = {
            "code": code,
            "fail_count": int(row.get("fail_count") or 0),
            "last_error": row.get("last_error"),
            "last_failed_at": row.get("last_failed_at"),
            "last_ok_at": row.get("last_ok_at"),
        }
        if error is None:
            merged.update(fail_count=0, last_error=None, last_ok_at=now)
        else:
            merged.update(
                fail_count=merged["fail_count"] + 1, last_error=error[:300], last_failed_at=now
            )
        self.db.upsert("quote_status", [merged])

    def sync_klines(self, codes: list[str] | None = None) -> int:
        codes = codes or quotable_codes(repo.held_codes(self.db))
        if not codes:
            return 0
        codes, skipped = self._live_codes(codes)
        if skipped:
            log.info(
                "跳过 %d 个长期无行情的标的（连续失败 >= %d 次，%d 天后重试）",
                skipped,
                DEAD_CODE_FAILURES,
                DEAD_CODE_RETRY_DAYS,
            )
        start = (date.today() - timedelta(days=self.cfg.sync.kline_lookback_days)).isoformat()
        end = _today()
        total = 0
        for code in codes:
            try:
                df = self.gw.get_klines(code, start, end)
            except Exception as exc:  # 单个标的失败不影响整体
                log.warning("拉取 %s 日线失败：%s", code, exc)
                self._record_quote(code, str(exc))
                continue
            self._record_quote(code, None)
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

    def sync_all(
        self, full: bool = False, with_klines: bool = True, with_cash_flow: bool = False
    ) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        all_ids = self.sync_accounts()
        acc_ids = self._syncable_accounts() or all_ids
        stats["accounts"] = len(acc_ids)
        deals = positions = orders = fees = cash = 0
        failed: list[str] = []
        for acc_id in acc_ids:
            # 单个账户失败不应中断整轮同步：销户账户、权限缺失的市场都会抛错。
            try:
                self.sync_account_info(acc_id)
                positions += self.sync_positions(acc_id)
                deals += self.sync_deals(acc_id, full=full)
                orders += self.sync_orders(acc_id, full=full)
                fees += self.sync_order_fees(acc_id)
                if with_cash_flow:
                    # Bounded by the account's own trading history: a closed
                    # account settles nothing after its last deal.
                    first = self.db.scalar(
                        "SELECT MIN(create_time) FROM deals WHERE acc_id = ?", [acc_id]
                    )
                    cash += self.sync_cash_flows(
                        acc_id, str(first)[:10] if first else self.cfg.sync.deals_start
                    )
            except Exception as exc:
                log.warning("账户 %s 同步失败，已跳过：%s", acc_id, exc)
                failed.append(f"{acc_id}: {exc}")
        stats.update(
            positions=positions, deals=deals, orders=orders, order_fees=fees, cash_flows=cash
        )
        if failed:
            stats["failed"] = failed
        if with_klines:
            stats["klines"] = self.sync_klines()
        self.db.set_state("last_sync_at", _now())
        return stats
