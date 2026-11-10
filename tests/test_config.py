"""Config loading: bad values fail loudly instead of misbehaving later."""

import logging

import pytest

from ftrade.config import ConfigError, load_config


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_an_unquoted_date_is_read_as_a_string(tmp_path):
    # YAML turns 2020-01-01 into a datetime.date, which broke slicing later.
    cfg = load_config(_write(tmp_path, "sync:\n  deals_start: 2020-01-01\n"))
    assert cfg.sync.deals_start == "2020-01-01"


@pytest.mark.parametrize("days", [0, -5, 91])
def test_window_days_outside_futus_limit_is_rejected(tmp_path, days):
    # 0 looped forever; over 90 makes Futu reject every window.
    with pytest.raises(ConfigError, match="window_days"):
        load_config(_write(tmp_path, f"sync:\n  window_days: {days}\n"))


def test_an_unknown_key_is_warned_about_not_silently_dropped(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        load_config(_write(tmp_path, "web:\n  margin_rates: {}\n"))
    assert "web.margin_rates" in caplog.text


def test_a_relative_db_path_resolves_against_the_config_files_folder(tmp_path):
    cfg = load_config(_write(tmp_path, "storage:\n  db_path: data/x.db\n"))
    assert cfg.db_path == tmp_path / "data" / "x.db"


def test_the_example_documents_analysis_settings_under_analysis():
    # Uncommenting margin_rates or net_asset_milestone where the example put
    # them must take effect; under web: they were silently ignored.
    from pathlib import Path

    import yaml

    text = (Path(__file__).resolve().parents[1] / "config.example.yaml").read_text("utf-8")
    uncommented = "\n".join(
        line.replace("  # ", "  ", 1)
        if line.startswith("  # margin_rates")
        or line.startswith("  #   US.")
        or line.startswith("  # net_asset_milestone")
        or line.startswith("  #   amount")
        or line.startswith("  #   currency")
        or line.startswith("  #   label")
        else line
        for line in text.splitlines()
    )
    data = yaml.safe_load(uncommented)
    assert "margin_rates" in data["analysis"] and "net_asset_milestone" in data["analysis"]
