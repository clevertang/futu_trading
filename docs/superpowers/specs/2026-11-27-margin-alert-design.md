# Margin alert — design

## Intent

The account was force-liquidated twice in November 2026, both times with about
6% of index room left and nobody watching: the only sync ran at 08:30 JST,
after the US close. The owner wants to be told, during the US session, when
the index fall that would trigger a margin call shrinks to **3%**, with how
much to sell to get back to **5%** — a single-day Nasdaq move of 5% is rare,
so 5% leaves time to react.

## Goals

- Check the margin distance every ~10 minutes during US regular hours.
- At or below `trigger` (3%), email the owner (send to self) with the distance
  and two sell-down options that restore `target` (5%): TQQQ only, QQQ only.
- Show the QQQ price at which the distance reaches `trigger`, in the report and
  web panel, so the owner can mirror it as a Futu app price alert.

## Non-goals / anti-goals

- Never place, modify or cancel an order. The read-only invariant and
  `FORBIDDEN_CALLS` are untouched; the watch uses only account-info and
  position queries.
- No credentials in config, files, CLI arguments or logs. No recipient from
  anywhere but config.
- Not advice: the mail states numbers, not instructions.

## Approaches considered

1. **Email from the owner's Gmail to itself via SMTP, app password in the
   Keychain** — chosen. Phone push through the Gmail app, one send-only secret.
2. Extend mail-assistant with `gmail.send` — rejected: breaks its guarantee
   that its tokens cannot send.
3. Mac notification only — kept as a fallback, insufficient alone because the
   US session is the owner's night.
4. Futu app price alert only — kept as the complement (trigger price shown),
   insufficient alone because the trigger moves with every trade.

## Design

- `analysis/margin.py`:
  - `sell_to_target(held, ewl, requirement, target)` → per marginable symbol,
    the base-currency value to sell so `index_fall_to_call` returns to
    `target`. Selling at market leaves equity with loan unchanged and lowers
    the requirement by `rate * value` and the slope by `value * beta * (1 - rate)`:
    `value = (target * slope - cushion) / (rate + target * beta * (1 - rate))`.
  - `index_level_at(held, ewl, requirement, distance)` → the index fall from
    today at which the remaining distance equals `distance` (used for the
    trigger price).
  - `margin_model()` gains `alert: {trigger, target, triggered, sell[], trigger_fall}`
    when `analysis.margin_alert` is configured.
- Config: `analysis.margin_alert: {trigger: 0.03, target: 0.05, email_to, smtp_user, keychain_service}`
  (`keychain_service` default `tangxin-client`). The example file carries
  placeholders only.
- `ftrade margin-watch` (CLI): exits at once outside 09:30–16:00 America/New_York
  on weekdays (`--force` overrides); syncs account info and positions for the
  configured-env accounts; builds the margin model; appends one line to
  `reports/margin_watch.log`; if triggered and not already alerted today at a
  distance within 0.5 points, sends the mail (and a Mac notification) and
  records the alert in `sync_state`. `--test-email` sends a test message and exits.
- `notify.py`: SMTP over SSL to smtp.gmail.com:465; password read with
  `security find-generic-password -s <service> -a <smtp_user> -w` at send time,
  never logged.
- `scripts/install_margin_watch.sh`: a second launchd agent, `StartInterval` 600.
- Report / web: one line "QQQ ≤ X → 离追保只剩 3%".

## Left to the builder

Mail wording (Chinese, matching runtime strings), log format, how the web
panel places the trigger line.
