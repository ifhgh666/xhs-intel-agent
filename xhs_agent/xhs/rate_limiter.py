"""小红书 MCP 防风控限流器。

职责：
1. 全局 asyncio.Lock 强制串行（xiaohongshu-mcp 底层只有一个浏览器实例）
2. 两次调用之间强制满足 min_interval_sec + random(0, jitter_sec) 间隔
3. 按自然日统计搜索 / 详情调用上限（本阶段默认内存存储，阶段 4 可直接替换为 SQLite Counter）
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date
from typing import Protocol


class RateLimitExceededError(RuntimeError):
    """超过每日调用上限时抛出。"""

    def __init__(self, category: str, count: int, limit: int) -> None:
        self.category = category
        self.count = count
        self.limit = limit
        super().__init__(f"已达到每日 {category} 调用上限（{count}/{limit}），为保护账号已拒绝本次请求。")


class UsageCounter(Protocol):
    """每日用量计数器协议，便于阶段 4 无缝替换为 SQLite 持久化实现。"""

    def get_count(self, day: str, category: str) -> int: ...

    def increment(self, day: str, category: str, delta: int = 1) -> int: ...


class InMemoryUsageCounter:
    """内存版每日用量计数器（阶段 2 默认实现）。"""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str], int] = {}

    def get_count(self, day: str, category: str) -> int:
        return self._counts.get((day, category), 0)

    def increment(self, day: str, category: str, delta: int = 1) -> int:
        key = (day, category)
        new_val = self._counts.get(key, 0) + delta
        self._counts[key] = new_val
        return new_val


class RateLimiter:
    """小红书 MCP 串行 + 间隔抖动 + 每日配额限流器。"""

    def __init__(
        self,
        *,
        min_interval_sec: float = 3.0,
        jitter_sec: float = 5.0,
        daily_limits: dict[str, int] | None = None,
        counter: UsageCounter | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand_fn: Callable[[float, float], float] = random.uniform,
        today_fn: Callable[[], str] | None = None,
    ) -> None:
        if min_interval_sec < 0 or jitter_sec < 0:
            raise ValueError("min_interval_sec 与 jitter_sec 不能为负数")
        self.min_interval_sec = float(min_interval_sec)
        self.jitter_sec = float(jitter_sec)
        self.daily_limits: dict[str, int] = dict(daily_limits or {"search": 50, "detail": 150})
        self._counter: UsageCounter = counter or InMemoryUsageCounter()
        self._clock = clock
        self._sleep = sleep_fn
        self._rand = rand_fn
        self._today_fn = today_fn or (lambda: date.today().isoformat())

        self._lock = asyncio.Lock()
        self._last_call_end: float | None = None
        self.total_waited_sec: float = 0.0

    def get_daily_usage(self, category: str) -> int:
        return self._counter.get_count(self._today_fn(), category)

    def get_remaining(self, category: str) -> int | None:
        limit = self.daily_limits.get(category)
        if limit is None:
            return None
        return max(0, limit - self.get_daily_usage(category))

    def _check_daily_limit(self, category: str | None) -> None:
        if not category:
            return
        limit = self.daily_limits.get(category)
        if limit is None:
            return
        current = self.get_daily_usage(category)
        if current >= limit:
            raise RateLimitExceededError(category=category, count=current, limit=limit)

    @asynccontextmanager
    async def acquire(self, category: str | None = None) -> AsyncIterator[float]:
        """串行获取一次 MCP 调用许可，返回本次实际等待秒数。"""
        async with self._lock:
            self._check_daily_limit(category)

            waited = 0.0
            if self._last_call_end is not None:
                target_gap = self.min_interval_sec + (
                    self._rand(0.0, self.jitter_sec) if self.jitter_sec > 0 else 0.0
                )
                elapsed = self._clock() - self._last_call_end
                remaining_wait = target_gap - elapsed
                if remaining_wait > 0:
                    await self._sleep(remaining_wait)
                    waited = remaining_wait
                    self.total_waited_sec += waited

            if category:
                self._counter.increment(self._today_fn(), category, 1)

            try:
                yield waited
            finally:
                self._last_call_end = self._clock()
