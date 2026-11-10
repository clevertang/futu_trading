"""Equity curve and risk metrics."""

import math

import numpy as np
import pandas as pd
import pytest

from ftrade.analysis.metrics import equity_curve, position_volatility, risk_metrics


def _snaps(rows):
    return pd.DataFrame(
        [{"snap_date": d, "acc_id": a, "total_assets": v, "currency": "USD"} for d, a, v in rows]
    )


def _curve(dates, values):
    return equity_curve(_snaps([(d, 1, v) for d, v in zip(dates, values, strict=True)]))


def test_annualised_return_uses_elapsed_time_not_snapshot_count():
    # Five quarterly snapshots spanning one year, +10% overall. Counting
    # snapshots as trading days annualised this to about +12,000%.
    dates = ["2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01", "2026-01-01"]
    m = risk_metrics(_curve(dates, [100, 102, 105, 107, 110]))
    assert m["annualized_return"] == pytest.approx(0.10, abs=0.002)


def test_volatility_scales_by_observed_snapshot_frequency():
    # Quarterly returns: annualise with sqrt(4), not sqrt(252).
    dates = ["2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01", "2026-01-01"]
    values = [100, 110, 99, 108.9, 98.01]
    rets = pd.Series(values).pct_change().dropna()
    m = risk_metrics(_curve(dates, values))
    assert m["volatility"] == pytest.approx(rets.std(ddof=1) * 2, rel=0.02)


def test_sortino_uses_downside_deviation_over_all_returns():
    dates = pd.bdate_range("2026-01-05", periods=6).strftime("%Y-%m-%d").tolist()
    values = [100.0]
    for r in [0.01, -0.02, 0.01, -0.02, 0.01]:
        values.append(values[-1] * (1 + r))
    m = risk_metrics(_curve(dates, values))
    # Equal losses have zero spread around their own mean; the old formula
    # therefore returned no Sortino at all.
    assert m["sortino"] is not None
    rets = np.array([0.01, -0.02, 0.01, -0.02, 0.01])
    years = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days / 365.25
    ddev = math.sqrt(np.mean(np.minimum(rets, 0) ** 2)) * math.sqrt(len(rets) / years)
    assert m["sortino"] == pytest.approx((m["annualized_return"] - 0.03) / ddev, rel=0.02)


def test_a_zero_ratio_is_reported_as_zero_not_missing():
    dates = ["2025-01-01", "2025-07-01", "2026-01-01"]
    m = risk_metrics(_curve(dates, [100, 110, 103]), risk_free_rate=0.03)
    assert m["annualized_return"] == pytest.approx(0.03, abs=0.001)
    assert m["sharpe"] == pytest.approx(0.0, abs=0.01)
    assert m["sharpe"] is not None


def test_an_account_missing_a_day_is_carried_forward_not_dropped():
    # One account failing to sync on d2 used to read as a 50% drawdown.
    snaps = _snaps(
        [
            ("2026-01-01", 1, 50),
            ("2026-01-01", 2, 50),
            ("2026-01-02", 1, 50),
            ("2026-01-03", 1, 50),
            ("2026-01-03", 2, 50),
        ]
    )
    curve = equity_curve(snaps)
    assert curve["total_assets"].tolist() == [100, 100, 100]
    assert curve["drawdown"].min() == 0


def test_an_account_that_starts_later_adds_from_its_first_snapshot():
    snaps = _snaps([("2026-01-01", 1, 50), ("2026-01-02", 1, 50), ("2026-01-02", 2, 30)])
    assert equity_curve(snaps)["total_assets"].tolist() == [50, 80]


def test_ret_60d_spans_sixty_bars():
    close = pd.Series(np.linspace(100, 200, 100))
    m = position_volatility(pd.DataFrame({"close": close}))
    assert m["ret_60d"] == pytest.approx(close.iloc[-1] / close.iloc[-61] - 1, abs=1e-4)


def test_an_account_that_stops_reporting_is_not_carried_forward():
    snaps = _snaps([("2026-01-01", 1, 50), ("2026-01-01", 2, 30), ("2026-01-02", 1, 50)])
    assert equity_curve(snaps)["total_assets"].tolist() == [80, 50]
