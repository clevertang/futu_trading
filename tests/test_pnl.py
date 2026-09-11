from datetime import date

import pandas as pd

from ftrade.analysis.pnl import fifo_round_trips, open_lots


def _deals(rows):
    return pd.DataFrame(
        [
            {
                "deal_id": f"d{i}",
                "code": c,
                "stock_name": c,
                "trd_side": side,
                "qty": qty,
                "price": px,
                "create_time": t,
            }
            for i, (c, side, qty, px, t) in enumerate(rows)
        ]
    )


def test_fifo_matches_oldest_lot_first():
    df = _deals(
        [
            ("HK.00700", "BUY", 100, 300.0, "2024-01-02 10:00:00"),
            ("HK.00700", "BUY", 100, 400.0, "2024-02-02 10:00:00"),
            ("HK.00700", "SELL", 100, 350.0, "2024-03-04 10:00:00"),
        ]
    )
    trips = fifo_round_trips(df)
    assert len(trips) == 1
    t = trips.iloc[0]
    assert t["open_price"] == 300.0  # 先进先出，配的是 300 那笔
    assert t["pnl"] == 5000.0
    assert t["holding_days"] == 62


def test_partial_sell_splits_across_lots():
    df = _deals(
        [
            ("US.AAPL", "BUY", 10, 100.0, "2024-01-02 10:00:00"),
            ("US.AAPL", "BUY", 10, 120.0, "2024-01-10 10:00:00"),
            ("US.AAPL", "SELL", 15, 130.0, "2024-02-01 10:00:00"),
        ]
    )
    trips = fifo_round_trips(df).sort_values("open_price")
    assert len(trips) == 2
    assert trips.iloc[0]["qty"] == 10 and trips.iloc[0]["pnl"] == 300.0
    assert trips.iloc[1]["qty"] == 5 and trips.iloc[1]["pnl"] == 50.0
    lots = open_lots(df)
    assert lots.iloc[0]["qty"] == 5 and lots.iloc[0]["avg_cost"] == 120.0


def test_empty_input_is_safe():
    assert fifo_round_trips(pd.DataFrame()).empty
    assert open_lots(pd.DataFrame()).empty


def test_currency_for_code_uses_market_prefix():
    from ftrade.analysis.fx import currency_for_code

    assert currency_for_code("US.AAPL") == "USD"
    assert currency_for_code("HK.00700") == "HKD"
    assert currency_for_code("JP.7203") == "JPY"
    assert currency_for_code("AAPL") is None  # no prefix, no guess
    assert currency_for_code(None) is None


def test_behavior_converts_currencies_before_aggregating():
    from ftrade.analysis.fx import FX
    from ftrade.analysis.trades import behavior

    # One HKD win and one USD win of the same nominal size: with USD at 7.8 the
    # USD trip must dominate. Summing raw pnl would call them equal.
    trips = pd.DataFrame(
        [
            {
                "code": "HK.00700",
                "pnl": 100.0,
                "pnl_pct": 10.0,
                "currency": "HKD",
                "holding_days": 5,
            },
            {
                "code": "US.AAPL",
                "pnl": 100.0,
                "pnl_pct": 10.0,
                "currency": "USD",
                "holding_days": 5,
            },
        ]
    )
    fx = FX({"HKD": 1.0, "USD": 7.8}, "HKD")

    raw = behavior(trips, pd.DataFrame(), fx=None)
    converted = behavior(trips, pd.DataFrame(), fx=fx)

    assert raw["realized_pnl"] == 200.0
    assert converted["realized_pnl"] == 880.0


def test_realized_curve_is_cumulative_and_sorted():
    from ftrade.analysis.fx import FX
    from ftrade.analysis.trades import realized_curve

    trips = pd.DataFrame(
        [
            {"close_time": "2024-03-02 10:00:00", "pnl": 100.0, "currency": "USD"},
            {"close_time": "2024-01-05 10:00:00", "pnl": -40.0, "currency": "USD"},
            {"close_time": "2024-01-05 15:00:00", "pnl": 10.0, "currency": "USD"},
        ]
    )
    curve = realized_curve(trips, FX({"USD": 2.0, "HKD": 1.0}, "HKD"))

    assert [c["date"] for c in curve] == ["2024-01-05", "2024-03-02"]
    # same-day trips collapse into one point, and USD is converted at 2.0
    assert curve[0]["trades"] == 2
    assert curve[0]["cum_pnl"] == -60.0
    assert curve[1]["cum_pnl"] == 140.0


def test_pnl_distribution_clamps_tails_and_splits_by_close_reason():
    from ftrade.analysis.trades import pnl_distribution

    trips = pd.DataFrame(
        {
            "pnl_pct": [-891.3, -5.0, 3.0, 12.0, 100.0, 100.0, 575.7],
            "close_reason": ["TRADE", "TRADE", "TRADE", "TRADE", "EXPIRY", "EXPIRY", "TRADE"],
        }
    )
    buckets = pnl_distribution(trips, bucket_pct=20.0)

    assert sum(b["count"] for b in buckets) == 7  # nothing silently dropped
    assert buckets[0]["left"] == -100.0 and buckets[0]["count"] == 1  # -891% clamped in
    # A short held to expiry is +100% by construction; it must not be mistaken
    # for an unusually good trade, so the counts stay separate.
    top = buckets[-1]
    assert top["right"] == 100.0
    assert top["expiry"] == 2 and top["trade"] == 1  # +575.7% clamps in beside them
    assert all(b["trade"] + b["expiry"] == b["count"] for b in buckets)


def test_fx_rebase_converts_between_bases():
    from ftrade.analysis.fx import FX

    # Configured relative to HKD: 1 USD = 7.8 HKD, 1 JPY = 0.052 HKD.
    fx = FX({"HKD": 1.0, "USD": 7.8, "JPY": 0.052}, "HKD")
    usd = fx.rebase("USD")

    assert usd.base == "USD"
    assert usd.to_base(1, "USD") == 1.0
    assert round(usd.to_base(78, "HKD"), 4) == 10.0  # 78 HKD -> 10 USD
    assert round(usd.to_base(1000, "JPY"), 4) == round(1000 * 0.052 / 7.8, 4)
    # Round-tripping back to the original base is lossless.
    assert round(usd.rebase("HKD").to_base(1, "USD"), 6) == 7.8


def test_fx_rebase_is_a_noop_for_unknown_currency():
    from ftrade.analysis.fx import FX

    fx = FX({"HKD": 1.0, "USD": 7.8}, "HKD")
    assert fx.rebase("BTC") is fx
    assert fx.rebase("HKD") is fx


def test_option_symbol_parsing():
    from datetime import date

    from ftrade.analysis.instruments import contract_multiplier, is_option, parse_option

    p = parse_option("US.TQQQ260911C75000")
    assert p["underlying"] == "US.TQQQ"
    assert p["expiry"] == date(2026, 9, 11)
    assert p["kind"] == "CALL"
    assert p["strike"] == 75.0
    assert contract_multiplier("US.TQQQ260911C75000") == 100

    assert not is_option("US.TQQQ")
    assert contract_multiplier("US.TQQQ") == 1  # plain shares are 1:1
    assert parse_option("US.AAPL261301C10000") is None  # month 13 is not a date


def test_short_round_trip_pnl_is_inverted_and_scaled():
    """Sell to open, buy to close: the premium is the gain, times 100 a contract."""
    deals = pd.DataFrame(
        [
            {
                "code": "US.TQQQ250905C90000",
                "stock_name": "TQQQ CALL",
                "trd_side": "SELL_SHORT",
                "qty": 1,
                "price": 1.5,
                "create_time": "2025-09-03 10:00:00",
            },
            {
                "code": "US.TQQQ250905C90000",
                "stock_name": "TQQQ CALL",
                "trd_side": "BUY_BACK",
                "qty": 1,
                "price": 0.4,
                "create_time": "2025-09-04 10:00:00",
            },
        ]
    )
    t = fifo_round_trips(deals, as_of=date(2025, 9, 10))

    assert len(t) == 1
    assert t.iloc[0]["direction"] == "SHORT"
    assert t.iloc[0]["multiplier"] == 100
    assert t.iloc[0]["pnl"] == 110.0  # (1.5 - 0.4) * 1 * 100
    assert t.iloc[0]["close_reason"] == "TRADE"


def test_short_option_held_to_expiry_realises_the_premium():
    """The 'let it expire' case: no closing fill exists, so nothing else would."""
    deals = pd.DataFrame(
        [
            {
                "code": "US.TQQQ250905C90000",
                "stock_name": "TQQQ CALL",
                "trd_side": "SELL_SHORT",
                "qty": 2,
                "price": 1.25,
                "create_time": "2025-09-02 10:00:00",
            },
        ]
    )
    after = fifo_round_trips(deals, as_of=date(2025, 9, 10))
    assert len(after) == 1
    assert after.iloc[0]["pnl"] == 250.0  # 1.25 * 2 * 100, premium kept
    assert after.iloc[0]["close_reason"] == "EXPIRY"
    assert after.iloc[0]["close_time"] == "2025-09-05"
    assert open_lots(deals, as_of=date(2025, 9, 10)).empty

    # Before expiry the position is still open and contributes no realised P&L.
    before = fifo_round_trips(deals, as_of=date(2025, 9, 4))
    assert before.empty
    assert open_lots(deals, as_of=date(2025, 9, 4)).iloc[0]["qty"] == -2.0


def test_closing_fill_without_a_position_is_dropped_not_flipped():
    """An IPO allotment or ticker change leaves a sale with no matching buy.

    Treating it as opening a short would invent a position that never existed.
    """
    deals = pd.DataFrame(
        [
            {
                "code": "HK.06666",
                "stock_name": "IPO",
                "trd_side": "SELL",
                "qty": 500,
                "price": 15.8,
                "create_time": "2021-01-21 13:00:00",
            },
        ]
    )
    assert fifo_round_trips(deals).empty
    assert open_lots(deals).empty


def _deal(code, side, qty, price, when):
    return {
        "code": code,
        "stock_name": code,
        "trd_side": side,
        "qty": qty,
        "price": price,
        "create_time": when,
    }


def test_rename_lets_a_holding_pair_with_its_later_sale():
    """A SPAC merger renames the ticker; buy and sell look like two symbols."""
    from ftrade.analysis.corporate import Action, apply_actions

    deals = pd.DataFrame(
        [
            _deal("US.CLA", "BUY", 70, 13.98, "2021-02-04 11:00:00"),
            _deal("US.OUST", "SELL", 70, 5.15, "2022-01-04 10:00:00"),
        ]
    )
    assert fifo_round_trips(deals).empty  # nothing pairs, and the sale is dropped

    out = fifo_round_trips(
        apply_actions(
            deals, [Action(type="rename", date="2021-03-12", code="US.CLA", to="US.OUST")]
        )
    )
    assert len(out) == 1
    assert out.iloc[0]["pnl"] == round((5.15 - 13.98) * 70, 2)  # -618.10


def test_split_restates_cost_basis_so_pnl_is_not_a_phantom_loss():
    """Buying pre-split and selling post-split otherwise looks like a 50% loss."""
    from ftrade.analysis.corporate import Action, apply_actions

    deals = pd.DataFrame(
        [
            _deal("US.TQQQ", "BUY", 100, 110.00, "2025-11-10 10:00:00"),
            _deal("US.TQQQ", "SELL", 200, 55.00, "2026-01-05 10:00:00"),
        ]
    )
    naive = fifo_round_trips(deals)
    assert naive.iloc[0]["pnl"] == -5500.0  # paired 100 @110 against 100 @55

    out = fifo_round_trips(
        apply_actions(deals, [Action(type="split", date="2025-11-18", code="US.TQQQ", ratio=2)])
    )
    assert len(out) == 1
    assert out.iloc[0]["qty"] == 200  # the position doubled
    assert out.iloc[0]["pnl"] == 0.0  # 200 @55 against 200 @55: flat, as it was


def test_split_restrikes_the_options_of_its_underlying():
    """A 2:1 split halves the strike and doubles the contracts."""
    from ftrade.analysis.corporate import Action, apply_actions

    deals = pd.DataFrame(
        [
            _deal("US.TQQQ251121P105000", "SELL_SHORT", 1, 4.60, "2025-11-10 10:00:00"),
            _deal("US.TQQQ251121P52500", "BUY_BACK", 2, 1.00, "2025-11-24 10:00:00"),
        ]
    )

    # Untreated, the two look like unrelated contracts: the short is never
    # closed and gets settled at expiry for the full premium, while the
    # buy-back has nothing to close and is discarded.
    naive = fifo_round_trips(deals)
    assert len(naive) == 1
    assert naive.iloc[0]["close_reason"] == "EXPIRY"
    assert naive.iloc[0]["pnl"] == 460.0  # overstated: closing cost is lost

    out = fifo_round_trips(
        apply_actions(deals, [Action(type="split", date="2025-11-18", code="US.TQQQ", ratio=2)])
    )
    assert len(out) == 1
    assert out.iloc[0]["close_reason"] == "TRADE"
    assert out.iloc[0]["qty"] == 2  # one pre-split contract became two
    # Premium is conserved (1 x 4.60 == 2 x 2.30) and the buy-back is charged.
    assert out.iloc[0]["pnl"] == 260.0  # (2.30 - 1.00) * 2 * 100


def test_actions_leave_deals_after_their_date_alone():
    from ftrade.analysis.corporate import Action, apply_actions

    deals = pd.DataFrame([_deal("US.TQQQ", "BUY", 100, 55.0, "2026-01-05 10:00:00")])
    out = apply_actions(deals, [Action(type="split", date="2025-11-18", code="US.TQQQ", ratio=2)])
    assert out.iloc[0]["qty"] == 100 and out.iloc[0]["price"] == 55.0


def test_reverse_split_uses_a_ratio_below_one():
    """Ten shares becoming one is the same operation with ratio 0.1."""
    from ftrade.analysis.corporate import Action, apply_actions

    deals = pd.DataFrame([_deal("US.QTT", "BUY", 1000, 2.85, "2021-03-12 10:00:00")])
    out = apply_actions(deals, [Action(type="split", date="2022-06-01", code="US.QTT", ratio=0.1)])

    assert out.iloc[0]["qty"] == 100  # ten-for-one
    assert out.iloc[0]["price"] == 28.5  # cost basis preserved


def test_writeoff_closes_the_residual_position_at_zero():
    """A delisting leaves no closing trade, so the loss is never realised."""
    from ftrade.analysis.corporate import Action, apply_actions

    deals = pd.DataFrame(
        [
            _deal("US.QTT", "BUY", 1000, 2.85, "2021-03-12 10:00:00"),
            _deal("US.QTT", "SELL", 150, 1.03, "2022-03-14 10:00:00"),
        ]
    )
    # Untreated, 850 shares sit open for ever and their loss stays invisible.
    assert open_lots(deals).iloc[0]["qty"] == 850

    acted = apply_actions(deals, [Action(type="writeoff", date="2023-01-01", code="US.QTT")])
    assert open_lots(acted).empty

    trips = fifo_round_trips(acted)
    written = trips[trips["close_price"] == 0.0]
    assert len(written) == 1
    assert written.iloc[0]["qty"] == 850
    assert written.iloc[0]["pnl"] == round(-850 * 2.85, 2)


def test_writeoff_does_nothing_when_the_position_is_already_flat():
    from ftrade.analysis.corporate import Action, apply_actions

    deals = pd.DataFrame(
        [
            _deal("US.QTT", "BUY", 100, 2.85, "2021-03-12 10:00:00"),
            _deal("US.QTT", "SELL", 100, 1.03, "2022-03-14 10:00:00"),
        ]
    )
    out = apply_actions(deals, [Action(type="writeoff", date="2023-01-01", code="US.QTT")])
    assert len(out) == len(deals)  # no synthetic row appended


def test_underlying_rollup_puts_options_under_their_stock():
    from ftrade.analysis.instruments import market_of, underlying_of

    # US option roots match the ticker, so they merge without help.
    assert underlying_of("US.TQQQ260911C75000") == "US.TQQQ"
    assert underlying_of("US.TQQQ") == "US.TQQQ"
    # HKEX gives derivatives their own root: Tencent trades as 00700, its
    # options are struck on TCH. Without the map they would never join up.
    assert underlying_of("HK.TCH240627C370000") == "HK.00700"
    assert underlying_of("HK.00700") == "HK.00700"
    assert market_of("HK.TCH240627C370000") == "HK"
    assert market_of("TQQQ") is None  # unprefixed, no guess


def test_realized_by_code_groups_a_name_traded_through_options():
    from ftrade.analysis.trades import behavior

    # One name traded as stock and through two contracts must rank as one row,
    # otherwise a symbol traded through hundreds of weeklies never appears.
    trips = pd.DataFrame(
        [
            {"code": "US.TQQQ", "pnl": 100.0, "pnl_pct": 5.0, "currency": "USD", "holding_days": 3},
            {
                "code": "US.TQQQ260911C75000",
                "pnl": 150.0,
                "pnl_pct": 100.0,
                "currency": "USD",
                "holding_days": 5,
            },
            {
                "code": "US.TQQQ260918C78000",
                "pnl": 250.0,
                "pnl_pct": 100.0,
                "currency": "USD",
                "holding_days": 5,
            },
            {"code": "US.PDD", "pnl": 400.0, "pnl_pct": 9.0, "currency": "USD", "holding_days": 20},
        ]
    )
    out = behavior(trips, pd.DataFrame())

    by_code = {r["code"]: r["pnl"] for r in out["realized_by_code"]}
    assert by_code == {"US.TQQQ": 500.0, "US.PDD": 400.0}
    assert out["realized_by_code"][0]["code"] == "US.TQQQ"  # ranked by the total


def test_realized_by_market_counts_stock_and_options_together():
    from ftrade.analysis.trades import behavior

    trips = pd.DataFrame(
        [
            {
                "code": "US.TQQQ",
                "pnl": -300.0,
                "pnl_pct": -5.0,
                "currency": "USD",
                "holding_days": 3,
            },
            {
                "code": "US.TQQQ260911C75000",
                "pnl": 500.0,
                "pnl_pct": 100.0,
                "currency": "USD",
                "holding_days": 5,
            },
            {"code": "HK.00700", "pnl": 80.0, "pnl_pct": 4.0, "currency": "HKD", "holding_days": 9},
        ]
    )
    markets = {r["market"]: r for r in behavior(trips, pd.DataFrame())["realized_by_market"]}

    assert markets["US"]["pnl"] == 200.0  # a covered call and its shares net out
    assert markets["US"]["trips"] == 2
    assert markets["HK"]["pnl"] == 80.0
