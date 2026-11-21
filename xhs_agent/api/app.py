"""FastAPI 应用工厂（lifespan 生命周期、CORS、路由挂载与静态前端托管）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from xhs_agent.api.routes import jobs, system, traces
from xhs_agent.api.state import WebAppState
from xhs_agent.config import AppConfig, ROOT_DIR
from xhs_agent.harness.llm import LLMClientProtocol
from xhs_agent.xhs import MCPClientProtocol, XhsAdapter

DEFAULT_WEB_DIST_DIR = ROOT_DIR / "web" / "dist"


def create_app(
    *,
    config: AppConfig | None = None,
    db_path: Path | str | None = None,
    traces_dir: Path | str | None = None,
    export_dir: Path | str | None = None,
    mcp_client: MCPClientProtocol | None = None,
    adapter: XhsAdapter | None = None,
    llm: LLMClientProtocol | None = None,
    extractor_llm: LLMClientProtocol | None = None,
    auto_connect_mcp: bool = True,
    web_dist_dir: Path | None = None,
) -> FastAPI:
    web_state = WebAppState(
        config=config,
        db_path=db_path,
        traces_dir=traces_dir,
        export_dir=export_dir,
        mcp_client=mcp_client,
        adapter=adapter,
        llm=llm,
        extractor_llm=extractor_llm,
        auto_connect_mcp=auto_connect_mcp,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await web_state.startup()
        try:
            yield
        finally:
            await web_state.shutdown()

    app = FastAPI(
        title="小红书实习情报 Agent API",
        version="0.1.0",
        description="本地小红书实习信息检索、结构化抽取与岗位管理 API",
        lifespan=lifespan,
    )
    app.state.web_state = web_state

    # 仅允许本地开发端口跨域访问
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(system.router)
    app.include_router(jobs.router)
    app.include_router(traces.router)

    # 若已构建 web/dist，直接由 FastAPI 单端口托管前端 SPA
    dist_dir = web_dist_dir if web_dist_dir is not None else DEFAULT_WEB_DIST_DIR
    if dist_dir.exists() and (dist_dir / "index.html").exists():
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        index_file = dist_dir / "index.html"

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            if full_path.startswith("api/"):
                return {"detail": "Not Found"}
            candidate = (dist_dir / full_path).resolve()
            if full_path and dist_dir.resolve() in candidate.parents and candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(index_file))

    return app


app = create_app()
