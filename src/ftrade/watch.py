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


def _intraday_report(cfg, gateway, session: str) -> dict:
    """Report on a throwaway DB whose snapshots carry the New York `session` date.

    Sync stamps snapshots with the local (Tokyo) date, which is already the
    next day from 10:00 EST to the close. The report drops options that
    expired before the snapshot date, so a call expiring that session looked
    gone and its value was counted as loan value.
    """
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
            with scratch.tx() as conn:
                for table in ("position_snapshots", "account_snapshots"):
                    conn.execute(f"UPDATE {table} SET snap_date = ?", (session,))
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

    session = now.astimezone(NY).date().isoformat()
    rep = _intraday_report(cfg, gateway, session)
    model = rep.get("margin_model") or {}
    alert = model.get("alert")
    distance = model.get("index_fall_to_call")
    if not alert:
        return report_blind(
            cfg,
            db,
            now,
            "无法计算追保距离（资金或持仓查询失败）",
            sender=sender,
            log_path=log_path,
        )

    day_key = STATE_PREFIX + session
    last = db.get_state(day_key)
    worse = (
        last is not None
        and distance is not None
        and (distance <= float(last) - REALERT_STEP or (distance == 0 < float(last)))
    )
    due = alert["triggered"] and (last is None or worse)
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


def report_blind(
    cfg,
    db: Database,
    now: datetime,
    reason: str,
    sender=notify.send_mail,
    log_path: Path = Path("reports/margin_watch.log"),
) -> int:
    """Tell the owner, once per New York day, that the session is not being watched.

    A watch that cannot see the account must not stay quiet: the November
    calls happened overnight, and a silent failure would only show at 08:30.
    """
    if not market_open(now):
        return 0
    a = cfg.analysis.margin_alert or {}
    _log_line(log_path, now, f"盯盘失效：{reason}")
    key = "margin_watch_blind:" + now.astimezone(NY).date().isoformat()
    if not a.get("email_to") or not a.get("smtp_user") or db.get_state(key):
        return 0
    try:
        sender(
            a["email_to"],
            a["smtp_user"],
            f"【ftrade】盯盘失效：{reason}",
            f"时间：{now.astimezone(NY):%Y-%m-%d %H:%M} 纽约\n"
            f"原因：{reason}\n\n"
            "今天剩下的交易时段可能没有追保提醒，请自己在富途 App 里留意保证金。"
            "恢复后会照常检查；今天不会再为这个问题发信。",
            service=a.get("keychain_service", notify.DEFAULT_SERVICE),
        )
    except Exception as exc:
        log.error("盯盘失效提醒发送失败：%s", exc)
        return 1
    db.set_state(key, reason)
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
