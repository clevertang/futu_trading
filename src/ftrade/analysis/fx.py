"""极简汇率换算：低频场景下用配置里的静态汇率即可。"""
from __future__ import annotations


class FX:
    def __init__(self, rates: dict[str, float] | None, base: str):
        self.rates = dict(rates or {})
        self.base = base
        self.rates.setdefault(base, 1.0)

    def has(self, currency: str | None) -> bool:
        return bool(currency) and currency in self.rates

    def to_base(self, amount: float | None, currency: str | None) -> float | None:
        if amount is None:
            return None
        if not currency:
            return float(amount)
        rate = self.rates.get(currency)
        if rate is None:
            return None
        return float(amount) * float(rate)
