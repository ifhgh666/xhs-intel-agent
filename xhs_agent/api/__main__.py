"""启动 FastAPI 后端服务：uv run python -m xhs_agent.api"""

from __future__ import annotations

import argparse

import uvicorn

from xhs_agent.logging_setup import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="启动小红书实习情报 Agent Web 服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument("--reload", action="store_true", help="开发热重载模式")
    parser.add_argument("-v", "--verbose", action="store_true", help="开启详细日志")
    args = parser.parse_args()

    configure_logging(verbose=args.verbose)
    uvicorn.run(
        "xhs_agent.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
