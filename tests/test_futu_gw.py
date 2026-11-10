"""FutuGateway against a scripted stand-in for the futu SDK -- no OpenD needed."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from ftrade.config import load_config
from ftrade.gateway import futu_gw
from ftrade.gateway.base import GatewayError

RET_OK, RET_ERROR = 0, -1


class _Enum:
    def __getattr__(self, name):
        return name


class _TradeCtx:
    """Answers get_acc_list per market; records every other call."""

    accounts: dict[str, list[int]] = {}
    instances: list["_TradeCtx"] = []

    def __init__(self, filter_trdmarket, host, port, security_firm):
        self.market = filter_trdmarket
        self.closed = False
        self.calls: list[str] = []
        _TradeCtx.instances.append(self)

    def get_acc_list(self):
        ids = _TradeCtx.accounts.get(self.market, [])
        return RET_OK, pd.DataFrame(
            [{"acc_id": i, "trd_env": "REAL", "acc_status": "ACTIVE"} for i in ids]
        )

    def position_list_query(self, **kw):
        self.calls.append("positions")
        return RET_OK, pd.DataFrame([{"code": "US.A", "qty": 1}])

    def close(self):
        self.closed = True


class _QuoteCtx:
    pages: list = []

    def __init__(self, host, port):
        self.closed = False

    def request_history_kline(self, code, page_req_key=None, **kw):
        return _QuoteCtx.pages.pop(0)

    def close(self):
        self.closed = True
        raise RuntimeError("quote close failed")


@pytest.fixture
def gw(monkeypatch):
    fake = SimpleNamespace(
        RET_OK=RET_OK,
        TrdMarket=_Enum(),
        SecurityFirm=_Enum(),
        TrdEnv=_Enum(),
        Currency=_Enum(),
        AuType=_Enum(),
        KLType=_Enum(),
        OpenSecTradeContext=_TradeCtx,
        OpenQuoteContext=_QuoteCtx,
    )
    monkeypatch.setattr(futu_gw, "_futu", lambda: fake)
    sleeps: list[float] = []
    monkeypatch.setattr(futu_gw.time, "sleep", sleeps.append)
    _TradeCtx.accounts = {"HK": [1], "US": [1, 2]}
    _TradeCtx.instances = []
    g = futu_gw.FutuGateway(load_config())
    g.sleeps = sleeps
    return g


def test_request_retries_a_rate_limit_then_succeeds(gw):
    answers = [(RET_ERROR, "请求频率太高"), (RET_ERROR, "high frequency"), (RET_OK, "data")]
    result = gw._request(futu_gw._Throttle(0), "x", lambda: answers.pop(0))
    assert result == (RET_OK, "data")
    assert [s for s in gw.sleeps if s >= 5] == [5.0, 10.0]  # exponential backoff


def test_request_gives_up_after_max_retries(gw):
    calls = []

    def limited():
        calls.append(1)
        return RET_ERROR, "high frequency"

    with pytest.raises(GatewayError, match="失败"):
        gw._request(futu_gw._Throttle(0), "x", limited)
    assert len(calls) == futu_gw.MAX_RETRIES + 1


def test_a_non_rate_limit_error_is_not_retried(gw):
    calls = []

    def broken():
        calls.append(1)
        return RET_ERROR, "Unknown stock"

    with pytest.raises(GatewayError, match="Unknown stock"):
        gw._request(futu_gw._Throttle(0), "x", broken)
    assert len(calls) == 1


def test_throttle_spaces_calls_by_its_interval(monkeypatch):
    clock = [100.0]
    slept: list[float] = []
    monkeypatch.setattr(futu_gw.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(futu_gw.time, "sleep", slept.append)
    t = futu_gw._Throttle(3.2)
    t.wait()
    clock[0] += 1.0
    t.wait()
    assert slept == [pytest.approx(2.2)]


def test_accounts_are_deduplicated_across_markets_and_mapped_to_one(gw):
    df = gw.get_accounts()
    assert sorted(df["acc_id"]) == [1, 2]
    assert gw._acc_market == {1: "HK", 2: "US"}


def test_an_account_outside_the_configured_markets_is_an_error(gw):
    with pytest.raises(GatewayError, match="不在配置的市场"):
        gw.get_positions(99)


def test_positions_use_the_context_of_the_accounts_market(gw):
    gw.get_positions(2)
    us = [c for c in _TradeCtx.instances if c.market == "US"]
    assert us and us[0].calls == ["positions"]


def test_klines_follow_page_keys_to_the_end(gw):
    _QuoteCtx.pages = [
        (RET_OK, pd.DataFrame([{"time_key": "2026-01-01", "close": 1.0}]), "k2"),
        (RET_OK, pd.DataFrame([{"time_key": "2026-01-02", "close": 2.0}]), None),
    ]
    df = gw.get_klines("US.A", "2026-01-01", "2026-01-02")
    assert df["close"].tolist() == [1.0, 2.0]


def test_close_closes_every_context_even_if_the_quote_close_fails(gw):
    gw.get_accounts()
    gw._quote()
    trade = list(_TradeCtx.instances)
    gw.close()  # must not raise over the quote context's own error
    assert trade and all(c.closed for c in trade)
    assert gw._quote_ctx is None


def test_every_sdk_call_goes_through_request():
    """A direct ctx.* call bypasses the throttle; across a multi-year pull that
    silently returns zero rows."""
    src = Path(futu_gw.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    allowed = {"close"}
    inside_request: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_request"
        ):
            for sub in ast.walk(node):
                inside_request.add(id(sub))
    offenders = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ctx"
            and node.attr not in allowed
            and id(node) not in inside_request
        ):
            offenders.append(f"line {node.lineno}: ctx.{node.attr}")
    assert not offenders, offenders
