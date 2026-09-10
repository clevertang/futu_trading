"""富途 OpenAPI 只读网关。

需要本机运行 OpenD 网关程序（默认 127.0.0.1:11111）。
本模块**不导入也不调用**任何下单相关接口。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import pandas as pd

from .base import GatewayError, to_str

log = logging.getLogger(__name__)

# 富途开放接口的频率限制（官方文档口径），留出余量避免踩线：
#   历史成交 / 历史订单 / 持仓 / 资金：10 次 / 30 秒
#   历史日线：60 次 / 30 秒
HISTORY_MIN_INTERVAL = 3.2
KLINE_MIN_INTERVAL = 0.55
MAX_RETRIES = 5
_RATE_LIMIT_HINTS = ("high frequency", "频率", "Maximum")


class _Throttle:
    """把同一类请求之间的间隔拉开到 min_interval 秒。"""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delay = self.min_interval - (time.monotonic() - self._last)
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


def _futu():
    try:
        import futu  # type: ignore
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的友好提示
        raise GatewayError(
            "未安装 futu-api，请先 `pip install futu-api`，并启动 OpenD 网关。"
        ) from exc
    return futu


class FutuGateway:
    def __init__(self, cfg):
        self.cfg = cfg
        self._trade_ctxs: dict[str, Any] = {}
        self._quote_ctx: Any = None
        self._acc_market: dict[int, str] = {}
        self._th_history = _Throttle(HISTORY_MIN_INTERVAL)
        self._th_kline = _Throttle(KLINE_MIN_INTERVAL)

    # ---------- 生命周期 ----------

    def __enter__(self) -> "FutuGateway":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        for ctx in self._trade_ctxs.values():
            try:
                ctx.close()
            except Exception:  # pragma: no cover
                log.debug("关闭交易连接失败", exc_info=True)
        self._trade_ctxs.clear()
        if self._quote_ctx is not None:
            try:
                self._quote_ctx.close()
            finally:
                self._quote_ctx = None

    # ---------- 连接 ----------

    def _trade_ctx(self, market: str):
        if market in self._trade_ctxs:
            return self._trade_ctxs[market]
        futu = _futu()
        f = self.cfg.futu
        try:
            ctx = futu.OpenSecTradeContext(
                filter_trdmarket=getattr(futu.TrdMarket, market),
                host=f.host,
                port=f.port,
                security_firm=getattr(futu.SecurityFirm, f.security_firm),
            )
        except Exception as exc:
            raise GatewayError(
                f"连接 OpenD 失败（{f.host}:{f.port}），请确认网关已启动并已登录：{exc}"
            ) from exc
        self._trade_ctxs[market] = ctx
        return ctx

    def _quote(self):
        if self._quote_ctx is None:
            futu = _futu()
            self._quote_ctx = futu.OpenQuoteContext(host=self.cfg.futu.host, port=self.cfg.futu.port)
        return self._quote_ctx

    def _env(self):
        futu = _futu()
        return getattr(futu.TrdEnv, self.cfg.futu.trd_env)

    @staticmethod
    def _check(ret, data, what: str) -> pd.DataFrame:
        futu = _futu()
        if ret != futu.RET_OK:
            raise GatewayError(f"{what} 失败：{data}")
        return data

    def _request(self, throttle: "_Throttle", what: str, call):
        """限流 + 撞到频率限制时退避重试。call 返回 (ret, data) 或 (ret, data, page_key)。"""
        for attempt in range(MAX_RETRIES + 1):
            throttle.wait()
            result = call()
            ret, data = result[0], result[1]
            futu = _futu()
            if ret == futu.RET_OK:
                return result
            msg = str(data)
            if attempt >= MAX_RETRIES or not any(h in msg for h in _RATE_LIMIT_HINTS):
                raise GatewayError(f"{what} 失败：{data}")
            backoff = min(35.0, 5.0 * 2**attempt)
            log.warning("%s 触发限频，%.0fs 后重试（%d/%d）", what, backoff, attempt + 1, MAX_RETRIES)
            time.sleep(backoff)
        raise GatewayError(f"{what} 失败：重试 {MAX_RETRIES} 次仍被限频")

    # ---------- 查询 ----------

    def get_accounts(self) -> pd.DataFrame:
        rows: dict[int, dict[str, Any]] = {}
        for market in self.cfg.futu.markets:
            ctx = self._trade_ctx(market)
            df = self._check(*ctx.get_acc_list(), what=f"获取账户列表({market})")
            for _, r in df.iterrows():
                acc_id = int(r["acc_id"])
                self._acc_market.setdefault(acc_id, market)
                rows[acc_id] = {
                    "acc_id": acc_id,
                    "trd_env": to_str(r.get("trd_env")),
                    "acc_type": to_str(r.get("acc_type")),
                    "security_firm": to_str(r.get("security_firm")),
                    "card_num": to_str(r.get("card_num")),
                    "trdmarket_auth": to_str(r.get("trdmarket_auth")),
                    "acc_status": to_str(r.get("acc_status")),
                }
        return pd.DataFrame(list(rows.values()))

    def _ctx_for(self, acc_id: int):
        market = self._acc_market.get(acc_id)
        if market is None:
            self.get_accounts()
            market = self._acc_market.get(acc_id)
        if market is None:
            raise GatewayError(f"账户 {acc_id} 不在配置的市场 {self.cfg.futu.markets} 中")
        return self._trade_ctx(market)

    def get_account_info(self, acc_id: int, currency: str) -> dict[str, Any]:
        futu = _futu()
        ctx = self._ctx_for(acc_id)
        ret, df = self._request(
            self._th_history,
            f"获取账户 {acc_id} 资金",
            lambda: ctx.accinfo_query(
                trd_env=self._env(),
                acc_id=acc_id,
                refresh_cache=True,
                currency=getattr(futu.Currency, currency),
            ),
        )
        if df.empty:
            return {}
        return df.iloc[0].to_dict()

    def get_positions(self, acc_id: int) -> pd.DataFrame:
        ctx = self._ctx_for(acc_id)
        ret, df = self._request(
            self._th_history,
            "获取持仓",
            lambda: ctx.position_list_query(trd_env=self._env(), acc_id=acc_id, refresh_cache=True),
        )
        return df if df is not None else pd.DataFrame()

    def get_history_deals(self, acc_id: int, start: str, end: str) -> pd.DataFrame:
        ctx = self._ctx_for(acc_id)
        ret, df = self._request(
            self._th_history,
            f"获取历史成交 {start}~{end}",
            lambda: ctx.history_deal_list_query(
                start=start, end=end, trd_env=self._env(), acc_id=acc_id
            ),
        )
        return df if df is not None else pd.DataFrame()

    def get_history_orders(self, acc_id: int, start: str, end: str) -> pd.DataFrame:
        ctx = self._ctx_for(acc_id)
        ret, df = self._request(
            self._th_history,
            f"获取历史订单 {start}~{end}",
            lambda: ctx.history_order_list_query(
                start=start, end=end, trd_env=self._env(), acc_id=acc_id
            ),
        )
        return df if df is not None else pd.DataFrame()

    def get_klines(self, code: str, start: str, end: str) -> pd.DataFrame:
        futu = _futu()
        ctx = self._quote()
        frames: list[pd.DataFrame] = []
        page_key = None
        while True:
            _ret, data, page_key = self._request(
                self._th_kline,
                f"获取 {code} 日线",
                lambda pk=page_key: ctx.request_history_kline(
                    code,
                    start=start,
                    end=end,
                    ktype=futu.KLType.K_DAY,
                    autype=futu.AuType.QFQ,
                    max_count=1000,
                    page_req_key=pk,
                ),
            )
            frames.append(data)
            if page_key is None:
                break
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)
