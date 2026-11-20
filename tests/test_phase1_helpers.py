"""阶段 1 探测脚本辅助函数的离线单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from _mcp_helper import (  # noqa: E402
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
