"""阶段 0：DeepSeek API Key 与 Function Calling 最小验证脚本。

用法：
    uv run python scripts/probe_llm.py
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT_DIR = Path(__file__).resolve().parent.parent


def load_llm_config() -> dict:
    with (ROOT_DIR / "config.toml").open("rb") as f:
        return tomllib.load(f)["llm"]


def main() -> int:
    load_dotenv(ROOT_DIR / ".env")
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key or api_key == "sk-your-deepseek-api-key-here":
        print("错误：请在项目根目录的 .env 文件中配置有效的 DEEPSEEK_API_KEY。", file=sys.stderr)
        return 1

    llm_cfg = load_llm_config()
    client = OpenAI(
        api_key=api_key,
        base_url=llm_cfg["base_url"],
        timeout=llm_cfg.get("timeout_sec", 60),
    )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_notes",
                "description": "在小红书搜索笔记",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "搜索关键词"},
                    },
                    "required": ["keyword"],
                },
            },
        }
    ]

    resp = client.chat.completions.create(
        model=llm_cfg["agent_model"],
        messages=[{"role": "user", "content": "帮我找广州的 AI Agent 实习"}],
        tools=tools,
        temperature=llm_cfg.get("temperature_agent", 0.3),
    )

    msg = resp.choices[0].message
    print("Tool Calls:", msg.tool_calls)
    print("Content:", msg.content)
    print("Usage:", resp.usage)

    if not msg.tool_calls:
        print("警告：模型未返回 tool_calls，请检查模型配置。", file=sys.stderr)
        return 2

    print("验证通过：DeepSeek Function Calling 工作正常。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
