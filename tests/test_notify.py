"""Alert mail: wording, SMTP over SSL, and the password staying in the Keychain."""

import subprocess
from datetime import datetime

import pytest

from ftrade import notify

ALERT = {
    "trigger": 0.03,
    "target": 0.05,
    "triggered": True,
    "trigger_fall": 0.0,
    "sell": [
        {"code": "US.TQQQ", "value": 2900.0, "reaches_target": True, "shares": 61, "price": 47.5},
        {"code": "US.QQQ", "value": 9000.0, "reaches_target": False},
    ],
}
EQUITY = {"total_assets": 24763.0, "maintenance_margin": 14960.0, "equity_with_loan": 24000.0}
NOW = datetime(2026, 12, 3, 10, 40)


def test_subject_names_the_distance_target_and_cheapest_sell():
    subject, _ = notify.alert_message(ALERT, EQUITY, 0.028, "USD", NOW)
    assert "2.8%" in subject and "5%" in subject and "TQQQ" in subject and "61" in subject


def test_body_lists_each_option_and_net_assets():
    _, body = notify.alert_message(ALERT, EQUITY, 0.028, "USD", NOW)
    assert "TQQQ" in body and "61 股" in body
    assert "QQQ" in body and "全部卖掉也不够" in body
    assert "24,763" in body


class FakeSMTP:
    sent = []

    def __init__(self, host, port, context=None, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, pw):
        self.login_args = (user, pw)
        FakeSMTP.sent.append(("login", user, pw))

    def send_message(self, msg):
        FakeSMTP.sent.append(("send", msg["To"], msg["Subject"], msg.get_content()))


def test_send_mail_uses_keychain_and_ssl():
    FakeSMTP.sent = []
    seen = {}

    def pw(service, account):
        seen["args"] = (service, account)
        return "secret-app-pw"

    notify.send_mail("me@example.com", "me@example.com", "subj", "body",
                     password_fn=pw, smtp_factory=FakeSMTP)
    assert seen["args"] == ("tangxin-client", "me@example.com")
    assert FakeSMTP.sent[0] == ("login", "me@example.com", "secret-app-pw")
    assert FakeSMTP.sent[1][:3] == ("send", "me@example.com", "subj")


def test_password_never_in_message():
    FakeSMTP.sent = []
    notify.send_mail("me@example.com", "me@example.com", "subj", "body",
                     password_fn=lambda s, a: "secret-app-pw", smtp_factory=FakeSMTP)
    send = [e for e in FakeSMTP.sent if e[0] == "send"][0]
    assert all("secret-app-pw" not in str(part) for part in send)


def test_missing_keychain_item_raises(monkeypatch):
    def fake_run(*a, **k):
        return subprocess.CompletedProcess(a, 44, stdout="", stderr="not found")

    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="钥匙串"):
        notify.keychain_password("tangxin-client", "me@example.com")
