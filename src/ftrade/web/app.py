"""本地只读 Web 面板（FastAPI）。默认只监听 127.0.0.1。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

from ..advice import observations
from ..analysis import build_report
from ..storage import repo
from ..storage.db import Database

STATIC = Path(__file__).parent / "static"


def _clean(obj):
    """把 numpy / NaN 之类转成可 JSON 化的值。"""
    return json.loads(json.dumps(obj, default=str, ensure_ascii=False))


def create_app(cfg) -> FastAPI:
    app = FastAPI(title="ftrade", docs_url="/api/docs")

    def db() -> Database:
        return Database(cfg.db_path)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/report")
    def api_report(acc_id: int | None = None) -> JSONResponse:
        with db() as d:
            rep = build_report(d, cfg, acc_id)
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
