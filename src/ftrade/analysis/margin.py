"""Per-symbol margin model, from rates read off the broker's app.

Futu reports one account-wide maintenance and initial requirement. Dividing
that by market value gives a single blended rate, which is fine for "how far
can everything fall together" but wrong for the two questions that matter
before a trade:

- how far can the *index* fall before a call, when a 3x ETF moves three
  times as far as the plain ETF next to it, and
- how much of a given symbol can still be bought.

Both need each symbol's own rate. Futu does not expose them through the API,
so they are declared in config (``analysis.margin_rates``), copied from the
app's 初始保证金 / 维持保证金 page::

    margin_rates:
      US.TQQQ: {initial: 0.50, maintenance: 0.45, beta: 3}
      US.QQQ:  {initial: 0.40, maintenance: 0.30}

``beta`` is how many times the reference index the symbol moves (default 1).
Long options carry no requirement and no loan value (see
``equity.account_equity``); covered short calls added nothing to the
requirement on the 2026-10-30 page, so short options are left out too.

Everything here is a what-if on today's numbers, not a prediction.
"""

from __future__ import annotations

from .fx import FX
from .instruments import parse_option


def _rate(rates: dict, code: str) -> dict | None:
    r = (rates or {}).get(code)
    if not r:
        return None
    try:
        return {
            "initial": float(r["initial"]),
            "maintenance": float(r["maintenance"]),
            "beta": float(r.get("beta", 1.0)),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _holdings(holdings: list[dict], rates: dict, fallback_mm: float | None) -> list[dict]:
    """Marginable holdings in the base currency, each with the rate it is held at."""
    out = []
    for h in holdings or []:
        code = h.get("code")
        value = h.get("market_val_base")
        if not code or parse_option(code) or value is None or float(value) <= 0:
            continue
        r = _rate(rates, code)
        out.append(
            {
                "code": code,
                "value": float(value),
                "maintenance": r["maintenance"] if r else fallback_mm,
                "initial": r["initial"] if r else None,
                "beta": r["beta"] if r else 1.0,
                "rated": r is not None,
            }
        )
    return out


def index_fall_to_call(holdings: list[dict], equity_with_loan: float, requirement: float):
    """Fall in the reference index that brings equity with loan value down to
    the maintenance requirement.

    Each holding of value v, rate r and beta b loses v*b*x and needs r*v*(1-b*x)
    after an index fall x, so with debt fixed the cushion shrinks linearly:

        x = (equity_with_loan - requirement) / sum(v * b * (1 - r))

    0.0 when already at or under the line, None when nothing is marginable.
    Capped where the most leveraged holding would reach zero.
    """
    slope = sum(h["value"] * h["beta"] * (1 - (h["maintenance"] or 0.0)) for h in holdings)
    if slope <= 0:
        return None
    cushion = equity_with_loan - requirement
    if cushion <= 0:
        return 0.0
    cap = 1 / max(h["beta"] for h in holdings)
    return round(min(cushion / slope, cap), 4)


def margin_model(
    holdings: list[dict],
    equity: dict,
    rates: dict | None,
    fx: FX,
    prices: dict[str, float] | None = None,
    currency_of=None,
) -> dict | None:
    """Per-symbol view of the account's margin, or None without rates or requirements.

    `holdings` is portfolio.summary()'s holdings_detail; `equity` is
    account_equity()'s output, so the broker's own requirement stays the
    baseline and the rates only shape how it moves. `prices` are last prices in
    each symbol's own currency, for turning headroom into a share count.
    """
    if not rates:
        return None
    ewl = equity.get("equity_with_loan")
    mm = equity.get("maintenance_margin") or equity.get("margin_call_margin")
    im = equity.get("initial_margin")
    if ewl is None or mm is None:
        return None

    marginable = sum(
        float(h.get("market_val_base") or 0)
        for h in holdings or []
        if h.get("code")
        and not parse_option(h["code"])
        and float(h.get("market_val_base") or 0) > 0
    )
    # Unrated holdings keep the account's blended rate rather than counting as free.
    blended = mm / marginable if marginable else None
    held = _holdings(holdings, rates, blended)
    if not held:
        return None

    modeled = sum(h["value"] * (h["maintenance"] or 0.0) for h in held)
    out: dict = {
        "base_currency": fx.base,
        "modeled_maintenance": round(modeled, 2),
        # A large gap means the declared rates no longer match the broker's.
        "maintenance_gap": round(mm - modeled, 2),
        "unrated": [h["code"] for h in held if not h["rated"]],
        "index_fall_to_call": index_fall_to_call(held, ewl, mm),
        "headroom": [],
    }

    excess = ewl - im if im is not None else None
    out["initial_excess"] = round(excess, 2) if excess is not None else None
    for code in sorted(rates):
        r = _rate(rates, code)
        if not r or excess is None:
            continue
        value = max(excess, 0.0) / r["initial"]
        after = held + [
            {"code": code, "value": value, "maintenance": r["maintenance"], "beta": r["beta"]}
        ]
        row = {
            "code": code,
            "max_buy": round(value, 2),
            "index_fall_after_max": index_fall_to_call(after, ewl, mm + value * r["maintenance"])
            if value
            else out["index_fall_to_call"],
        }
        price = (prices or {}).get(code)
        cur = currency_of(code) if currency_of else None
        if price and cur:
            local = value / (fx.to_base(1.0, cur) or 1.0)
            row["max_shares"] = int(local // float(price))
            row["price"] = float(price)
        out["headroom"].append(row)

    # Long options count toward net assets but not toward the margin check;
    # selling them at market turns that value into loan value. Shown because
    # it is the first lever when the account is at the line.
    total = equity.get("total_assets")
    long_options = (total - ewl) if total is not None else 0.0
    if long_options > 0.005 and not equity.get("_what_if"):
        sold = margin_model(
            holdings,
            {**equity, "equity_with_loan": total, "_what_if": True},
            rates,
            fx,
            prices=prices,
            currency_of=currency_of,
        )
        if sold:
            out["if_long_options_sold"] = {
                "proceeds": round(long_options, 2),
                "index_fall_to_call": sold["index_fall_to_call"],
                "headroom": sold["headroom"],
            }
    return out
