"""阶段 1 探测脚本辅助函数的离线单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from _mcp_helper import (  # noqa: E402
    decode_feed_id_timestamp,
    extract_detail_summary,
    extract_feed_items,
    extract_feed_summary,
    format_timestamp,
    slugify_keyword,
)


def test_extract_feed_items_handles_common_shapes():
    assert len(extract_feed_items([{"id": "1"}, {"id": "2"}])) == 2
    assert len(extract_feed_items({"feeds": [{"id": "1"}]})) == 1
    assert len(extract_feed_items({"data": {"feeds": [{"id": "1"}]}})) == 1
    assert extract_feed_items({"other": 123}) == []


def test_extract_feed_summary_nested_note_card():
    raw = {
        "id": "abc123",
        "xsecToken": "tok_xyz",
        "noteCard": {
            "displayTitle": "广州 AI Agent 实习急招",
            "user": {"nickname": "招聘小助手"},
            "interactInfo": {"likedCount": "42"},
            "type": "normal",
        },
    }
    summary = extract_feed_summary(raw)
    assert summary["feed_id"] == "abc123"
    assert summary["xsec_token"] == "tok_xyz"
    assert summary["title"] == "广州 AI Agent 实习急招"
    assert summary["author"] == "招聘小助手"
    assert summary["liked_count"] == "42"


def test_extract_detail_summary_nested_data_note():
    raw = {
        "data": {
            "note": {
                "noteId": "abc123",
                "title": "广州 RAG 实习",
                "desc": "岗位要求：熟悉 Python 与 RAG，欢迎 2028 届本科生投递 hr@example.com",
                "user": {"nickname": "HR_A"},
                "time": 1730000000000,
                "imageList": [{"url": "https://img1"}, {"url": "https://img2"}],
                "tagList": [{"name": "实习"}, {"name": "AI"}],
            }
        }
    }
    summary = extract_detail_summary(raw)
    assert summary["feed_id"] == "abc123"
    assert summary["title"] == "广州 RAG 实习"
    assert "RAG" in summary["desc"]
    assert summary["author"] == "HR_A"
    assert summary["image_count"] == 2
    assert summary["tags"] == ["实习", "AI"]


def test_slugify_and_timestamp():
    assert slugify_keyword("广州 AI实习") == "广州_AI实习"
    assert format_timestamp(None) == "-"
    assert "-" in format_timestamp(1730000000000)


def test_decode_feed_id_timestamp():
    assert decode_feed_id_timestamp("6afdc5df000000002c00cff2") == 1795016159
    assert decode_feed_id_timestamp("not-a-hex") is None


def test_recorded_fixtures_and_eval_samples_integrity():
    import json

    fixtures_dir = ROOT_DIR / "tests" / "fixtures"
    assert (fixtures_dir / "tools.json").exists()
    search_files = sorted(fixtures_dir.glob("search_*.json"))
    detail_files = sorted(fixtures_dir.glob("detail_*.json"))
    assert len(search_files) >= 3
    assert len(detail_files) >= 5

    # 校验所有详情样本的 feed_id 前 8 位与 time 一致
    for p in detail_files:
        data = json.loads(p.read_text(encoding="utf-8"))
        assert not data["is_error"]
        summary = extract_detail_summary(data["parsed"])
        assert summary["feed_id"]
        assert decode_feed_id_timestamp(summary["feed_id"]) == int(summary["published_at"]) // 1000

    # 校验评估样本的 evidence 均真实存在于原文中
    samples_dir = ROOT_DIR / "evals" / "samples"
    sample_files = sorted(samples_dir.glob("sample_*.json"))
    assert len(sample_files) >= 10
    for p in sample_files:
        sample = json.loads(p.read_text(encoding="utf-8"))
        assert sample["annotated"] is True
        haystack = f"{sample['note']['title']}\n{sample['note']['desc']}"
        for job in sample["expected"]["jobs"]:
            assert job["evidence"] in haystack, f"evidence mismatch in {p.name}"


