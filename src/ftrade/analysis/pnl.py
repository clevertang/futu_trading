"""基于成交流水的 FIFO 归因。

富途接口给的是「当前持仓的浮盈」，做长期复盘需要自己把每一笔买卖配对成
完整的一轮交易（round trip），才能算胜率、持有期、已实现盈亏。
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import date, datetime

import pandas as pd

from .instruments import contract_multiplier, option_expiry

BUY_WORDS = {"BUY", "BUY_BACK", "TrdSide.BUY"}
SELL_WORDS = {"SELL", "SELL_SHORT", "TrdSide.SELL"}

# Futu distinguishes opening from closing on both sides of the book. Collapsing
# these four into BUY/SELL throws away the only signal that says whether a sale
# opened a short or closed a long -- and for a premium seller that is most of
# the account.
OPEN_LONG, CLOSE_LONG, OPEN_SHORT, CLOSE_SHORT = (
    "OPEN_LONG",
    "CLOSE_LONG",
    "OPEN_SHORT",
    "CLOSE_SHORT",
)
_ACTIONS = {
    "BUY": OPEN_LONG,
    "SELL": CLOSE_LONG,
    "SELL_SHORT": OPEN_SHORT,
    "BUY_BACK": CLOSE_SHORT,
}


def _side(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).upper().replace("TRDSIDE.", "")
    if s.startswith("BUY"):
        return "BUY"
    if s.startswith("SELL"):
        return "SELL"
    return None


def _action(raw: str | None) -> str | None:
    """Map a Futu trd_side onto an open/close intent.

    Feeds that only ever say BUY/SELL (the mock gateway, and any long-only
    history) keep their long-only meaning.
    """
    if not raw:
        return None
    return _ACTIONS.get(str(raw).upper().replace("TRDSIDE.", ""))


def _ts(raw: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(raw)[:26], fmt)
        except ValueError:
            continue
    return datetime.min


@dataclass
class RoundTrip:
    code: str
    stock_name: str | None
    qty: float
    open_time: str
    close_time: str
    open_price: float
    close_price: float
    pnl: float
    pnl_pct: float
    holding_days: int
    currency: str | None
    direction: str = "LONG"
    multiplier: int = 1
    close_reason: str = "TRADE"


def _close_against(
    trips: list,
    book: deque,
    direction: str,
    code: str,
    name: str | None,
    currency: str | None,
    multiplier: int,
    qty: float,
    price: float,
    ts,
) -> None:
    """Consume FIFO lots from one side of the book, recording each pairing."""
    remaining = qty
    while remaining > 1e-9 and book:
        lot = book[0]
        take = min(lot[0], remaining)
        # A short earns the difference in the other direction.
        gross = (price - lot[1]) if direction == "LONG" else (lot[1] - price)
        pnl = gross * take * multiplier
        cost = lot[1] * take * multiplier
        trips.append(
            RoundTrip(
                code=code,
                stock_name=name,
                qty=take,
                open_time=lot[2].strftime("%Y-%m-%d"),
                close_time=ts.strftime("%Y-%m-%d"),
                open_price=lot[1],
                close_price=price,
                pnl=round(pnl, 2),
                pnl_pct=round(pnl / cost * 100, 2) if cost else 0.0,
                holding_days=max((ts - lot[2]).days, 0),
                currency=currency,
                direction=direction,
                multiplier=multiplier,
                close_reason="TRADE",
            )
        )
        lot[0] -= take
        remaining -= take
        if lot[0] <= 1e-9:
            book.popleft()


def _settle_expired(
    trips: list,
    longs: deque,
    shorts: deque,
    code: str,
    name: str | None,
    currency: str | None,
    multiplier: int,
    as_of: date,
) -> None:
    """Close out option lots whose expiry has passed with no closing fill.

    An option that is held to expiry produces no closing trade -- the position
    simply ceases to exist. For a premium seller who lets contracts run to
    expiry, that is where most of the income is, and leaving those lots open
    means the premium is never realised and the book never reconciles.

    Whether the contract expired worthless or was assigned, the premium is kept
    either way; assignment only additionally moves the underlying, which shows
    up as its own stock deal.
    """
    expiry = option_expiry(code)
    if expiry is None or expiry >= as_of:
        return
    close_time = datetime.combine(expiry, datetime.min.time())
    for book, direction in ((longs, "LONG"), (shorts, "SHORT")):
        while book:
            qty, open_price, open_ts = book.popleft()
            # Worthless at expiry: a long loses what it paid, a short keeps it.
            pnl = (-1 if direction == "LONG" else 1) * open_price * qty * multiplier
            cost = open_price * qty * multiplier
            trips.append(
                RoundTrip(
                    code=code,
                    stock_name=name,
                    qty=qty,
                    open_time=open_ts.strftime("%Y-%m-%d"),
                    close_time=expiry.isoformat(),
                    open_price=open_price,
                    close_price=0.0,
                    pnl=round(pnl, 2),
                    pnl_pct=round(pnl / cost * 100, 2) if cost else 0.0,
                    holding_days=max((close_time - open_ts).days, 0),
                    currency=currency,
                    direction=direction,
                    multiplier=multiplier,
                    close_reason="EXPIRY",
                )
            )


def fifo_round_trips(
    deals: pd.DataFrame, currency_of=None, as_of: date | None = None
) -> pd.DataFrame:
    """Pair deals into closed round trips, FIFO, on both sides of the book.

    Long trips pair an opening buy with a closing sell; short trips pair an
    opening sell with a closing buy-back. A closing fill with nothing to close
    against is dropped rather than flipped into an opening position -- that
    happens when the deal history is genuinely incomplete (a corporate action
    moved shares, an IPO allotment was never a trade) and inventing a position
    there would corrupt every statistic downstream.
    """
    cols = [f.name for f in RoundTrip.__dataclass_fields__.values()]
    as_of = as_of or date.today()
    if deals is None or deals.empty:
        return pd.DataFrame(columns=cols)

    df = deals.copy()
    df["_action"] = df["trd_side"].map(_action)
    df = df[df["_action"].notna()]
    df["_ts"] = df["create_time"].map(_ts)
    df = df.sort_values("_ts")

    trips: list[RoundTrip] = []
    for code, sub in df.groupby("code"):
        longs: deque[list] = deque()  # [qty, price, ts]
        shorts: deque[list] = deque()
        name = sub["stock_name"].dropna().iloc[0] if sub["stock_name"].notna().any() else None
        cur = currency_of(code) if currency_of else None
        mult = contract_multiplier(code)

        for _, r in sub.iterrows():
            qty, price, ts = float(r["qty"] or 0), float(r["price"] or 0), r["_ts"]
            if qty <= 0:
                continue
            action = r["_action"]
            if action == OPEN_LONG:
                longs.append([qty, price, ts])
            elif action == OPEN_SHORT:
                shorts.append([qty, price, ts])
            elif action == CLOSE_LONG:
                _close_against(trips, longs, "LONG", code, name, cur, mult, qty, price, ts)
            elif action == CLOSE_SHORT:
                _close_against(trips, shorts, "SHORT", code, name, cur, mult, qty, price, ts)

        _settle_expired(trips, longs, shorts, code, name, cur, mult, as_of)

    return pd.DataFrame([asdict(t) for t in trips], columns=cols)


def open_lots(deals: pd.DataFrame, as_of: date | None = None) -> pd.DataFrame:
    """Net open position per symbol after FIFO, signed: long positive, short negative.

    Used to reconcile the locally reconstructed book against the broker's. Short
    option positions are ordinary here -- the broker reports them as a negative
    quantity too, so a premium seller's book only lines up if this stays signed.
    """
    as_of = as_of or date.today()
    if deals is None or deals.empty:
        return pd.DataFrame(columns=["code", "qty", "avg_cost"])
    df = deals.copy()
    df["_action"] = df["trd_side"].map(_action)
    df = df[df["_action"].notna()].copy()
    df["_ts"] = df["create_time"].map(_ts)
    df = df.sort_values("_ts")

    rows = []
    for code, sub in df.groupby("code"):
        longs: deque[list] = deque()
        shorts: deque[list] = deque()
        for _, r in sub.iterrows():
            qty, price = float(r["qty"] or 0), float(r["price"] or 0)
            if qty <= 0:
                continue
            action = r["_action"]
            if action == OPEN_LONG:
                longs.append([qty, price])
            elif action == OPEN_SHORT:
                shorts.append([qty, price])
            else:
                book = longs if action == CLOSE_LONG else shorts
                remaining = qty
                while remaining > 1e-9 and book:
                    take = min(book[0][0], remaining)
                    book[0][0] -= take
                    remaining -= take
                    if book[0][0] <= 1e-9:
                        book.popleft()

        # An expired option is gone whether or not a closing fill exists.
        expiry = option_expiry(code)
        if expiry is not None and expiry < as_of:
            continue

        long_qty = sum(lot[0] for lot in longs)
        short_qty = sum(lot[0] for lot in shorts)
        net = long_qty - short_qty
        if abs(net) <= 1e-9:
            continue
        book = longs if net > 0 else shorts
        held = long_qty if net > 0 else short_qty
        cost = sum(lot[0] * lot[1] for lot in book) / held if held else 0.0
        rows.append({"code": code, "qty": round(net, 4), "avg_cost": round(cost, 4)})
    return pd.DataFrame(rows)
