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


def test_pnl_distribution_buckets_and_clamps_tails():
    from ftrade.analysis.trades import pnl_distribution

    trips = pd.DataFrame({"pnl_pct": [-95.0, -5.0, 3.0, 12.0, 240.0]})
    buckets = pnl_distribution(trips, bucket_pct=10.0)

    assert sum(b["count"] for b in buckets) == 5  # nothing silently dropped
    assert buckets[0]["left"] == -50.0 and buckets[0]["count"] == 1  # -95% clamped in
    assert buckets[-1]["right"] == 50.0 and buckets[-1]["count"] == 1  # +240% clamped in


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
