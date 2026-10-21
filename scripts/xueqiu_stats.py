"""Reads and reactions per Xueqiu post, recorded over time, and a backtest of titles.

    python scripts/xueqiu_stats.py record CAPTURE.json   # append today's capture
    python scripts/xueqiu_stats.py note "TITLE" "what was tried"
    python scripts/xueqiu_stats.py report [--min-age-days 3]

The numbers come from the creator centre's 长文管理 list, scraped in the
browser (the skill has the snippet) and saved as JSON: a list of
{title, published, reads, likes, comments, reposts, favorites}. Xueqiu keeps
only the running total, so a post's growth exists only if it was captured
more than once -- capture on every sync.

`report` answers "what kind of post gets read" from the owner's own history:
it splits posts by a title feature and compares median reads. It is a
backtest over a few dozen posts, with official recommendation dominating
reach, so a gap is a hint to try, not a finding. Posts younger than
--min-age-days are left out of the comparison because their reads have not
settled. Read-only: it touches nothing but files under reports/xueqiu/.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "reports" / "xueqiu" / "post_stats.jsonl"
NOTES = ROOT / "reports" / "xueqiu" / "experiments.json"
FIELDS = ("reads", "likes", "comments", "reposts", "favorites")

# What a title says, as regexes. Kept deliberately coarse: with this few
# posts, finer categories would leave one or two posts in each.
FEATURES = {
    "hot_name": r"(?i)英伟达|NVDA|特斯拉|TSLA|拼多多|PDD|中概|苹果|AAPL|纳指",
    "option_jargon": r"(?i)\bcall\b|\bput\b|行权|作废|权利金|期权",
    "question": r"[?？]",
    "loss": r"亏|跌|失误|被套|回撤|强平|错过",
    "gain": r"回血|赚|涨|新高",
    "date_first": r"^\D{0,4}\d{4}[-.]\d{2}",
}


def title_features(title: str) -> dict[str, bool]:
    return {k: bool(re.search(p, title)) for k, p in FEATURES.items()}


def load(log: Path = LOG) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def record(posts: list[dict], log: Path = LOG, captured_at: str | None = None) -> int:
    """Append a capture; an earlier capture of the same post on the same day is replaced."""
    captured_at = captured_at or datetime.now().isoformat(timespec="minutes")
    day = captured_at[:10]
    new = {p["title"] for p in posts}
    kept = [r for r in load(log) if not (r["captured_at"][:10] == day and r["title"] in new)]
    rows = kept + [
        {
            "captured_at": captured_at,
            "title": p["title"],
            "published": p["published"],
            **{f: int(p.get(f) or 0) for f in FIELDS},
        }
        for p in posts
    ]
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    return len(posts)


def latest(rows: list[dict]) -> list[dict]:
    """The newest capture of each post, with its age in days at that capture."""
    by_title: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: r["captured_at"]):
        by_title[r["title"]] = r
    out = []
    for r in by_title.values():
        age = datetime.fromisoformat(r["captured_at"][:10]) - datetime.fromisoformat(
            r["published"][:10]
        )
        out.append({**r, "age_days": age.days})
    return sorted(out, key=lambda r: r["published"], reverse=True)


def compare(posts: list[dict], min_age_days: int = 3) -> list[dict]:
    """Median reads with and without each title feature, among settled posts."""
    settled = [p for p in posts if p["age_days"] >= min_age_days]
    out = []
    for feature in FEATURES:
        has = [p["reads"] for p in settled if title_features(p["title"])[feature]]
        not_ = [p["reads"] for p in settled if not title_features(p["title"])[feature]]
        out.append(
            {
                "feature": feature,
                "n_with": len(has),
                "n_without": len(not_),
                "median_with": median(has) if has else None,
                "median_without": median(not_) if not_ else None,
            }
        )
    return out


def _fmt(v) -> str:
    return "-" if v is None else f"{v:,.0f}"


def _report(min_age_days: int) -> None:
    rows = load()
    if not rows:
        sys.exit(f"no captures yet in {LOG}; run `record` first")
    posts = latest(rows)
    notes = json.loads(NOTES.read_text(encoding="utf-8")) if NOTES.exists() else {}
    print(f"# 雪球长文数据（最近一次抓取 {max(r['captured_at'] for r in rows)}）\n")

    print("## 每篇（最新）\n")
    print("| 发布 | 天数 | 阅读 | 赞 | 讨论 | 收藏 | 标题 |")
    print("|---|---:|---:|---:|---:|---:|---|")
    for p in posts:
        print(
            f"| {p['published'][:10]} | {p['age_days']} | {p['reads']:,} | {p['likes']} "
            f"| {p['comments']} | {p['favorites']} | {p['title']} |"
        )

    # 2024's posts were read far more than 2026's whatever their titles, so
    # pooling the years lets the era pose as a title effect; show each too.
    years = sorted({p["published"][:4] for p in posts})
    for label, group in [("全部", posts)] + [
        (y, [p for p in posts if p["published"][:4] == y]) for y in years
    ]:
        print(f"\n## 标题特征回测：{label}（只算发布满 {min_age_days} 天的帖子，中位数阅读）\n")
        print("| 特征 | 有：篇数 | 有：中位数 | 无：篇数 | 无：中位数 |")
        print("|---|---:|---:|---:|---:|")
        for c in compare(group, min_age_days):
            print(
                f"| {c['feature']} | {c['n_with']} | {_fmt(c['median_with'])} "
                f"| {c['n_without']} | {_fmt(c['median_without'])} |"
            )
    print(
        "\n样本只有几十篇，官方推荐对阅读的影响远大于标题，2024 年和现在的流量环境也不同；"
        "差距只能当作值得试的线索，不是结论。"
    )

    grown = [t for t in {r["title"] for r in rows} if sum(r["title"] == t for r in rows) > 1]
    if grown:
        print("\n## 增长（抓取过多次的帖子）\n")
        for t in sorted(grown):
            hist = sorted((r for r in rows if r["title"] == t), key=lambda r: r["captured_at"])
            print(f"- {t}：" + " → ".join(f"{r['captured_at'][5:10]} {r['reads']:,}" for r in hist))

    if notes:
        print("\n## 尝试记录\n")
        current = {p["title"]: p for p in posts}
        for t, what in notes.items():
            p = current.get(t)
            seen = f"{p['reads']:,} 阅读（第 {p['age_days']} 天）" if p else "还没有抓到"
            print(f"- {t}：{what} → {seen}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("capture", type=Path)
    note = sub.add_parser("note")
    note.add_argument("title")
    note.add_argument("what")
    rep = sub.add_parser("report")
    rep.add_argument("--min-age-days", type=int, default=3)
    args = ap.parse_args()

    if args.cmd == "record":
        n = record(json.loads(args.capture.read_text(encoding="utf-8")))
        print(f"recorded {n} posts -> {LOG}")
    elif args.cmd == "note":
        notes = json.loads(NOTES.read_text(encoding="utf-8")) if NOTES.exists() else {}
        notes[args.title] = args.what
        NOTES.parent.mkdir(parents=True, exist_ok=True)
        NOTES.write_text(json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"noted -> {NOTES}")
    else:
        _report(args.min_age_days)


if __name__ == "__main__":
    main()
