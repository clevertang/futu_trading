"""把各个分析模块拼成一份完整报告（dict），供 CLI / Web / 后续建议引擎消费。"""

from __future__ import annotations

from datetime import datetime

from ..storage import repo
from ..storage.db import Database
from . import metrics, portfolio, trades
from .fx import FX
from .pnl import fifo_round_trips, open_lots


def build_report(db: Database, cfg, acc_id: int | None = None) -> dict:
    a = cfg.analysis
    fx = FX(a.fx_rates, a.base_currency)

    pos = repo.positions(db, acc_id=acc_id)
    dls = repo.deals(db, acc_id=acc_id)
    snaps = repo.account_snapshots(db, acc_id=acc_id)

    cur_map = dict(zip(pos.get("code", []), pos.get("currency", []))) if not pos.empty else {}
    trips = fifo_round_trips(dls, currency_of=cur_map.get)

    curve = metrics.equity_curve(snaps)

    # 本地 FIFO 推算的持仓 vs 券商返回的持仓，对不上通常意味着历史成交没拉全
    lots = open_lots(dls)
    reconciliation = []
    if not lots.empty and not pos.empty:
        broker = dict(zip(pos["code"], pos["qty"]))
        for _, r in lots.iterrows():
            b = float(broker.get(r["code"], 0) or 0)
            if abs(b - float(r["qty"])) > 1e-6:
                reconciliation.append(
                    {"code": r["code"], "local_fifo_qty": float(r["qty"]), "broker_qty": b}
                )

    per_position_risk = {}
    for code in pos["code"].tolist() if not pos.empty else []:
        kl = repo.klines(db, code)
        m = metrics.position_volatility(kl)
        if m:
            per_position_risk[code] = m

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "snapshot_date": repo.latest_snapshot_date(db, acc_id),
        "last_sync_at": db.get_state("last_sync_at"),
        "base_currency": a.base_currency,
        "accounts": repo.accounts(db).to_dict("records"),
        "portfolio": portfolio.summary(pos, fx, a.concentration_warn),
        "risk": metrics.risk_metrics(curve, a.risk_free_rate),
        "equity_curve": curve[["snap_date", "total_assets", "drawdown"]].to_dict("records")
        if not curve.empty
        else [],
        "behavior": trades.behavior(trips, dls),
        "round_trips": trips.sort_values("close_time", ascending=False).head(50).to_dict("records")
        if not trips.empty
        else [],
        "position_risk": per_position_risk,
        "reconciliation": reconciliation,
    }
