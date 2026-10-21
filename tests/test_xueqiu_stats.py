"""Post statistics: captures are recorded per day, and titles are compared fairly."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "xueqiu_stats", Path(__file__).resolve().parents[1] / "scripts" / "xueqiu_stats.py"
)
stats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stats)


def _post(title, published, reads, **over):
    row = {
        "title": title,
        "published": published,
        "reads": reads,
        "likes": 0,
        "comments": 0,
        "reposts": 0,
        "favorites": 0,
    }
    row.update(over)
    return row


def test_title_features_pick_out_names_jargon_and_tone():
    f = stats.title_features("2024-08-01英伟达大幅回血")
    assert f["hot_name"] and f["gain"] and f["date_first"]
    assert not f["option_jargon"] and not f["loss"] and not f["question"]

    f = stats.title_features("2026-09-30 美股操作：put 上移到 76.5，补满最后一张 call")
    assert f["option_jargon"] and not f["hot_name"]

    f = stats.title_features("2024-10-01 中概这一轮是否走到尽头？")
    assert f["hot_name"] and f["question"]

    assert stats.title_features("2024-11-26本周亏损20%")["loss"]


def test_a_second_capture_on_the_same_day_replaces_the_first(tmp_path):
    log = tmp_path / "post_stats.jsonl"
    stats.record([_post("A", "2026-10-19 12:32", 30)], log, captured_at="2026-10-19T13:00")
    stats.record([_post("A", "2026-10-19 12:32", 80)], log, captured_at="2026-10-19T20:00")
    stats.record([_post("A", "2026-10-19 12:32", 300)], log, captured_at="2026-10-21T14:00")

    rows = stats.load(log)
    assert [(r["captured_at"][:10], r["reads"]) for r in rows] == [
        ("2026-10-19", 80),
        ("2026-10-21", 300),
    ]


def test_latest_keeps_one_row_per_post_with_its_age():
    rows = [
        {**_post("A", "2026-10-01 14:00", 100), "captured_at": "2026-10-05T10:00"},
        {**_post("A", "2026-10-01 14:00", 900), "captured_at": "2026-10-21T10:00"},
        {**_post("B", "2026-10-19 12:00", 30), "captured_at": "2026-10-21T10:00"},
    ]
    latest = {r["title"]: r for r in stats.latest(rows)}
    assert latest["A"]["reads"] == 900 and latest["A"]["age_days"] == 20
    assert latest["B"]["age_days"] == 2


def test_compare_reports_medians_only_for_posts_old_enough():
    posts = [
        {**_post("英伟达大涨", "2024-08-01 10:00", 50000), "age_days": 800},
        {**_post("特斯拉", "2024-11-14 10:00", 13000), "age_days": 700},
        {**_post("put 上移", "2026-10-01 10:00", 300), "age_days": 20},
        {**_post("call 作废", "2026-10-14 10:00", 100), "age_days": 7},
        # Too young to count: its reads have not settled yet.
        {**_post("拼多多新帖", "2026-10-21 10:00", 5), "age_days": 0},
    ]
    out = {c["feature"]: c for c in stats.compare(posts, min_age_days=3)}
    hot = out["hot_name"]
    assert (hot["n_with"], hot["n_without"]) == (2, 2)
    assert hot["median_with"] == 31500 and hot["median_without"] == 200
