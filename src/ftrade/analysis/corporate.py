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

from dataclasses import dataclass

import pandas as pd

from .instruments import parse_option

RENAME = "rename"
SPLIT = "split"


@dataclass(frozen=True)
class Action:
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


def apply_actions(deals: pd.DataFrame, actions: list[Action]) -> pd.DataFrame:
    """Restate deals that predate each action in post-action terms."""
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

    return df


def from_config(cfg) -> list[Action]:
    raw = getattr(getattr(cfg, "analysis", None), "corporate_actions", None) or []
    return [Action.from_config(item) for item in raw if isinstance(item, dict)]
