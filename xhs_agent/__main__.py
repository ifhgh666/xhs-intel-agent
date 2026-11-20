"""支持 `python -m xhs_agent "..."` 直接运行 CLI。"""

from xhs_agent.main import main

if __name__ == "__main__":
    raise SystemExit(main())
