"""Fact sheet for a Xueqiu post: what happened in a period, on one consistent basis.

    python scripts/xueqiu_facts.py --start 2026-09-27 [--end 2026-09-30] [--base USD]

Prints Markdown with four sections:

1. Period figures -- the same numbers share mode shows for the same dates,
   because both come from build_report(start=..., end=...).
2. Every fill in the period, labelled by what it actually was. The broker feed
   shows an expiry as a zero-price BUY_BACK and an assignment as an ordinary
   stock sale; a post that calls those "bought back" or "sold" misreports
   what the person did, so they are marked as expiry / assignment here.
3. Positions as of the latest snapshot, with days to expiry and distance from
   spot for each option -- clearly labelled as *today*, not as the period.
4. This year and this month up to that snapshot (analysis/todate.py), the
   same figures the holdings image shows. Like section 3 it belongs under
   the post's 当前持仓 heading.

Read-only: it only queries the local database. It gives no recommendation;
the post's reasoning is the author's to write.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from ftrade.analysis import build_report  # noqa: E402
from ftrade.analysis.instruments import parse_option, underlying_of  # noqa: E402
from ftrade.config import load_config  # noqa: E402
from ftrade.storage import repo  # noqa: E402
from ftrade.storage.db import Database  # noqa: E402

SIDE = {"BUY": "买入", "SELL": "卖出", "SELL_SHORT": "卖出开仓", "BUY_BACK": "买回平仓"}
# How far apart an option's settlement and the resulting share delivery can be
# booked and still be read as one exercise.
ASSIGN_WINDOW_S = 10 * 60
# Without a settlement fill, how long after expiry a delivery at the strike is
# still read as that contract's exercise (expiry Friday, booked Sunday, here).
DELIVERY_LAG_DAYS = 7


def _money(v) -> str:
    return "-" if v is None else f"{float(v):+,.2f}"


def label_fills(deals, history=None) -> list[dict]:
    """Classify each fill: a real order, an option settlement, or an assignment.

    Futu books the end of an option position as a zero-price closing fill --
    whether it expired worthless or was exercised, and whether that happened
    at expiry or early. An exercise also produces a stock fill at the strike,
    a minute or so apart (01:23 for the option, 01:24 for the shares, in
    this history). So pair each zero-price option fill with a stock fill on
    the same underlying, at the strike, within a few minutes: paired means
    exercised, unpaired means it lapsed. The stock fill is then not an order
    the person placed, and must not be described as one.

    Before 2025 Futu booked no settlement fill at all -- the option simply
    vanished -- so an exercise left only the share delivery, typically on a
    weekend (NVDA: 100 shares "sold" at 130 on Sunday 2025-06-01, right after
    a short 130 call expired on the Friday). For those, `history` (all fills,
    so a contract opened before the period still counts) is searched for a
    short option on the same underlying, struck at the fill price, that
    expired within the preceding week, with a matching direction: a short
    call delivers shares away (SELL), a short put delivers them in (BUY).
    """
    rows = deals.to_dict("records")
    for r in rows:
        r["opt"] = parse_option(r["code"])
        r["ts"] = pd.to_datetime(str(r["create_time"])[:19])
        r["kind"] = "order"

    settlements = [r for r in rows if r["opt"] and float(r["price"] or 0) == 0]
    for s in settlements:
        s["kind"] = "lapsed"
        s["early"] = s["ts"].date() < s["opt"]["expiry"]
    for r in rows:
        if r["opt"]:
            continue
        for s in settlements:
            if (
                s["kind"] == "lapsed"
                and s["opt"]["underlying"] == r["code"]
                and abs(float(s["opt"]["strike"]) - float(r["price"] or 0)) < 1e-6
                and abs((r["ts"] - s["ts"]).total_seconds()) <= ASSIGN_WINDOW_S
            ):
                s["kind"], r["kind"] = "exercised", "assignment"
                break

    shorts = []  # contracts written at some point, from the whole history
    for h in (history if history is not None else deals).to_dict("records"):
        opt = parse_option(h["code"])
        if opt and h["trd_side"] == "SELL_SHORT":
            shorts.append(opt)
    for r in rows:
        if r["opt"] or r["kind"] != "order":
            continue
        day = r["ts"].date()
        want = {"SELL": "CALL", "BUY": "PUT"}.get(r["trd_side"])
        for opt in shorts:
            if (
                opt["underlying"] == r["code"]
                and opt["kind"] == want
                and abs(float(opt["strike"]) - float(r["price"] or 0)) < 1e-6
                and 0 <= (day - opt["expiry"]).days <= DELIVERY_LAG_DAYS
            ):
                r["kind"], r["from_opt"] = "assignment", opt
                break
    return rows


def describe(f: dict) -> str:
    t, qty, px, opt = str(f["create_time"])[:16], float(f["qty"]), float(f["price"]), f["opt"]
    if opt:
        name = f"{underlying_of(f['code']).split('.')[-1]} {opt['expiry']} {opt['strike']:g} {opt['kind']}"
        unit = "张"
    else:
        name, unit = f["code"].split(".")[-1], "股"
    if f["kind"] == "lapsed":
        return f"- {t}　{name} ×{qty:g} **到期作废**（非主动操作）"
    if f["kind"] == "exercised":
        when = "**提前被行权**" if f.get("early") else "**到期被行权**"
        return f"- {t}　{name} ×{qty:g} {when}（非主动操作）"
    if f["kind"] == "assignment":
        verb = "行权交割卖出" if f["trd_side"] == "SELL" else "行权交割买入"
        src = f.get("from_opt")
        why = (
            f"{src['expiry']} 到期的 {src['strike']:g} {src['kind']} 被行权"
            if src
            else "上一行期权的结果"
        )
        return f"- {t}　{name} {qty:g}{unit} @ {px:g} **{verb}**（{why}，非主动操作）"
    return f"- {t}　{SIDE.get(f['trd_side'], f['trd_side'])} {name} {qty:g}{unit} @ {px:g}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Fact sheet for a Xueqiu post.")
    ap.add_argument("--start", required=True, help="first day of the period, YYYY-MM-DD")
    ap.add_argument("--end", default=date.today().isoformat(), help="last day, default today")
    ap.add_argument("--base", default="USD")
    args = ap.parse_args()

    cfg = load_config()
    with Database(cfg.db_path) as db:
        rep = build_report(db, cfg, base=args.base, start=args.start, end=args.end)
        fills = repo.deals(db, start=args.start, end=args.end + " 23:59:59")
        history = repo.deals(db)
        pos = repo.positions(db)
        snap = repo.latest_snapshot_date(db)

    bh, fees, cash = rep["behavior"], rep["fees"], rep["cash_flows"]
    cur = rep["base_currency"]
    print(f"# 事实清单 {args.start} → {args.end}（{cur}）\n")
    print(
        "口径：已实现 = FIFO 配对的已平仓交易，不含持仓浮动。与面板分享模式同一区间的数字一致。\n"
    )

    print("## 1. 区间统计\n")
    print(f"- 平仓 {bh.get('round_trips', 0)} 笔，已实现 {_money(bh.get('realized_pnl'))}")
    print(f"- 扣费后 {_money(bh.get('realized_pnl_net'))}（费用 {fees.get('total', 0):,.2f}）")
    if bh.get("win_rate") is not None:
        print(f"- 胜率 {bh['win_rate'] * 100:.1f}%，盈亏比 {bh.get('profit_factor', '-')}")
    if cash.get("dividends"):
        print(f"- 股息 {cash['dividends']:,.2f}，代扣税 {abs(cash.get('dividend_tax') or 0):,.2f}")
    by_code = [r for r in bh.get("realized_by_code", []) if r.get("pnl")]
    if by_code:
        print(
            "- 分标的已实现："
            + "，".join(
                f"{r['code'].split('.')[-1]} {r['pnl']:+,.0f}"
                for r in sorted(by_code, key=lambda r: -r["pnl"])
            )
        )
    months = bh.get("realized_by_month") or {}
    if len(months) > 1:
        print("- 分月：" + "，".join(f"{m} {v:+,.0f}" for m, v in sorted(months.items())))

    print("\n## 2. 区间内成交（标注了到期与行权）\n")
    if fills.empty:
        print("- 无")
    else:
        for f in label_fills(fills.sort_values("create_time"), history):
            print(describe(f))

    print(f"\n## 3. 当前持仓（{snap} 快照，是**今天**的状态，不是区间结果）\n")
    spot = {r["code"]: float(r["nominal_price"] or 0) for r in pos.to_dict("records")}
    for r in pos.to_dict("records"):
        q = float(r["qty"] or 0)
        if not q:
            continue
        opt = parse_option(r["code"])
        if not opt:
            print(
                f"- {r['code'].split('.')[-1]} {q:g} 股，成本 {float(r['cost_price'] or 0):.3f}，"
                f"现价 {float(r['nominal_price'] or 0):.3f}"
            )
            continue
        s = spot.get(opt["underlying"])
        dte = (opt["expiry"] - date.fromisoformat(str(snap))).days
        dist = f"，距现价 {(opt['strike'] / s - 1) * 100:+.1f}%" if s else ""
        side = "卖出" if q < 0 else "持有"
        print(
            f"- {side} {underlying_of(r['code']).split('.')[-1]} {opt['expiry']} "
            f"{opt['strike']:g} {opt['kind']} ×{abs(q):g}，剩 {dte} 天{dist}，"
            f"开仓价 {float(r['cost_price'] or 0):.2f} 现价 {float(r['nominal_price'] or 0):.2f}"
        )

    print(f"\n## 4. 今年 / 本月（截至 {snap}，放在当前持仓一节，持仓图里也有）\n")
    for line in to_date_lines(rep["to_date"]):
        print(line)


def to_date_lines(td: dict) -> list[str]:
    """Section 4: the report's `to_date` block, one realised and one
    net-asset line per period."""
    lines = []
    for key, label in (("year", "今年"), ("month", "本月")):
        p = td.get(key) or {}
        div = f"，股息（税后）{p['dividends']:+,.2f}" if p.get("dividends") else ""
        lines.append(
            f"- {label}已实现 {_money(p.get('net'))}（平仓 {p.get('realized', 0):+,.2f}，"
            f"费用 {p.get('fees', 0):,.2f}{div}；{p.get('since')} 起）"
        )
        nav = p.get("nav")
        if nav:
            ext = (
                f"，期间出入金 {nav['external']:+,.2f} 已扣除"
                if nav["external"]
                else "，期间无出入金"
            )
            how = (
                f"按年初持仓重建，年初净资产 {nav['start']:,.2f} 为倒推；"
                f"浮盈变化 {_money(nav.get('open_pl_change'))}，利息等 {_money(nav.get('other'))}"
                + (
                    f"；{'、'.join(nav['carried_at_cost'])} 年初按开仓价计"
                    if nav.get("carried_at_cost")
                    else ""
                )
                if nav.get("method") == "rebuilt"
                else f"净资产 {nav['start']:,.2f} → {nav['end']:,.2f}"
            )
            lines.append(
                f"- {label}收益（含浮盈）{_money(nav['pnl'])}（{nav['pct'] * 100:+.2f}%），"
                f"{nav['from']} → {nav['to']}{ext}。{how}"
            )
        else:
            lines.append(
                f"- {label}收益（含浮盈）：算不出（{p.get('since')} 之前没有快照，持仓也无法重建）"
            )
    return lines


if __name__ == "__main__":
    main()
