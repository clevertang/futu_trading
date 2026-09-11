"""Cash that moves without a trade.

Dividends, withholding tax, margin interest and transfers never appear in the
deal feed. P&L built from deals alone omits every one of them, which is why a
per-symbol figure computed here reads lower than the broker's own: Futu's
number is

    trade P&L  -  fees  +  dividends  +  other

and this module supplies the third term.
"""

from __future__ import annotations

import re

import pandas as pd

from .fx import FX

DIVIDEND_TYPES = ("cash dividend", "dividend")
TAX_TYPES = ("dividend tax", "withholding tax")
# "TQQQ 680.00000000 SHARES DIVIDENDS 0.17122900 USD PER SHARE"
_TICKER = re.compile(r"^\s*([A-Z][A-Z0-9.]{0,9})\b")


def _signed(amount: float | None, direction: str | None) -> float:
    """Futu reports magnitude plus a direction; OUT rows are already negative."""
    value = float(amount or 0.0)
    if str(direction or "").upper() == "OUT" and value > 0:
        return -value
    return value


def ticker_from_remark(remark: str | None) -> str | None:
    match = _TICKER.match(str(remark or ""))
    return match.group(1) if match else None


def dividends_by_symbol(flows: pd.DataFrame, fx: FX, known_codes: set[str]) -> dict[str, float]:
    """Net dividend (payment less withholding tax) per market-prefixed symbol.

    Remarks carry a bare ticker, so it is resolved against symbols the account
    has actually traded rather than guessing a market prefix.
    """
    if flows is None or flows.empty:
        return {}
    lookup = {code.split(".", 1)[-1]: code for code in known_codes if "." in code}
    out: dict[str, float] = {}
    for _, row in flows.iterrows():
        kind = str(row.get("cashflow_type") or "").lower()
        if not any(k in kind for k in DIVIDEND_TYPES + TAX_TYPES):
            continue
        code = lookup.get(ticker_from_remark(row.get("remark")) or "")
        if not code:
            continue
        value = fx.to_base(_signed(row.get("amount"), row.get("direction")), row.get("currency"))
        out[code] = round(out.get(code, 0.0) + (value or 0.0), 2)
    return out


def cash_summary(flows: pd.DataFrame, fx: FX) -> dict:
    """Totals by kind and year, in the base currency."""
    if flows is None or flows.empty:
        return {"count": 0, "base_currency": fx.base, "by_type": {}, "by_year": {}}
    df = flows.copy()
    df["signed"] = [_signed(a, d) for a, d in zip(df["amount"], df.get("direction"), strict=True)]
    df["base"] = [
        fx.to_base(v, c) or 0.0 for v, c in zip(df["signed"], df.get("currency"), strict=True)
    ]
    df["year"] = df["clearing_date"].astype(str).str[:4]

    kinds = df.groupby("cashflow_type")["base"].sum().sort_values()
    dividend = df[df["cashflow_type"].astype(str).str.lower().isin(["cash dividend"])]["base"].sum()
    tax = df[df["cashflow_type"].astype(str).str.lower().str.contains("tax", na=False)][
        "base"
    ].sum()
    return {
        "base_currency": fx.base,
        "count": int(len(df)),
        "total": round(float(df["base"].sum()), 2),
        "dividends": round(float(dividend), 2),
        "dividend_tax": round(float(tax), 2),
        "by_type": {str(k): round(float(v), 2) for k, v in kinds.items() if k},
        "by_year": {
            str(k): round(float(v), 2)
            for k, v in df.groupby("year")["base"].sum().sort_index().items()
            if k
        },
    }
