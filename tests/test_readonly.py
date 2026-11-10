"""确保网关层不含任何交易能力。"""

import ast
from pathlib import Path

from ftrade.gateway.base import FORBIDDEN_CALLS

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "ftrade"
# scripts/ import ftrade and could import futu directly; they are in scope too.
SCANNED = [SRC, ROOT / "scripts"]
DEFINITION = SRC / "gateway" / "base.py"  # 常量定义本身


def _python_files():
    for root in SCANNED:
        yield from (p for p in root.rglob("*.py") if p != DEFINITION)


def _forbidden_names(tree: ast.AST) -> list[str]:
    """Every attribute, name or string constant spelling a forbidden call.

    Catches `ctx.place_order(...)`, a bare `place_order`, `getattr(ctx,
    "place_order")` and whitespace before the parenthesis -- the plain text
    search this replaces missed the last two.
    """
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_CALLS:
            hits.append(node.attr)
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_CALLS:
            hits.append(node.id)
        elif isinstance(node, ast.Constant) and node.value in FORBIDDEN_CALLS:
            hits.append(node.value)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name in FORBIDDEN_CALLS
        ):
            hits.append(node.name)
    return hits


def test_no_trading_calls_in_source():
    offenders = []
    for py in _python_files():
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        offenders += [f"{py.relative_to(ROOT)}: {name}" for name in _forbidden_names(tree)]
    assert not offenders, f"发现交易接口调用：{offenders}"


def test_the_scan_catches_indirect_spellings():
    for snippet in (
        "ctx.place_order(1)",
        "ctx.place_order (1)",
        "getattr(ctx, 'modify_order')(1)",
        "f = unlock_trade",
    ):
        assert _forbidden_names(ast.parse(snippet)), snippet


def test_gateway_protocol_has_no_write_methods():
    from ftrade.gateway.base import Gateway

    for bad in FORBIDDEN_CALLS:
        assert not hasattr(Gateway, bad)
