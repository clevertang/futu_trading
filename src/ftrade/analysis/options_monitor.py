"""Factual, per-contract state for currently open option positions.

Deliberately produces no close/roll/hold recommendation. Whether to buy back a
short call, let it ride, or roll it to a later expiry is a trading decision --
this module's job stops at laying out the inputs a holder needs to make that
decision themselves: days to expiry, how far in or out of the money, and how
much of the collected (or paid) premium has already moved. See
advice/engine.py's module docstring for why Phase 1 draws this line.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .instruments import parse_option

NEAR_EXPIRY_DAYS = 3


def _moneyness(kind: str, underlying_px: float, strike: float) -> tuple[bool, float]:
    """(is_itm, distance) where distance is a positive fraction either way --
    how far in the money if itm, how far out of the money otherwise."""
    if kind == "CALL":
        itm = underlying_px > strike
        distance = (
            (underlying_px - strike) / underlying_px
            if itm
            else (strike - underlying_px) / underlying_px
        )
    else:
        itm = underlying_px < strike
        distance = (
            (strike - underlying_px) / underlying_px
            if itm
            else (underlying_px - strike) / underlying_px
        )
    return itm, abs(distance)


def open_option_positions(
    positions: pd.DataFrame, klines_by_code: dict[str, pd.DataFrame], as_of: date | None = None
) -> list[dict]:
    """One row per open option contract, with no directional advice attached.

    `klines_by_code` supplies the underlying's latest close for moneyness --
    the option's own `nominal_price` in `positions` is the option's own mark,
    not the underlying's, and a short PDD call written against long PDD calls
    (no PDD shares held) has no other source for the underlying's price.
    """
    as_of = as_of or date.today()
    if positions is None or positions.empty:
        return []

    rows = []
    for _, r in positions.iterrows():
        parsed = parse_option(r["code"])
        if not parsed or float(r["qty"] or 0) == 0:
            continue
        dte = (parsed["expiry"] - as_of).days
        kl = klines_by_code.get(parsed["underlying"])
        underlying_px = float(kl["close"].iloc[-1]) if kl is not None and not kl.empty else None

        entry = float(r["cost_price"] or 0)
        current = float(r["nominal_price"] or 0)
        direction = "SHORT" if float(r["qty"]) < 0 else "LONG"
        # For a short, premium "captured" is entry minus what it costs to close
        # now; for a long, it's simply the change in what the contract is worth.
        premium_change_pct = (
            round((entry - current) / entry * 100, 1)
            if direction == "SHORT" and entry
            else round((current - entry) / entry * 100, 1)
            if entry
            else None
        )

        itm = distance = None
        if underlying_px:
            itm, distance = _moneyness(parsed["kind"], underlying_px, parsed["strike"])

        rows.append(
            {
                "code": r["code"],
                "underlying": parsed["underlying"],
                "kind": parsed["kind"],
                "direction": direction,
                "qty": abs(float(r["qty"])),
                "strike": parsed["strike"],
                "expiry": parsed["expiry"].isoformat(),
                "days_to_expiry": dte,
                "underlying_price": underlying_px,
                "is_itm": itm,
                "moneyness_pct": round(distance * 100, 1) if distance is not None else None,
                "entry_price": entry,
                "current_price": current,
                "premium_change_pct": premium_change_pct,
                "near_expiry": dte <= NEAR_EXPIRY_DAYS,
                # Early assignment is a real, if uncommon, risk specifically for
                # a short call that is in the money -- flagged as a fact about
                # the position's shape, not a prediction of whether it happens.
                "assignment_watch": direction == "SHORT" and parsed["kind"] == "CALL" and bool(itm),
            }
        )
    return sorted(rows, key=lambda x: x["days_to_expiry"])
