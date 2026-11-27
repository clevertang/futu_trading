# Margin Alert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Email the owner during US hours when the index fall to a margin call is at or below 3%, with the sell-down that restores 5%.

**Architecture:** Pure math in `analysis/margin.py`; a `watch` module that syncs account info and positions into a throwaway SQLite file (so intraday numbers never overwrite the daily snapshots), runs `build_report` on it, and hands a triggered alert to `notify.py` (SMTP + Keychain). A second launchd agent runs `ftrade margin-watch` every 10 minutes.

**Tech Stack:** Python 3.10+, stdlib `smtplib`/`ssl`/`zoneinfo`/`subprocess`, pytest, launchd.

**Spec:** `docs/superpowers/specs/2026-11-27-margin-alert-design.md`

## Global Constraints

- `trigger: 0.03`, `target: 0.05`, re-alert step `0.005`, `keychain_service` default `tangxin-client`.
- Market hours: weekdays 09:30–16:00 `America/New_York`.
- No name from `FORBIDDEN_CALLS` anywhere; `tests/test_readonly.py` must stay green.
- The SMTP password is read only inside `notify.keychain_password()` and never logged, printed or stored.
- `config.example.yaml` gets placeholders only, never the owner's address.
- Runtime log and mail text in Chinese; code, comments, commits in English.

## Review Focus

- No margin rates configured → `alert` absent, watch exits 0 without mailing.
- Account already below maintenance (distance 0) → still alerts; sell sizes are finite and positive.
- Holding only TQQQ (QQQ not held, no live QQQ price) → QQQ option shows value only, no share count; no crash.
- Keychain item missing or SMTP failure → logged as a failure, exit code 1, no alert recorded (so the next run retries).
- Two runs within the same distance band on the same NY date → one mail only.

---

### Task 1: Sell-to-target math and the `alert` block

**Files:**
- Modify: `src/ftrade/analysis/margin.py`, `src/ftrade/config.py` (`AnalysisConfig.margin_alert: dict = field(default_factory=dict)`), `src/ftrade/analysis/report.py` (pass `getattr(a, "margin_alert", None)`)
- Test: `tests/test_margin.py`

**Interfaces:**
- Produces: `sell_to_target(held: list[dict], equity_with_loan: float, requirement: float, target: float) -> list[dict]` — one row per held symbol `{"code", "value"}` (base currency, ≥ 0, 0 when already at/above target).
- Produces: `fall_at_distance(held, equity_with_loan, requirement, distance: float) -> float | None` — index fall from today at which the remaining fall-to-call equals `distance`.
- Produces: `margin_model(..., alert: dict | None = None)` adds `out["alert"] = {"trigger", "target", "triggered": bool, "sell": [{"code", "value", "shares"?, "price"?}], "trigger_fall": float | None}` when `alert` has `trigger` and `target`.

- [ ] **Step 1: failing tests** — `test_sell_to_target_restores_target_distance` (sell the returned value of TQQQ, rebuild `held`/requirement, assert `index_fall_to_call(...) == pytest.approx(0.05, abs=1e-4)`); `test_sell_to_target_is_zero_when_already_safe`; `test_alert_triggered_below_trigger` (distance 0.028 → `triggered` True, both rows present); `test_alert_absent_without_config`; `test_fall_at_distance_leaves_requested_room` (after applying the returned fall to values, distance == 0.03 ± 1e-4); `test_sell_when_already_under_the_line_is_finite`.
- [ ] **Step 2:** `pytest tests/test_margin.py -q` → new tests FAIL.
- [ ] **Step 3: implement.** Selling value v of a symbol (rate r, beta b) keeps equity with loan and lowers requirement by r·v and slope by v·b·(1−r): `v = (target*slope - cushion) / (r + target*b*(1-r))`, clamped to `[0, symbol value]`. `fall_at_distance`: bisection on f ∈ [0, cap] of `index_fall_to_call(repriced(f), ewl - loss(f), requirement(f)) - distance`. Shares via the existing `prices`/`currency_of` path, as `headroom` does.
- [ ] **Step 4:** `pytest tests -q` twice → all pass; `ruff check src tests` clean.
- [ ] **Step 5:** commit `feat(analysis): size the sell-down that restores a target margin distance`.

### Task 2: Mail and notification

**Files:**
- Create: `src/ftrade/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Produces: `alert_message(alert: dict, equity: dict, base: str, now: datetime) -> tuple[str, str]` (subject, body).
- Produces: `keychain_password(service: str, account: str) -> str` (raises `RuntimeError("钥匙串里没有 …")` when missing).
- Produces: `send_mail(to: str, user: str, subject: str, body: str, password_fn=keychain_password, smtp_factory=smtplib.SMTP_SSL, service="tangxin-client") -> None` (smtp.gmail.com:465).
- Produces: `mac_notify(title: str, text: str) -> None` (osascript; never raises).

- [ ] **Step 1: failing tests** — subject contains the distance (`2.8%`), `5%`, and `TQQQ`; body lists each sell row and net assets; `test_send_mail_uses_keychain_and_ssl` with a fake SMTP recording `login(user, pw)` and `send_message`; `test_password_never_in_message`; `test_missing_keychain_item_raises`.
- [ ] **Step 2:** run → FAIL.  **Step 3:** implement.  **Step 4:** full suite twice + ruff.
- [ ] **Step 5:** commit `feat(notify): email and desktop alerts read the SMTP secret from the Keychain`.

### Task 3: `ftrade margin-watch`

**Files:**
- Create: `src/ftrade/watch.py`
- Modify: `src/ftrade/cli.py` (subcommand `margin-watch` with `--force`, `--test-email`)
- Test: `tests/test_watch.py`

**Interfaces:**
- Consumes: Task 1 `report["margin_model"]["alert"]`; Task 2 `alert_message`, `send_mail`, `mac_notify`.
- Produces: `market_open(now: datetime) -> bool`; `run_watch(cfg, db, gateway, now, sender=send_mail, notifier=mac_notify, force=False) -> int` (exit code). Dedupe key in `sync_state`: `margin_alert_last:<NY date>` holding the last alerted distance.

- [ ] **Step 1: failing tests** — `market_open` for Sat, Mon 09:29/09:30/15:59/16:00 NY; `test_watch_writes_no_snapshots_to_main_db` (MockGateway; `position_snapshots`/`account_snapshots` row counts unchanged); `test_watch_mails_once_per_band` (rates that put the mock account under trigger; two runs → one send; a third run 0.6 pt worse → second send); `test_watch_without_rates_sends_nothing`; `test_send_failure_returns_1_and_records_nothing`.
- [ ] **Step 2:** run → FAIL.  **Step 3:** implement (scratch DB in `tempfile.TemporaryDirectory`; append one line per run to `reports/margin_watch.log`).  **Step 4:** full suite twice + ruff.
- [ ] **Step 5:** commit `feat(cli): margin-watch checks the call distance during US hours`.

### Task 4: Schedule, config, report line

**Files:**
- Create: `scripts/install_margin_watch.sh` (label `com.ftrade.marginwatch`, `StartInterval` 600, same `install|run|status|uninstall` verbs as `install_launchd.sh`)
- Modify: `config.example.yaml` (commented `margin_alert` block with placeholders), `src/ftrade/web/static/index.html` (one row: trigger QQQ price), README (one paragraph)
- Test: `tests/test_pipeline.py` (`report["margin_model"]["alert"]["trigger_fall"]` present when configured)

- [ ] **Step 1:** failing pipeline test.  **Step 2:** implement.  **Step 3:** suite twice + ruff.
- [ ] **Step 4:** live check: `ftrade margin-watch --test-email` run through the launchd agent (`install_margin_watch.sh run`), owner confirms receipt.
- [ ] **Step 5:** commit `chore(scripts): schedule margin-watch every 10 minutes`; open PR.
