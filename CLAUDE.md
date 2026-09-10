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
```

Tests and lint:

```bash
pytest tests -q
pytest tests/test_pnl.py::test_fifo_matching -q     # a single test
ruff check src tests
```

Run the suite **more than once** when touching the mock gateway or anything
weight-related — a single green run has hidden non-determinism here before.

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
  `(snap_date, acc_id, ...)`. Re-running the same day overwrites rather than
  duplicates, so syncing repeatedly is safe.
- `_syncable_accounts()` filters to accounts matching the configured `trd_env`.
  Futu returns paper-trading accounts in the same list and they always fail
  through a REAL context. Closed (`DISABLED`) real accounts are deliberately
  **kept** — their history is the whole reason this tool exists, since the
  broker's own UI window is limited.
- Each account is wrapped in its own `try`; one failure must not abort the
  sweep. Reasons land in `stats["failed"]`.

**Rate limits are real and unforgiving.** Futu allows 10 history deal/order
queries per 30s and 60 K-line queries per 30s. `futu_gw` enforces spacing via
`_Throttle` (`HISTORY_MIN_INTERVAL`, `KLINE_MIN_INTERVAL`) and retries with
exponential backoff through `_request()`. Every query path must go through
`_request()` — a direct SDK call bypasses the throttle and will silently return
zero rows across a multi-year pull.

**`analysis/`** — pure functions over DataFrames, no DB writes:

- `pnl.fifo_round_trips()` pairs buys and sells FIFO into closed round trips;
  `open_lots()` reconstructs what should still be held.
- `portfolio.summary()` computes weights, HHI, effective position count, and
  market/currency exposure.
- `metrics` builds the equity curve, drawdown, volatility, Sharpe/Sortino.
- `trades.behavior()` derives win rate, payoff ratio, holding periods, and the
  disposition-effect comparison.
- `fx.FX` converts to `analysis.base_currency` using **static** rates from config.

**`report.build_report()`** assembles all of the above into one dict — this is
what the CLI, the web panel, and (in Phase 2) the advice engine all consume.

Its `reconciliation` field is a deliberate honesty check: it diffs locally
reconstructed FIFO quantities against what the broker reports. A non-empty
`reconciliation` usually means history is incomplete, or that a corporate action
(split, bonus issue, dividend) moved shares without a corresponding deal record.

**`advice/`** — Phase 1 emits rule-based `Observation`s (concentration, currency
mismatch, disposition effect, data gaps) only. The `AdviceEngine` protocol is a
placeholder for the Phase 2 LLM path; keep factual observations and generated
advice separate.

## Known measurement caveats

Documented in the README and worth preserving in any analysis work:

- **Returns are not TWR.** `account_snapshots.total_assets` includes deposits and
  withdrawals, so returns distort when cash flows are frequent. Real TWR needs
  cash-flow records — Phase 2.
- **FX rates are static**, hand-maintained in `config.yaml`.
- **Corporate actions are absent** from the deal feed, which is what makes
  `reconciliation` drift.
- Expired option contracts return `Unknown stock` from the quote API; K-line
  fetches for them fail harmlessly and are logged as warnings.

## Conventions

- **Repo artifacts are English**: code comments, docstrings, commit messages, PR
  titles and bodies, branch names. Runtime log strings and user-facing README
  prose are currently Chinese — match the surrounding file rather than
  mass-translating as a side effect of another change.
- **Every fix goes through a pull request.** Do not commit to `main` directly.
- Ruff's rule set is pinned in `pyproject.toml` (`E4, E7, E9, F, I, UP`) because
  ruff widens its defaults between releases. The tree is **not** `ruff format`
  clean; reformatting is a separate change, not a drive-by.
- Config resolution order is `config.yaml` → `config.yml` → `config.example.yaml`,
  so the example file makes the tool runnable before `ftrade init`.
- `config.yaml`, `data/`, and `reports/` are gitignored — they hold account data.
