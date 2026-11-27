"""Intraday margin watch: mail the owner when the index fall to a call gets small.

The daily sync runs after the US close, so it never sees the account during
the session -- which is when both November forced liquidations happened.
`run_watch` is meant to run every few minutes during US hours. It pulls
account info and positions into a throwaway database, so intraday numbers
never overwrite the daily snapshots (they are keyed on the local date and an
evening run would replace that morning's close), builds the same report the
CLI and web panel use, and sends one mail per distance band per New York day.

Only account-info and position queries are made; nothing here can trade.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from . import notify
from .analysis import build_report
from .storage.db import Database
from .sync import SyncService

log = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
OPEN, CLOSE = time(9, 30), time(16, 0)
REALERT_STEP = 0.005  # mail again the same day only when 0.5 points worse
STATE_PREFIX = "margin_alert_last:"


def market_open(now: datetime) -> bool:
    """US regular session, weekdays 09:30-16:00 New York time (holidays not modelled)."""
    ny = now.astimezone(NY)
    return ny.weekday() < 5 and OPEN <= ny.time() < CLOSE


def _report_base(cfg) -> str | None:
    rates = cfg.analysis.fx_rates or {}
    return "USD" if "USD" in rates else None


def _intraday_report(cfg, gateway) -> dict:
    with tempfile.TemporaryDirectory(prefix="ftrade-watch-") as tmp:
        scratch = Database(Path(tmp) / "watch.db")
        try:
            svc = SyncService(scratch, gateway, cfg)
            all_ids = svc.sync_accounts()
            for acc_id in svc._syncable_accounts() or all_ids:
                try:
                    svc.sync_account_info(acc_id)
                    svc.sync_positions(acc_id)
                except Exception as exc:  # a closed account must not stop the check
                    log.warning("账户 %s 盯盘查询失败，已跳过：%s", acc_id, exc)
            return build_report(scratch, cfg, base=_report_base(cfg))
        finally:
            scratch.close()


def _log_line(path: Path, now: datetime, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{now.astimezone(NY):%Y-%m-%d %H:%M} NY | {text}\n")


def run_watch(
    cfg,
    db: Database,
    gateway,
    now: datetime,
    sender=notify.send_mail,
    notifier=notify.mac_notify,
    force: bool = False,
    log_path: Path = Path("reports/margin_watch.log"),
) -> int:
    """One check. Returns a process exit code: 1 only when an alert failed to send."""
    alert_cfg = cfg.analysis.margin_alert or {}
    if not force and not market_open(now):
        return 0
    if not alert_cfg or not cfg.analysis.margin_rates:
        log.info("未配置 margin_alert 或 margin_rates，跳过盯盘")
        return 0

    rep = _intraday_report(cfg, gateway)
    model = rep.get("margin_model") or {}
    alert = model.get("alert")
    distance = model.get("index_fall_to_call")
    if not alert:
        _log_line(log_path, now, "无法计算追保距离（缺少资金或持仓数据）")
        return 0

    day_key = STATE_PREFIX + now.astimezone(NY).date().isoformat()
    last = db.get_state(day_key)
    due = alert["triggered"] and (
        last is None or (distance is not None and distance <= float(last) - REALERT_STEP)
    )
    status = "触发" if alert["triggered"] else "正常"
    _log_line(log_path, now, f"离追保 {notify._pct(distance)} | {status}{' | 发信' if due else ''}")
    if not due:
        return 0

    subject, body = notify.alert_message(
        alert, rep.get("equity") or {}, distance, rep.get("base_currency") or "", now
    )
    notifier(subject, body.splitlines()[1])
    try:
        sender(
            alert_cfg["email_to"],
            alert_cfg["smtp_user"],
            subject,
            body,
            service=alert_cfg.get("keychain_service", notify.DEFAULT_SERVICE),
        )
    except Exception as exc:
        log.error("追保提醒邮件发送失败：%s", exc)
        _log_line(log_path, now, f"发信失败：{type(exc).__name__}")
        return 1
    db.set_state(day_key, f"{distance if distance is not None else 0.0:.4f}")
    log.info("已发送追保提醒：%s", subject)
    return 0


def send_test_mail(cfg, sender=notify.send_mail) -> None:
    """Prove the Keychain -> SMTP -> inbox path without waiting for a real alert."""
    a = cfg.analysis.margin_alert or {}
    if not a.get("email_to") or not a.get("smtp_user"):
        raise RuntimeError("config 里的 analysis.margin_alert 缺少 email_to / smtp_user")
    sender(
        a["email_to"],
        a["smtp_user"],
        "【ftrade】测试邮件：追保提醒通道正常",
        "这是 ftrade margin-watch 的测试邮件。收到说明钥匙串和发信设置都正常。",
        service=a.get("keychain_service", notify.DEFAULT_SERVICE),
    )
