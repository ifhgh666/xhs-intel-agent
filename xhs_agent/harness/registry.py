"""ToolSpec 与 ToolRegistry：统一管理暴露给主 LLM 的业务工具白名单与 Schema。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    """单个可注册工具的元数据与处理器。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[Any]]
    read_only: bool = True
    timeout: float = 30.0
    max_calls_per_task: int | None = None
    budget_group: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_openai(self) -> dict[str, Any]:
        """转换为 OpenAI / DeepSeek Function Calling 工具定义格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具白名单注册表。仅注册到这里的工具对主 LLM 可见且允许执行。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.name or not spec.name.strip():
            raise ValueError("工具名称不能为空")
        if spec.name in self._tools:
            raise ValueError(f"工具 '{spec.name}' 已注册，请勿重复注册")
        self._tools[spec.name] = spec

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def all_specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        """按注册顺序返回 OpenAI Function Calling 工具列表（顺序固定以利于 DeepSeek 前缀缓存）。"""
        return [spec.to_openai() for spec in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)
