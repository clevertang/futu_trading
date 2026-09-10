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
