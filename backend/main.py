# -*- coding: utf-8 -*-
# SoundBot - AI 音效管理器
# Copyright (C) 2026 Huckrick and SoundBot contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""FastAPI 后端服务，用于音效管理器的 AI 语义搜索功能。"""

import os
import re
import time
import asyncio
import json
import math
import functools
import hmac
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor


def validate_path(path: str, allow_absolute: bool = True) -> bool:
    """
    验证路径是否安全，防止路径遍历攻击
    
    Args:
        path: 要验证的路径
        allow_absolute: 是否允许绝对路径
        
    Returns:
        bool: 路径是否安全
    """
    if not path:
        return False
    
    # FastAPI already decodes URL path parameters once. JSON body paths are
    # native filesystem strings. Decoding again corrupts legitimate ``%``
    # names, so validation operates on the value exactly as received.
    if '\x00' in path:
        return False
    normalized = os.path.normpath(path)
    components = re.split(r"[\\/]", path)
    if any(component == ".." for component in components):
        return False
    
    # 如果不允许绝对路径，检查是否是相对路径
    if not allow_absolute and os.path.isabs(normalized):
        return False
    
    return True

from fastapi import FastAPI, HTTPException, Query, Path as PathParam, BackgroundTasks, WebSocket, WebSocketDisconnect, Body
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, StreamingResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

import config
from models import schemas
from core import catalog
from core.media_api import router as media_router
from core.database import (
    get_db_manager,
    reset_db_manager,
    AudioFileRecord,
    is_safe_project_id,
)
from core.websocket_manager import (
    get_ws_manager,
    reset_ws_manager,
)
from utils.logger import logger

# 线程池用于 CPU 密集型任务
_executor = ThreadPoolExecutor(max_workers=4)
_thumbnail_executor = ThreadPoolExecutor(max_workers=2)
_artwork_executor = ThreadPoolExecutor(max_workers=2)
_project_index_locks: Dict[str, asyncio.Lock] = {}
_projects_deleting: set[str] = set()


async def _run_audio_work(function, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor, functools.partial(function, *args, **kwargs)
    )


async def _run_thumbnail_work(function, *args):
    return await asyncio.get_running_loop().run_in_executor(_thumbnail_executor, function, *args)


def _get_project_index_lock(project_id: str) -> asyncio.Lock:
    return _project_index_locks.setdefault(str(project_id), asyncio.Lock())


class SoundBotAPIError(Exception):
    """Structured error contract shared by backend, Electron and renderer."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.payload = {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        }


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.catalog_slots = asyncio.Semaphore(2)
    await asyncio.to_thread(get_db_manager)
    from core.delivery import initialize, recover_managed_files

    with get_db_manager().get_cursor() as cur:
        initialize(cur)
    await asyncio.to_thread(recover_managed_files)
    try:
        yield
    finally:
        from core.ai_chat_service import reset_ai_chat_service
        from core.middleware import disconnect

        await disconnect()
        reset_ai_chat_service()
        reset_db_manager()
        reset_ws_manager()


# 创建 FastAPI 应用
app = FastAPI(
    title="SoundBot API",
    description="SoundBot local audio library and editing backend",
    version=config.APP_VERSION,
    lifespan=lifespan
)

app.include_router(media_router)


@app.middleware("http")
async def authorize_desktop_request(request, call_next):
    if (os.getenv("SOUNDBOT_REQUIRE_DESKTOP_TOKEN") == "1"
        and request.method != "OPTIONS" and request.url.path != "/api/v1/health"):
        expected = os.getenv("SOUNDBOT_IPC_TOKEN", "")
        supplied = request.headers.get("x-soundbot-ipc", "")
        if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
            return JSONResponse(status_code=403, content={
                "code": "desktop_required", "message": "此请求需要从 SoundBot 桌面发起"
            })
    return await call_next(request)


@app.exception_handler(SoundBotAPIError)
async def soundbot_api_error_handler(_request, exc: SoundBotAPIError):
    return JSONResponse(status_code=exc.status_code, content=exc.payload)


@app.exception_handler(HTTPException)
async def http_error_handler(_request, exc: HTTPException):
    if isinstance(exc.detail, dict) and exc.detail.get("code"):
        payload = {
            "code": str(exc.detail["code"]),
            "message": str(exc.detail.get("message") or "请求失败"),
            "retryable": bool(exc.detail.get("retryable", False)),
            "details": dict(exc.detail.get("details") or {}),
        }
    else:
        payload = {
            "code": f"http_{exc.status_code}",
            "message": str(exc.detail or "请求失败"),
            "retryable": exc.status_code in {408, 425, 429, 502, 503, 504},
            "details": {},
        }
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request, exc: RequestValidationError):
    # Exclude Pydantic's raw input values: configuration requests may contain
    # credentials that must never be echoed into renderer-visible responses.
    issues = [
        {
            "field": ".".join(str(part) for part in error.get("loc", ())),
            "message": str(error.get("msg", "invalid value")),
            "type": str(error.get("type", "validation_error")),
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "请求参数无效",
            "retryable": False,
            "details": {"issues": issues},
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_request, exc: Exception):
    logger.exception("未处理的 API 异常: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "code": "internal_error",
            "message": "后端发生未预期错误，请查看日志",
            "retryable": False,
            "details": {},
        },
    )

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=["X-SoundBot-Artwork"],
)


# ==================== WebSocket 端点 ====================

@app.websocket("/ws/scan/{client_id}")
async def websocket_scan_progress(websocket: WebSocket, client_id: str):
    """
    WebSocket 端点：接收扫描进度推送

    前端通过此 WebSocket 接收实时扫描进度。

    接收消息格式:
    - {"type": "cancel", "task_id": "xxx"} - 取消扫描任务
    - {"type": "ping"} - 心跳检测

    发送消息格式:
    - scan_progress: {"type": "scan_progress", "task_id": "xxx", "data": {...}}
    - scan_complete: {"type": "scan_complete", "task_id": "xxx", "data": {...}}
    - scan_error: {"type": "scan_error", "task_id": "xxx", "data": {...}}
    """
    logger.info(f"[WS] WebSocket 连接尝试: client_id={client_id}, origin={websocket.headers.get('origin')}")
    ws_manager = get_ws_manager()
    await ws_manager.connect(websocket, client_id)
    logger.info(f"[WS] WebSocket 连接成功: client_id={client_id}")
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            msg_type = message.get("type", "")

            if msg_type == "cancel":
                task_id = message.get("task_id")
                if task_id:
                    ws_manager.cancel_task(task_id)
                    logger.info(f"任务已取消: {task_id}")

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info(f"[WS] WebSocket 断开: client_id={client_id}")
        ws_manager.disconnect(websocket, client_id)
    except json.JSONDecodeError:
        logger.warning(f"[WS] 无效的 JSON 消息 from {client_id}")
    except Exception as e:
        logger.error(f"[WS] WebSocket 错误: {e}")
        ws_manager.disconnect(websocket, client_id)


# ==================== 健康检查 ====================

@app.get("/api/v1/health", response_model=schemas.HealthResponse)
async def health_check():
    """
    健康检查接口

    返回服务状态、版本号和当前设备信息
    """
    from core.audio_service import get_audio_service

    audio_runtime = get_audio_service().runtime_status()
    audio_decoder_available = bool(audio_runtime["available"])
    return schemas.HealthResponse(
        status="healthy" if audio_decoder_available else "degraded",
        version=config.APP_VERSION,
        device="cpu",
        model_loaded=False,
        audio_decoder_available=audio_decoder_available,
    )


@app.get(
    "/api/v1/runtime/capabilities",
    response_model=schemas.RuntimeCapabilitiesResponse,
)
async def get_runtime_capabilities():
    """Report required and optional frozen-runtime capabilities.

    The response is safe to show in a startup diagnostic: it never includes
    local paths, environment variables, exception messages, or credentials.
    """
    from core.audio_service import get_audio_service

    audio_runtime = get_audio_service().runtime_status()
    return schemas.RuntimeCapabilitiesResponse(
        status="ready" if audio_runtime["available"] else "degraded",
        version=config.APP_VERSION,
        audio_decoder=schemas.AudioDecoderCapability(**audio_runtime),
        semantic_search=schemas.SemanticSearchCapability(
            available=False
        ),
        supported_audio_extensions=sorted(config.AUDIO_FORMAT_CAPABILITIES),
    )


@app.get("/api/v1/model/status")
async def get_model_status():
    return {"status": "success", "model_status": {"status": "disabled"},
            "embedder_available": False}


@app.post("/api/v1/scan", deprecated=True)
async def scan_and_index(request: schemas.ScanRequest):
    """Reject the pre-v0.2 path that wrote Chroma without durable SQLite state."""
    raise SoundBotAPIError(
        "legacy_scan_removed",
        "该入口已停用；请使用项目导入作业接口",
        status_code=410,
        details={"replacement": "/api/v1/projects/{project_id}/imports"},
    )


# ==================== 仅扫描文件（不建索引）====================
@app.post("/api/v1/scan-only", response_model=schemas.ScanResponse)
async def scan_only(request: schemas.ScanRequest):
    """
    仅扫描音频文件，不建立索引（用于没有模型的情况）
    """
    # 验证路径安全性，防止路径遍历攻击
    if not validate_path(request.folder_path):
        raise HTTPException(status_code=400, detail="路径包含非法字符")
    
    folder = Path(request.folder_path)
    
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"文件夹不存在: {request.folder_path}")
    
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不是文件夹: {request.folder_path}")
    
    try:
        from core.scanner import AudioScanner
        scanner = AudioScanner()
        audio_files = await _run_audio_work(
            scanner.scan, str(folder), request.recursive
        )
        
        audio_file_list = []
        for f in audio_files:
            audio_file_list.append(schemas.AudioFile(
                path=f.path,
                filename=f.filename,
                duration=f.duration,
                sample_rate=f.sample_rate,
                channels=f.channels,
                format=f.format,
                size=f.size
            ))
        
        return schemas.ScanResponse(
            total=len(audio_file_list),
            files=audio_file_list
        )
        
    except Exception as e:
        logger.error(f"扫描失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 异步导入（带进度推送） ====================

def _require_library(project_id):
    db = get_db_manager()
    if not is_safe_project_id(project_id) or not db.get_project(project_id):
        raise SoundBotAPIError("project_not_found", "音效库不存在", status_code=404)
    if project_id in _projects_deleting:
        raise SoundBotAPIError("project_deleting", "音效库正在移除", status_code=409)
    return db


async def _run_catalog_job(job_id, project_id, client_id="default"):
    async with _get_project_index_lock(project_id):
        async with app.state.catalog_slots:
            await asyncio.to_thread(catalog.run_import, get_db_manager(), job_id)
    job = await asyncio.to_thread(catalog.import_status, get_db_manager(), job_id)
    if job and job["state"] == "completed":
        await get_ws_manager().send_scan_complete(
            client_id, job_id, job["processed"], job["imported"], job["unchanged"]
        )


async def _start_folder_import(project_id, folder_path, recursive, client_id, background_tasks):
    db = _require_library(project_id)
    if not validate_path(folder_path):
        raise SoundBotAPIError("invalid_folder", "无效的来源目录")
    folder = Path(folder_path).expanduser()
    if not folder.is_absolute() or not folder.is_dir():
        raise SoundBotAPIError("invalid_folder", "来源目录不可用")
    job_id = await asyncio.to_thread(
        catalog.create_import, db, project_id, root=str(folder.resolve()), recursive=recursive
    )
    background_tasks.add_task(_run_catalog_job, job_id, project_id, client_id)
    return {"task_id": job_id, "job_id": job_id, "project_id": project_id, "state": "pending"}


@app.post("/api/v1/import/async", deprecated=True)
async def import_folder_async(request: schemas.ScanRequest, background_tasks: BackgroundTasks,
                              client_id: str = "default"):
    return await _start_folder_import(config.CURRENT_PROJECT_ID, request.folder_path,
                                      request.recursive, client_id, background_tasks)


@app.post("/api/v1/import/files", deprecated=True)
async def import_selected_files(request: schemas.ImportFilesRequest, background_tasks: BackgroundTasks):
    project_id = request.project_id or config.CURRENT_PROJECT_ID
    db = _require_library(project_id)
    accepted, rejected = [], []
    for value in dict.fromkeys(request.file_paths):
        candidate = Path(value)
        if not validate_path(value) or not candidate.is_absolute():
            rejected.append({"path": value, "reason": "invalid_path"})
        elif candidate.suffix.lower() not in config.AUDIO_FORMAT_CAPABILITIES:
            rejected.append({"path": value, "reason": "unsupported_format"})
        else:
            accepted.append(value)
    if not accepted:
        raise SoundBotAPIError("no_importable_files", "没有可导入的音频文件", details={"rejected": rejected})
    job_id = await asyncio.to_thread(catalog.create_import, db, project_id, paths=accepted)
    background_tasks.add_task(_run_catalog_job, job_id, project_id, request.client_id or "default")
    return {"job_id": job_id, "task_id": job_id, "project_id": project_id,
            "state": "pending", "total": len(accepted), "rejected": rejected}


@app.post("/api/v1/projects/{project_id}/imports")
async def import_project_files(project_id: str, request: schemas.ProjectImportRequest,
                               background_tasks: BackgroundTasks):
    if request.folder_path is not None:
        return await _start_folder_import(project_id, request.folder_path, request.recursive,
                                          request.client_id or "default", background_tasks)
    return await import_selected_files(
        schemas.ImportFilesRequest(project_id=project_id, client_id=request.client_id,
                                   file_paths=request.file_paths), background_tasks
    )


@app.get("/api/v1/jobs/{job_id}")
async def get_background_job(job_id: str):
    job = await asyncio.to_thread(catalog.import_status, get_db_manager(), job_id)
    if not job:
        raise SoundBotAPIError("job_not_found", "任务不存在", status_code=404)
    return job


@app.get("/api/v1/projects/{project_id}/jobs")
async def list_library_jobs(project_id: str):
    db = _require_library(project_id)
    return await asyncio.to_thread(catalog.list_jobs, db, project_id)


@app.delete("/api/v1/jobs/{job_id}")
async def cancel_background_job(job_id: str):
    db = get_db_manager()
    with db.get_cursor() as cur:
        cur.execute("""UPDATE jobs SET cancel_requested=1,
            stage=CASE WHEN state='pending' THEN 'paused' ELSE stage END,
            state=CASE WHEN state='pending' THEN 'cancelled' ELSE state END
            WHERE id=? AND state IN ('pending','running')""", (job_id,))
    return await get_background_job(job_id)


@app.post("/api/v1/jobs/{job_id}/resume")
async def resume_background_job(job_id: str, background_tasks: BackgroundTasks):
    db = get_db_manager()
    job = await get_background_job(job_id)
    _require_library(job["project_id"])
    with db.get_cursor() as cur:
        row = cur.execute("""UPDATE jobs SET state='pending',stage='queued',cancel_requested=0,
            error_message=NULL,error_code=NULL WHERE id=? AND kind='catalog_import'
            AND state IN ('cancelled','failed') RETURNING id""", (job_id,)).fetchone()
        if not row:
            raise SoundBotAPIError("job_not_resumable", "此任务无法继续", status_code=409)
    background_tasks.add_task(_run_catalog_job, job_id, job["project_id"])
    return {"job_id": job_id}


@app.post("/api/v1/jobs/{job_id}/retry")
async def retry_background_job(job_id: str, background_tasks: BackgroundTasks):
    job = await get_background_job(job_id)
    project_id = job["project_id"]
    db = _require_library(project_id)
    if job["kind"] != "catalog_import" or job["state"] != "completed":
        raise SoundBotAPIError("job_not_retryable", "请等待导入结束后重试失败项", status_code=409)
    try:
        retry_id = await asyncio.to_thread(catalog.retry_import, db, job_id, project_id)
    except ValueError as exc:
        raise SoundBotAPIError("job_not_retryable", str(exc), status_code=409) from exc
    background_tasks.add_task(_run_catalog_job, retry_id, project_id)
    return {"job_id": retry_id}


@app.get("/api/v1/projects/{project_id}/catalog")
async def list_library_items(project_id: str, q: str = Query("", max_length=300),
                             folder: str = None, group: str = None, tag: str = None,
                             ucs: str = None, favorite: bool = False, format: str = None,
                             channels: int = None, min_duration: float = None, max_duration: float = None,
                             sort: str = "newest", limit: int = Query(100, ge=1, le=200),
                             cursor: str = None):
    db = _require_library(project_id)
    decoded = None
    if cursor is not None:
        try:
            decoded = json.loads(cursor)
            if (not isinstance(decoded, list) or len(decoded) != 2 or
                    type(decoded[1]) is not int or type(decoded[0]) not in (str, float, int)):
                raise ValueError
        except ValueError:
            raise SoundBotAPIError("invalid_cursor", "无效的分页位置")
    return await asyncio.to_thread(
        catalog.list_items, db, project_id, query=q, folder=folder, group=group, tag=tag,
        ucs=ucs, favorite=favorite, audio_format=format, channels=channels, min_duration=min_duration,
        max_duration=max_duration, sort=sort, limit=limit, cursor=decoded, count=not cursor
    )


@app.get("/api/v1/projects/{project_id}/catalog/folders")
async def list_library_folders(project_id: str, folder: str = None):
    return await asyncio.to_thread(catalog.folders, _require_library(project_id), project_id, folder)


@app.get("/api/v1/projects/{project_id}/catalog/items/{file_id}")
async def get_library_item(project_id: str, file_id: str):
    result = await asyncio.to_thread(catalog.list_items, _require_library(project_id), project_id,
                                     file_id=file_id, limit=1)
    if not result["files"]:
        raise SoundBotAPIError("file_not_found", "素材已移出音效库", status_code=404)
    return result["files"][0]


@app.get("/api/v1/projects/{project_id}/catalog/facets")
async def get_library_facets(project_id: str):
    return await asyncio.to_thread(catalog.facets, _require_library(project_id), project_id)


@app.get("/api/v1/projects/{project_id}/catalog/items/{file_id}/groups")
async def get_asset_groups(project_id: str, file_id: str):
    return await asyncio.to_thread(catalog.asset_groups, _require_library(project_id), project_id, file_id)


@app.post("/api/v1/projects/{project_id}/sources/{source_id}/scan")
async def scan_library_source(project_id: str, source_id: str, background_tasks: BackgroundTasks):
    db = _require_library(project_id)
    with db.get_cursor() as cur:
        source = cur.execute("SELECT path,recursive FROM library_sources WHERE id=? AND project_id=?",
                             (source_id, project_id)).fetchone()
    if not source:
        raise SoundBotAPIError("source_not_found", "来源目录不存在", status_code=404)
    job_id = await asyncio.to_thread(catalog.create_import, db, project_id, source_id=source_id)
    background_tasks.add_task(_run_catalog_job, job_id, project_id)
    return {"task_id": job_id, "job_id": job_id, "project_id": project_id, "state": "pending"}


class LibraryItemUpdate(BaseModel):
    ids: List[str]
    action: str
    value: Any = None


@app.post("/api/v1/projects/{project_id}/catalog/update")
async def update_library_items(project_id: str, request: LibraryItemUpdate):
    db = _require_library(project_id)
    if not 1 <= len(request.ids) <= 500:
        raise SoundBotAPIError("invalid_selection", "请一次选择 1 至 500 条素材")
    if request.action == "tags":
        if not isinstance(request.value, list) or not all(
            isinstance(item, str) and 0 < len(item) <= 200 for item in request.value
        ) or len(request.value) > 100:
            raise SoundBotAPIError("invalid_tags", "标签无效")
    if request.action == "metadata":
        value = request.value
        if len(request.ids) != 1 or not isinstance(value, dict) or not all(
            isinstance(value.get(key), str) and len(value[key]) <= limit
            for key, limit in (("title", 255), ("description", 4096))
        ):
            raise SoundBotAPIError("invalid_metadata", "名称或描述无效")
        tags = value.get("tags")
        if tags is not None and (
            not isinstance(tags, list) or len(tags) > 100
            or not all(isinstance(tag, str) and 0 < len(tag) <= 200 for tag in tags)
        ):
            raise SoundBotAPIError("invalid_tags", "标签无效")
    try:
        changed = await asyncio.to_thread(catalog.mutate_items, db, project_id,
                                          request.ids, request.action, request.value)
        return {"updated": changed}
    except ValueError as exc:
        raise SoundBotAPIError("invalid_action", str(exc)) from exc


class RelocateRequest(BaseModel):
    destination: str
    file_id: Optional[str] = None
    source_id: Optional[str] = None


@app.post("/api/v1/projects/{project_id}/catalog/relocate")
async def relocate_library_items(project_id: str, request: RelocateRequest, background_tasks: BackgroundTasks):
    import sqlite3

    db = _require_library(project_id)
    if bool(request.file_id) == bool(request.source_id) or not Path(request.destination).is_absolute():
        raise SoundBotAPIError("invalid_relocation", "请选择素材或来源目录的新位置")
    try:
        async with _get_project_index_lock(project_id):
            updated = await asyncio.to_thread(
                catalog.relocate, db, project_id, request.destination,
                file_id=request.file_id, source_id=request.source_id
            )
            response = {"updated": updated}
            if request.source_id:
                job_id = await asyncio.to_thread(catalog.create_import, db, project_id, source_id=request.source_id)
                background_tasks.add_task(_run_catalog_job, job_id, project_id)
                response["job_id"] = job_id
            return response
    except (OSError, ValueError) as exc:
        raise SoundBotAPIError("invalid_relocation", str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise SoundBotAPIError("relocation_conflict", "新位置已有库记录", status_code=409) from exc


@app.get("/api/v1/db/files")
async def get_all_db_files(
    limit: int = Query(default=200, ge=1, le=500),
    cursor: Optional[int] = Query(default=None, ge=1),
):
    """
    从 SQLite 获取当前工程的所有文件列表（启动时加载）

    返回格式：
    {
        "total": 1000,
        "files": [
            {
                "path": "/path/to/file.wav",
                "filename": "file.wav",
                "duration": 12.5,
                "sample_rate": 48000,
                "channels": 2,
                "size": 1234567,
                "peaks": [0.1, 0.2, ...],  # 波形峰值数组
                "tags": ["标签1", "标签2"],
                "created_at": "2024-01-01T00:00:00"
            }
        ]
    }
    """
    try:
        db_manager = get_db_manager()
        current_project_id = getattr(config, 'CURRENT_PROJECT_ID', 'default')
        items, next_cursor = db_manager.get_files_cursor_page(
            current_project_id, limit=limit, before_id=cursor
        )
        for item in items:
            item["size"] = item.pop("file_size")
            # Compatibility marker: absence is null, never an empty truthy array.
            item["peaks"] = None
        return {
            "project_id": current_project_id,
            "total": db_manager.get_file_count(current_project_id),
            "files": items,
            "next_cursor": next_cursor,
        }
    except Exception as e:
        logger.error(f"获取文件列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/db/file/{path:path}")
async def get_db_file(path: str):
    """
    获取单个文件详情

    - **path**: URL 编码的文件路径
    """
    file_path = path

    db_manager = get_db_manager()
    current_project_id = getattr(config, 'CURRENT_PROJECT_ID', 'default')
    record = db_manager.get_file(file_path, current_project_id)

    if not record:
        raise HTTPException(status_code=404, detail="文件不存在")

    return {
        "id": record.file_uuid,
        "path": record.path,
        "filename": record.filename,
        "duration": record.duration,
        "sample_rate": record.sample_rate,
        "channels": record.channels,
        "size": record.file_size,
        "peaks": record.get_peaks(),
        "tags": record.get_tags(),
        "created_at": record.created_at
    }


@app.put("/api/v1/db/file/{path:path}/tags")
async def update_file_tags(path: str, payload: schemas.TagsRequest | List[str] = Body(...)):
    tags = payload.tags if isinstance(payload, schemas.TagsRequest) else payload
    if not get_db_manager().update_tags(path, tags, config.CURRENT_PROJECT_ID):
        raise SoundBotAPIError("file_not_found", "素材不存在", status_code=404)
    return {"success": True}


@app.delete("/api/v1/db/file/{path:path}")
async def delete_db_file(path: str):
    db = get_db_manager()
    project_id = config.CURRENT_PROJECT_ID
    record = db.get_file(path, project_id)
    if not record:
        raise SoundBotAPIError("file_not_found", "素材不存在", status_code=404)
    await asyncio.to_thread(catalog.mutate_items, db, project_id, [record.file_uuid], "remove", None)
    return {"success": True}


@app.get("/api/v1/db/stats")
async def get_db_stats():
    """
    获取数据库统计信息
    """
    try:
        db_manager = get_db_manager()
        current_project_id = getattr(config, 'CURRENT_PROJECT_ID', 'default')
        return {
            "project_id": current_project_id,
            "total_files": db_manager.get_file_count(current_project_id),
            "total_duration": db_manager.get_total_duration(current_project_id)
        }
    except Exception as e:
        logger.error(f"获取统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 语义搜索 ====================


@app.post("/api/v1/search", response_model=schemas.SearchResponse)
async def search_audio(request: schemas.SearchRequest):
    payload = await asyncio.to_thread(
        catalog.list_items, _require_library(request.project_id), request.project_id,
        query=request.query, audio_format=request.format, channels=request.channels,
        sample_rate=request.sample_rate, min_duration=request.min_duration,
        max_duration=request.max_duration, limit=request.page_size,
        offset=(request.page - 1) * request.page_size, count=True
    )
    results = []
    for item in payload["files"]:
        score = min(1.0, abs(item["rank"]))
        results.append(schemas.SearchResult(
            audio_file=schemas.AudioFile(**item), score=score, distance=1.0-score,
            metadata={"project_id": request.project_id, "keyword_score": score, "tags": item["tags"]},
        ))
    total = payload["total"]
    return schemas.SearchResponse(query=request.query, total=total, page=request.page,
                                  page_size=request.page_size, results=results,
                                  total_pages=(total + request.page_size - 1) // request.page_size)


@app.post("/api/v1/search/async")
async def search_audio_async(request: schemas.SearchRequest, background_tasks: BackgroundTasks):
    return await search_audio(request)


@app.get("/api/waveform")
async def get_waveform(path: str = Query(..., description="音频文件路径")):
    """
    获取音频波形数据

    优先从 SQLite 数据库读取缓存的波形数据，如果没有则实时计算并缓存。
    将原始波形降采样到 2000 个峰值点，用于前端波形显示。

    返回格式：
    {
        "peaks": [0.1, 0.4, -0.3, ...],  # 降采样后的峰值数组
        "duration": 12.4,                  # 时长（秒）
        "sample_rate": 48000,               # 采样率
        "channels": 2,                       # 声道数
        "cached": true                       # 是否从缓存读取
    }
    """
    current_project_id = getattr(config, 'CURRENT_PROJECT_ID', 'default') or 'default'
    return await _waveform_payload(path, current_project_id)


async def _waveform_payload(file_path: str, project_id: str, *, cached_only: bool = False,
                            background: bool = False) -> Dict[str, Any]:
    from core.audio_service import get_audio_service, AudioServiceError

    service = get_audio_service()
    db_manager = get_db_manager()
    record = db_manager.get_file(file_path, project_id)
    work = _run_thumbnail_work if background else _run_audio_work
    try:
        fingerprint = await work(service.fingerprint, file_path)
        cached_peaks = record.get_peaks() if record else None
        artifact = (
            db_manager.get_artifact(project_id, record.file_uuid, "waveform")
            if record else None
        )
        if (
            record
            and cached_peaks
            and len(cached_peaks) == config.WAVEFORM_PEAK_COUNT
            and all(
                isinstance(value, (int, float))
                and math.isfinite(value)
                and 0 <= value <= 1
                for value in cached_peaks
            )
            and artifact
            and artifact.get("state") == "ready"
            and record.source_fingerprint == fingerprint.source_key
            and record.waveform_fingerprint == fingerprint.key
            and str(record.waveform_version) == str(fingerprint.waveform_version)
        ):
            return {
                "file_id": record.file_uuid,
                "peaks": cached_peaks,
                "duration": record.duration,
                "sample_rate": record.sample_rate,
                "channels": record.channels,
                "cached": True,
                "source_fingerprint": fingerprint.source_key,
                "waveform_version": fingerprint.waveform_version,
            }

        if cached_only:
            raise SoundBotAPIError("waveform_not_cached", "波形尚未生成", status_code=404)
        waveform = await work(service.waveform, file_path)
        metadata = (
            None if record else await _run_audio_work(service.probe, file_path)
        )
        if record:
            updated = db_manager.update_peaks(
                file_path,
                waveform.peaks,
                project_id,
                source_fingerprint=waveform.fingerprint.source_key,
                waveform_fingerprint=waveform.fingerprint.key,
                waveform_version=int(waveform.fingerprint.waveform_version),
            )
            if not updated:
                raise SoundBotAPIError(
                    "waveform_cache_update_failed",
                    "波形已计算，但缓存状态更新失败",
                    status_code=503,
                    retryable=True,
                )
        return {
            "file_id": record.file_uuid if record else None,
            "peaks": waveform.peaks,
            "duration": waveform.duration,
            "sample_rate": waveform.sample_rate,
            "channels": record.channels if record else metadata.channels,
            "cached": False,
            "source_fingerprint": waveform.fingerprint.source_key,
            "waveform_version": waveform.fingerprint.waveform_version,
        }
    except AudioServiceError as exc:
        if record:
            db_manager.set_artifact_state(
                project_id,
                record.file_uuid,
                "waveform",
                "failed",
                error_code=exc.code,
                error_message=exc.message,
            )
        raise SoundBotAPIError(
            exc.code,
            exc.message,
            status_code=404 if exc.code == "audio_not_found" else 422,
            retryable=exc.retryable,
            details=exc.details,
        )


@app.get("/api/v1/files/{file_id}/waveform")
async def get_file_waveform(file_id: str, project_id: Optional[str] = None):
    effective_project = project_id or getattr(config, 'CURRENT_PROJECT_ID', 'default')
    record = get_db_manager().get_file_by_uuid(file_id, effective_project)
    if not record:
        raise SoundBotAPIError("file_not_found", "文件不存在", status_code=404)
    return await _waveform_payload(record.path, effective_project)


@app.get("/api/v1/files/{file_id}/artwork")
async def get_file_artwork(file_id: str, project_id: str):
    from core.audio_service import AudioServiceError, get_audio_service

    record = _require_library(project_id).get_file_by_uuid(file_id, project_id)
    if not record:
        raise SoundBotAPIError("file_not_found", "文件不存在", status_code=404)
    try:
        loop = asyncio.get_running_loop()
        cover = await loop.run_in_executor(_artwork_executor, get_audio_service().artwork, record.path)
    except AudioServiceError as exc:
        raise SoundBotAPIError("artwork_unavailable", "无法读取封面", status_code=422) from exc
    if cover:
        info = json.dumps({"format": cover.format, "width": cover.width, "height": cover.height,
                           "size": cover.size, "role": cover.role, "description": cover.description}, ensure_ascii=True)
        return Response(cover.data, media_type=cover.mime,
                        headers={"Cache-Control": "no-store", "X-SoundBot-Artwork": info})
    return Response(status_code=204)


@app.get("/api/v1/files/{file_id}/details")
async def get_file_details(file_id: str, project_id: str):
    from core.audio_service import AudioServiceError, get_audio_service

    record = _require_library(project_id).get_file_by_uuid(file_id, project_id)
    if not record:
        raise SoundBotAPIError("file_not_found", "文件不存在", status_code=404)
    try:
        return await _run_audio_work(get_audio_service().details, record.path)
    except AudioServiceError as exc:
        raise SoundBotAPIError("metadata_unavailable", "无法读取源文件信息", status_code=422) from exc


@app.post("/api/v1/waveforms/batch")
async def get_waveforms_batch(request: schemas.WaveformBatchRequest):
    project_id = request.project_id or getattr(config, 'CURRENT_PROJECT_ID', 'default')
    semaphore = asyncio.Semaphore(4)

    async def load(file_id: str) -> Dict[str, Any]:
        async with semaphore:
            record = get_db_manager().get_file_by_uuid(file_id, project_id)
            if not record:
                return {
                    "file_id": file_id,
                    "ok": False,
                    "error": {
                        "code": "file_not_found",
                        "message": "文件不存在",
                        "retryable": False,
                        "details": {},
                    },
                }
            if request.cached_only and not record.waveform_fingerprint:
                return {"file_id": file_id, "ok": False, "error": {"code": "waveform_not_cached"}}
            try:
                payload = await _waveform_payload(record.path, project_id, cached_only=request.cached_only,
                                                  background=True)
                return {"file_id": file_id, "ok": True, **payload}
            except SoundBotAPIError as exc:
                return {"file_id": file_id, "ok": False, "error": exc.payload}

    items = await asyncio.gather(*(load(file_id) for file_id in request.file_ids))
    return {"project_id": project_id, "items": items}


@app.get("/api/v1/files/{file_id}/playback-source")
async def get_playback_source(file_id: str, project_id: Optional[str] = None):
    """Return an original path or a cached PCM WAV for Chromium-incompatible files."""
    from core.audio_service import get_audio_service, AudioServiceError

    effective_project = project_id or getattr(config, 'CURRENT_PROJECT_ID', 'default')
    record = get_db_manager().get_file_by_uuid(file_id, effective_project)
    if not record:
        raise SoundBotAPIError("file_not_found", "文件不存在", status_code=404)
    service = get_audio_service()
    try:
        if service.requires_playback_transcode(record.path):
            source = await _run_audio_work(service.prepare_playback_wav, record.path)
            mode = "transcoded_wav"
        else:
            source = Path(record.path)
            mode = "original"
        return {
            "file_id": file_id,
            "path": str(source),
            "mode": mode,
            "source_fingerprint": (
                await _run_audio_work(service.fingerprint, record.path)
            ).source_key,
        }
    except AudioServiceError as exc:
        raise SoundBotAPIError(
            exc.code,
            exc.message,
            status_code=422,
            retryable=exc.retryable,
            details=exc.details,
        )


# ==================== 通用音频文件服务 ====================

@app.get("/api/v1/audio/{file_path:path}")
async def get_audio(file_path: str = PathParam(..., description="音频文件路径")):
    """
    提供音频文件播放服务（通用路由，放在子路由之后）

    支持范围请求（用于前端波形显示和流式播放）
    """
    audio_file = config.validate_audio_path(file_path)

    # 获取文件大小
    file_size = audio_file.stat().st_size

    capability = config.AUDIO_FORMAT_CAPABILITIES.get(audio_file.suffix.lower(), {})
    mime_type = capability.get('mime_type', 'application/octet-stream')

    # 创建文件响应，支持范围请求
    return FileResponse(
        path=str(audio_file),
        media_type=mime_type,
        filename=audio_file.name
    )


# ==================== 索引状态 ====================

@app.get("/api/v1/index/status", response_model=schemas.IndexStatus)
async def get_index_status():
    total = get_db_manager().get_file_count(config.CURRENT_PROJECT_ID)
    return schemas.IndexStatus(total_files=total, indexed_files=total)


@app.get("/api/v1/projects/{project_id}/index/status")
async def get_project_index_status(project_id: str):
    db = _require_library(project_id)
    total = db.get_file_count(project_id)
    return {"project_id": project_id, "total_files": total,
            "keyword": {"available": True, "indexed": total},
            "artifacts": db.get_artifact_counts(project_id)}


@app.post("/api/v1/projects/{project_id}/index/rebuild")
async def rebuild_project_index(project_id: str, background_tasks: BackgroundTasks):
    db = _require_library(project_id)
    job_id = db.create_job(project_id, "keyword_rebuild")
    background_tasks.add_task(catalog.rebuild_job, db, job_id)
    return {"job_id": job_id, "state": "pending"}


@app.get("/api/v1/files")
async def get_indexed_files(
    project_id: Optional[str] = None,
    limit: int = Query(default=200, ge=1, le=500),
    cursor: Optional[int] = Query(default=None, ge=1),
):
    """获取工程内的分页元数据，波形按可见项另行加载。"""
    try:
        effective_project = project_id or getattr(config, 'CURRENT_PROJECT_ID', 'default')
        db_manager = get_db_manager()
        if not is_safe_project_id(effective_project) or not db_manager.get_project(effective_project):
            raise SoundBotAPIError("project_not_found", "工程不存在", status_code=404)
        files, next_cursor = db_manager.get_files_cursor_page(
            effective_project, limit=limit, before_id=cursor
        )
        for item in files:
            item["size"] = item.pop("file_size")
        return {
            "project_id": effective_project,
            "total": db_manager.get_file_count(effective_project),
            "files": files,
            "next_cursor": next_cursor,
        }
    except SoundBotAPIError:
        raise
    except Exception as e:
        logger.error(f"获取文件列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/projects")
async def create_project(request: schemas.CreateProjectRequest):
    """
    创建新工程

    - **name**: 工程名称
    - **description**: 工程描述
    """
    try:
        import uuid
        project_id = str(uuid.uuid4())

        db_manager = get_db_manager()
        success = db_manager.create_project(
            project_id=project_id,
            name=request.name,
            description=request.description,
        )

        if not success:
            raise SoundBotAPIError("project_create_failed", "创建工程失败")

        return {
            "success": True,
            "project_id": project_id,
            "message": f"工程 '{request.name}' 创建成功"
        }
    except SoundBotAPIError:
        raise
    except Exception as e:
        logger.error(f"创建工程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/projects")
async def get_all_projects():
    """
    获取所有工程列表
    """
    try:
        db_manager = get_db_manager()
        projects = db_manager.get_all_projects()

        # 添加每个工程的文件数量
        for project in projects:
            project['file_count'] = db_manager.get_project_file_count(project['id'])

        return {
            "total": len(projects),
            "projects": projects
        }
    except Exception as e:
        logger.error(f"获取工程列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# 注意：/api/v1/projects/recent 必须在 /api/v1/projects/{project_id} 之前定义
# 否则 FastAPI 会将 "recent" 匹配为 project_id 参数
@app.get("/api/v1/projects/recent")
async def get_recent_projects(limit: int = 10):
    """
    获取最近使用的工程列表
    """
    try:
        db_manager = get_db_manager()
        projects = db_manager.get_recent_projects(limit)

        # 添加每个工程的文件数量
        for project in projects:
            project['file_count'] = db_manager.get_project_file_count(project['id'])

        return {
            "total": len(projects),
            "projects": projects
        }
    except Exception as e:
        logger.error(f"获取最近工程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/projects/{project_id}")
async def get_project(project_id: str):
    """
    获取工程详情
    """
    try:
        db_manager = get_db_manager()
        project = db_manager.get_project(project_id)

        if not project:
            raise SoundBotAPIError(
                "project_not_found", "工程不存在", status_code=404
            )

        # 添加文件数量
        project['file_count'] = db_manager.get_project_file_count(project_id)

        return project
    except SoundBotAPIError:
        raise
    except Exception as e:
        logger.error(f"获取工程详情失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/v1/projects/{project_id}")
async def update_project(project_id: str, request: schemas.UpdateProjectRequest):
    """
    更新工程信息
    """
    try:
        db_manager = get_db_manager()

        # 检查工程是否存在
        existing = db_manager.get_project(project_id)
        if not existing:
            raise HTTPException(status_code=404, detail="工程不存在")

        success = db_manager.update_project(
            project_id=project_id,
            name=request.name,
            description=request.description,
            settings=request.settings
        )

        if not success:
            raise HTTPException(status_code=400, detail="更新工程失败")

        return {
            "success": True,
            "message": "工程更新成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新工程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/v1/projects/{project_id}")
async def delete_project(project_id: str):
    db = _require_library(project_id)
    if project_id == "default":
        raise SoundBotAPIError("default_project_protected", "默认音效库不能移除")
    _projects_deleting.add(project_id)
    try:
        db.request_project_job_cancellation(project_id)
        async with _get_project_index_lock(project_id):
            if not await asyncio.to_thread(db.delete_project, project_id):
                raise SoundBotAPIError("project_delete_failed", "移除音效库失败", status_code=503)
        if config.CURRENT_PROJECT_ID == project_id:
            config.CURRENT_PROJECT_ID = "default"
        return {"success": True}
    finally:
        _projects_deleting.discard(project_id)


@app.post("/api/v1/projects/{project_id}/switch")
async def switch_project(project_id: str):
    db = _require_library(project_id)
    db.add_to_recent_projects(project_id)
    config.CURRENT_PROJECT_ID = project_id
    return {"success": True, "project_id": project_id,
            "project_name": db.get_project(project_id)["name"]}


@app.get("/api/v1/projects/{project_id}/files")
async def get_project_files(
    project_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    cursor: Optional[int] = Query(default=None, ge=1),
):
    """Compatibility alias for the metadata-only cursor-paginated file list."""
    db_manager = get_db_manager()
    if not is_safe_project_id(project_id):
        raise SoundBotAPIError("invalid_project_id", "工程 ID 非法", status_code=400)
    project = db_manager.get_project(project_id)
    if not project:
        raise SoundBotAPIError("project_not_found", "工程不存在", status_code=404)
    files, next_cursor = db_manager.get_files_cursor_page(
        project_id, limit=limit, before_id=cursor
    )
    for item in files:
        item["size"] = item.pop("file_size")
    return {
        "project_id": project_id,
        "project_name": project["name"],
        "total": db_manager.get_file_count(project_id),
        "files": files,
        "next_cursor": next_cursor,
    }


# ==================== 用户自定义文件夹 API ====================

class CreateFolderRequest(BaseModel):
    name: str
    description: Optional[str] = None
    color: Optional[str] = '#3b82f6'


class UpdateFolderRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    sort_order: Optional[int] = None


@app.get("/api/v1/projects/{project_id}/folders")
async def get_user_folders(project_id: str):
    """
    获取指定工程的所有用户自定义文件夹
    """
    try:
        db_manager = get_db_manager()

        # 检查工程是否存在
        project = db_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="工程不存在")

        folders = db_manager.get_user_folders(project_id)

        # 获取每个文件夹下的导入文件夹数量
        for folder in folders:
            mappings = db_manager.get_imported_folder_mappings(project_id, folder['id'])
            folder['imported_folder_count'] = len(mappings)
            folder['total_file_count'] = sum(m['file_count'] for m in mappings)

        return {
            "project_id": project_id,
            "total": len(folders),
            "folders": folders
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户文件夹失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/projects/{project_id}/folders")
async def create_user_folder(project_id: str, request: CreateFolderRequest):
    """
    创建用户自定义文件夹
    """
    try:
        async with _get_project_index_lock(project_id):
            db_manager = get_db_manager()
            if project_id in _projects_deleting:
                raise HTTPException(status_code=409, detail="工程正在删除")
            if not db_manager.get_project(project_id):
                raise HTTPException(status_code=404, detail="工程不存在")

            import uuid
            folder_id = f"folder_{uuid.uuid4().hex[:8]}"
            sort_order = len(db_manager.get_user_folders(project_id))
            success = db_manager.create_user_folder(
                folder_id=folder_id,
                project_id=project_id,
                name=request.name,
                description=request.description,
                color=request.color,
                sort_order=sort_order
            )
            if not success:
                raise HTTPException(status_code=400, detail="创建文件夹失败")
            return {
                "success": True,
                "folder_id": folder_id,
                "name": request.name,
                "message": f"文件夹 '{request.name}' 创建成功"
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建用户文件夹失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/v1/projects/{project_id}/folders/{folder_id}")
async def update_user_folder(project_id: str, folder_id: str, request: UpdateFolderRequest):
    db = _require_library(project_id)
    folder = db.get_user_folder(folder_id)
    if not folder or folder["project_id"] != project_id:
        raise SoundBotAPIError("folder_not_found", "分组不存在", status_code=404)
    if not db.update_user_folder(folder_id=folder_id, **request.model_dump(exclude_none=True)):
        raise SoundBotAPIError("folder_update_failed", "分组更新失败")
    return {"success": True}


@app.delete("/api/v1/projects/{project_id}/folders/{folder_id}")
async def delete_user_folder(project_id: str, folder_id: str):
    db = _require_library(project_id)
    folder = db.get_user_folder(folder_id)
    if not folder or folder["project_id"] != project_id:
        raise SoundBotAPIError("folder_not_found", "分组不存在", status_code=404)
    if not db.delete_user_folder(folder_id):
        raise SoundBotAPIError("folder_delete_failed", "分组移除失败")
    return {"success": True}


@app.get("/api/v1/projects/{project_id}/folder-mappings")
async def get_imported_folder_mappings(project_id: str, user_folder_id: Optional[str] = None):
    """
    获取导入文件夹的映射关系
    """
    try:
        db_manager = get_db_manager()

        # 检查工程是否存在
        project = db_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="工程不存在")

        mappings = db_manager.get_imported_folder_mappings(project_id, user_folder_id)

        return {
            "project_id": project_id,
            "total": len(mappings),
            "mappings": mappings
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取导入文件夹映射失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/ai/chat")
async def ai_chat(request: schemas.AIChatRequest):
    """
    AI 对话 - 自然语言搜索
    
    支持流式响应，前端需要使用 EventSource 接收。
    
    请求格式：
    - **message**: 用户消息
    - **history**: 对话历史（可选）
    - **top_k**: 返回结果数量（默认 20）
    - **threshold**: 相似度阈值（默认 0.1）
    - **locale**: 首选对话语言（可选）
    
    SSE 流式响应：
    - thinking: 正在分析
    - analyzing: 分析完成
    - searching: 正在搜索
    - results: 搜索结果
    - error: 错误
    - done: 完成
    """
    from core.ai_chat_service import get_ai_chat_service, stream_to_sse
    
    try:
        project_id = request.project_id or "default"
        db_manager = get_db_manager()
        if not is_safe_project_id(project_id) or not db_manager.get_project(project_id):
            raise SoundBotAPIError(
                "project_not_found", "工程不存在", status_code=404,
                details={"project_id": project_id},
            )
        chat_service = get_ai_chat_service(project_id)
        
        return StreamingResponse(
            stream_to_sse(chat_service.chat(
                message=request.message,
                conversation_history=request.history,
                top_k=request.top_k,
                threshold=request.threshold,
                project_id=project_id,
                locale=request.locale,
            )),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
    except SoundBotAPIError:
        raise
    except Exception as e:
        logger.error(f"AI Chat 请求失败: {e}")
        import traceback
        logger.error(f"详细错误: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"AI 服务暂时不可用: {str(e)}")


@app.get("/api/v1/ai/config")
async def get_ai_config():
    """
    获取 AI 配置
    
    返回当前 LLM 和 Embedding 的配置
    """
    try:
        from core.llm_config_manager import get_llm_config_manager
        
        config_manager = get_llm_config_manager()
        
        return {
            "success": True,
            "llm": {
                "provider": config_manager.get_llm_provider(),
                "config": config_manager.get_public_llm_config(),
                "available_services": config_manager.detect_available_local_services()
            },
            "embedding": {
                "provider": config_manager.get_embedding_provider(),
                "config": config_manager.get_public_embedding_config()
            }
        }
    except Exception as e:
        logger.error(f"获取 AI 配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/ai/config")
async def save_ai_config(request: schemas.AIConfigRequest):
    from core.llm_config_manager import get_llm_config_manager
    from core.llm_client import reset_llm_client
    from core.ai_chat_service import reset_ai_chat_service

    get_llm_config_manager().save_full_config(**request.model_dump())
    reset_llm_client()
    reset_ai_chat_service()
    return {"success": True, "message": "配置已保存", "text_index_stale": []}


@app.post("/api/v1/ai/config/test")
async def test_ai_config(request: schemas.AIConfigRequest):
    """Test the optional assistant connection."""
    from core.llm_config_manager import get_llm_config_manager

    result = await get_llm_config_manager().test_llm_connection(
        provider=request.llm_provider, provider_config=request.llm_config
    )
    return {**result, "llm": result}


@app.get("/api/v1/ai/status")
async def get_ai_status():
    """
    获取 AI 服务状态
    
    返回 LLM 和 Embedding 的可用状态
    """
    try:
        from core.llm_config_manager import get_llm_config_manager
        from core.llm_client import get_llm_client
        
        config_manager = get_llm_config_manager()
        
        # 检查 LLM 可用性
        llm_available = False
        llm_provider = config_manager.get_llm_provider()
        available_services = config_manager.detect_available_local_services()
        
        if llm_provider == "lm_studio":
            llm_available = available_services.get("lm_studio", False)
        elif llm_provider == "ollama":
            llm_available = available_services.get("ollama", False)
        else:
            # 外部 API，尝试连接
            try:
                llm_client = get_llm_client()
                llm_available = llm_client.is_available
            except:
                llm_available = False
        
        # 检查 Embedding 可用性
        embedding_available = False
        
        return {
            "success": True,
            "llm": {
                "available": llm_available,
                "provider": llm_provider
            },
            "embedding": {
                "available": embedding_available,
                "provider": config_manager.get_embedding_provider()
            }
        }
    except Exception as e:
        logger.error(f"获取 AI 状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 主入口 ====================

if __name__ == "__main__":
    # PyInstaller 打包后的 Windows 多进程支持
    import sys
    import socket
    import multiprocessing
    multiprocessing.freeze_support()

    # 自动寻找可用端口（避免端口冲突）
    _port = config.PORT
    for _attempt in range(20):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
                _s.bind((config.HOST, _port))
                break
        except OSError:
            _port += 1
    else:
        print(f"[FATAL] No free port found in range {config.PORT}–{config.PORT + 19}", flush=True)
        sys.exit(1)

    if _port != config.PORT:
        print(f"[SoundBot] Port {config.PORT} busy, using port {_port}", flush=True)
    print(f"[SoundBot] BOUND_PORT={_port}", flush=True)

    # 必须传 app 对象而非字符串 "main:app"
    # 字符串形式会让 uvicorn 尝试 importlib.import_module("main")，
    # 在 PyInstaller 冻结环境中会失败：Could not import module "main"
    uvicorn.run(
        app,
        host=config.HOST,
        port=_port,
        log_level="info"
    )
