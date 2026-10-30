"""Per-symbol margin model, checked against Futu's 初始保证金 page of 2026-10-30."""

import pytest

from ftrade.analysis.fx import FX
from ftrade.analysis.margin import index_fall_to_call, margin_model

FXU = FX({"USD": 1.0}, "USD")
RATES = {
    "US.TQQQ": {"initial": 0.50, "maintenance": 0.45, "beta": 3},
    "US.QQQ": {"initial": 0.40, "maintenance": 0.30},
}
# The app's figures, per symbol in USD: TQQQ 600 @ 64.26, QQQ 3 @ 698.37.
TQQQ, QQQ = 600 * 64.26, 3 * 698.37
HOLDINGS = [
    {"code": "US.TQQQ", "market_val_base": TQQQ},
    {"code": "US.QQQ", "market_val_base": QQQ},
    {"code": "US.PDD280121C70000", "market_val_base": 14399.07},  # no loan value
    {"code": "US.TQQQ261030C76000", "market_val_base": -2.24},  # covered, adds nothing
]
MM = 0.45 * TQQQ + 0.30 * QQQ  # 17,979.03: the page's 140,936.20 HKD at 7.839
IM = 0.50 * TQQQ + 0.40 * QQQ  # 20,116.34: the page's 157,691.39 HKD


def _equity(ewl):
    return {"equity_with_loan": ewl, "maintenance_margin": MM, "initial_margin": IM}


def test_declared_rates_reproduce_the_brokers_requirement():
    m = margin_model(HOLDINGS, _equity(31634.0), RATES, FXU)
    assert m["modeled_maintenance"] == pytest.approx(MM, abs=0.01)
    assert abs(m["maintenance_gap"]) < 0.01
    assert m["unrated"] == []


def test_index_fall_counts_a_3x_etf_three_times():
    m = margin_model(HOLDINGS, _equity(31634.0), RATES, FXU)
    x = m["index_fall_to_call"]
    # At that fall, equity with loan value meets the requirement.
    ewl_after = 31634.0 - TQQQ * 3 * x - QQQ * x
    mm_after = 0.45 * TQQQ * (1 - 3 * x) + 0.30 * QQQ * (1 - x)
    assert ewl_after == pytest.approx(mm_after, abs=1.0)
    assert x == pytest.approx(0.210, abs=0.001)  # after selling the PDD calls


def test_headroom_is_the_initial_excess_over_the_symbols_initial_rate():
    m = margin_model(
        HOLDINGS,
        _equity(31634.0),
        RATES,
        FXU,
        prices={"US.TQQQ": 64.26, "US.QQQ": 698.37},
        currency_of=lambda c: "USD",
    )
    rows = {r["code"]: r for r in m["headroom"]}
    excess = 31634.0 - IM
    assert rows["US.QQQ"]["max_buy"] == pytest.approx(excess / 0.40, abs=0.01)
    assert rows["US.QQQ"]["max_shares"] == 41
    assert rows["US.TQQQ"]["max_shares"] == 358
    # Buying the most it can leaves far less room than holding.
    assert rows["US.TQQQ"]["index_fall_after_max"] < m["index_fall_to_call"]


def test_at_the_line_there_is_no_room_and_no_fall_left():
    m = margin_model(HOLDINGS, _equity(MM - 6.0), RATES, FXU)
    assert m["index_fall_to_call"] == 0.0
    assert all(r["max_buy"] == 0.0 for r in m["headroom"])


def test_unrated_holdings_keep_the_blended_rate_instead_of_counting_free():
    holdings = HOLDINGS + [{"code": "US.NVDA", "market_val_base": 5000.0}]
    m = margin_model(holdings, _equity(31634.0), RATES, FXU)
    assert m["unrated"] == ["US.NVDA"]
    assert m["modeled_maintenance"] > MM


def test_no_rates_or_no_requirement_means_no_model():
    assert margin_model(HOLDINGS, _equity(31634.0), {}, FXU) is None
    assert margin_model(HOLDINGS, {"equity_with_loan": 1.0}, RATES, FXU) is None


def test_index_fall_without_marginable_holdings_is_unknown():
    assert index_fall_to_call([], 100.0, 50.0) is None


def test_selling_long_options_turns_their_value_into_loan_value():
    equity = {**_equity(31634.0 - 14399.07), "total_assets": 31634.0}
    m = margin_model(HOLDINGS, equity, RATES, FXU)
    sold = m["if_long_options_sold"]
    assert sold["proceeds"] == pytest.approx(14399.07, abs=0.01)
    assert sold["index_fall_to_call"] == pytest.approx(0.210, abs=0.001)
    assert sold["index_fall_to_call"] > m["index_fall_to_call"]
