"""The CLI end to end against the mock gateway, in a throwaway folder."""

import json

import pytest

from ftrade import cli


@pytest.fixture
def config(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(f"storage:\n  db_path: {tmp_path / 't.db'}\n", encoding="utf-8")
    return str(p)


def test_mock_sync_then_every_read_command_succeeds(config, tmp_path):
    assert cli.main(["-c", config, "sync", "--source", "mock", "--no-klines"]) == 0
    assert cli.main(["-c", config, "positions"]) == 0
    assert cli.main(["-c", config, "deals", "--limit", "5"]) == 0
    assert cli.main(["-c", config, "trips"]) == 0
    out = tmp_path / "r.json"
    assert cli.main(["-c", config, "report", "--json", str(out)]) == 0
    assert "portfolio" in json.loads(out.read_text(encoding="utf-8"))


def test_acc_id_is_accepted_after_the_subcommand(config):
    cli.main(["-c", config, "sync", "--source", "mock", "--no-klines"])
    assert cli.main(["-c", config, "positions", "--acc-id", "900000001"]) == 0
    assert cli.main(["-c", config, "--acc-id", "900000001", "positions"]) == 0


def test_sync_with_a_failed_account_exits_nonzero(config, monkeypatch):
    from ftrade.sync import SyncService

    def boom(self, acc_id):
        raise RuntimeError("Disconnect")

    monkeypatch.setattr(SyncService, "sync_account_info", boom)
    assert cli.main(["-c", config, "sync", "--source", "mock", "--no-klines"]) == cli.EXIT_PARTIAL


def test_a_mock_sync_refuses_a_database_holding_a_real_account(config, tmp_path):
    from ftrade.storage.db import Database

    with Database(tmp_path / "t.db") as db:
        db.upsert("accounts", [{"acc_id": 1, "trd_env": "REAL"}])
    assert cli.main(["-c", config, "sync", "--source", "mock", "--no-klines"]) == 1
    with Database(tmp_path / "t.db") as db:
        assert db.scalar("SELECT COUNT(*) FROM deals") == 0


def test_serve_refuses_a_non_loopback_host_without_the_explicit_flag(config, monkeypatch):
    import uvicorn

    ran = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: ran.append(k))
    assert cli.main(["-c", config, "serve", "--host", "0.0.0.0"]) == 1
    assert ran == []
    assert cli.main(["-c", config, "serve", "--host", "127.0.0.1"]) == 0
    assert ran
