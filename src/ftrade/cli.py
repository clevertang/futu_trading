"""ftrade 命令行入口。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .advice import observations
from .analysis import build_report
from .config import load_config, project_root
from .gateway import build_gateway
from .logging_conf import setup_logging
from .storage import repo
from .storage.db import Database
from .sync import SyncService

console = Console()


def _db(cfg) -> Database:
    return Database(cfg.db_path)


def _fmt(v, digits: int = 2) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.{digits}f}"
    return str(v)


# ---------- 子命令 ----------


def cmd_init(args, cfg) -> int:
    root = project_root()
    src, dst = root / "config.example.yaml", root / "config.yaml"
    if dst.exists():
        console.print(f"[yellow]config.yaml 已存在，未覆盖：{dst}")
    else:
        shutil.copy(src, dst)
        console.print(f"[green]已生成 {dst}，按需修改后再跑 sync")
    _db(cfg).close()
    console.print(f"[green]数据库已就绪：{cfg.db_path}")
    return 0


def cmd_sync(args, cfg) -> int:
    with _db(cfg) as db, build_gateway(cfg, args.source) as gw:
        stats = SyncService(db, gw, cfg).sync_all(full=args.full, with_klines=not args.no_klines)
    console.print("[green]同步完成：", stats)
    return 0


def cmd_positions(args, cfg) -> int:
    with _db(cfg) as db:
        rep = build_report(db, cfg, args.acc_id)
    pf = rep["portfolio"]
    if not pf.get("holdings"):
        console.print("[yellow]本地没有持仓数据，先跑 `ftrade sync`")
        return 1

    t = Table(title=f"持仓 @ {rep['snapshot_date']}（本位币 {pf['base_currency']}）")
    for col in ("代码", "名称", "市场", "数量", "成本", "现价", "市值", "权重", "浮盈", "浮盈%"):
        t.add_column(col, justify="right" if col not in ("代码", "名称", "市场") else "left")
    for h in pf["holdings_detail"]:
        t.add_row(
            h["code"],
            str(h["name"] or ""),
            str(h["market"] or ""),
            _fmt(h["qty"], 0),
            _fmt(h["cost_price"], 3),
            _fmt(h["last_price"], 3),
            _fmt(h["market_val"]),
            f"{h['weight']:.1%}" if h["weight"] is not None else "-",
            _fmt(h["pl_val"]),
            f"{h['pl_ratio']:.2f}%" if h["pl_ratio"] is not None else "-",
        )
    console.print(t)
    console.print(
        f"总市值 {_fmt(pf['market_value'])} {pf['base_currency']}｜"
        f"浮动盈亏 {_fmt(pf['unrealized_pl'])}（{_fmt(pf.get('unrealized_pl_pct'))}%）｜"
        f"有效持仓数 {pf.get('effective_positions')}"
    )
    return 0


def cmd_deals(args, cfg) -> int:
    with _db(cfg) as db:
        df = repo.deals(db, start=args.start, end=args.end, code=args.code, acc_id=args.acc_id)
    if df.empty:
        console.print("[yellow]没有成交记录")
        return 1
    df = df.tail(args.limit)
    t = Table(title=f"成交记录（最近 {len(df)} 条）")
    for col in ("时间", "代码", "名称", "方向", "数量", "价格", "金额"):
        t.add_column(col)
    for _, r in df.iterrows():
        t.add_row(
            str(r["create_time"])[:16],
            r["code"],
            str(r["stock_name"] or ""),
            str(r["trd_side"]).replace("TrdSide.", ""),
            _fmt(r["qty"], 0),
            _fmt(r["price"], 3),
            _fmt(float(r["qty"] or 0) * float(r["price"] or 0)),
        )
    console.print(t)
    return 0


def cmd_trips(args, cfg) -> int:
    with _db(cfg) as db:
        rep = build_report(db, cfg, args.acc_id)
    trips = rep["round_trips"]
    if not trips:
        console.print("[yellow]还没有已平仓的完整交易")
        return 1
    t = Table(title="已平仓交易（FIFO 配对，最近 50 笔）")
    for col in (
        "代码",
        "名称",
        "开仓",
        "平仓",
        "数量",
        "开仓价",
        "平仓价",
        "盈亏",
        "盈亏%",
        "持有天",
    ):
        t.add_column(col)
    for r in trips[: args.limit]:
        color = "green" if r["pnl"] > 0 else "red"
        t.add_row(
            r["code"],
            str(r["stock_name"] or ""),
            r["open_time"],
            r["close_time"],
            _fmt(r["qty"], 0),
            _fmt(r["open_price"], 3),
            _fmt(r["close_price"], 3),
            f"[{color}]{_fmt(r['pnl'])}[/{color}]",
            f"[{color}]{r['pnl_pct']:.2f}%[/{color}]",
            str(r["holding_days"]),
        )
    console.print(t)
    b = rep["behavior"]
    console.print(
        f"胜率 {b.get('win_rate', 0):.1%}｜盈亏比 {b.get('profit_factor')}｜"
        f"已实现盈亏 {_fmt(b.get('realized_pnl'))}｜平均持有 {b.get('avg_holding_days')} 天"
    )
    return 0


def cmd_report(args, cfg) -> int:
    with _db(cfg) as db:
        rep = build_report(db, cfg, args.acc_id)
    obs = observations(rep, cfg)
    rep["observations"] = [o.__dict__ for o in obs]

    if args.json:
        Path(args.json).write_text(
            json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        console.print(f"[green]已写入 {args.json}")

    pf, risk, bh = rep["portfolio"], rep["risk"], rep["behavior"]
    console.rule(f"组合报告 {rep['generated_at']}")
    console.print(
        f"[bold]持仓[/bold] {pf.get('holdings', 0)} 只｜市值 {_fmt(pf.get('market_value'))} "
        f"{pf.get('base_currency')}｜浮盈 {_fmt(pf.get('unrealized_pl'))}"
    )
    console.print(f"[bold]市场分布[/bold] {pf.get('by_market')}")
    console.print(f"[bold]币种分布[/bold] {pf.get('by_currency')}")
    if "note" not in risk:
        console.print(
            f"[bold]净值[/bold] {risk['start_date']}~{risk['end_date']}｜"
            f"累计 {risk['total_return']:.2%}｜最大回撤 {risk['max_drawdown']:.2%}｜"
            f"Sharpe {risk.get('sharpe')}"
        )
    else:
        console.print(f"[bold]净值[/bold] {risk['note']}")
    console.print(
        f"[bold]交易[/bold] 成交 {bh.get('deal_count', 0)} 笔｜完整交易 {bh.get('round_trips', 0)} 笔｜"
        f"胜率 {bh.get('win_rate', 0):.1%}｜已实现 {_fmt(bh.get('realized_pnl'))}"
    )

    console.rule("观察")
    for o in obs:
        style = "yellow" if o.level == "warn" else "cyan"
        console.print(f"[{style}]· [{o.topic}] {o.message}")
    console.print(
        "\n[dim]以上为基于历史数据的事实性统计，不构成投资建议。建议引擎见 ROADMAP 的 Phase 2。"
    )
    return 0


def cmd_serve(args, cfg) -> int:
    import uvicorn

    from .web.app import create_app

    host = args.host or cfg.web.host
    port = args.port or cfg.web.port
    app = create_app(cfg)
    console.print(f"[green]面板启动：http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


# ---------- 入口 ----------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ftrade", description="富途只读持仓/交易分析平台")
    p.add_argument("-c", "--config", help="配置文件路径")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--acc-id", type=int, default=None, help="只看某个账户")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="生成 config.yaml 并初始化数据库").set_defaults(func=cmd_init)

    s = sub.add_parser("sync", help="从富途增量同步数据到本地")
    s.add_argument("--full", action="store_true", help="忽略断点，全量重拉历史")
    s.add_argument("--no-klines", action="store_true", help="跳过日线同步")
    s.add_argument("--source", choices=["futu", "mock"], default="futu", help="数据源")
    s.set_defaults(func=cmd_sync)

    sub.add_parser("positions", help="查看当前持仓").set_defaults(func=cmd_positions)

    s = sub.add_parser("deals", help="查看成交记录")
    s.add_argument("--start")
    s.add_argument("--end")
    s.add_argument("--code")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_deals)

    s = sub.add_parser("trips", help="查看 FIFO 配对后的完整交易")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(func=cmd_trips)

    s = sub.add_parser("report", help="输出组合分析报告")
    s.add_argument("--json", help="同时导出 JSON")
    s.set_defaults(func=cmd_report)

    s_serve = sub.add_parser("serve", help="启动本地 Web 面板")
    s_serve.add_argument("--host", help="覆盖配置里的监听地址")
    s_serve.add_argument("--port", type=int, help="覆盖配置里的端口")
    s_serve.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    cfg = load_config(args.config)
    try:
        return args.func(args, cfg)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        console.print(f"[red]出错：{exc}")
        if args.verbose:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
