"""Instrument identity helpers derived from the Futu symbol itself.

Futu's deal feed carries no instrument type, multiplier or expiry -- but its
option codes encode all three: ``US.TQQQ260911C75000`` is a TQQQ call expiring
2026-09-11 struck at 75.000. Deriving them here keeps that parsing in one place
instead of scattering regexes through the analysis layer.
"""

from __future__ import annotations

import re
from datetime import date

# MARKET.UNDERLYING + YYMMDD + C|P + strike (strike is in thousandths).
_OPTION = re.compile(
    r"^(?P<market>[A-Z]{2})\.(?P<under>[A-Z]+)(?P<exp>\d{6})(?P<kind>[CP])(?P<strike>\d+)$"
)

# US listed options are 100 shares per contract, without exception.
_US_OPTION_MULTIPLIER = 100

# HKEX stock options size their contract from the underlying's board lot, which
# varies per name and is NOT derivable from the symbol. 100 is the common case
# for the large caps, but it is an assumption: verify against the HKEX contract
# specifications before trusting HK option P&L, and override here if it differs.
_HK_OPTION_MULTIPLIER_DEFAULT = 100
HK_CONTRACT_SIZE: dict[str, int] = {}


def parse_option(code: str | None) -> dict | None:
    """Return the parts of an option symbol, or None if it is not one."""
    m = _OPTION.match(str(code or ""))
    if not m:
        return None
    exp = m.group("exp")
    try:
        expiry = date(2000 + int(exp[:2]), int(exp[2:4]), int(exp[4:6]))
    except ValueError:
        return None
    return {
        "market": m.group("market"),
        "underlying": f"{m.group('market')}.{m.group('under')}",
        "expiry": expiry,
        "kind": "CALL" if m.group("kind") == "C" else "PUT",
        "strike": int(m.group("strike")) / 1000,
    }


def is_option(code: str | None) -> bool:
    return parse_option(code) is not None


def option_expiry(code: str | None) -> date | None:
    parsed = parse_option(code)
    return parsed["expiry"] if parsed else None


def contract_multiplier(code: str | None) -> int:
    """Shares per contract. 1 for anything that is not a US option.

    Without this every option P&L is understated by 100x -- a 2.50 premium on
    one contract is 250 dollars, not 2.50.
    """
    parsed = parse_option(code)
    if not parsed:
        return 1
    if parsed["market"] == "US":
        return _US_OPTION_MULTIPLIER
    if parsed["market"] == "HK":
        return HK_CONTRACT_SIZE.get(parsed["underlying"], _HK_OPTION_MULTIPLIER_DEFAULT)
    return 1
