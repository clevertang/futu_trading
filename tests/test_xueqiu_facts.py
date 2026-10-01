"""The fact sheet behind public posts must not describe a broker event as an order."""

import importlib.util
from pathlib import Path

import pandas as pd

_spec = importlib.util.spec_from_file_location(
    "xueqiu_facts", Path(__file__).resolve().parents[1] / "scripts" / "xueqiu_facts.py"
)
facts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(facts)


def _fills(rows):
    return pd.DataFrame(
        [
            {"code": c, "trd_side": s, "qty": q, "price": p, "create_time": t}
            for c, s, q, p, t in rows
        ]
    )


def _kinds(rows):
    return [(f["code"], f["kind"]) for f in facts.label_fills(_fills(rows))]


def test_a_worthless_expiry_is_marked_as_lapsed():
    out = _kinds([("US.TQQQ260918C78000", "BUY_BACK", 2, 0.0, "2026-09-19 01:13:00")])
    assert out == [("US.TQQQ260918C78000", "lapsed")]


def test_an_exercise_pairs_the_option_with_its_share_delivery():
    out = _kinds(
        [
            ("US.TQQQ260918C69000", "BUY_BACK", 1, 0.0, "2026-09-19 01:13:05"),
            ("US.TQQQ", "SELL", 100, 69.0, "2026-09-19 01:13:07"),
        ]
    )
    assert out == [("US.TQQQ260918C69000", "exercised"), ("US.TQQQ", "assignment")]


def test_early_assignment_is_caught_across_a_minute_boundary():
    """Seen in the real history: option closed 01:23, shares delivered 01:24, two days early."""
    fills = facts.label_fills(
        _fills(
            [
                ("US.TQQQ260925C73000", "BUY_BACK", 1, 0.0, "2026-09-23 01:23:50"),
                ("US.TQQQ", "SELL", 100, 73.0, "2026-09-23 01:24:10"),
            ]
        )
    )
    assert [f["kind"] for f in fills] == ["exercised", "assignment"]
    assert fills[0]["early"] is True


def test_a_real_sale_is_left_alone_even_at_the_strike_price():
    """Same price as a strike, but hours away from any settlement: that was an order."""
    out = _kinds(
        [
            ("US.TQQQ260925C73000", "BUY_BACK", 1, 0.0, "2026-09-23 01:23:50"),
            ("US.TQQQ", "SELL", 100, 73.0, "2026-09-23 10:30:00"),
        ]
    )
    assert out == [("US.TQQQ260925C73000", "lapsed"), ("US.TQQQ", "order")]


def test_one_settlement_explains_only_one_delivery():
    out = _kinds(
        [
            ("US.TQQQ260918C69000", "BUY_BACK", 1, 0.0, "2026-09-19 01:13:00"),
            ("US.TQQQ", "SELL", 100, 69.0, "2026-09-19 01:13:02"),
            ("US.TQQQ", "SELL", 100, 69.0, "2026-09-19 01:13:03"),
        ]
    )
    assert [k for _, k in out] == ["exercised", "assignment", "order"]


def test_a_delivery_without_a_settlement_fill_is_matched_to_the_expired_short():
    """Before 2025 Futu booked no zero-price close; only the weekend delivery remained."""
    history = _fills(
        [
            ("US.NVDA250530C130000", "SELL_SHORT", 1, 2.76, "2025-05-12 11:24:00"),
            ("US.NVDA", "SELL", 100, 130.0, "2025-06-01 19:47:00"),
        ]
    )
    period = history.iloc[[1]]
    fills = facts.label_fills(period, history)

    assert fills[0]["kind"] == "assignment"
    assert fills[0]["from_opt"]["strike"] == 130.0


def test_a_sale_at_a_strike_long_after_that_expiry_stays_an_order():
    history = _fills(
        [
            ("US.NVDA250530C130000", "SELL_SHORT", 1, 2.76, "2025-05-12 11:24:00"),
            ("US.NVDA", "SELL", 100, 130.0, "2025-07-15 10:00:00"),
        ]
    )
    fills = facts.label_fills(history.iloc[[1]], history)

    assert fills[0]["kind"] == "order"


def test_a_put_delivers_shares_in_not_out():
    """A short put's exercise is a BUY at the strike; a SELL there is unrelated."""
    history = _fills(
        [
            ("US.TQQQ261002P73000", "SELL_SHORT", 1, 1.06, "2026-09-23 10:23:00"),
            ("US.TQQQ", "SELL", 100, 73.0, "2026-10-04 19:00:00"),
            ("US.TQQQ", "BUY", 100, 73.0, "2026-10-04 19:00:00"),
        ]
    )
    fills = facts.label_fills(history.iloc[[1, 2]], history)

    assert [f["kind"] for f in fills] == ["order", "assignment"]
