"""岗位结构化抽取器（抽取 LLM + Pydantic 校验 + 原文证据校验）。

对照 docs/design.md 第 9 节与 docs/development_plan.md 阶段 4：
- 独立调用 DeepSeek JSON 输出模式（response_format={"type": "json_object"}，temperature=0）
- 不占用主 Agent 上下文
- 返回后经 Pydantic 校验（失败自动重试一次）
- 对每条岗位执行原文证据校验（verify_evidence），不通过的条目直接丢弃
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from xhs_agent.config import LLMConfig, ROOT_DIR
from xhs_agent.harness.llm import LLMClientProtocol, TokenUsage
from xhs_agent.jobs.dedup import verify_evidence
from xhs_agent.jobs.models import ExtractionPayload, Internship

DEFAULT_EXTRACT_PROMPT_PATH = ROOT_DIR / "xhs_agent" / "prompts" / "extract.md"

_IMAGE_HINT_KEYWORDS = ("见图", "详见图", "工作要求如图", "要求如图", "岗位如图", "看图", "图里", "图片里", "长图")
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def detect_needs_image_heuristic(desc: str, image_count: int, *, is_job_post: bool) -> bool:
    """结合正文长度、图片提示词和图片数量判断是否需要看图补充 JD。"""
    if image_count <= 0:
        return False
    stripped = desc.strip()
    if any(kw in stripped for kw in _IMAGE_HINT_KEYWORDS):
        return True
    if is_job_post and len(stripped) < 120:
        return True
    return False


def _parse_json_object(raw_text: str) -> dict:
    text = raw_text.strip()
    if not text:
        raise ValueError("抽取模型返回空内容")
    m = _JSON_FENCE_RE.search(text)
    if m:
        text = m.group(1)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("抽取结果顶层必须为 JSON 对象")
    return parsed


@dataclass
class ExtractionResult:
    jobs: list[Internship] = field(default_factory=list)
    needs_image: bool = False
    dropped_unverified: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)
    raw_text: str = ""
    retries_used: int = 0


class JobExtractor:
    """单篇笔记结构化岗位抽取器。"""

    def __init__(
        self,
        llm: LLMClientProtocol,
        llm_config: LLMConfig,
        *,
        prompt_path: Path | None = None,
    ) -> None:
        self.llm = llm
        self.llm_config = llm_config
        path = prompt_path or DEFAULT_EXTRACT_PROMPT_PATH
        self.system_prompt = path.read_text(encoding="utf-8")
        self.total_usage = TokenUsage()
        self.call_count = 0

    def snapshot_stats(self) -> tuple[int, TokenUsage]:
        """返回当前累计抽取 LLM 调用次数与 Token 用量快照（供按任务/轮次求差值）。"""
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
    ) -> ExtractionResult:
        """从单篇笔记标题 + 正文中抽取通过 Pydantic 与原文证据校验的岗位列表。"""
        user_content = (
            f"笔记标题：{title.strip() or '(无标题)'}\n"
            f"图片数量：{image_count}\n"
            f"笔记正文：\n{desc.strip() or '(无正文)'}\n\n"
            "请严格按照规则抽取并返回 json 对象。"
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]

        total_usage = TokenUsage()
        payload: ExtractionPayload | None = None
        last_error: str | None = None
        last_raw_text = ""
        retries_used = 0

        for attempt in range(2):  # 首次 + 校验失败重试一次
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
            last_raw_text = resp.content

            try:
                raw_obj = _parse_json_object(resp.content)
                payload = ExtractionPayload.model_validate(raw_obj)
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
                                "请严格按照 schema 重新输出合法的 json 对象（包含 jobs 列表和 needs_image 布尔值）。"
                            ),
                        }
                    )
                else:
                    raise ValueError(f"岗位抽取 JSON 校验连续两次失败: {last_error}") from exc

        assert payload is not None
        source_text = f"{title}\n{desc}"

        verified_jobs: list[Internship] = []
        dropped = 0
        for job in payload.jobs:
            if verify_evidence(job.evidence, source_text):
                verified_jobs.append(job)
            else:
                dropped += 1

        # 若原文中明确写了“如图/见图”或正文极短且有图，即使模型漏标也补标 needs_image
        is_job_post = len(verified_jobs) > 0 or len(payload.jobs) > 0
        needs_image = bool(
            (payload.needs_image and image_count > 0)
            or detect_needs_image_heuristic(desc, image_count, is_job_post=is_job_post)
        )

        return ExtractionResult(
            jobs=verified_jobs,
            needs_image=needs_image,
            dropped_unverified=dropped,
            usage=total_usage,
            raw_text=last_raw_text,
            retries_used=retries_used,
        )
