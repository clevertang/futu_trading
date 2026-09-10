"""极简汇率换算：低频场景下用配置里的静态汇率即可。"""

from __future__ import annotations


class FX:
    def __init__(self, rates: dict[str, float] | None, base: str):
        self.rates = dict(rates or {})
        self.base = base
        self.rates.setdefault(base, 1.0)

    def has(self, currency: str | None) -> bool:
        return bool(currency) and currency in self.rates

    def rebase(self, base: str) -> FX:
        """Return an FX table expressed in a different base currency.

        Configured rates are relative to the configured base, so converting
        currency ``c`` into currency ``X`` is ``rates[c] / rates[X]``.
        """
        if not base or base == self.base:
            return self
        divisor = self.rates.get(base)
        if not divisor:
            return self
        return FX({c: r / divisor for c, r in self.rates.items()}, base)

    def currencies(self) -> list[str]:
        return sorted(self.rates)

    def to_base(self, amount: float | None, currency: str | None) -> float | None:
        if amount is None:
            return None
        if not currency:
            return float(amount)
        rate = self.rates.get(currency)
        if rate is None:
            return None
        return float(amount) * float(rate)


# Futu codes are market-prefixed ("US.AAPL", "HK.00700"). The deals table has no
# currency column, so the prefix is the only currency signal available for a
# symbol that is no longer held.
MARKET_CURRENCY = {
    "HK": "HKD",
    "US": "USD",
    "JP": "JPY",
    "SH": "CNH",
    "SZ": "CNH",
    "SG": "SGD",
    "AU": "AUD",
}


def currency_for_code(code: str | None) -> str | None:
    """Best-effort currency for a symbol, from its market prefix."""
    if not code or "." not in str(code):
        return None
    return MARKET_CURRENCY.get(str(code).split(".", 1)[0].upper())
