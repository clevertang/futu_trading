"""把各个分析模块拼成一份完整报告（dict），供 CLI / Web / 后续建议引擎消费。"""

from __future__ import annotations

from datetime import datetime

from ..storage import repo
from ..storage.db import Database
from . import corporate, equity, metrics, portfolio, trades
from . import fees as fees_mod
from .fx import FX, currency_for_code
from .instruments import underlying_of
from .pnl import fifo_round_trips, open_lots


def _merge_unrealized(by_code: list[dict], holdings: list[dict]) -> list[dict]:
    """Add open-position P&L to each symbol's realised figure.

    Realised alone answers "what did I bank"; the broker's own per-symbol view
    answers "how has this name done", which includes what is still open. A name
    sitting on a large unrealised loss otherwise ranks as a winner.
    """
    unrealized: dict[str, float] = {}
    for h in holdings or []:
        code = underlying_of(h.get("code"))
        value = h.get("pl_val_base")
        if code and value is not None:
            unrealized[code] = unrealized.get(code, 0.0) + float(value)

    merged = []
    for row in by_code or []:
        code = row.get("code")
        open_pl = unrealized.pop(code, 0.0)
        merged.append(
            {
                **row,
                "unrealized": round(open_pl, 2),
                "total": round(float(row.get("pnl") or 0.0) + open_pl, 2),
            }
        )
    # Names held but never closed have no realised row of their own.
    for code, open_pl in unrealized.items():
        merged.append(
            {"code": code, "pnl": 0.0, "unrealized": round(open_pl, 2), "total": round(open_pl, 2)}
        )
    return sorted(merged, key=lambda r: r["total"], reverse=True)


def _with_net_of_fees(behavior: dict, fee_stats: dict) -> dict:
    """Attach a fees-inclusive realised figure beside the gross one.

    Kept as a separate field rather than folded into `realized_pnl`: the fee
    total covers every order, including those that opened positions still held,
    so it is a whole-account cost rather than a per-round-trip one.
    """
    total = fee_stats.get("total") or 0.0
    gross = behavior.get("realized_pnl")
    behavior["fees_total"] = round(total, 2)
    if gross is not None:
        behavior["realized_pnl_net"] = round(gross - total, 2)
    return behavior


def build_report(db: Database, cfg, acc_id: int | None = None, base: str | None = None) -> dict:
    a = cfg.analysis
    fx = FX(a.fx_rates, a.base_currency).rebase(base) if base else FX(a.fx_rates, a.base_currency)

    pos = repo.positions(db, acc_id=acc_id)
    dls = corporate.apply_actions(repo.deals(db, acc_id=acc_id), corporate.from_config(cfg))
    snaps = repo.account_snapshots(db, acc_id=acc_id)

    cur_map = (
        dict(zip(pos.get("code", []), pos.get("currency", []), strict=True))
        if not pos.empty
        else {}
    )

    def currency_of(code):
        # Current holdings carry the broker's own currency; everything else --
        # which is most of the history -- falls back to the market prefix.
        return cur_map.get(code) or currency_for_code(code)

    trips = fifo_round_trips(dls, currency_of=currency_of)

    fee_stats = fees_mod.fee_summary(repo.order_fees(db, acc_id=acc_id), fx)
    curve = metrics.equity_curve(snaps)

    # 本地 FIFO 推算的持仓 vs 券商返回的持仓，对不上通常意味着历史成交没拉全
    lots = open_lots(dls)
    reconciliation = []
    if not lots.empty and not pos.empty:
        broker = dict(zip(pos["code"], pos["qty"], strict=True))
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

    acct = equity.account_equity(snaps, fx, a.base_currency)
    pf = portfolio.summary(pos, fx, a.concentration_warn)
    bh = _with_net_of_fees(trades.behavior(trips, dls, fx), fee_stats)
    net = acct.get("total_assets")
    pf["holdings_detail"] = equity.net_asset_weights(pf.get("holdings_detail", []), net)
    bh["realized_by_code"] = _merge_unrealized(
        bh.get("realized_by_code", []), pf.get("holdings_detail", [])
    )
    pf["concentrated"] = equity.net_asset_weights(pf.get("concentrated", []), net)
    if net:
        pf["net_assets"] = net
        top = pf.get("top1_weight")
        mv = pf.get("market_value") or 0.0
        pf["top1_weight_of_net"] = round(top * mv / net, 4) if top is not None else None
        pf["gross_exposure"] = acct.get("gross_exposure")

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "snapshot_date": repo.latest_snapshot_date(db, acc_id),
        "last_sync_at": db.get_state("last_sync_at"),
        "base_currency": fx.base,
        "available_currencies": fx.currencies(),
        "accounts": repo.accounts(db).to_dict("records"),
        "equity": acct,
        "portfolio": pf,
        "risk": metrics.risk_metrics(curve, a.risk_free_rate),
        "equity_curve": curve[["snap_date", "total_assets", "drawdown"]].to_dict("records")
        if not curve.empty
        else [],
        "behavior": bh,
        "fees": fee_stats,
        "realized_curve": trades.realized_curve(trips, fx),
        "pnl_distribution": trades.pnl_distribution(trips),
        "round_trips": trips.sort_values("close_time", ascending=False).head(50).to_dict("records")
        if not trips.empty
        else [],
        "position_risk": per_position_risk,
        "reconciliation": reconciliation,
    }
