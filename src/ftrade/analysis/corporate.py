"""Corporate actions: the trades that never happened.

A ticker change, a SPAC merger or a share split moves a position without
producing a deal. FIFO sees the consequences and not the cause: shares sold
under a symbol that was never bought, a holding that outlives every sale, an
option closed on a contract that was never opened.

Rewriting the affected deals into post-action terms puts them back in the same
book, so the matcher can pair them normally. Nothing is invented -- each event
has to be declared in config, because the broker's feed gives no way to detect
one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from .instruments import parse_option

log = logging.getLogger(__name__)

RENAME = "rename"
SPLIT = "split"
WRITEOFF = "writeoff"

# How far past an action to look for fills still quoted in pre-action terms,
# and how loosely their scale has to match the ratio before it is called out.
BOUNDARY_WINDOW_DAYS = 3
BOUNDARY_TOLERANCE = 0.2


@dataclass(frozen=True)
class Action:
    """One declared corporate action.

    ``date`` is the **ex-date**: the first session already quoted in
    post-action terms. Fills on that date are left alone; only what precedes
    it is restated. Getting this off by a day is easy and, for a split, silent
    -- the last pre-split fill keeps a strike on the old scale, which then
    reads as a wildly out-of-the-money contract. ``_warn_on_boundary`` exists
    to make that visible instead.
    """

    type: str
    date: str
    code: str
    to: str | None = None
    ratio: float = 1.0

    @classmethod
    def from_config(cls, raw: dict) -> Action:
        kind = str(raw.get("type") or "").lower()
        code = str(raw.get("code") or raw.get("from") or "")
        return cls(
            type=kind,
            date=str(raw.get("date") or ""),
            code=code,
            to=str(raw["to"]) if raw.get("to") else None,
            ratio=float(raw.get("ratio") or 1.0),
        )


def _split_option_code(code: str, ratio: float) -> str | None:
    """Re-strike an option symbol for a split of its underlying.

    A 2:1 split halves the strike and doubles the contract count, which is why
    a pre-split ``US.TQQQ251121P105000`` and a post-split
    ``US.TQQQ251121P52500`` are the same position under two names.
    """
    parsed = parse_option(code)
    if not parsed or ratio <= 0:
        return None
    new_strike = parsed["strike"] / ratio
    thousandths = round(new_strike * 1000)
    if abs(thousandths - new_strike * 1000) > 1e-6:
        return None  # not representable; leave it alone rather than distort it
    body = str(code).rsplit(parsed["kind"][0], 1)[0]
    return f"{body}{parsed['kind'][0]}{thousandths}"


def _writeoff_row(template: pd.Series, code: str, qty: float, when: str, long: bool) -> dict:
    row = {c: template.get(c) for c in template.index}
    row.update(
        code=code,
        qty=abs(qty),
        price=0.0,
        trd_side="SELL" if long else "BUY_BACK",
        create_time=f"{when} 00:00:00",
        deal_id=f"writeoff:{code}:{when}",
    )
    return row


def _strikes(df: pd.DataFrame, code: str, lo: str, hi: str) -> list[float]:
    """Strikes of `code`'s option fills dated in [lo, hi)."""
    when = df["create_time"].astype(str).str[:10]
    rows = df[(when >= lo) & (when < hi) & df["code"].str.startswith(code) & (df["code"] != code)]
    out = []
    for c in rows["code"]:
        parsed = parse_option(c)
        if parsed:
            out.append(float(parsed["strike"]))
    return out


def _warn_on_boundary(df: pd.DataFrame, act: Action) -> None:
    """Flag a split date that looks a day or two early.

    The failure this catches is silent and was live in this repo: a split
    dated on the last *pre*-split session instead of the ex-date leaves that
    session's fills unrestated, so a 110 strike sits next to a 55 spot and
    reads as 125% out of the money -- a contract nobody would ever write.
    Nothing downstream can tell that apart from a real far-OTM position.

    So compare the strikes just after the declared date against the restated
    ones just before it. If the later ones are still roughly `ratio` times
    larger, they never got converted and the date wants moving forward.
    """
    if act.type != SPLIT or not act.ratio or act.ratio == 1:
        return
    start = pd.Timestamp(act.date)
    before = _strikes(df, act.code, (start - pd.Timedelta(days=30)).strftime("%Y-%m-%d"), act.date)
    after = _strikes(
        df,
        act.code,
        act.date,
        (start + pd.Timedelta(days=BOUNDARY_WINDOW_DAYS)).strftime("%Y-%m-%d"),
    )
    if not before or not after:
        return
    ref = pd.Series(before).median()
    seen = pd.Series(after).median()
    if not ref:
        return
    if abs(seen / ref / act.ratio - 1) <= BOUNDARY_TOLERANCE:
        log.warning(
            "%s split dated %s: option strikes on/after that date (median %.4g) are still "
            "about %.4gx the restated ones before it (median %.4g). The ex-date is probably "
            "a session or two later -- fills on %s are keeping pre-split strikes.",
            act.code,
            act.date,
            seen,
            act.ratio,
            ref,
            act.date,
        )


def apply_actions(deals: pd.DataFrame, actions: list[Action]) -> pd.DataFrame:
    """Restate deals that precede each action in post-action terms.

    ``Action.date`` is the ex-date, so a fill dated on it is already quoted
    post-action and is deliberately left alone.
    """
    if deals is None or deals.empty or not actions:
        return deals
    df = deals.copy()
    df["code"] = df["code"].astype(str)
    date = df["create_time"].astype(str).str[:10]

    for act in actions:
        if not act.date or not act.code:
            continue
        before = date < act.date

        if act.type == RENAME and act.to:
            df.loc[before & (df["code"] == act.code), "code"] = act.to

        elif act.type == SPLIT and act.ratio and act.ratio != 1:
            # The underlying itself: more shares, proportionally cheaper.
            hit = before & (df["code"] == act.code)
            df.loc[hit, "qty"] = pd.to_numeric(df.loc[hit, "qty"], errors="coerce") * act.ratio
            df.loc[hit, "price"] = pd.to_numeric(df.loc[hit, "price"], errors="coerce") / act.ratio

            # Its options are re-struck by the same ratio.
            opt = before & df["code"].str.startswith(act.code) & (df["code"] != act.code)
            for idx in df.index[opt]:
                new_code = _split_option_code(df.at[idx, "code"], act.ratio)
                if not new_code:
                    continue
                df.at[idx, "code"] = new_code
                df.at[idx, "qty"] = float(df.at[idx, "qty"] or 0) * act.ratio
                df.at[idx, "price"] = float(df.at[idx, "price"] or 0) / act.ratio

            _warn_on_boundary(df, act)

    # Write-offs are appended rather than rewritten: a delisting closes the
    # position at zero, and there is no earlier deal to restate.
    for act in actions:
        if act.type != WRITEOFF or not act.code or not act.date:
            continue
        prior = df[(df["code"] == act.code) & (df["create_time"].astype(str).str[:10] <= act.date)]
        if prior.empty:
            continue
        signed = sum(
            float(r["qty"] or 0) * (1 if str(r["trd_side"]).upper().startswith("BUY") else -1)
            for _, r in prior.iterrows()
        )
        if abs(signed) <= 1e-9:
            continue
        row = _writeoff_row(prior.iloc[-1], act.code, signed, act.date, signed > 0)
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    return df


def from_config(cfg) -> list[Action]:
    raw = getattr(getattr(cfg, "analysis", None), "corporate_actions", None) or []
    return [Action.from_config(item) for item in raw if isinstance(item, dict)]
