"""Token 费用估算模块（基于 DeepSeek 缓存命中 / 未命中 / 输出分档单价）。"""

from __future__ import annotations

from dataclasses import dataclass

from xhs_agent.config import PricingConfig
from xhs_agent.harness.llm import TokenUsage


@dataclass(frozen=True)
class CostEstimate:
    currency: str
    cache_hit_cost: float
    cache_miss_cost: float
    output_cost: float
    total_cost: float
    cache_savings: float

    def to_dict(self) -> dict[str, float | str]:
        return {
            "currency": self.currency,
            "cache_hit_cost": round(self.cache_hit_cost, 6),
            "cache_miss_cost": round(self.cache_miss_cost, 6),
            "output_cost": round(self.output_cost, 6),
            "total_cost": round(self.total_cost, 6),
            "cache_savings": round(self.cache_savings, 6),
        }


def estimate_cost(usage: TokenUsage, pricing: PricingConfig | None = None) -> CostEstimate:
    """按配置单价估算单次或累计调用的费用与缓存节省金额。"""
    cfg = pricing or PricingConfig()
    hit_tokens = max(0, usage.cache_hit_tokens)
    miss_tokens = (
        max(0, usage.cache_miss_tokens)
        if usage.cache_miss_tokens > 0 or hit_tokens > 0
        else max(0, usage.prompt_tokens)
    )
    out_tokens = max(0, usage.completion_tokens)

    cache_hit_cost = (hit_tokens / 1_000_000.0) * cfg.input_cache_hit_per_1m
    cache_miss_cost = (miss_tokens / 1_000_000.0) * cfg.input_cache_miss_per_1m
    output_cost = (out_tokens / 1_000_000.0) * cfg.output_per_1m
    total_cost = cache_hit_cost + cache_miss_cost + output_cost

    # 相比完全未命中缓存省下的输入费用
    no_cache_hit_cost = (hit_tokens / 1_000_000.0) * cfg.input_cache_miss_per_1m
    cache_savings = max(0.0, no_cache_hit_cost - cache_hit_cost)

    return CostEstimate(
        currency=cfg.currency,
        cache_hit_cost=cache_hit_cost,
        cache_miss_cost=cache_miss_cost,
        output_cost=output_cost,
        total_cost=total_cost,
        cache_savings=cache_savings,
    )
