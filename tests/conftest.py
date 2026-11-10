"""Shared test setup."""

import pytest

import ftrade.config


@pytest.fixture(autouse=True)
def _example_config_only(monkeypatch):
    """Tests never read the developer's private config.yaml.

    load_config() with no path prefers config.yaml when it exists. Locally
    that is the owner's real file -- real FX rates, margin rates, corporate
    actions -- while CI has only the example, so the same suite asserted
    against different settings in the two places.
    """
    monkeypatch.setattr(ftrade.config, "DEFAULT_CONFIG_NAMES", ("config.example.yaml",))
