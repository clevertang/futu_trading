"""Web layer: the panel is read-only and must never serve a stale page."""

from fastapi.testclient import TestClient

from ftrade.config import load_config
from ftrade.web.app import create_app


def _client(tmp_path):
    cfg = load_config()
    cfg.storage.db_path = str(tmp_path / "t.db")
    # A real browser on the panel sends Host: 127.0.0.1:<port>, not "testserver".
    return TestClient(create_app(cfg), base_url="http://127.0.0.1:8765")


def test_static_files_are_not_heuristically_cached(tmp_path):
    """StaticFiles ships Last-Modified/ETag but no Cache-Control.

    Without one a browser may apply heuristic freshness and keep serving a
    cached i18n.js after new keys land, which renders raw key names instead
    of text -- while the report data, read from SQLite per request, stays
    correct. The page looks broken rather than stale, so pin the header.
    """
    r = _client(tmp_path).get("/static/i18n.js")

    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_the_page_itself_is_not_cached(tmp_path):
    r = _client(tmp_path).get("/")

    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_revalidation_still_yields_304(tmp_path):
    """`no-cache` must not become `no-store`: an unchanged file costs no body."""
    client = _client(tmp_path)
    first = client.get("/static/i18n.js")
    etag = first.headers["etag"]

    again = client.get("/static/i18n.js", headers={"If-None-Match": etag})

    assert again.status_code == 304
    assert not again.content
    assert again.headers["cache-control"] == "no-cache"


def test_a_foreign_host_header_is_refused(tmp_path):
    # DNS rebinding: a page on attacker.example re-pointed at 127.0.0.1 sends
    # its own name as Host. Loopback binding alone does not stop it reading
    # the whole account.
    r = _client(tmp_path).get("/api/report", headers={"Host": "attacker.example:8765"})
    assert r.status_code == 400


def test_loopback_host_names_are_served(tmp_path):
    client = _client(tmp_path)
    for host in ("127.0.0.1:8765", "localhost:8765"):
        assert client.get("/", headers={"Host": host}).status_code == 200


def test_nan_in_the_report_becomes_null_instead_of_a_500(tmp_path, monkeypatch):
    import numpy as np

    from ftrade.web import app as web

    monkeypatch.setattr(
        web,
        "build_report",
        lambda *a, **k: {"x": float("nan"), "n": np.int64(5), "b": np.bool_(False)},
    )
    monkeypatch.setattr(web, "observations", lambda rep, cfg: [])
    r = _client(tmp_path).get("/api/report")
    assert r.status_code == 200
    body = r.json()
    assert body["x"] is None and body["n"] == 5 and body["b"] is False


def test_requests_read_through_a_read_only_connection(tmp_path, monkeypatch):
    from ftrade.storage import db as dbmod

    opened = []
    real = dbmod.Database.__init__

    def spy(self, path, readonly=False):
        opened.append(readonly)
        real(self, path, readonly=readonly)

    monkeypatch.setattr(dbmod.Database, "__init__", spy)
    client = _client(tmp_path)
    opened.clear()
    client.get("/api/deals")
    assert opened == [True]


def test_a_negative_deals_limit_is_rejected(tmp_path):
    assert _client(tmp_path).get("/api/deals?limit=-3").status_code == 422
