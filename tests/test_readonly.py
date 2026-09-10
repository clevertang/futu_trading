"""确保网关层不含任何交易能力。"""
from pathlib import Path

from ftrade.gateway.base import FORBIDDEN_CALLS

SRC = Path(__file__).resolve().parents[1] / "src" / "ftrade"


def test_no_trading_calls_in_source():
    offenders = []
    for py in SRC.rglob("*.py"):
        if py.name == "base.py" and py.parent.name == "gateway":
            continue  # 常量定义本身
        text = py.read_text(encoding="utf-8")
        for bad in FORBIDDEN_CALLS:
            if f"{bad}(" in text:
                offenders.append(f"{py.relative_to(SRC)}: {bad}")
    assert not offenders, f"发现交易接口调用：{offenders}"


def test_gateway_protocol_has_no_write_methods():
    from ftrade.gateway.base import Gateway

    for bad in FORBIDDEN_CALLS:
        assert not hasattr(Gateway, bad)
