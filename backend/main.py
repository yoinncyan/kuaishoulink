"""FastAPI control plane for the Kuaishou network probe."""

from __future__ import annotations

import logging
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import Settings
from backend.kuaishou.browser_session import BrowserProbeManager
from backend.kuaishou.sampling_task import SamplingTaskManager
from backend.models import (
    BrowserNavigateRequest,
    BrowserScrollRequest,
    BrowserWindowModeRequest,
    ProbeMarkRequest,
    ProbeStartRequest,
    SamplingStartRequest,
    VideoInspectRequest,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("kuaishou.web")

settings = Settings.from_env()
probe_manager = BrowserProbeManager(settings)
sampling_manager = SamplingTaskManager(settings.data_dir)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings.ensure_directories()
    yield
    await sampling_manager.shutdown()
    await probe_manager.shutdown()


app = FastAPI(
    title="快手 Web 网络探针",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/probe/status")
async def probe_status() -> dict:
    return probe_manager.status()


@app.get("/api/task/status")
async def sampling_task_status() -> dict:
    return sampling_manager.status()


@app.get("/api/task/comment-output")
async def download_comment_output() -> FileResponse:
    path = sampling_manager.comment_output_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="第二阶段 Markdown 尚未生成")
    return FileResponse(
        path,
        media_type="text/markdown; charset=utf-8",
        filename=path.name,
    )


@app.post("/api/task/start")
async def start_sampling_task(request: SamplingStartRequest) -> dict:
    try:
        config = request.model_dump()
        await probe_manager.configure_window_mode(config["open_browser_window"])
        return await sampling_manager.start(config)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Sampling task start failed")
        raise HTTPException(status_code=500, detail=f"采集任务启动失败：{exc}") from exc


@app.post("/api/task/pause")
async def pause_sampling_task() -> dict:
    try:
        return await sampling_manager.pause()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Sampling task pause failed")
        raise HTTPException(status_code=500, detail=f"采集任务暂停失败：{exc}") from exc


@app.post("/api/task/reset-identity")
async def reset_sampling_identity(request: BrowserWindowModeRequest) -> dict:
    if sampling_manager.running:
        raise HTTPException(status_code=409, detail="请先暂停采集任务再重置身份")
    try:
        await probe_manager.configure_window_mode(request.open_browser_window)
        browser = await probe_manager.reset_identity()
        sampling_manager.acknowledge_identity_reset()
        return {"ok": True, "browser": browser, "task": sampling_manager.status()}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Sampling identity reset failed")
        raise HTTPException(status_code=500, detail=f"浏览器身份重置失败：{exc}") from exc


@app.post("/api/browser/open-login")
async def open_login() -> dict:
    try:
        return await probe_manager.open_login()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Browser login page failed")
        raise HTTPException(status_code=500, detail=f"浏览器启动失败：{exc}") from exc


@app.post("/api/browser/navigate")
async def navigate_browser(request: BrowserNavigateRequest) -> dict:
    try:
        return await probe_manager.navigate(request.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/browser/reload")
async def reload_browser() -> dict:
    try:
        return await probe_manager.reload_page()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/browser/scroll")
async def scroll_browser(request: BrowserScrollRequest) -> dict:
    try:
        return await probe_manager.scroll_page(request.distance)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/browser/search-page-state")
async def search_page_state() -> dict:
    try:
        return await probe_manager.search_page_state()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Search page state inspection failed")
        raise HTTPException(
            status_code=500, detail=f"搜索页状态检测失败：{exc}"
        ) from exc


@app.post("/api/browser/close")
async def close_browser() -> dict:
    return await probe_manager.close_browser()


@app.post("/api/browser/reset-identity")
async def reset_browser_identity() -> dict:
    try:
        return await probe_manager.reset_identity()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Browser identity reset failed")
        raise HTTPException(status_code=500, detail=f"浏览器身份重置失败：{exc}") from exc


@app.get("/api/browser/login-status")
async def browser_login_status() -> dict:
    try:
        return await probe_manager.check_login_status()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/browser/screenshot")
async def browser_screenshot() -> Response:
    try:
        image = await probe_manager.screenshot()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Browser screenshot failed: %s", exc)
        raise HTTPException(status_code=504, detail="浏览器预览截图超时") from exc
    return Response(
        image,
        media_type="image/png",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.post("/api/probe/start")
async def start_probe(request: ProbeStartRequest) -> dict:
    try:
        return await probe_manager.start(request.keyword)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Probe start failed")
        raise HTTPException(status_code=500, detail=f"浏览器启动失败：{exc}") from exc


@app.post("/api/probe/mark")
async def mark_probe(request: ProbeMarkRequest) -> dict[str, bool]:
    try:
        await probe_manager.mark(request.action, request.details)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/probe/inspect-video")
async def inspect_video(request: VideoInspectRequest) -> dict:
    try:
        return await probe_manager.inspect_video_comments(
            request.video_id, request.timeout_seconds
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="等待快手 commentListQuery 响应超时",
        ) from exc


@app.post("/api/probe/direct-comment")
async def direct_comment(request: VideoInspectRequest) -> dict:
    try:
        return await probe_manager.direct_comment_count(
            request.video_id, request.timeout_seconds
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="评论数轻量查询超时") from exc


@app.post("/api/probe/repeat-search")
async def repeat_search() -> dict:
    try:
        return await probe_manager.repeat_search()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc


@app.post("/api/probe/stop")
async def stop_probe() -> dict:
    try:
        return await probe_manager.stop()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/probe/sessions")
async def probe_sessions() -> list[dict]:
    return probe_manager.list_sessions()


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    async def frontend(full_path: str):
        candidate = (FRONTEND_DIST / full_path).resolve()
        if (
            full_path
            and candidate.is_file()
            and FRONTEND_DIST.resolve() in candidate.parents
        ):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
