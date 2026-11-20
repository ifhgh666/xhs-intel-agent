"""防编造证据校验与岗位去重规则。

对照 docs/design.md 第 9 节与第 10.3 节：
1. 证据校验：去除空白与常见标点后，evidence 必须真实出现在笔记标题或正文中
2. 岗位级去重键：normalize(company) + normalize(position) + normalize(city)
   - 公司名或岗位名为空时返回 None（不做岗位级去重，避免误合并）
"""

from __future__ import annotations

import re

# 去除空白、常见中英文标点与符号
_STRIP_PUNCT_RE = re.compile(r"[\s\W_]+", flags=re.UNICODE)
_COMPANY_SUFFIX_RE = re.compile(
    r"(股份有限公司|有限责任公司|科技有限公司|技术有限公司|有限公司|集团|公司)$"
)
_POSITION_SUFFIX_RE = re.compile(r"(实习生|日常实习|暑期实习|寒假实习|实习|intern)$", re.IGNORECASE)
_CITY_SUFFIX_RE = re.compile(r"(市|区|省)$")


def strip_whitespace_and_punct(text: str) -> str:
    """去掉所有空白、下划线与标点并转小写，用于稳健子串校验。"""
    return _STRIP_PUNCT_RE.sub("", text).lower()


def verify_evidence(evidence: str | None, source_text: str) -> bool:
    """检查 evidence 是否真实出现在原文（标题 + 正文）中。"""
    if not evidence or not source_text:
        return False
    norm_ev = strip_whitespace_and_punct(evidence)
    if len(norm_ev) < 4:
        # 过短片段（如仅 1-3 个字）不足以作为防编造证据，除非原文极短且完全匹配
        return bool(norm_ev) and norm_ev in strip_whitespace_and_punct(source_text) and len(evidence.strip()) >= 4
    norm_src = strip_whitespace_and_punct(source_text)
    return norm_ev in norm_src


def normalize_company(company: str | None) -> str:
    if not company:
        return ""
    cleaned = company.strip().lower()
    # 去掉括号内补充说明（如 “趣丸千音（All Voice Lab）” -> “趣丸千音”）
    cleaned = re.sub(r"[（(][^）)]*[）)]", "", cleaned)
    cleaned = _STRIP_PUNCT_RE.sub("", cleaned)
    cleaned = _COMPANY_SUFFIX_RE.sub("", cleaned)
    return cleaned


def normalize_position(position: str | None) -> str:
    if not position:
        return ""
    cleaned = position.strip().lower()
    cleaned = _STRIP_PUNCT_RE.sub("", cleaned)
    # 反复去掉尾部 “实习生/实习/intern” 与 “类” 以便合并 “AI研发类实习生” 与 “AI研发实习生”
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = _POSITION_SUFFIX_RE.sub("", cleaned)
        if cleaned.endswith("类") and len(cleaned) > 2:
            cleaned = cleaned[:-1]
    return cleaned


def normalize_city(city: str | None) -> str:
    if not city:
        return ""
    first = re.split(r"[,，、/\s]+", city.strip())[0]
    cleaned = _STRIP_PUNCT_RE.sub("", first).lower()
    cleaned = _CITY_SUFFIX_RE.sub("", cleaned)
    # 若写的是 “广州天河” / “广州南沙”，规范为 “广州”
    for major in ("北京", "上海", "广州", "深圳", "杭州", "成都", "南京", "武汉", "西安", "苏州", "合肥", "天津", "重庆", "厦门"):
        if cleaned.startswith(major):
            return major
    return cleaned


def make_dedup_key(
    company: str | None,
    position: str | None,
    city: str | None,
) -> str | None:
    """生成岗位级去重键；公司名或岗位名为空时返回 None，避免误合并。"""
    norm_c = normalize_company(company)
    norm_p = normalize_position(position)
    if not norm_c or not norm_p:
        return None
    norm_city = normalize_city(city)
    return f"{norm_c}|{norm_p}|{norm_city}"
