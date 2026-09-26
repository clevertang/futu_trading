"""Web layer: the panel is read-only and must never serve a stale page."""

from fastapi.testclient import TestClient

from ftrade.config import load_config
from ftrade.web.app import create_app


def _client(tmp_path):
    cfg = load_config()
    cfg.storage.db_path = str(tmp_path / "t.db")
    return TestClient(create_app(cfg))


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
