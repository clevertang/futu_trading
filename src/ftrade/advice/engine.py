"""建议引擎（Phase 1 只做事实层的规则提示，不做择时/荐股）。

Phase 1 的目标是把「客观事实」摆出来：集中度、币种错配、处置效应、数据缺口。
真正的投资建议留到 Phase 2 接入 LLM 时再做，接口见 AdviceEngine。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Observation:
    level: str  # info | warn
    topic: str
    message: str


class AdviceEngine(Protocol):
    """Phase 2 预留：把 report 交给 LLM，产出中文建议正文。"""

    def advise(self, report: dict) -> str: ...


def observations(report: dict, cfg) -> list[Observation]:
    out: list[Observation] = []
    pf = report.get("portfolio") or {}
    bh = report.get("behavior") or {}
    thr = cfg.analysis.concentration_warn

    for item in pf.get("concentrated", []):
        out.append(
            Observation(
                "warn",
                "集中度",
                f"{item['name'] or item['code']} 占组合 {item['weight']:.1%}，超过阈值 {thr:.0%}",
            )
        )

    eff = pf.get("effective_positions")
    if eff is not None and eff < 3:
        out.append(
            Observation("warn", "分散度", f"有效持仓数仅 {eff}，组合高度依赖少数标的")
        )

    by_cur = pf.get("by_currency") or {}
    total = sum(by_cur.values()) or 1
    for cur, val in by_cur.items():
        share = val / total
        if cur != cfg.analysis.base_currency and share > 0.7:
            out.append(
                Observation("info", "币种暴露", f"{share:.0%} 的市值在 {cur}，存在汇率敞口")
            )

    if bh.get("disposition_effect"):
        out.append(
            Observation(
                "warn",
                "交易行为",
                f"盈利单平均持有 {bh['avg_win_holding_days']} 天，亏损单 {bh['avg_loss_holding_days']} 天，"
                "呈现「赚了就跑、亏了死扛」的处置效应",
            )
        )

    pf_factor = bh.get("profit_factor")
    if pf_factor is not None and pf_factor < 1:
        out.append(Observation("warn", "盈亏比", f"盈亏比 {pf_factor}，历史已实现交易整体亏损"))

    dpm = bh.get("deals_per_month")
    if dpm is not None and dpm > 20:
        out.append(
            Observation("info", "交易频率", f"月均成交 {dpm} 笔，已经不太像低频策略，注意摩擦成本")
        )

    if report.get("reconciliation"):
        codes = ", ".join(r["code"] for r in report["reconciliation"][:5])
        out.append(
            Observation(
                "warn",
                "数据完整性",
                f"本地 FIFO 推算持仓与券商不一致（{codes}），历史成交可能没拉全或含拆股/红股，"
                "可以试试 `ftrade sync --full`",
            )
        )

    risk = report.get("risk") or {}
    if risk.get("max_drawdown") is not None and risk["max_drawdown"] < -0.2:
        out.append(
            Observation("info", "回撤", f"快照区间最大回撤 {risk['max_drawdown']:.1%}")
        )

    if not out:
        out.append(Observation("info", "总体", "未触发任何风险规则"))
    return out
