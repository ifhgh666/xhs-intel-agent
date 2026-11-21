"""项目配置加载模块（读取 config.toml）。"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "config.toml"


class LLMConfig(BaseModel):
    base_url: str = "https://api.deepseek.com"
    agent_model: str = "deepseek-chat"
    extract_model: str = "deepseek-chat"
    temperature_agent: float = 0.3
    temperature_extract: float = 0.0
    timeout_sec: float = 60.0


class MCPConfig(BaseModel):
    url: str = "http://localhost:18060/mcp"


class RateLimitConfig(BaseModel):
    min_interval_sec: float = Field(default=3.0, ge=0.0)
    jitter_sec: float = Field(default=5.0, ge=0.0)
    daily_search_limit: int = Field(default=50, ge=1)
    daily_detail_limit: int = Field(default=150, ge=1)


class BudgetConfig(BaseModel):
    max_steps: int = Field(default=25, ge=1)
    max_search_calls: int = Field(default=6, ge=1)
    max_detail_calls: int = Field(default=30, ge=1)
    max_consecutive_errors: int = Field(default=3, ge=1)


class PricingConfig(BaseModel):
    currency: str = "CNY"
    input_cache_hit_per_1m: float = Field(default=0.2, ge=0.0)
    input_cache_miss_per_1m: float = Field(default=2.0, ge=0.0)
    output_per_1m: float = Field(default=3.0, ge=0.0)


class AppConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    pricing: PricingConfig = Field(default_factory=PricingConfig)


def load_config(path: Path | str | None = None) -> AppConfig:
    """从 TOML 文件加载配置；文件不存在时返回默认值。"""
    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        return AppConfig()
    with cfg_path.open("rb") as f:
        raw: dict[str, Any] = tomllib.load(f)
    return AppConfig.model_validate(raw)
