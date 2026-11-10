# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## The one invariant: this codebase is read-only

`ftrade` reads a Futu brokerage account and analyses it. It must never be able to
trade. This is enforced mechanically, not by convention:

- `gateway/base.py` defines `FORBIDDEN_CALLS` — `place_order`, `modify_order`,
  `cancel_order`, `unlock_trade`, `acctradinginfo_query` (that last one carries
  order-precheck semantics and is not needed).
- `tests/test_readonly.py` statically scans every `.py` under `src/ftrade/` for
  those names and asserts the `Gateway` protocol exposes none of them.

Adding any of those calls breaks the test suite by design. If a task seems to
require one, stop and raise it rather than working around the scan.

The config deliberately has **no trade-unlock password field**, for the same reason.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ftrade init                 # copy config.example.yaml -> config.yaml, create the DB
```

Work offline against the mock gateway — no OpenD, no account, deterministic data:

```bash
ftrade sync --source mock
ftrade positions
ftrade report
ftrade serve                # http://127.0.0.1:8765, binds loopback only
```

Against a real account (requires the Futu OpenD gateway running and logged in,
default `127.0.0.1:11111`):

```bash
ftrade sync --full          # first run: pulls from sync.deals_start (default 2020-01-01)
ftrade sync                 # afterwards: resumes from sync_state, re-reads 3 days for safety
ftrade sync --cash-flow     # what the daily job runs; first backfill takes hours, resumable
```

`ftrade sync` exits **3** when any account or step failed (the rest is stored,
`sync_state.last_sync_status = partial`), and refuses to start while another
sync holds the `flock` beside the DB. A mock sync refuses a DB that already
holds a REAL account — point `-c` at a separate config for offline work.

Tests and lint:

```bash
pytest tests -q
pytest tests/test_pnl.py::test_fifo_matches_oldest_lot_first -q     # a single test
ruff check src tests scripts && ruff format --check src tests scripts
```

Run the suite **more than once** when touching the mock gateway or anything
weight-related — a single green run has hidden non-determinism here before
(CI re-runs it under `PYTHONHASHSEED` 1–5). Anything that orders fills by time
must sort with `kind="stable"`: same-second fills are common.

`tests/conftest.py` pins `load_config()` to `config.example.yaml`, so tests never
see the owner's real `config.yaml`. `tests/test_futu_gw.py` drives `FutuGateway`
through a fake SDK, so gateway changes are testable without OpenD.

## Architecture

The pipeline is one direction, with no layer reaching backwards:

```
gateway/  ->  sync/  ->  storage/ (SQLite)  ->  analysis/  ->  advice/, web/, cli
```

**`gateway/`** — `base.Gateway` is a `Protocol`; every method returns a normalized
DataFrame. Two implementations: `futu_gw.FutuGateway` (talks to OpenD) and
`mock.MockGateway` (offline, seeded per-symbol from `crc32` so runs are
reproducible). Nothing above this layer knows which one it has.

**`sync/service.py`** — incremental, and the incrementality is the point:

- History queries are sliced into 90-day windows because that is Futu's hard
  ceiling for a single call.
- Progress is checkpointed per account in the `sync_state` table
  (`deals_last_end:<acc_id>`); a non-`--full` run resumes from there minus
  `OVERLAP_DAYS` for late-settling trades.
- Positions and account equity are stored as **daily snapshots**, keyed on
  `(snap_date, acc_id, ...)`. A re-run replaces that day's positions for the
  account (closed positions do not linger), and the date is also recorded in
  `sync_state` (`positions_date:<acc_id>`) so an empty book still counts as
  today's snapshot.
- Cash flows (`--cash-flow`) are walked one clearing date at a time; today is
  fetched but never checkpointed, and the last `OVERLAP_DAYS` are re-read.
- `_syncable_accounts()` filters to accounts matching the configured `trd_env`.
  Futu returns paper-trading accounts in the same list and they always fail
  through a REAL context. Closed (`DISABLED`) real accounts are deliberately
  **kept** — their history is the whole reason this tool exists, since the
  broker's own UI window is limited.
- Each account is wrapped in its own `try`; one failure must not abort the
  sweep. Reasons land in `stats["failed"]` — fee and cash-flow failures too,
  each independently.
- The owner's several `acc_id`s are **one account** Futu migrated
  automatically. FIFO deliberately pairs across them; do not "fix" that by
  grouping per account.

**Rate limits are real and unforgiving.** Futu allows 10 history deal/order
queries per 30s and 60 K-line queries per 30s. `futu_gw` enforces spacing via
`_Throttle` (`HISTORY_MIN_INTERVAL`, `KLINE_MIN_INTERVAL`) and retries with
exponential backoff through `_request()`. Every query path must go through
`_request()` — a direct SDK call bypasses the throttle and will silently return
zero rows across a multi-year pull.

**`analysis/`** — pure functions over DataFrames, no DB writes:

- `pnl.fifo_round_trips()` pairs buys and sells FIFO into closed round trips;
  `open_lots()` reconstructs what should still be held.
- `corporate.apply_actions()` rewrites deals for renames, splits and write-offs
  **declared** in `analysis.corporate_actions` (the feed gives no way to detect
  them), in date order, before anything else sees the deals.
- `instruments` parses option codes (`US.TQQQ260911C75000`) for underlying,
  expiry, strike and contract multiplier; `options_monitor` lays out facts
  about open options with no close/roll recommendation.
- `cash` (dividends, withholding tax, interest from `cash_flows`; only
  `Cash Dividend` counts — bare `Dividend` rows are fund distributions),
  `fees` (per-order fees; roughly half the gross realised result), `equity`
  (own capital vs. margin debt) and `todate` (this year / this month, rebuilt
  from Jan-1 holdings because snapshots start 2026-09-10).
- `portfolio.summary()` computes weights (against gross exposure, so short
  options cannot push them past 100%), HHI, effective position count, and
  market/currency exposure.
- `metrics` builds the equity curve (an account missing a day is gap-filled),
  drawdown, volatility, Sharpe/Sortino — annualised by elapsed calendar time,
  since snapshots are taken whenever a sync runs.
- `trades.behavior()` derives win rate, payoff ratio, holding periods, and the
  disposition-effect comparison.
- `fx.FX` converts to `analysis.base_currency` using **static** rates from config.
- `margin.margin_model()` applies per-symbol margin rates declared in
  `analysis.margin_rates` (copied from the app; the API only gives account
  totals). The broker's own requirement stays the baseline; the rates only
  shape how it moves: the index fall to a call (a `beta: 3` ETF counts three
  times), per-symbol buying headroom, and the same after selling long options,
  which carry no loan value. `maintenance_gap` flags rates gone stale.

**`report.build_report()`** assembles all of the above into one dict — this is
what the CLI, the web panel, and (in Phase 2) the advice engine all consume.

Its `reconciliation` field is a deliberate honesty check: it diffs locally
reconstructed FIFO quantities against what the broker reports, in both
directions (a broker-only holding is flagged too), summed per code. A non-empty
`reconciliation` usually means history is incomplete, or that a corporate action
(split, bonus issue, dividend) moved shares without a corresponding deal record.

**`web/`** — the panel has no login, so: `TrustedHostMiddleware` accepts only
loopback Host names (DNS rebinding), `serve` refuses a non-loopback `--host`
without `--allow-remote`, and requests open SQLite **read-only** (the schema is
migrated once at startup). Report values pass through `_clean()` — NaN must
become null, or the whole endpoint 500s.

**`scripts/`** — `daily_report.sh` (sync + report, run by launchd via
`install_launchd.sh`; exit 3 = partial), `share_snapshot.sh` (share-mode PNG
through headless Chrome), and the Xueqiu helpers `xueqiu_facts.py` /
`xueqiu_stats.py` used by `.claude/skills/xueqiu-sync`. They are linted and
covered by the read-only scan like `src/`.

**`advice/`** — Phase 1 emits rule-based `Observation`s (concentration, currency
mismatch, disposition effect, data gaps) only. The `AdviceEngine` protocol is a
placeholder for the Phase 2 LLM path; keep factual observations and generated
advice separate.

## Known measurement caveats

Documented in the README and worth preserving in any analysis work:

- **Returns are not TWR.** `metrics`' equity curve uses
  `account_snapshots.total_assets`, which includes deposits and withdrawals.
  `todate` does net out external flows from `cash_flows`; a real TWR series is
  still Phase 2.
- **FX rates are static**, hand-maintained in `config.yaml`.
- **Corporate actions are absent** from the deal feed; they must be declared in
  config, and an undeclared one is what makes `reconciliation` drift.
- Expired option contracts return `Unknown stock` from the quote API. Failures
  are counted in `quote_status`, and a symbol is dropped from K-line sync after
  `DEAD_CODE_FAILURES` in a row. Futu also keeps an expired contract in the
  position list for about a day after settlement; the report drops it.

## Conventions

- **Repo artifacts are English**: code comments, docstrings, commit messages, PR
  titles and bodies, branch names. Runtime log strings and user-facing README
  prose are currently Chinese — match the surrounding file rather than
  mass-translating as a side effect of another change.
- **Every fix goes through a pull request.** Do not commit to `main` directly.
- Ruff's rule set is pinned in `pyproject.toml` (`E4, E7, E9, F, I, UP, B, SIM`)
  because ruff widens its defaults between releases. The tree is `ruff format`
  clean and CI enforces `ruff format --check` on `src tests scripts`.
- Never put backticks inside a double-quoted `git commit -m "..."`: the shell
  runs them as commands. Use a quoted heredoc (`-F - <<'EOF'`).
- Config resolution order is `config.yaml` → `config.yml` → `config.example.yaml`,
  so the example file makes the tool runnable before `ftrade init`.
- `config.yaml`, `data/`, and `reports/` are gitignored — they hold account data.
  Unknown config keys are warned about, not silently dropped; check the log
  after editing config.
