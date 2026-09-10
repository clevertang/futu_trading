"""本地只读 Web 面板（FastAPI）。默认只监听 127.0.0.1。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..advice import observations
from ..analysis import build_report
from ..storage import repo
from ..storage.db import Database

STATIC = Path(__file__).parent / "static"


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


def _clean(obj):
    """把 numpy / NaN 之类转成可 JSON 化的值。"""
    return _stringify_big_ints(json.loads(json.dumps(obj, default=str, ensure_ascii=False)))


def create_app(cfg) -> FastAPI:
    app = FastAPI(title="ftrade", docs_url="/api/docs")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def db() -> Database:
        return Database(cfg.db_path)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/report")
    def api_report(acc_id: int | None = None, base: str | None = None) -> JSONResponse:
        with db() as d:
            rep = build_report(d, cfg, acc_id, base=base)
        rep["observations"] = [o.__dict__ for o in observations(rep, cfg)]
        return JSONResponse(_clean(rep))

    @app.get("/api/deals")
    def api_deals(
        start: str | None = None,
        end: str | None = None,
        code: str | None = None,
        acc_id: int | None = None,
        limit: int = Query(500, le=5000),
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
