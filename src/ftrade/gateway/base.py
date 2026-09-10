"""网关抽象。

设计约束：本层**只读**。任何下单/改单/撤单/解锁交易的能力都不在接口里，
也不允许在实现中出现，这样即使上层代码写错也不可能触发真实交易。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import pandas as pd


class GatewayError(RuntimeError):
    pass


# 禁止出现在网关实现中的富途 SDK 方法名，由 tests/test_readonly.py 静态校验
FORBIDDEN_CALLS = (
    "place_order",
    "modify_order",
    "cancel_order",
    "unlock_trade",
    "acctradinginfo_query",  # 该接口带下单预检语义，Phase 1 不需要
)


@runtime_checkable
class Gateway(Protocol):
    """只读数据源接口。所有方法返回规范化后的 DataFrame。"""

    def __enter__(self) -> Gateway: ...

    def __exit__(self, *exc: Any) -> None: ...

    def get_accounts(self) -> pd.DataFrame:
        """acc_id, trd_env, acc_type, security_firm, card_num, trdmarket_auth, acc_status"""
        ...

    def get_account_info(self, acc_id: int, currency: str) -> dict[str, Any]:
        """账户资金快照。"""
        ...

    def get_positions(self, acc_id: int) -> pd.DataFrame:
        """当前持仓。"""
        ...

    def get_history_deals(self, acc_id: int, start: str, end: str) -> pd.DataFrame:
        """区间历史成交（调用方负责按 90 天切片）。"""
        ...

    def get_history_orders(self, acc_id: int, start: str, end: str) -> pd.DataFrame:
        """区间历史订单。"""
        ...

    def get_klines(self, code: str, start: str, end: str) -> pd.DataFrame:
        """日线：time_key, open, high, low, close, volume"""
        ...


def to_str(value: Any) -> str | None:
    """把富途的枚举/列表规范化成可入库的字符串。"""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)
