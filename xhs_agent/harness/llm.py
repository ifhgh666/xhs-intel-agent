"""DeepSeek LLM 客户端封装（基于 openai AsyncOpenAI SDK）。

要点（见 docs/development_plan.md DeepSeek 使用要点）：
- 统一从 config.toml 读取 base_url、模型名、温度与超时，不写死
- 统计 token 用量，包含 DeepSeek 上下文缓存命中数（prompt_cache_hit_tokens）
- 遇到网络异常、超时、429 / 5xx 限流错误自动指数退避重试
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from xhs_agent.config import LLMConfig, ROOT_DIR


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments_raw: str

    def to_openai_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments_raw,
            },
        }


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0

    def add(self, other: TokenUsage) -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.cache_hit_tokens += other.cache_hit_tokens
        self.cache_miss_tokens += other.cache_miss_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
        }


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    finish_reason: str | None = None
    duration_ms: int = 0


class LLMClientProtocol(Protocol):
    """最小 LLM 客户端协议，便于测试中注入 MockLLM。"""

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse: ...


def _extract_usage(raw_usage: Any) -> TokenUsage:
    if raw_usage is None:
        return TokenUsage()
    prompt_tokens = int(getattr(raw_usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(raw_usage, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(raw_usage, "total_tokens", prompt_tokens + completion_tokens) or 0)

    cache_hit = getattr(raw_usage, "prompt_cache_hit_tokens", None)
    if cache_hit is None:
        details = getattr(raw_usage, "prompt_tokens_details", None)
        cache_hit = getattr(details, "cached_tokens", 0) if details is not None else 0
    cache_hit_int = int(cache_hit or 0)

    cache_miss = getattr(raw_usage, "prompt_cache_miss_tokens", None)
    cache_miss_int = int(cache_miss) if cache_miss is not None else max(0, prompt_tokens - cache_hit_int)

    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cache_hit_tokens=cache_hit_int,
        cache_miss_tokens=cache_miss_int,
    )


def _is_retryable_openai_error(exc: Exception) -> bool:
    if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in {408, 429, 500, 502, 503, 504}
    return False


class DeepSeekClient:
    """DeepSeek 异步客户端（支持 Function Calling、JSON 模式与指数退避重试）。"""

    def __init__(
        self,
        config: LLMConfig,
        *,
        api_key: str | None = None,
        max_retries: int = 3,
        base_backoff_sec: float = 1.5,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        openai_client: AsyncOpenAI | None = None,
    ) -> None:
        self.config = config
        self.max_retries = int(max_retries)
        self.base_backoff_sec = float(base_backoff_sec)
        self._sleep = sleep_fn
        self.total_usage = TokenUsage()
        self.call_count = 0

        if openai_client is not None:
            self._client = openai_client
        else:
            load_dotenv(ROOT_DIR / ".env")
            key = (api_key or os.environ.get("DEEPSEEK_API_KEY", "")).strip()
            if not key or key == "sk-your-deepseek-api-key-here":
                raise RuntimeError("未检测到有效的 DEEPSEEK_API_KEY，请在项目根目录 .env 中配置。")
            self._client = AsyncOpenAI(
                api_key=key,
                base_url=config.base_url,
                timeout=config.timeout_sec,
                max_retries=0,  # 由本类统一做可控退避重试
            )

    async def close(self) -> None:
        close_fn = getattr(self._client, "close", None)
        if callable(close_fn):
            res = close_fn()
            if asyncio.iscoroutine(res):
                await res

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        target_model = model or self.config.agent_model
        target_temp = self.config.temperature_agent if temperature is None else temperature

        kwargs: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": target_temp,
        }
        if tools:
            kwargs["tools"] = tools
        if response_format is not None:
            kwargs["response_format"] = response_format

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            t0 = time.monotonic()
            try:
                raw_resp = await self._client.chat.completions.create(**kwargs)
                duration_ms = int((time.monotonic() - t0) * 1000)
                choice = raw_resp.choices[0]
                msg = choice.message

                tool_calls: list[ToolCallRequest] = []
                for tc in msg.tool_calls or []:
                    tool_calls.append(
                        ToolCallRequest(
                            id=tc.id,
                            name=tc.function.name,
                            arguments_raw=tc.function.arguments or "{}",
                        )
                    )

                usage = _extract_usage(raw_resp.usage)
                self.total_usage.add(usage)
                self.call_count += 1

                return LLMResponse(
                    content=msg.content or "",
                    tool_calls=tool_calls,
                    usage=usage,
                    model=getattr(raw_resp, "model", target_model) or target_model,
                    finish_reason=getattr(choice, "finish_reason", None),
                    duration_ms=duration_ms,
                )
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries and _is_retryable_openai_error(exc):
                    delay = self.base_backoff_sec * (2**attempt)
                    await self._sleep(delay)
                    continue
                raise

        assert last_exc is not None
        raise last_exc
