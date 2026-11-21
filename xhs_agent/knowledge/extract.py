"""学习经验与面经抽取器（独立 DeepSeek JSON 模式 + Pydantic 校验 + 原文证据校验）。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from xhs_agent.config import LLMConfig, ROOT_DIR
from xhs_agent.harness.llm import LLMClientProtocol, TokenUsage
from xhs_agent.jobs.dedup import verify_evidence
from xhs_agent.knowledge.models import ExperienceExtractionPayload, ExperienceItem

DEFAULT_EXPERIENCE_PROMPT_PATH = ROOT_DIR / "xhs_agent" / "prompts" / "extract_experience.md"
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
_IMAGE_HINT_KEYWORDS = ("见图", "详见图", "路线图", "导图在图", "看图", "如图", "长图", "图里", "图片里")


def _parse_json_object(raw_text: str) -> dict:
    text = raw_text.strip()
    if not text:
        raise ValueError("经验抽取模型返回空内容")
    m = _JSON_FENCE_RE.search(text)
    if m:
        text = m.group(1)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("经验抽取结果顶层必须为 JSON 对象")
    return parsed


def _detect_needs_image(desc: str, image_count: int, *, has_item: bool) -> bool:
    if image_count <= 0:
        return False
    stripped = desc.strip()
    if any(kw in stripped for kw in _IMAGE_HINT_KEYWORDS):
        return True
    if has_item and len(stripped) < 120:
        return True
    return False


@dataclass
class ExperienceExtractionResult:
    item: ExperienceItem | None = None
    needs_image: bool = False
    dropped_unverified: bool = False
    usage: TokenUsage = field(default_factory=TokenUsage)
    raw_text: str = ""
    retries_used: int = 0


class ExperienceExtractor:
    """单篇笔记学习经验与面经结构化抽取器。"""

    def __init__(
        self,
        llm: LLMClientProtocol,
        llm_config: LLMConfig,
        *,
        prompt_path: Path | None = None,
    ) -> None:
        self.llm = llm
        self.llm_config = llm_config
        path = prompt_path or DEFAULT_EXPERIENCE_PROMPT_PATH
        self.system_prompt = path.read_text(encoding="utf-8")
        self.total_usage = TokenUsage()
        self.call_count = 0

    def snapshot_stats(self) -> tuple[int, TokenUsage]:
        return (
            self.call_count,
            TokenUsage(
                prompt_tokens=self.total_usage.prompt_tokens,
                completion_tokens=self.total_usage.completion_tokens,
                total_tokens=self.total_usage.total_tokens,
                cache_hit_tokens=self.total_usage.cache_hit_tokens,
                cache_miss_tokens=self.total_usage.cache_miss_tokens,
            ),
        )

    async def extract(
        self,
        *,
        title: str,
        desc: str,
        image_count: int = 0,
    ) -> ExperienceExtractionResult:
        user_content = (
            f"笔记标题：{title.strip() or '(无标题)'}\n"
            f"图片数量：{image_count}\n"
            f"笔记正文：\n{desc.strip() or '(无正文)'}\n\n"
            "请严格按照规则判断并抽取，返回合法 json 对象。"
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]

        total_usage = TokenUsage()
        payload: ExperienceExtractionPayload | None = None
        last_error: str | None = None
        last_raw = ""
        retries_used = 0

        for attempt in range(2):
            resp = await self.llm.chat(
                messages,
                tools=None,
                temperature=self.llm_config.temperature_extract,
                model=self.llm_config.extract_model,
                response_format={"type": "json_object"},
            )
            total_usage.add(resp.usage)
            self.total_usage.add(resp.usage)
            self.call_count += 1
            last_raw = resp.content

            try:
                raw_obj = _parse_json_object(resp.content)
                payload = ExperienceExtractionPayload.model_validate(raw_obj)
                break
            except (json.JSONDecodeError, ValueError, ValidationError) as exc:
                last_error = str(exc)
                if attempt == 0:
                    retries_used = 1
                    messages.append({"role": "assistant", "content": resp.content})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"上一次输出的 JSON 校验失败：{last_error[:300]}。\n"
                                "请严格按照 schema 重新输出合法 json 对象（含 item 与 needs_image）。"
                            ),
                        }
                    )
                else:
                    raise ValueError(f"经验抽取 JSON 校验连续两次失败: {last_error}") from exc

        assert payload is not None
        source_text = f"{title}\n{desc}"
        verified_item: ExperienceItem | None = None
        dropped = False

        if payload.item is not None:
            if verify_evidence(payload.item.evidence, source_text):
                verified_item = payload.item
            else:
                dropped = True

        needs_image = bool(
            (payload.needs_image and image_count > 0)
            or _detect_needs_image(desc, image_count, has_item=verified_item is not None)
        )

        return ExperienceExtractionResult(
            item=verified_item,
            needs_image=needs_image,
            dropped_unverified=dropped,
            usage=total_usage,
            raw_text=last_raw,
            retries_used=retries_used,
        )
