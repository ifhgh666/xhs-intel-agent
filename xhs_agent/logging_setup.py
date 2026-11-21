"""应用日志分级配置（与 JSONL Trace 解耦）。

- 简洁模式（默认）：仅输出 WARNING 及以上运行日志，CLI 界面清爽
- 详细模式（--verbose）：输出 INFO / DEBUG 级运行日志，便于排查网络、重试、缓存与数据库细节
"""

from __future__ import annotations

import logging


def configure_logging(*, verbose: bool = False) -> logging.Logger:
    """配置 xhs_agent 命名空间日志级别（不影响 TraceLogger 的 JSONL 写入）。"""
    logger = logging.getLogger("xhs_agent")
    logger.setLevel(logging.DEBUG if verbose else logging.WARNING)

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    else:
        for handler in logger.handlers:
            handler.setLevel(logging.DEBUG if verbose else logging.WARNING)

    # 第三方库降噪
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)
    return logger
