"""Tell the owner about a margin alert: an email to themselves, plus a desktop notice.

The SMTP password is a Gmail app password kept in the macOS login Keychain
(service ``tangxin-client`` by default). It is read here at send time and
nowhere else -- never from config, arguments or the environment, and never
logged. The only recipient is the one configured in ``analysis.margin_alert``.
"""

from __future__ import annotations

import contextlib
import smtplib
import ssl
import subprocess
from datetime import datetime
from email.message import EmailMessage

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
DEFAULT_SERVICE = "tangxin-client"


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _money(x: float | None, base: str) -> str:
    return "—" if x is None else f"{x:,.0f} {base}"


def _option(row: dict, base: str) -> str:
    code = row["code"].split(".", 1)[-1]
    if not row.get("reaches_target", True):
        return f"{code}：全部卖掉也不够（持有 {_money(row['value'], base)}）"
    if "shares" in row:
        return f"{code}：卖出约 {row['shares']} 股（约 {_money(row['value'], base)}，现价 {row['price']:.2f}）"
    return f"{code}：卖出约 {_money(row['value'], base)}"


def alert_message(
    alert: dict, equity: dict, distance: float | None, base: str, now: datetime
) -> tuple[str, str]:
    """Subject and body of the alert mail. States numbers, not instructions."""
    reachable = [r for r in alert["sell"] if r.get("reaches_target", True) and r["value"] > 0]
    best = min(reachable, key=lambda r: r["value"]) if reachable else None
    target = f"{alert['target'] * 100:.0f}%"
    headline = f"【ftrade】离追保 {_pct(distance)}"
    if best:
        unit = f"{best['shares']} 股" if "shares" in best else _money(best["value"], base)
        headline += f"，回到 {target} 需卖 {best['code'].split('.', 1)[-1]} 约 {unit}"
    lines = [
        f"时间：{now:%Y-%m-%d %H:%M}",
        f"纳指再跌 {_pct(distance)} 会触发追保（提醒线 {_pct(alert['trigger'])}）。",
        "",
        f"净资产：{_money(equity.get('total_assets'), base)}",
        f"含贷权益值：{_money(equity.get('equity_with_loan'), base)}",
        f"维持保证金：{_money(equity.get('maintenance_margin'), base)}",
        "",
        f"把距离拉回 {target}，单独卖出以下任一项即可：",
        *[f"- {_option(r, base)}" for r in alert["sell"]],
        "",
        "以上是按配置里的保证金率算的数字，不是交易建议；下单请在富途 App 里自己操作。",
    ]
    return headline, "\n".join(lines)


def keychain_password(service: str, account: str) -> str:
    """The app password from the login Keychain. Raises when it is missing."""
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            timeout=20,  # a Keychain prompt would otherwise block every later run
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"读取钥匙串 {service} 超时（可能在等授权弹窗）") from exc
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"钥匙串里没有 {service} / {account} 的发信密码")
    return r.stdout.strip()


def send_mail(
    to: str,
    user: str,
    subject: str,
    body: str,
    password_fn=keychain_password,
    smtp_factory=smtplib.SMTP_SSL,
    service: str = DEFAULT_SERVICE,
) -> None:
    """Send one plain-text mail from `user` to `to` over SMTPS."""
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, to, subject
    msg.set_content(body)
    password = password_fn(service, user)
    with smtp_factory(
        SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=30
    ) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)


def mac_notify(title: str, text: str) -> None:
    """Best-effort desktop notice; a failure here must never stop the mail."""
    script = f"display notification {_applescript(text)} with title {_applescript(title)}"
    with contextlib.suppress(Exception):
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)


def _applescript(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
