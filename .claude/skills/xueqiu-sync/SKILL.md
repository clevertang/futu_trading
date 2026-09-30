---
name: xueqiu-sync
description: Draft posts for the owner's Xueqiu (雪球) account from this repo's trading data, and save them as Xueqiu drafts. Use when asked to sync operations to Xueqiu, write a 雪球 post or summary, catch up on posts, or after a sync shows new trades the owner wants to share. Covers per-operation posts and multi-month summaries, with a share-mode dashboard image.
---

# Xueqiu sync

Turn what happened in the account into a post in the owner's own voice, backed
by numbers that match an attached image, and leave it as a **draft** on
Xueqiu for the owner to finish and publish.

Account: https://xueqiu.com/u/7394617568 (`sz_tangxin`, real-name verified).
The owner is logged in to Xueqiu in their own Chrome; work through Claude in
Chrome (load the `chrome-browser` skill first).

## Hard rules

These exist because the post is public, under a real name, and permanent.

1. **Never publish.** Save as a Xueqiu draft only. Publish solely when the
   owner says so in chat for that specific post. If no draft option can be
   found, stop and ask — do not click 发布 as a fallback.
2. **Never invent the owner's reasoning.** Numbers and events come from the
   data; *why* something was done is theirs. Leave `【……】` placeholders
   (e.g. `【为什么减仓 PDD】`) and point them out when showing the draft.
3. **No recommendation language.** Posts say what was done and what resulted.
   No "建议", "应该", no forecasts presented as the account's view unless the
   owner writes them.
4. **Period figures describe the period only.** Everything under a period
   title comes from `scripts/xueqiu_facts.py` / share mode for exactly those
   dates. Today's holdings, net assets or leverage may appear only in a
   section explicitly headed 当前持仓 / 当前状态 with its date.
5. **Call broker events what they are.** A lapsed option is 到期作废, an
   exercise is 被行权 (提前被行权 if early) and the matching share fill is
   its delivery, not a sale the owner placed. Use the fact sheet's labels.
6. **Nothing that identifies the brokerage account.** No account numbers. No
   screenshots of the normal dashboard, and never of the 账户与数据 tab —
   images come only from share mode.
7. **Text and image must agree.** Same dates, same currency, same totals.
   If you round in the text, round from the fact-sheet figure.

## Workflow

### 1. Make the data current

```bash
source .venv/bin/activate
python -c "import sqlite3;from ftrade.config import load_config as l;c=l();print(sqlite3.connect(c.db_path).execute(\"select value from sync_state where key='last_sync_at'\").fetchone())"
```

If it is older than the last trading day and OpenD is up
(`nc -z 127.0.0.1 11111`), run `scripts/daily_report.sh` (it holds the sync
lock; do not run a second sync alongside it). The dashboard must be serving
for images: `ftrade serve --port 8791`.

### 2. Find where the last post left off

Open the profile in Chrome and read the date of the newest post. The new
period starts there. Trades dated on that same day may already be in the
last post — compare section 2 of the fact sheet for that date against the
post's text and say which way you resolved it.

### 3. Choose the shape

- **Per-operation post** (the normal case going forward): one post per
  batch of the owner's own orders — typically a day. Expiries alone are not
  worth a post unless the owner asks.
- **Summary post** (catching up a gap of months): one post per natural
  period — a half-year, a quarter, or a change of strategy. Two to three
  posts for a year is about right; do not produce a post per month.

### 4. Gather facts and the image

```bash
python scripts/xueqiu_facts.py --start START --end END
scripts/share_snapshot.sh START END reports/xueqiu/NAME.png
```

`share_snapshot.sh` renders the dashboard in share mode (period figures only,
exact dates in the banner, today's state hidden) at 2× through headless
Chrome with a throwaway profile. It takes **two to three minutes per image
and is not hung** — run it in the background. `reports/` is gitignored.

Look at the image before using it: confirm the banner dates and that the
headline figure equals the fact sheet's.

### 5. Draft in the owner's style

From the owner's 24 posts (2024-07 → 2025-05):

- **Title**: date first, then a short plain claim.
  `2024-11-26本周亏损20%` · `2024-11-05计划两周内清仓中概` ·
  `美股2024-10-17操作：无操作` · `2025-05-12美股纪实`
- **Body**: short lines, first person, no headings, no marketing tone.
  Concrete contracts: strike, expiry, premium, what happens if exercised
  (e.g. `卖了本周五255的call，8块钱`).
- **Candid about losses and mistakes** (`中概确实决策失误`); do not soften.
- Often opens with 当前持仓如下 / 昨日操作 and ends with the plan
  (`今晚计划……`) — the plan is the owner's to write: placeholder.
- One image, the share-mode snapshot.

Per-operation skeleton:

```
YYYY-MM-DD 【一句话概括】

操作：
卖出开仓 TQQQ 10-09 81 call 1张，1.56
TQQQ 09-28 80 call 2张到期作废
【为什么这么做】

当前持仓（MM-DD）：
TQQQ 600股，上面卖了……
```

### 6. Review, then save as draft

Show the complete title, body, image path and the list of `【】`
placeholders in chat. Only after the owner approves:

1. In Chrome, open https://xueqiu.com and use 发帖 (long-form if the body
   needs it).
2. Fill title and body; attach the image with the file-upload tool.
3. Save as draft (草稿 / 存草稿). Confirm it appears in the drafts list.
4. Close the tabs you opened.

## Notes

- The dashboard's share mode is `/?share=1&start=YYYY-MM-DD&end=YYYY-MM-DD&lang=zh&base=USD`.
- Account snapshots begin 2026-09-10, so net-asset history for earlier
  periods does not exist; realized figures (from fills) go back to 2020.
- The tool is described publicly as: local, read-only (no order placement
  code, enforced by a test), archives Futu data, presents facts rather than
  advice. Code: https://github.com/clevertang/futu_trading
