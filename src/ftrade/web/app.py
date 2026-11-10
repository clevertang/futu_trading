"""本地只读 Web 面板（FastAPI）。默认只监听 127.0.0.1。"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..advice import observations
from ..analysis import build_report
from ..storage import repo
from ..storage.db import Database

STATIC = Path(__file__).parent / "static"

# Names a browser may legitimately send as Host for a loopback-bound panel.
# Anything else is a DNS-rebinding page pointed at 127.0.0.1.
LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "[::1]", "::1"]


# JavaScript numbers lose precision past 2**53-1. Futu account ids are 18-digit
# integers, so JSON.parse would silently rewrite their last digits -- they are
# identifiers, not quantities, and must cross the wire as strings.
JS_SAFE_INT = 2**53 - 1


def _stringify_big_ints(obj):
    if isinstance(obj, dict):
        return {k: _stringify_big_ints(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify_big_ints(v) for v in obj]
    if isinstance(obj, int) and not isinstance(obj, bool) and abs(obj) > JS_SAFE_INT:
        return str(obj)
    return obj


def _jsonable(obj):
    """numpy scalars to Python, NaN/inf to None -- JSON has no NaN, and
    Starlette refuses to send one (the whole report became a 500)."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def _clean(obj):
    """把 numpy / NaN 之类转成可 JSON 化的值。"""
    return _stringify_big_ints(
        json.loads(json.dumps(_jsonable(obj), default=str, ensure_ascii=False))
    )


def create_app(cfg, allow_remote: bool = False) -> FastAPI:
    app = FastAPI(title="ftrade", docs_url="/api/docs")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    # allow_remote is the owner's explicit choice to serve beyond loopback
    # (`serve --allow-remote`); only then is any Host name accepted.
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["*"] if allow_remote else LOOPBACK_HOSTS
    )

    # Create or migrate the schema once, here; requests then open read-only.
    Database(cfg.db_path).close()

    @app.middleware("http")
    async def _no_stale_cache(request, call_next):
        """Force revalidation on every response, static files included.

        StaticFiles sends Last-Modified and ETag but no Cache-Control, which
        leaves browsers free to apply heuristic freshness and keep serving a
        cached copy for a while after the file changes on disk. That happened
        here: new i18n keys landed, and a tab went on rendering the raw key
        names from a stale i18n.js. The report data never went stale -- it is
        read from SQLite per request -- so the page looked simply broken
        rather than old, which is the confusing failure mode.

        `no-cache` rather than `no-store`: the ETag still works, so an
        unchanged file costs a 304 and no body, while a changed one can never
        be served from cache.
        """
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache"
        return response

    def db() -> Database:
        return Database(cfg.db_path, readonly=True)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/report")
    def api_report(
        acc_id: int | None = None,
        base: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> JSONResponse:
        with db() as d:
            rep = build_report(d, cfg, acc_id, base=base, start=start, end=end)
        rep["observations"] = [o.__dict__ for o in observations(rep, cfg)]
        return JSONResponse(_clean(rep))

    @app.get("/api/deals")
    def api_deals(
        start: str | None = None,
        end: str | None = None,
        code: str | None = None,
        acc_id: int | None = None,
        limit: int = Query(500, ge=1, le=5000),
    ) -> JSONResponse:
        with db() as d:
            df = repo.deals(d, start=start, end=end, code=code, acc_id=acc_id)
        return JSONResponse(_clean(df.tail(limit).to_dict("records")))

    @app.get("/api/klines")
    def api_klines(code: str, start: str | None = None) -> JSONResponse:
        with db() as d:
            df = repo.klines(d, code, start)
        return JSONResponse(_clean(df.to_dict("records")))

    return app
