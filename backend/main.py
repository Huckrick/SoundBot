# -*- coding: utf-8 -*-
# SoundBot - AI 音效管理器
# Copyright (C) 2026 Nagisa_Huckrick (胡杨)
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
from pathlib import Path
from typing import Optional, List, Dict, Any, Mapping, Sequence
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
from core.indexer import (
    get_indexer,
    AudioIndexer,
    reset_all_indexers,
    reset_chroma_client,
)
from core.searcher import get_searcher, AudioSearcher, reset_searcher
from core.embedder import get_embedder, reset_embedder, is_embedder_available, is_embedder_loaded
from core.database import (
    get_db_manager,
    reset_db_manager,
    AudioFileRecord,
    is_safe_project_id,
    canonicalize_path,
    canonical_path_is_within,
)
from core.websocket_manager import (
    get_ws_manager,
    reset_ws_manager,
)
from utils.logger import logger

# 线程池用于 CPU 密集型任务
_executor = ThreadPoolExecutor(max_workers=4)
_model_executor = ThreadPoolExecutor(max_workers=1)
_project_index_locks: Dict[str, asyncio.Lock] = {}
_projects_deleting: set[str] = set()
_automatic_index_tasks: Dict[str, Dict[str, Any]] = {}
_shutting_down = False


async def _run_audio_work(function, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor, functools.partial(function, *args, **kwargs)
    )


async def _run_model_work(function, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _model_executor, functools.partial(function, *args, **kwargs)
    )


def _get_project_index_lock(project_id: str) -> asyncio.Lock:
    return _project_index_locks.setdefault(str(project_id), asyncio.Lock())


async def _prepare_import_candidate(
    file_path: str,
    project_id: str,
    scanner: Any,
    audio_service: Any,
    *,
    import_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist path/stat identity before any decoder or model work begins."""
    absolute = Path(file_path).resolve()
    existing = get_db_manager().get_file(str(absolute), project_id)
    stat = await _run_audio_work(absolute.stat)
    source = await _run_audio_work(audio_service.fingerprint, str(absolute))
    parsed_name, name_tokens, name_description = scanner._parse_filename(absolute.name)
    folder_path = ""
    if import_root:
        try:
            relative = absolute.parent.relative_to(Path(import_root).resolve())
            folder_path = "" if str(relative) == "." else str(relative)
        except ValueError:
            folder_path = ""

    from core.scanner import AudioFile as ScannedAudioFile

    placeholder = ScannedAudioFile(
        path=str(absolute),
        filename=absolute.name,
        duration=0.0,
        sample_rate=0,
        channels=0,
        format=absolute.suffix.lower().lstrip("."),
        size=stat.st_size,
        folder_path=folder_path,
        parsed_name=parsed_name,
        name_tokens=name_tokens,
        name_description=name_description,
        metadata_tags={},
    )
    record = AudioFileRecord(
        path=str(absolute),
        filename=absolute.name,
        file_size=stat.st_size,
        tags="[]",
        source_fingerprint=source.source_key,
        waveform_version=int(source.waveform_version),
    )
    file_uuid = await _run_audio_work(
        get_db_manager().upsert_file, record, project_id
    )
    return {
        "audio_file": placeholder,
        "file_uuid": file_uuid,
        "source": source,
        "existing": existing,
    }


async def _record_probe_failure(
    project_id: str,
    candidate: Dict[str, Any],
    *,
    message: str = "PyAV could not decode this supported file",
    folder_context: Optional[Sequence[Mapping[str, Any]]] = None,
) -> None:
    db_manager = get_db_manager()
    file_uuid = candidate["file_uuid"]
    source_key = candidate["source"].source_key
    for kind in ("waveform", "audio_vector"):
        db_manager.set_artifact_state(
            project_id,
            file_uuid,
            kind,
            "failed",
            source_fingerprint=source_key,
            error_code="audio_probe_failed",
            error_message=message,
        )
    await _index_text_metadata_artifact(
        project_id,
        file_uuid,
        candidate["audio_file"],
        tags=(candidate["existing"].tags if candidate["existing"] else "[]"),
        folder_context=folder_context,
    )


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


def cleanup_old_clips(max_keep=100):
    """
    清理多余的临时文件，只保留最新的max_keep个
    
    注意：此功能已禁用，临时文件由用户自行管理
    保留函数是为了向后兼容，但不再执行实际清理操作
    """
    # 临时文件由用户自行管理，不再自动清理
    logger.info("临时文件自动清理已禁用，由用户自行管理")
    return


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global _shutting_down
    _shutting_down = False
    logger.info(f"SoundBot API 启动中...")
    logger.info(f"设备: {config.get_device()}")
    logger.info(f"数据库路径: {config.get_db_path()}")

    # 环境检查：记录路径信息，模型缺失时提前打印明确错误
    try:
        from bootstrap import check_environment
        env_result = check_environment()
        logger.info(f"[Bootstrap] 路径检查: {env_result['paths']}")
        if not env_result['ok']:
            for err in env_result['errors']:
                logger.error(f"[Bootstrap] {err['type']}: {err['message']}")
                logger.error(f"[Bootstrap] 解决方案: {err['solution']}")
    except Exception as _e:
        logger.debug(f"[Bootstrap] 环境检查跳过: {_e}")

    # 初始化 SQLite 数据库
    db_manager = get_db_manager()
    file_count = db_manager.get_file_count()
    logger.info(f"SQLite 数据库已加载，当前文件数: {file_count}")

    # 使用动态获取的临时文件目录
    temp_dir = Path(config.get_temp_clip_dir())
    temp_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"临时文件目录: {temp_dir}")

    logger.info("临时文件由用户自行管理，不执行自动清理")

    # 启动模型预加载（后台异步）。完成事件会自动修复所有工程中
    # 因模型缺失而保持 pending/failed/stale 的双索引 artifact。
    from core.model_preloader import (
        get_preloader,
        preload_models_on_startup,
        reset_preloader,
    )
    preloader = get_preloader()
    event_loop = asyncio.get_running_loop()

    def on_model_progress(stage: str, _progress: float) -> None:
        if stage == "complete" and not _shutting_down:
            event_loop.call_soon_threadsafe(_schedule_model_ready_reconcile)

    preloader.add_progress_callback(on_model_progress)
    await preload_models_on_startup()

    yield

    _shutting_down = True
    logger.info("SoundBot API 关闭中...")
    logger.info("临时文件由用户自行管理，不执行自动清理")
    preloader.remove_progress_callback(on_model_progress)
    for entry in list(_automatic_index_tasks.values()):
        get_db_manager().update_job(entry["job_id"], cancel_requested=1)
    if _automatic_index_tasks:
        await asyncio.gather(
            *(entry["task"] for entry in list(_automatic_index_tasks.values())),
            return_exceptions=True,
        )
    await preloader.close()
    reset_preloader()
    reset_embedder()
    from core.search_engine import reset_optimized_searcher
    from core.ai_chat_service import reset_ai_chat_service

    reset_optimized_searcher()
    reset_ai_chat_service()
    reset_all_indexers()
    reset_searcher()
    reset_chroma_client()
    reset_db_manager()
    reset_ws_manager()
    logger.info("全局单例状态已清理")


# 创建 FastAPI 应用
app = FastAPI(
    title="SoundBot API",
    description="AI 音效管理器的语义搜索后端",
    version=config.APP_VERSION,
    lifespan=lifespan
)


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
    return schemas.HealthResponse(
        status="healthy",
        version=config.APP_VERSION,
        device=config.get_device(),
        model_loaded=is_embedder_loaded()
    )


@app.get("/api/v1/model/status")
async def get_model_status():
    """
    获取 AI 模型加载状态

    返回模型是否已预加载到内存，以及加载进度
    """
    from core.model_preloader import get_preloader
    preloader = get_preloader()
    # Polling this endpoint is also the recovery signal after the desktop app
    # installs/replaces a previously missing local model package. Unchanged
    # corrupt packages are not retried on every poll.
    preloader.retry_if_source_changed()

    return {
        "status": "success",
        "model_status": preloader.get_status(),
        "embedder_available": preloader.get_embedder() is not None
    }


# ==================== 扫描与索引 ====================

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

async def _start_folder_import(
    project_id: str,
    folder_path: str,
    recursive: bool,
    client_id: str,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """Validate and enqueue one immutable project-scoped folder import."""
    db_manager = get_db_manager()
    if not is_safe_project_id(project_id) or not db_manager.get_project(project_id):
        raise SoundBotAPIError("project_not_found", "工程不存在", status_code=404)
    if project_id in _projects_deleting:
        raise SoundBotAPIError("project_deleting", "工程正在删除", status_code=409)

    # 验证路径安全性，防止路径遍历攻击
    if not validate_path(folder_path):
        raise HTTPException(status_code=400, detail="路径包含非法字符")

    folder = Path(folder_path).expanduser()

    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=400, detail="无效的文件夹路径")
    folder = folder.resolve(strict=True)

    task_id = db_manager.create_job(project_id, "folder_import")
    ws_manager = get_ws_manager()
    ws_manager.register_task(task_id, client_id)

    # 后台执行扫描
    background_tasks.add_task(
        _scan_and_import_task,
        task_id=task_id,
        folder_path=str(folder),
        recursive=recursive,
        client_id=client_id,
        project_id=project_id,
    )

    return {
        "task_id": task_id,
        "job_id": task_id,
        "project_id": project_id,
        "state": "pending",
        "message": "扫描任务已启动",
    }


@app.post("/api/v1/import/async", deprecated=True)
async def import_folder_async(
    request: schemas.ScanRequest,
    background_tasks: BackgroundTasks,
    client_id: str = Query(default="default"),
):
    """Compatibility adapter; new callers use the project-explicit route."""
    project_id = getattr(config, "CURRENT_PROJECT_ID", "default") or "default"
    return await _start_folder_import(
        project_id,
        request.folder_path,
        request.recursive,
        client_id,
        background_tasks,
    )


@app.post("/api/v1/import/files", deprecated=True)
async def import_selected_files(
    request: schemas.ImportFilesRequest,
    background_tasks: BackgroundTasks,
):
    """Persist and index files selected by Electron without copying bytes over IPC."""
    from core.audio_service import get_audio_service

    db_manager = get_db_manager()
    project_id = request.project_id or getattr(config, 'CURRENT_PROJECT_ID', 'default')
    if not is_safe_project_id(project_id) or not db_manager.get_project(project_id):
        raise SoundBotAPIError("project_not_found", "工程不存在", status_code=404)
    if project_id in _projects_deleting:
        raise SoundBotAPIError("project_deleting", "工程正在删除", status_code=409)
    audio_service = get_audio_service()
    accepted: List[str] = []
    rejected: List[Dict[str, str]] = []
    for raw_path in request.file_paths:
        path = Path(raw_path)
        if not path.is_file():
            rejected.append({"path": raw_path, "reason": "not_found"})
        elif not audio_service.is_supported(path):
            rejected.append({"path": raw_path, "reason": "unsupported_format"})
        else:
            accepted.append(str(path.resolve()))
    if not accepted:
        raise SoundBotAPIError(
            "no_importable_files",
            "没有可导入的音频文件",
            details={"rejected": rejected},
        )
    job_id = db_manager.create_job(project_id, "file_import", len(accepted))
    client_id = request.client_id or "default"
    get_ws_manager().register_task(job_id, client_id)
    background_tasks.add_task(
        _import_selected_files_task,
        job_id,
        accepted,
        project_id,
        client_id,
    )
    return {
        "job_id": job_id,
        "task_id": job_id,
        "project_id": project_id,
        "state": "pending",
        "total": len(accepted),
        "rejected": rejected,
    }


@app.post("/api/v1/projects/{project_id}/imports")
async def import_project_files(
    project_id: str,
    request: schemas.ProjectImportRequest,
    background_tasks: BackgroundTasks,
):
    """Import exactly one folder or file list into the path-scoped project."""
    if request.folder_path is not None:
        return await _start_folder_import(
            project_id,
            request.folder_path,
            request.recursive,
            request.client_id or "default",
            background_tasks,
        )
    payload = schemas.ImportFilesRequest(
        file_paths=request.file_paths or [],
        client_id=request.client_id,
        project_id=project_id,
    )
    return await import_selected_files(payload, background_tasks)


async def _import_selected_files_task(
    job_id: str,
    file_paths: List[str],
    project_id: str,
    client_id: str,
) -> None:
    if project_id in _projects_deleting:
        get_db_manager().update_job(
            job_id, state="cancelled", stage="project_deleting"
        )
        return
    async with _get_project_index_lock(project_id):
        if project_id in _projects_deleting or not get_db_manager().get_project(project_id):
            get_db_manager().update_job(
                job_id, state="cancelled", stage="project_deleting"
            )
            return
        await _import_selected_files_task_locked(
            job_id, file_paths, project_id, client_id
        )


async def _import_selected_files_task_locked(
    job_id: str,
    file_paths: List[str],
    project_id: str,
    client_id: str,
) -> None:
    from core.audio_service import get_audio_service, AudioServiceError
    from core.scanner import AudioScanner

    db_manager = get_db_manager()
    ws_manager = get_ws_manager()
    service = get_audio_service()
    scanner = AudioScanner(service)
    folder_context = _load_logical_folder_context(project_id)
    db_manager.update_job(job_id, state="running", stage="analyzing")
    added = 0
    failed = 0
    try:
        for index, file_path in enumerate(file_paths):
            job = db_manager.get_job(job_id)
            if (job and job["cancel_requested"]) or ws_manager.is_task_cancelled(job_id):
                db_manager.update_job(
                    job_id, state="cancelled", stage="cancelled", processed=index
                )
                return
            candidate = await _prepare_import_candidate(
                file_path, project_id, scanner, service
            )
            existing = candidate["existing"]
            if not existing:
                added += 1
            audio_file = await _run_audio_work(scanner._process_file, Path(file_path))
            if not audio_file:
                failed += 1
                await _record_probe_failure(
                    project_id, candidate, folder_context=folder_context
                )
                db_manager.update_job(job_id, processed=index + 1, stage="failed_item")
                await ws_manager.send_scan_progress(
                    client_id,
                    job_id,
                    index + 1,
                    len(file_paths),
                    Path(file_path).name,
                    "failed",
                    progress=int(((index + 1) / len(file_paths)) * 100),
                )
                continue
            waveform = None
            waveform_error = None
            try:
                waveform = await _run_audio_work(service.waveform, audio_file.path)
            except AudioServiceError as exc:
                waveform_error = exc
            source_fp = await _run_audio_work(service.fingerprint, audio_file.path)
            record = AudioFileRecord(
                path=audio_file.path,
                filename=audio_file.filename,
                duration=audio_file.duration,
                sample_rate=audio_file.sample_rate,
                channels=audio_file.channels,
                file_size=audio_file.size,
                peaks_json=json.dumps(waveform.peaks) if waveform else None,
                tags="[]",
                source_fingerprint=source_fp.source_key,
                waveform_fingerprint=waveform.fingerprint.key if waveform else None,
                waveform_version=int(source_fp.waveform_version),
            )
            file_uuid = await _run_audio_work(
                db_manager.upsert_file, record, project_id
            )
            if waveform_error:
                db_manager.set_artifact_state(
                    project_id, file_uuid, "waveform", "failed",
                    source_fingerprint=source_fp.source_key,
                    error_code=waveform_error.code,
                    error_message=waveform_error.message,
                )
            if is_embedder_available():
                try:
                    indexer = _get_active_audio_indexer(project_id)
                    indexed = await _run_model_work(
                        indexer.add_single_audio,
                        audio_file.path,
                        {
                            "filename": audio_file.filename,
                            "duration": audio_file.duration,
                            "sample_rate": audio_file.sample_rate,
                            "channels": audio_file.channels,
                            "format": audio_file.format,
                            "size": audio_file.size,
                            "project_id": project_id,
                            "file_id": file_uuid,
                        },
                    )
                    db_manager.set_artifact_state(
                        project_id,
                        file_uuid,
                        "audio_vector",
                        "ready" if indexed else "failed",
                        source_fingerprint=source_fp.source_key,
                        engine_fingerprint=(
                            indexer.get_manifest().get("engine_fingerprint")
                            if indexed else None
                        ),
                        error_code=None if indexed else "audio_index_failed",
                    )
                except Exception as exc:
                    db_manager.set_artifact_state(
                        project_id, file_uuid, "audio_vector", "failed",
                        error_code="audio_index_failed", error_message=str(exc),
                    )
            await _index_text_metadata_artifact(
                project_id,
                file_uuid,
                audio_file,
                tags=existing.tags if existing else "[]",
                folder_context=folder_context,
            )
            db_manager.update_job(job_id, processed=index + 1, stage="indexing")
            await ws_manager.send_scan_progress(
                client_id,
                job_id,
                index + 1,
                len(file_paths),
                audio_file.filename,
                "indexing",
                progress=int(((index + 1) / len(file_paths)) * 100),
            )
        await _bump_project_index_revision(project_id)
        db_manager.update_job(
            job_id,
            state="completed",
            stage="done",
            processed=len(file_paths),
            error_message=f"{failed} files failed" if failed else None,
        )
        await ws_manager.send_scan_complete(
            client_id, job_id, len(file_paths), added, len(file_paths) - added
        )
    except Exception as exc:
        logger.exception(f"文件导入失败: {exc}")
        db_manager.update_job(
            job_id,
            state="failed",
            stage="failed",
            error_code="import_failed",
            error_message=str(exc),
        )
        await ws_manager.send_scan_error(client_id, job_id, str(exc))
    finally:
        ws_manager.unregister_task(job_id)


@app.get("/api/v1/jobs/{job_id}")
async def get_background_job(job_id: str):
    job = get_db_manager().get_job(job_id)
    if not job:
        raise SoundBotAPIError("job_not_found", "任务不存在", status_code=404)
    return job


@app.delete("/api/v1/jobs/{job_id}")
async def cancel_background_job(job_id: str):
    db_manager = get_db_manager()
    job = db_manager.get_job(job_id)
    if not job:
        raise SoundBotAPIError("job_not_found", "任务不存在", status_code=404)
    if job["state"] in {"completed", "failed", "cancelled"}:
        return {"job_id": job_id, "state": job["state"], "cancel_requested": False}
    db_manager.update_job(job_id, cancel_requested=1)
    get_ws_manager().cancel_task(job_id)
    return {"job_id": job_id, "state": job["state"], "cancel_requested": True}


async def _index_text_metadata_artifact(
    project_id: str,
    file_uuid: str,
    audio_file: Any,
    tags: Any = "[]",
    *,
    folder_context: Optional[Sequence[Mapping[str, Any]]] = None,
) -> bool:
    db_manager = get_db_manager()
    record = db_manager.get_file_by_uuid(file_uuid, project_id)
    source_fingerprint = record.source_fingerprint if record else None
    try:
        metadata = _build_text_metadata_payload(
            project_id,
            file_uuid,
            audio_file,
            tags=tags,
            source_fingerprint=source_fingerprint,
            folder_context=folder_context,
        )
        result = await _get_active_text_indexer(project_id).upsert_metadata([metadata])
        success = result.get("indexed", 0) == 1
        db_manager.set_artifact_state(
            project_id,
            file_uuid,
            "text_vector",
            "ready" if success else "failed",
            source_fingerprint=source_fingerprint,
            engine_fingerprint=result.get("fingerprint"),
            error_code=None if success else "text_index_failed",
        )
        return success
    except Exception as exc:
        db_manager.set_artifact_state(
            project_id,
            file_uuid,
            "text_vector",
            "failed",
            error_code="text_index_failed",
            error_message=str(exc),
        )
        logger.warning(f"文本元数据索引失败 {audio_file.path}: {exc}")
        return False


def _source_value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _flatten_metadata_values(value: Any) -> List[str]:
    """Flatten user/file metadata into deterministic searchable strings."""
    if value is None:
        return []
    if isinstance(value, str):
        rendered = value.strip()
        if not rendered:
            return []
        if rendered[:1] in {"[", "{"}:
            try:
                return _flatten_metadata_values(json.loads(rendered))
            except (TypeError, ValueError):
                pass
        return [rendered]
    if isinstance(value, Mapping):
        result: List[str] = []
        for key in sorted(value, key=lambda item: str(item)):
            result.extend(_flatten_metadata_values(key))
            result.extend(_flatten_metadata_values(value[key]))
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        result = []
        for item in value:
            result.extend(_flatten_metadata_values(item))
        return result
    return [str(value)]


def _load_logical_folder_context(project_id: str) -> List[Dict[str, Any]]:
    """Snapshot imported mappings and their user-facing folder metadata."""
    db_manager = get_db_manager()
    folders = {
        str(folder["id"]): folder
        for folder in db_manager.get_user_folders(project_id)
    }
    context: List[Dict[str, Any]] = []
    for mapping in db_manager.get_imported_folder_mappings(project_id):
        folder = folders.get(str(mapping.get("user_folder_id") or ""))
        context.append({
            "folder_path": mapping.get("folder_path") or "",
            "canonical_path": canonicalize_path(mapping.get("folder_path") or ""),
            "logical_folder": (folder or {}).get("name") or "",
            "description": (folder or {}).get("description") or "",
            "user_folder_id": mapping.get("user_folder_id"),
        })
    # Longest ancestor wins when an import root and one of its nested folders
    # are both present in the mapping table.
    return sorted(
        context,
        key=lambda item: len(str(item.get("canonical_path") or "")),
        reverse=True,
    )


def _derive_ucs_metadata(values: Sequence[Any]) -> Dict[str, Any]:
    """Derive UCS categories and their synonyms from filename/user metadata."""
    from core.ucs_keywords import get_ucs_keywords

    flattened: List[str] = []
    for value in values:
        flattened.extend(_flatten_metadata_values(value))
    corpus = " ".join(flattened).casefold()
    normalized_corpus = " ".join(
        token for token in re.split(r"[^0-9a-z\u3400-\u9fff]+", corpus) if token
    )
    corpus_tokens = set(normalized_corpus.split())

    def matches(term: str) -> bool:
        normalized = " ".join(
            token
            for token in re.split(
                r"[^0-9a-z\u3400-\u9fff]+", str(term).casefold()
            )
            if token
        )
        if not normalized:
            return False
        if re.search(r"[\u3400-\u9fff]", normalized):
            return normalized.replace(" ", "") in normalized_corpus.replace(" ", "")
        if " " not in normalized:
            return normalized in corpus_tokens
        return f" {normalized} " in f" {normalized_corpus} "

    categories: List[str] = []
    keywords: List[str] = []
    seen_keywords = set()
    for category, synonyms in get_ucs_keywords().items():
        if not (matches(category) or any(matches(synonym) for synonym in synonyms)):
            continue
        categories.append(str(category))
        for synonym in synonyms:
            rendered = str(synonym).strip()
            identity = rendered.casefold()
            if rendered and identity not in seen_keywords:
                seen_keywords.add(identity)
                keywords.append(rendered)
    return {
        "categories": categories,
        "keywords": keywords,
        # build_metadata_text consumes this field, so include both the UCS
        # labels and their searchable synonyms in a deterministic string.
        "search_text": " ".join([*categories, *keywords]),
    }


def _build_text_metadata_payload(
    project_id: str,
    file_uuid: str,
    source: Any,
    *,
    tags: Any = "[]",
    source_fingerprint: Optional[str] = None,
    folder_context: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the complete, project-scoped metadata document for text indexing."""
    file_path = str(_source_value(source, "file_path") or _source_value(source, "path") or "")
    filename = str(_source_value(source, "filename") or Path(file_path).name)
    parsed_name = str(_source_value(source, "parsed_name") or "")
    name_description = str(_source_value(source, "name_description") or "")
    if not parsed_name or not name_description:
        stem = Path(filename).stem
        derived = " ".join(
            part for part in re.split(r"[_\-\s.]+", stem) if part
        )
        parsed_name = parsed_name or derived
        name_description = name_description or derived

    context = list(folder_context) if folder_context is not None else _load_logical_folder_context(project_id)
    logical = next(
        (
            item for item in context
            if item.get("user_folder_id")
            and canonical_path_is_within(
                file_path, str(item.get("folder_path") or "")
            )
        ),
        {},
    )
    logical_folder = str(logical.get("logical_folder") or "")
    description = str(logical.get("description") or "")
    metadata_tags = _source_value(source, "metadata_tags", {}) or {}
    ucs = _derive_ucs_metadata((
        filename,
        tags,
        metadata_tags,
        logical_folder,
        description,
        name_description,
    ))
    return {
        "file_id": file_uuid,
        "file_path": file_path,
        "filename": filename,
        "folder_path": _source_value(source, "folder_path", "") or str(Path(file_path).parent),
        "logical_folder": logical_folder,
        "logical_folder_id": logical.get("user_folder_id") or "",
        "description": description,
        "parsed_name": parsed_name,
        "name_description": name_description,
        "metadata_tags": metadata_tags,
        "ucs_category": ucs["search_text"],
        "ucs_categories": ucs["categories"],
        "ucs_keywords": ucs["keywords"],
        "duration": float(_source_value(source, "duration", 0.0) or 0.0),
        "sample_rate": int(_source_value(source, "sample_rate", 0) or 0),
        "channels": int(_source_value(source, "channels", 0) or 0),
        "format": str(_source_value(source, "format") or Path(file_path).suffix.lower().lstrip(".")),
        "size": int(_source_value(source, "size", _source_value(source, "file_size", 0)) or 0),
        "tags": tags,
        "source_fingerprint": source_fingerprint,
        "project_id": project_id,
    }


def _get_active_audio_indexer(project_id: str):
    from core.index_lifecycle import get_active_audio_indexer

    return get_active_audio_indexer(get_db_manager(), project_id)


def _get_active_text_indexer(project_id: str):
    from core.index_lifecycle import get_active_text_indexer

    return get_active_text_indexer(get_db_manager(), project_id)


def _manifest_update_values(manifest: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "collection_name": manifest.get("collection"),
        "engine_fingerprint": manifest.get("engine_fingerprint"),
        "model_id": manifest.get("model_id"),
        "model_revision": manifest.get("model_revision"),
        "dimensions": manifest.get("dimensions"),
        "preprocessing_version": manifest.get("preprocessing_version"),
        "metric": manifest.get("metric") or "cosine",
        "state": "rebuild_required" if manifest.get("needs_rebuild") else "ready",
    }


async def _bump_project_index_revision(project_id: str) -> None:
    db_manager = get_db_manager()
    for kind, indexer in (
        ("audio_vector", _get_active_audio_indexer(project_id)),
        ("text_vector", _get_active_text_indexer(project_id)),
    ):
        db_manager.upsert_index_manifest(
            project_id,
            kind,
            **_manifest_update_values(indexer.get_manifest()),
            revision_increment=1,
        )
    try:
        await _get_project_searcher(project_id).clear_cache()
    except Exception as exc:
        logger.debug(f"索引版本已更新，旧搜索缓存将自然失效: {exc}")


def _find_mapping_by_path(
    mappings: Sequence[Mapping[str, Any]], folder_path: str
) -> Optional[Mapping[str, Any]]:
    target = canonicalize_path(folder_path)
    return next(
        (
            mapping for mapping in mappings
            if canonicalize_path(str(mapping.get("folder_path") or "")) == target
        ),
        None,
    )


def _invalidate_text_metadata_for_paths_locked(
    project_id: str,
    folder_paths: Sequence[str],
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """Invalidate affected metadata and enqueue repair while caller holds the lock."""
    db_manager = get_db_manager()
    affected = db_manager.mark_text_artifacts_for_folders(
        project_id, folder_paths, "stale"
    )
    if not affected:
        return {"affected_files": 0, "reindex_job_id": None}

    db_manager.upsert_index_manifest(
        project_id,
        "text_vector",
        state="stale",
        revision_increment=1,
    )
    # Reset, rather than instantiate, the project-scoped search clients.  A
    # subsequent request will observe the new manifest revision in its cache
    # key while the active collection remains searchable during reconciliation.
    from core.search_engine import reset_optimized_searcher
    from core.ai_chat_service import reset_ai_chat_service

    reset_optimized_searcher(project_id)
    reset_ai_chat_service(project_id)
    job_id = db_manager.create_job(project_id, "index_reconcile", affected)
    background_tasks.add_task(
        _repair_index_task,
        job_id,
        project_id,
        ["text_vector"],
        "reconcile",
    )
    return {"affected_files": affected, "reindex_job_id": job_id}


async def _scan_and_import_task(
    task_id: str,
    folder_path: str,
    recursive: bool,
    client_id: str,
    project_id: str,
):
    if project_id in _projects_deleting:
        get_db_manager().update_job(
            task_id, state="cancelled", stage="project_deleting"
        )
        return
    async with _get_project_index_lock(project_id):
        if project_id in _projects_deleting or not get_db_manager().get_project(project_id):
            get_db_manager().update_job(
                task_id, state="cancelled", stage="project_deleting"
            )
            return
        await _scan_and_import_task_locked(
            task_id, folder_path, recursive, client_id, project_id
        )


async def _scan_and_import_task_locked(
    task_id: str,
    folder_path: str,
    recursive: bool,
    client_id: str,
    project_id: str,
):
    """
    后台扫描导入任务

    执行流程：
    1. 扫描所有音频文件
    2. 检查文件是否已存在于数据库
    3. 对新文件计算波形峰值
    4. 写入 SQLite 数据库
    5. 通过 WebSocket 推送进度
    """
    from core.scanner import AudioScanner
    from core.audio_service import get_audio_service, AudioServiceError

    ws_manager = get_ws_manager()
    db_manager = get_db_manager()
    scanner = AudioScanner()
    audio_service = get_audio_service()

    logger.info(f"[SCAN_TASK] 任务ID: {task_id}, 文件夹: {folder_path}, 递归: {recursive}, 客户端: {client_id}, 工程: {project_id}")
    logger.info(f"[SCAN_TASK] ws_manager 连接数: {ws_manager.get_connection_count()}")
    logger.info(f"[SCAN_TASK] 活跃连接: {list(ws_manager.active_connections.keys())}")

    try:
        # 第一步：扫描所有文件
        logger.info(f"[SCAN_TASK] 发送扫描状态: 正在扫描文件...")
        await ws_manager.send_scan_status(
            client_id, task_id, "scanning", "正在扫描文件..."
        )
        logger.info(f"[SCAN_TASK] 扫描状态已发送，当前连接数: {ws_manager.get_connection_count()}")

        # Persist every supported path before probing it. A corrupt file is
        # still a durable library record with repairable failed artifacts.
        db_manager.update_job(task_id, state="running", stage="scanning")
        candidate_paths = await _run_audio_work(
            scanner.collect_audio_paths, folder_path, recursive
        )
        total = len(candidate_paths)
        db_manager.update_job(task_id, total=total)
        candidates: Dict[str, Dict[str, Any]] = {}
        added = 0
        for candidate_index, candidate_path in enumerate(candidate_paths):
            if ws_manager.is_task_cancelled(task_id):
                db_manager.update_job(
                    task_id,
                    state="cancelled",
                    stage="cancelled",
                    processed=candidate_index,
                )
                return
            candidate = await _prepare_import_candidate(
                str(candidate_path),
                project_id,
                scanner,
                audio_service,
                import_root=folder_path,
            )
            candidates[canonicalize_path(str(candidate_path))] = candidate
            if not candidate["existing"]:
                added += 1

        # Decode/probe only after SQLite owns the candidate set.
        audio_files, folder_structure = await _run_audio_work(
            scanner.scan_with_structure, folder_path, recursive
        )
        decoded_keys = {canonicalize_path(item.path) for item in audio_files}
        probe_failures = [
            candidate for key, candidate in candidates.items() if key not in decoded_keys
        ]
        logger.info(
            f"[SCAN_TASK] 扫描完成，候选 {total} 个，"
            f"可解码 {len(audio_files)} 个，失败 {len(probe_failures)} 个"
        )

        # 发送扫描统计日志到前端
        await ws_manager.send_scan_log(
            client_id, task_id, 'info',
            f"扫描完成统计: 找到 {total} 个音频文件",
            {
                'total': total,
                'decodable': len(audio_files),
                'failed': len(probe_failures),
                'folder_path': folder_path,
            }
        )

        # 发送文件夹结构到前端
        await ws_manager.send_folder_structure(
            client_id, task_id, folder_structure.model_dump()
        )

        # 创建导入文件夹映射记录（未分类）
        try:
            from core.scanner import FolderNode

            def collect_folder_paths(node: FolderNode, paths: list):
                """递归收集所有文件夹路径"""
                if node.path:
                    paths.append(node.path)
                for child in node.children:
                    collect_folder_paths(child, paths)

            folder_paths = []
            collect_folder_paths(folder_structure, folder_paths)

            # 为每个文件夹创建映射记录（未分类，user_folder_id 为 None）
            for folder_path in folder_paths:
                db_manager.add_imported_folder_mapping(
                    project_id=project_id,
                    folder_path=folder_path,
                    user_folder_id=None,
                    folder_name=Path(folder_path).name
                )

            logger.info(f"[SCAN_TASK] 创建了 {len(folder_paths)} 个文件夹映射记录")
        except Exception as e:
            logger.warning(f"[SCAN_TASK] 创建文件夹映射记录失败: {e}")

        # Use one immutable mapping snapshot for this locked import.  Nested
        # mappings are already ordered longest-first by the helper.
        folder_context = _load_logical_folder_context(project_id)

        if total == 0:
            await ws_manager.send_scan_complete(
                client_id, task_id, 0, 0, 0, "未找到音频文件"
            )
            ws_manager.unregister_task(task_id)
            db_manager.update_job(task_id, state="completed", stage="done", processed=0)
            return

        logger.info(f"开始导入 {total} 个文件到 SQLite")

        # 第二步：逐个处理文件
        skipped = 0
        indexed = 0

        for i, audio_file in enumerate(audio_files):
            # 检查是否取消
            if ws_manager.is_task_cancelled(task_id):
                await ws_manager.send_scan_complete(
                    client_id, task_id, total, added, i - added,
                    "用户取消"
                )
                ws_manager.unregister_task(task_id)
                db_manager.update_job(
                    task_id, state="cancelled", stage="cancelled", processed=i
                )
                return

            current_file = Path(audio_file.path).name
            progress_pct = int((i / total) * 100)

            # 发送进度 - 扫描阶段 (0-40%)
            await ws_manager.send_scan_progress(
                client_id, task_id, i, total, current_file, "scanning",
                progress=int(progress_pct * 0.4)
            )

            # Existing metadata is not sufficient evidence that waveform/vector
            # artifacts are healthy. Reconcile it instead of permanently skipping.
            candidate = candidates[canonicalize_path(audio_file.path)]
            existing_record = candidate["existing"]

            # 发送进度 - 分析阶段 (40-70%)
            await ws_manager.send_scan_progress(
                client_id, task_id, i, total, current_file, "analyzing",
                progress=int(40 + progress_pct * 0.3)
            )

            # 计算波形峰值
            peaks_json = None
            waveform_fingerprint = None
            waveform_version = 0
            source_fingerprint = None
            waveform_error = None
            try:
                source_fp = await _run_audio_work(
                    audio_service.fingerprint, audio_file.path
                )
                source_fingerprint = source_fp.source_key
                cached_is_current = bool(
                    existing_record
                    and existing_record.get_peaks()
                    and existing_record.source_fingerprint == source_fingerprint
                    and str(existing_record.waveform_version) == str(source_fp.waveform_version)
                )
                if not cached_is_current:
                    waveform = await _run_audio_work(
                        audio_service.waveform, audio_file.path
                    )
                    peaks_json = json.dumps(waveform.peaks)
                    waveform_fingerprint = waveform.fingerprint.key
                    waveform_version = int(waveform.fingerprint.waveform_version)
                else:
                    skipped += 1
            except AudioServiceError as e:
                logger.warning(f"计算波形失败 {audio_file.path}: {e.message}")
                waveform_error = e

            # 发送进度 - 保存到数据库阶段 (70-85%)
            await ws_manager.send_scan_progress(
                client_id, task_id, i, total, current_file, "saving",
                progress=int(70 + progress_pct * 0.15)
            )

            # 写入数据库（使用当前工程ID）
            record = AudioFileRecord(
                path=audio_file.path,
                filename=audio_file.filename,
                duration=audio_file.duration,
                sample_rate=audio_file.sample_rate,
                channels=audio_file.channels,
                file_size=audio_file.size,
                peaks_json=peaks_json,
                tags='[]',
                source_fingerprint=source_fingerprint,
                waveform_fingerprint=waveform_fingerprint,
                waveform_version=waveform_version,
            )
            file_uuid = await _run_audio_work(
                db_manager.upsert_file, record, project_id
            )
            if file_uuid:
                if waveform_error:
                    db_manager.set_artifact_state(
                        project_id,
                        file_uuid,
                        "waveform",
                        "failed",
                        source_fingerprint=source_fingerprint,
                        error_code=waveform_error.code,
                        error_message=waveform_error.message,
                    )
                if added and (i + 1) % 10 == 0:
                    logger.info(f"[SCAN_TASK] 已添加 {added} 个文件，当前: {current_file}")

                # 发送进度 - 向量索引阶段 (85-100%)
                await ws_manager.send_scan_progress(
                    client_id, task_id, i, total, current_file, "indexing",
                    progress=int(85 + progress_pct * 0.15)
                )

                try:
                    if is_embedder_available():
                        indexer = _get_active_audio_indexer(project_id)
                        index_success = await _run_model_work(indexer.add_single_audio, audio_file.path, {
                            "filename": audio_file.filename,
                            "duration": audio_file.duration,
                            "sample_rate": audio_file.sample_rate,
                            "channels": audio_file.channels,
                            "format": audio_file.format,
                            "size": audio_file.size,
                            "project_id": project_id,
                            "file_id": file_uuid,
                        })
                        if index_success:
                            indexed += 1
                            db_manager.set_artifact_state(
                                project_id,
                                file_uuid,
                                "audio_vector",
                                "ready",
                                source_fingerprint=source_fingerprint,
                                engine_fingerprint=indexer.get_manifest().get(
                                    "engine_fingerprint"
                                ),
                            )
                        else:
                            db_manager.set_artifact_state(
                                project_id, file_uuid, "audio_vector", "failed",
                                error_code="audio_index_failed",
                                error_message="CLAP indexer returned false",
                            )
                except Exception as e:
                    logger.warning(f"生成语义索引失败 {audio_file.path}: {e}")
                    db_manager.set_artifact_state(
                        project_id, file_uuid, "audio_vector", "failed",
                        error_code="audio_index_failed", error_message=str(e),
                    )
                await _index_text_metadata_artifact(
                    project_id,
                    file_uuid,
                    audio_file,
                    tags=existing_record.tags if existing_record else "[]",
                    folder_context=folder_context,
                )
            db_manager.update_job(task_id, processed=i + 1, stage="indexing")

        for failure_index, candidate in enumerate(probe_failures, start=len(audio_files)):
            if ws_manager.is_task_cancelled(task_id):
                db_manager.update_job(
                    task_id,
                    state="cancelled",
                    stage="cancelled",
                    processed=failure_index,
                )
                return
            await _record_probe_failure(
                project_id, candidate, folder_context=folder_context
            )
            db_manager.update_job(
                task_id,
                processed=failure_index + 1,
                stage="failed_item",
            )
            await ws_manager.send_scan_progress(
                client_id,
                task_id,
                failure_index + 1,
                total,
                candidate["audio_file"].filename,
                "failed",
                progress=int(((failure_index + 1) / total) * 100),
            )

        await _bump_project_index_revision(project_id)
        await ws_manager.send_scan_complete(
            client_id, task_id, total, added, skipped
        )
        ws_manager.unregister_task(task_id)
        db_manager.update_job(
            task_id,
            state="completed",
            stage="done",
            processed=total,
            error_message=(
                f"{len(probe_failures)} files failed to decode"
                if probe_failures else None
            ),
        )
        logger.info(f"导入完成: 新增 {added} 个文件，跳过 {skipped} 个")

    except Exception as e:
        logger.error(f"扫描导入失败: {e}")
        db_manager.update_job(
            task_id, state="failed", stage="failed",
            error_code="import_failed", error_message=str(e),
        )
        await ws_manager.send_scan_error(client_id, task_id, str(e))
        ws_manager.unregister_task(task_id)


# ==================== SQLite 数据库 API ====================

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
async def update_file_tags(
    path: str,
    background_tasks: BackgroundTasks,
    payload: schemas.TagsRequest | List[str] = Body(...),
):
    """
    更新文件标签

    - **path**: URL 编码的文件路径
    - **tags**: 新的标签列表
    """
    file_path = path
    tags = payload.tags if isinstance(payload, schemas.TagsRequest) else payload

    db_manager = get_db_manager()
    current_project_id = getattr(config, 'CURRENT_PROJECT_ID', 'default')
    if current_project_id in _projects_deleting:
        raise SoundBotAPIError("project_deleting", "工程正在删除", status_code=409)
    async with _get_project_index_lock(current_project_id):
        success = db_manager.update_tags(file_path, tags, current_project_id)

        if not success:
            raise HTTPException(status_code=404, detail="文件不存在")
        try:
            await _get_project_searcher(current_project_id).clear_cache()
        except Exception as exc:
            logger.debug(f"清理搜索缓存失败: {exc}")
        job_id = db_manager.create_job(current_project_id, "text_metadata_refresh", 1)
        background_tasks.add_task(
            _repair_index_task,
            job_id,
            current_project_id,
            ["text_vector"],
            "reconcile",
        )
    return {"success": True, "message": "标签已更新", "job_id": job_id}


@app.delete("/api/v1/db/file/{path:path}")
async def delete_db_file(path: str):
    """
    从数据库删除文件记录

    - **path**: URL 编码的文件路径
    """
    file_path = path

    db_manager = get_db_manager()
    current_project_id = getattr(config, 'CURRENT_PROJECT_ID', 'default')
    if current_project_id in _projects_deleting:
        raise SoundBotAPIError("project_deleting", "工程正在删除", status_code=409)
    async with _get_project_index_lock(current_project_id):
        record = db_manager.get_file(file_path, current_project_id)
        if not record:
            raise HTTPException(status_code=404, detail="文件不存在")
        try:
            await _run_model_work(
                _get_active_audio_indexer(current_project_id).remove_audio,
                file_path,
                record.file_uuid,
            )
            await _run_model_work(
                _get_active_text_indexer(current_project_id).remove,
                file_id=record.file_uuid,
            )
        except Exception as exc:
            db_manager.set_artifact_state(
                current_project_id,
                record.file_uuid,
                "audio_vector",
                "stale",
                error_code="delete_sync_failed",
                error_message=str(exc),
            )
            db_manager.set_artifact_state(
                current_project_id,
                record.file_uuid,
                "text_vector",
                "stale",
                error_code="delete_sync_failed",
                error_message=str(exc),
            )
            raise SoundBotAPIError(
                "delete_sync_failed",
                "向量删除未完成，文件记录已保留，可重试",
                status_code=503,
                retryable=True,
            ) from exc

        success = db_manager.delete_file(file_path, current_project_id)
        if not success:
            for kind in ("audio_vector", "text_vector"):
                db_manager.set_artifact_state(
                    current_project_id,
                    record.file_uuid,
                    kind,
                    "stale",
                    error_code="delete_metadata_failed",
                    error_message="SQLite delete returned false after vector removal",
                )
            raise SoundBotAPIError(
                "delete_metadata_failed",
                "元数据删除失败；已删除的向量会由修复任务恢复",
                status_code=503,
                retryable=True,
            )

        for kind in ("audio_vector", "text_vector"):
            db_manager.upsert_index_manifest(
                current_project_id, kind, revision_increment=1
            )
        try:
            await _get_project_searcher(current_project_id).clear_cache()
        except Exception as exc:
            logger.debug(f"清理搜索缓存失败: {exc}")
    return {"success": True, "message": "文件已删除"}


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


def _get_project_searcher(project_id: str):
    from core.search_engine import get_optimized_searcher_sync

    db_manager = get_db_manager()
    audio_manifest = db_manager.get_index_manifest(project_id, "audio_vector") or {}
    text_manifest = db_manager.get_index_manifest(project_id, "text_vector") or {}
    revision = int(audio_manifest.get("revision", 0)) + int(text_manifest.get("revision", 0))
    fingerprint = "|".join(filter(None, (
        audio_manifest.get("engine_fingerprint"),
        text_manifest.get("engine_fingerprint"),
    ))) or None
    return get_optimized_searcher_sync(
        project_id=project_id,
        collection_name=audio_manifest.get("collection_name") or "audio_embeddings",
        text_collection_name=text_manifest.get("collection_name")
        or "text_metadata_embeddings",
        index_revision=revision,
        model_fingerprint=fingerprint,
    )

@app.post("/api/v1/search", response_model=schemas.SearchResponse)
async def search_audio(request: schemas.SearchRequest):
    """
    语义搜索音频（同步版本，保持向后兼容）

    - **query**: 自然语言查询（如"清脆的铃铛声"）
    - **top_k**: 返回结果数量（默认 20）
    - **threshold**: 相似度阈值（默认 0.15）
    """
    try:
        project_id = request.project_id
        if not is_safe_project_id(project_id) or not get_db_manager().get_project(project_id):
            raise SoundBotAPIError("project_not_found", "工程不存在", status_code=404)
        searcher = _get_project_searcher(project_id)

        filters = {}
        if request.min_duration is not None:
            filters["duration"] = {"$gte": request.min_duration}
        if request.max_duration is not None:
            if "duration" in filters:
                filters["duration"]["$lte"] = request.max_duration
            else:
                filters["duration"] = {"$lte": request.max_duration}
        if request.sample_rate is not None:
            filters["sample_rate"] = request.sample_rate
        if request.channels is not None:
            filters["channels"] = request.channels
        if request.format is not None:
            filters["format"] = request.format

        # 使用异步搜索但不推送进度
        results, stats = await searcher.search_async(
            query=request.query,
            top_k=request.top_k,
            min_similarity=request.threshold,
            filters=filters if filters else None,
            use_cache=True
        )

        logger.info(f"搜索 '{request.query}': 找到 {len(results)} 个结果, 耗时 {stats.get('duration', 0):.3f}s, 缓存命中: {stats.get('cache_hit', False)}")

        # 转换为响应格式
        search_results = []
        for r in results:
            # 使用 getattr 避免与 Python 内置 format 函数冲突
            file_format = getattr(r, 'format', '') or ''
            file_uuid = r.metadata.get("file_id")
            if not file_uuid:
                matched_record = db_manager.get_file(r.file_path, project_id)
                file_uuid = matched_record.file_uuid if matched_record else None
            audio_file = schemas.AudioFile(
                id=file_uuid,
                path=r.file_path,
                filename=r.filename,
                duration=r.duration,
                sample_rate=r.metadata.get("sample_rate", 0),
                channels=r.metadata.get("channels", 0),
                format=file_format,
                size=r.metadata.get("size", 0)
            )
            search_results.append(schemas.SearchResult(
                audio_file=audio_file,
                score=r.similarity,
                distance=1.0 - r.similarity,
                metadata={
                    "project_id": project_id,
                    "audio_score": r.metadata.get("audio_score", r.metadata.get("semantic_score", 0.0)),
                    "text_score": r.metadata.get("text_score", 0.0),
                    "keyword_score": r.metadata.get("keyword_score", 0.0),
                    "score_weights": r.metadata.get("score_weights", {
                        "audio": 0.55, "text": 0.30, "keyword": 0.15,
                    }),
                }
            ))

        # 分页处理
        total = len(search_results)
        page = request.page
        page_size = request.page_size
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_results = search_results[start_idx:end_idx]
        total_pages = (total + page_size - 1) // page_size

        return schemas.SearchResponse(
            query=request.query,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            results=paginated_results
        )

    except SoundBotAPIError:
        raise
    except Exception as e:
        import traceback
        logger.error(f"搜索失败: {e}")
        logger.error(f"搜索失败详细堆栈: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/search/async")
async def search_audio_async(
    request: schemas.SearchRequest,
    background_tasks: BackgroundTasks,
    client_id: str = Query(default="default")
):
    """
    异步语义搜索（带 WebSocket 进度推送）

    - **query**: 自然语言查询
    - **top_k**: 返回结果数量
    - **threshold**: 相似度阈值
    - **client_id**: WebSocket 客户端标识

    返回搜索任务ID，通过 WebSocket 接收进度和结果
    """
    try:
        project_id = request.project_id
        db_manager = get_db_manager()
        if not is_safe_project_id(project_id) or not db_manager.get_project(project_id):
            raise SoundBotAPIError(
                "project_not_found",
                "工程不存在",
                status_code=404,
                details={"project_id": project_id},
            )
        search_id = f"search_{int(time.time() * 1000)}"

        # 后台执行搜索
        background_tasks.add_task(
            _search_task,
            search_id=search_id,
            query=request.query,
            top_k=request.top_k,
            min_similarity=request.threshold,
            filters={
                k: v for k, v in {
                    "min_duration": request.min_duration,
                    "max_duration": request.max_duration,
                    "sample_rate": request.sample_rate,
                    "channels": request.channels,
                    "format": request.format
                }.items() if v is not None
            },
            client_id=client_id,
            project_id=project_id,
        )

        return {"search_id": search_id, "message": "搜索任务已启动"}

    except SoundBotAPIError:
        raise
    except Exception as e:
        logger.error(f"启动搜索任务失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _search_task(
    search_id: str,
    query: str,
    top_k: Optional[int],
    min_similarity: Optional[float],
    filters: Dict[str, Any],
    client_id: str,
    project_id: str,
):
    """
    后台搜索任务（带 WebSocket 进度推送）
    """
    ws_manager = get_ws_manager()
    searcher = _get_project_searcher(project_id)

    # 构建过滤条件
    where_clause = {}
    if filters.get("min_duration") is not None:
        where_clause["duration"] = {"$gte": filters["min_duration"]}
    if filters.get("max_duration") is not None:
        if "duration" in where_clause:
            where_clause["duration"]["$lte"] = filters["max_duration"]
        else:
            where_clause["duration"] = {"$lte": filters["max_duration"]}
    if filters.get("sample_rate") is not None:
        where_clause["sample_rate"] = filters["sample_rate"]
    if filters.get("channels") is not None:
        where_clause["channels"] = filters["channels"]
    if filters.get("format") is not None:
        where_clause["format"] = filters["format"]

    try:
        # 定义进度回调
        async def progress_callback(stage: str, progress: float):
            await ws_manager.send_search_progress(
                client_id=client_id,
                search_id=search_id,
                stage=stage,
                progress=progress,
                message=f"搜索阶段: {stage}"
            )

        # 执行搜索
        results, stats = await searcher.search_async(
            query=query,
            top_k=top_k,
            min_similarity=min_similarity,
            filters=where_clause if where_clause else None,
            use_cache=True,
            progress_callback=progress_callback
        )

        # 发送完成消息
        await ws_manager.send_search_complete(
            client_id=client_id,
            search_id=search_id,
            results_count=len(results),
            duration=stats.get("duration", 0),
            cache_hit=stats.get("cache_hit", False)
        )

        # 发送搜索结果
        search_results_data = []
        for r in results:
            file_uuid = r.metadata.get("file_id")
            if not file_uuid:
                record = get_db_manager().get_file(r.file_path, project_id)
                file_uuid = record.file_uuid if record else None
            search_results_data.append({
                "file_id": file_uuid,
                "path": r.file_path,
                "filename": r.filename,
                "duration": r.duration,
                "format": r.format,
                "similarity": r.similarity,
                "metadata": r.metadata
            })

        await ws_manager.broadcast({
            "type": "search_results",
            "search_id": search_id,
            "data": {
                "query": query,
                "total": len(results),
                "results": search_results_data,
                "stats": stats
            }
        }, client_id)

        logger.info(f"异步搜索 '{query}' 完成: 找到 {len(results)} 个结果, 耗时 {stats.get('duration', 0):.3f}s")

    except Exception as e:
        logger.error(f"异步搜索失败: {e}")
        await ws_manager.send_search_error(
            client_id=client_id,
            search_id=search_id,
            error=str(e)
        )


@app.get("/api/v1/search/cache/stats")
async def get_search_cache_stats():
    """
    获取搜索缓存统计信息
    """
    try:
        project_id = getattr(config, 'CURRENT_PROJECT_ID', 'default')
        searcher = _get_project_searcher(project_id)
        stats = searcher.get_cache_stats()
        return {
            "status": "success",
            "cache_stats": stats
        }
    except Exception as e:
        logger.error(f"获取缓存统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/search/cache/clear")
async def clear_search_cache():
    """
    清空搜索缓存
    """
    try:
        project_id = getattr(config, 'CURRENT_PROJECT_ID', 'default')
        searcher = _get_project_searcher(project_id)
        await searcher.clear_cache()
        return {
            "status": "success",
            "message": "搜索缓存已清空"
        }
    except Exception as e:
        logger.error(f"清空缓存失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 音频波形 ====================

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


async def _waveform_payload(file_path: str, project_id: str) -> Dict[str, Any]:
    from core.audio_service import get_audio_service, AudioServiceError

    service = get_audio_service()
    db_manager = get_db_manager()
    record = db_manager.get_file(file_path, project_id)
    try:
        fingerprint = await _run_audio_work(service.fingerprint, file_path)
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

        waveform = await _run_audio_work(service.waveform, file_path)
        metadata = (
            None if record else await _run_audio_work(service.probe, file_path)
        )
        if record:
            source_changed = (
                record.source_fingerprint != waveform.fingerprint.source_key
            )
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
            if source_changed:
                from core.search_engine import reset_optimized_searcher
                from core.ai_chat_service import reset_ai_chat_service

                reset_optimized_searcher(project_id)
                reset_ai_chat_service(project_id)
                _schedule_project_reconcile(
                    project_id,
                    ("audio_vector", "text_vector"),
                    reason="source_changed",
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
            try:
                payload = await _waveform_payload(record.path, project_id)
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

async def _run_automatic_reconcile(
    job_id: str,
    project_id: str,
    kinds: List[str],
) -> None:
    try:
        await _repair_index_task(job_id, project_id, kinds, "reconcile")
    finally:
        current = _automatic_index_tasks.get(project_id)
        if current and current.get("job_id") == job_id:
            _automatic_index_tasks.pop(project_id, None)


def _schedule_project_reconcile(
    project_id: str,
    kinds: Sequence[str],
    *,
    reason: str,
) -> Optional[str]:
    """Create one durable automatic reconcile job per project at a time."""
    if _shutting_down or project_id in _projects_deleting:
        return None
    db_manager = get_db_manager()
    if not is_safe_project_id(project_id) or not db_manager.get_project(project_id):
        return None
    valid_kinds = [
        kind for kind in dict.fromkeys(kinds)
        if kind in {"waveform", "audio_vector", "text_vector"}
    ]
    if not valid_kinds:
        return None
    current = _automatic_index_tasks.get(project_id)
    if current and not current["task"].done():
        return str(current["job_id"])
    work = db_manager.list_artifacts_for_work(
        project_id, valid_kinds, limit=100000
    )
    if not work:
        return None
    job_id = db_manager.create_job(
        project_id, f"index_auto_{reason}", len(work)
    )
    task = asyncio.create_task(
        _run_automatic_reconcile(job_id, project_id, valid_kinds),
        name=f"soundbot-index-{project_id}-{reason}",
    )
    _automatic_index_tasks[project_id] = {"job_id": job_id, "task": task}
    return job_id


def _schedule_model_ready_reconcile() -> None:
    """Backfill vector artifacts after a newly available CLAP model loads."""
    if _shutting_down:
        return
    db_manager = get_db_manager()
    for project in db_manager.get_all_projects():
        project_id = str(project["id"])
        _schedule_project_reconcile(
            project_id,
            ("audio_vector", "text_vector"),
            reason="model_ready",
        )

@app.get("/api/v1/index/status", response_model=schemas.IndexStatus)
async def get_index_status():
    """获取当前索引状态"""
    try:
        project_id = getattr(config, 'CURRENT_PROJECT_ID', 'default')
        db_manager = get_db_manager()
        artifacts = db_manager.get_artifact_counts(project_id)
        audio_ready = artifacts["audio_vector"]["ready"]
        return schemas.IndexStatus(
            project_id=project_id,
            total_files=db_manager.get_file_count(project_id),
            indexed_files=audio_ready,
            artifacts=artifacts,
            manifests={
                kind: db_manager.get_index_manifest(project_id, kind) or {}
                for kind in ("audio_vector", "text_vector")
            },
        )
    except Exception as e:
        logger.error(f"获取索引状态失败: {e}")
        return schemas.IndexStatus(total_files=0, indexed_files=0)


@app.get("/api/v1/projects/{project_id}/index/status")
async def get_project_index_status(project_id: str):
    db_manager = get_db_manager()
    if not is_safe_project_id(project_id) or not db_manager.get_project(project_id):
        raise SoundBotAPIError("project_not_found", "工程不存在", status_code=404)
    if project_id in _projects_deleting:
        raise SoundBotAPIError("project_deleting", "工程正在删除", status_code=409)
    return {
        "project_id": project_id,
        "total_files": db_manager.get_file_count(project_id),
        "artifacts": db_manager.get_artifact_counts(project_id),
        "manifests": {
            kind: db_manager.get_index_manifest(project_id, kind) or {}
            for kind in ("audio_vector", "text_vector")
        },
    }


@app.post("/api/v1/projects/{project_id}/index/reconcile")
async def reconcile_project_index(
    project_id: str,
    request: schemas.IndexActionRequest,
    background_tasks: BackgroundTasks,
):
    return await _start_index_job(project_id, request.kinds, "reconcile", background_tasks)


@app.post("/api/v1/projects/{project_id}/index/rebuild")
async def rebuild_project_index(
    project_id: str,
    request: schemas.IndexActionRequest,
    background_tasks: BackgroundTasks,
):
    return await _start_index_job(project_id, request.kinds, "rebuild", background_tasks)


async def _start_index_job(
    project_id: str,
    kinds: List[str],
    mode: str,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    db_manager = get_db_manager()
    if not is_safe_project_id(project_id) or not db_manager.get_project(project_id):
        raise SoundBotAPIError("project_not_found", "工程不存在", status_code=404)
    if project_id in _projects_deleting:
        raise SoundBotAPIError("project_deleting", "工程正在删除", status_code=409)
    valid_kinds = [
        kind for kind in dict.fromkeys(kinds)
        if kind in {"waveform", "audio_vector", "text_vector"}
    ]
    if not valid_kinds:
        raise SoundBotAPIError("invalid_index_kinds", "没有有效的索引类型")
    if mode == "rebuild" and "waveform" in valid_kinds:
        # Waveforms have no shadow store, so an explicit rebuild invalidates
        # them immediately. Vector rebuilds keep the old active artifacts
        # ready until a verified shadow is atomically activated.
        db_manager.mark_project_artifacts(project_id, ["waveform"], "stale")
    if mode == "rebuild":
        total = 0
        if "waveform" in valid_kinds:
            total += len(db_manager.list_artifacts_for_work(
                project_id, ["waveform"], limit=100000
            ))
        for kind in ("audio_vector", "text_vector"):
            if kind in valid_kinds:
                total += len(db_manager.list_artifacts_for_work(
                    project_id,
                    [kind],
                    states=("pending", "processing", "ready", "failed", "stale"),
                    limit=100000,
                ))
    else:
        total = len(db_manager.list_artifacts_for_work(
            project_id, valid_kinds, limit=100000
        ))
    job_id = db_manager.create_job(project_id, f"index_{mode}", total)
    background_tasks.add_task(
        _repair_index_task, job_id, project_id, valid_kinds, mode
    )
    return {
        "job_id": job_id,
        "project_id": project_id,
        "state": "pending",
        "mode": mode,
        "kinds": valid_kinds,
        "total": total,
    }


async def _repair_index_task(
    job_id: str,
    project_id: str,
    kinds: List[str],
    mode: str,
) -> None:
    if project_id in _projects_deleting:
        get_db_manager().update_job(
            job_id, state="cancelled", stage="project_deleting"
        )
        return
    async with _get_project_index_lock(project_id):
        if project_id in _projects_deleting or not get_db_manager().get_project(project_id):
            get_db_manager().update_job(
                job_id, state="cancelled", stage="project_deleting"
            )
            return
        await _repair_index_task_locked(job_id, project_id, kinds, mode)


async def _repair_index_task_locked(
    job_id: str,
    project_id: str,
    kinds: List[str],
    mode: str,
) -> None:
    from core.audio_service import get_audio_service, AudioServiceError
    from core.index_lifecycle import (
        activate_verified_shadows,
        create_shadow_indexer,
    )
    from core.search_engine import reset_optimized_searcher
    from core.ai_chat_service import reset_ai_chat_service

    db_manager = get_db_manager()
    service = get_audio_service()
    folder_context = _load_logical_folder_context(project_id)
    db_manager.update_job(job_id, state="running", stage="reconciling")
    try:
        records = db_manager.get_files_by_project(project_id)
        expected = [
            {
                "file_id": record.file_uuid,
                "file_path": record.path,
                "source_fingerprint": record.source_fingerprint,
            }
            for record in records
        ]
        vector_targets: Dict[str, Any] = {}
        shadow_kinds = set()
        if "audio_vector" in kinds:
            active_audio = _get_active_audio_indexer(project_id)
            needs_shadow = mode == "rebuild" or active_audio.get_manifest().get("needs_rebuild")
            if needs_shadow:
                shadow_kinds.add("audio_vector")
                vector_targets["audio_vector"] = create_shadow_indexer(
                    project_id, "audio_vector", job_id
                )
            else:
                vector_targets["audio_vector"] = active_audio
        if "text_vector" in kinds:
            active_text = _get_active_text_indexer(project_id)
            needs_shadow = mode == "rebuild" or active_text.get_manifest().get("needs_rebuild")
            if needs_shadow:
                shadow_kinds.add("text_vector")
                vector_targets["text_vector"] = create_shadow_indexer(
                    project_id, "text_vector", job_id
                )
            else:
                vector_targets["text_vector"] = active_text

        if mode != "rebuild":
            for kind, indexer in vector_targets.items():
                if kind in shadow_kinds:
                    continue
                reconciliation = await _run_model_work(
                    indexer.reconcile, expected, remove_orphans=True
                )
                for file_id in (
                    reconciliation.get("missing", [])
                    + reconciliation.get("stale", [])
                ):
                    db_manager.set_artifact_state(project_id, file_id, kind, "stale")

        direct_kinds = [kind for kind in kinds if kind not in shadow_kinds]
        work = db_manager.list_artifacts_for_work(
            project_id,
            direct_kinds,
            states=("pending", "failed", "stale"),
            limit=100000,
        )
        # A shadow must be a complete snapshot of all currently indexable
        # records, including rows whose old active vectors remain ready.
        # Reading ready rows here does not mutate their online artifact state.
        for kind in sorted(shadow_kinds):
            work.extend(db_manager.list_artifacts_for_work(
                project_id,
                [kind],
                states=("pending", "processing", "ready", "failed", "stale"),
                limit=100000,
            ))
        db_manager.update_job(job_id, total=len(work), stage="processing")
        shadow_success: Dict[str, List[str]] = {
            kind: [] for kind in shadow_kinds
        }
        shadow_outcomes: Dict[str, Dict[str, Dict[str, Any]]] = {
            kind: {} for kind in shadow_kinds
        }
        shadow_candidate_counts = {
            kind: sum(1 for artifact in work if artifact["kind"] == kind)
            for kind in shadow_kinds
        }
        failures: Dict[str, int] = {kind: 0 for kind in kinds}
        for index, artifact in enumerate(work):
            job = db_manager.get_job(job_id)
            if job and job["cancel_requested"]:
                if artifact["kind"] not in shadow_kinds:
                    db_manager.set_artifact_state(
                        project_id, artifact["file_uuid"], artifact["kind"], "stale"
                    )
                db_manager.update_job(
                    job_id, state="cancelled", stage="cancelled", processed=index
                )
                return
            kind = artifact["kind"]
            file_uuid = artifact["file_uuid"]
            is_shadow = kind in shadow_kinds
            if not is_shadow:
                db_manager.set_artifact_state(project_id, file_uuid, kind, "processing")
            try:
                if kind == "waveform":
                    waveform = await _run_audio_work(service.waveform, artifact["path"])
                    db_manager.update_peaks(
                        artifact["path"],
                        waveform.peaks,
                        project_id,
                        source_fingerprint=waveform.fingerprint.source_key,
                        waveform_fingerprint=waveform.fingerprint.key,
                        waveform_version=int(waveform.fingerprint.waveform_version),
                    )
                elif kind == "audio_vector":
                    if not is_embedder_available():
                        outcome = {
                            "state": "pending",
                            "error_code": "model_unavailable",
                            "error_message": "CLAP model is not installed or ready",
                            "permanent": False,
                        }
                        if is_shadow:
                            shadow_outcomes[kind][file_uuid] = outcome
                        else:
                            db_manager.set_artifact_state(
                                project_id, file_uuid, kind, "pending",
                                error_code=outcome["error_code"],
                                error_message=outcome["error_message"],
                            )
                        failures[kind] += 1
                        continue
                    indexed = await _run_model_work(
                        vector_targets[kind].add_single_audio,
                        artifact["path"],
                        {
                            "file_id": file_uuid,
                            "filename": artifact["filename"],
                            "duration": artifact["duration"],
                            "sample_rate": artifact["sample_rate"],
                            "channels": artifact["channels"],
                            "size": artifact["file_size"],
                            "tags": artifact["tags"],
                            "project_id": project_id,
                        },
                    )
                    if not indexed:
                        raise RuntimeError("CLAP indexer returned false")
                    if is_shadow:
                        shadow_success[kind].append(file_uuid)
                    else:
                        db_manager.set_artifact_state(
                            project_id, file_uuid, kind, "ready",
                            source_fingerprint=artifact["source_fingerprint"],
                        )
                else:
                    metadata = _build_text_metadata_payload(
                        project_id,
                        file_uuid,
                        artifact,
                        tags=artifact["tags"],
                        source_fingerprint=artifact["source_fingerprint"],
                        folder_context=folder_context,
                    )
                    result = await vector_targets[kind].upsert_metadata([metadata])
                    if result.get("indexed", 0) != 1:
                        raise RuntimeError("metadata embedding returned no vector")
                    if is_shadow:
                        shadow_success[kind].append(file_uuid)
                    else:
                        db_manager.set_artifact_state(
                            project_id, file_uuid, kind, "ready",
                            source_fingerprint=artifact["source_fingerprint"],
                            engine_fingerprint=result.get("fingerprint"),
                        )
            except AudioServiceError as exc:
                failures[kind] += 1
                if is_shadow:
                    shadow_outcomes[kind][file_uuid] = {
                        "state": "failed",
                        "error_code": exc.code,
                        "error_message": exc.message,
                        "permanent": not exc.retryable,
                    }
                else:
                    db_manager.set_artifact_state(
                        project_id, file_uuid, kind, "failed",
                        error_code=exc.code, error_message=exc.message,
                    )
            except Exception as exc:
                failures[kind] += 1
                if is_shadow:
                    permanent_codes = {
                        "audio_probe_failed",
                        "audio_decode_failed",
                        "unsupported_audio_format",
                    }
                    shadow_outcomes[kind][file_uuid] = {
                        "state": "failed",
                        "error_code": f"{kind}_failed",
                        "error_message": str(exc),
                        "permanent": bool(
                            kind == "audio_vector"
                            and artifact.get("error_code") in permanent_codes
                        ),
                    }
                else:
                    db_manager.set_artifact_state(
                        project_id, file_uuid, kind, "failed",
                        error_code=f"{kind}_failed", error_message=str(exc),
                    )
            finally:
                db_manager.update_job(job_id, processed=index + 1, stage=kind)

        if shadow_kinds:
            eligible: Dict[str, Any] = {}
            for kind in shadow_kinds:
                success_count = len(set(shadow_success.get(kind, [])))
                outcomes = shadow_outcomes.get(kind, {})
                candidate_count = shadow_candidate_counts.get(kind, 0)
                all_permanently_excluded = bool(
                    candidate_count > 0
                    and len(outcomes) == candidate_count
                    and all(
                        outcome.get("state") == "failed"
                        and outcome.get("permanent")
                        for outcome in outcomes.values()
                    )
                )
                # At least one valid vector proves the target engine works.
                # An empty project or a library consisting solely of known
                # permanently unindexable audio may also activate an empty
                # verified shadow. A total transient/provider failure may not.
                if success_count > 0 or candidate_count == 0 or all_permanently_excluded:
                    eligible[kind] = vector_targets[kind]
            if eligible:
                activate_verified_shadows(
                    db_manager,
                    project_id,
                    eligible,
                    {
                        kind: len(set(shadow_success.get(kind, [])))
                        for kind in eligible
                    },
                )
                for kind in eligible:
                    engine_fingerprint = eligible[kind].get_manifest().get(
                        "engine_fingerprint"
                    )
                    for file_id in shadow_success[kind]:
                        record = db_manager.get_file_by_uuid(file_id, project_id)
                        db_manager.set_artifact_state(
                            project_id,
                            file_id,
                            kind,
                            "ready",
                            source_fingerprint=(record.source_fingerprint if record else None),
                            engine_fingerprint=engine_fingerprint,
                        )
                    for file_id, outcome in shadow_outcomes.get(kind, {}).items():
                        record = db_manager.get_file_by_uuid(file_id, project_id)
                        db_manager.set_artifact_state(
                            project_id,
                            file_id,
                            kind,
                            outcome["state"],
                            source_fingerprint=(record.source_fingerprint if record else None),
                            error_code=outcome.get("error_code"),
                            error_message=outcome.get("error_message"),
                        )
            for kind, indexer in vector_targets.items():
                if kind not in shadow_kinds:
                    db_manager.upsert_index_manifest(
                        project_id,
                        kind,
                        **_manifest_update_values(indexer.get_manifest()),
                        revision_increment=1,
                    )
                    continue
                if kind in eligible:
                    continue
                db_manager.upsert_index_manifest(
                    project_id, kind, state="rebuild_failed"
                )
        else:
            for kind, indexer in vector_targets.items():
                db_manager.upsert_index_manifest(
                    project_id,
                    kind,
                    **_manifest_update_values(indexer.get_manifest()),
                    revision_increment=1,
                )

        reset_optimized_searcher(project_id)
        reset_ai_chat_service(project_id)
        total_failures = sum(failures.values())
        if total_failures:
            db_manager.update_job(
                job_id,
                state="failed",
                stage="done_with_errors",
                error_code="index_items_failed",
                error_message=f"{total_failures} artifact(s) failed or remain pending",
            )
        else:
            db_manager.update_job(job_id, state="completed", stage="done")
    except Exception as exc:
        logger.exception(f"索引修复任务失败: {exc}")
        db_manager.update_job(
            job_id, state="failed", stage="failed",
            error_code="index_job_failed", error_message=str(exc),
        )


# ==================== 文件列表 ====================

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


# ==================== 音频裁切 ====================

def _render_audio_edit(
    source_file: Path,
    output_path: Path,
    *,
    start: float = 0.0,
    end: Optional[float] = None,
    fade_in: float = 0.0,
    fade_out: float = 0.0,
) -> Dict[str, Any]:
    """Decode once through AudioService and atomically export a PCM WAV."""
    import numpy as np
    from core.audio_service import get_audio_service, AudioServiceError

    service = get_audio_service()
    decoded = service.decode(source_file, mono=False)
    total_frames = int(decoded.samples.shape[0])
    start_frame = max(0, int(float(start) * decoded.sample_rate))
    end_frame = (
        total_frames
        if end is None
        else min(total_frames, int(float(end) * decoded.sample_rate))
    )
    if start_frame >= total_frames or end_frame <= start_frame:
        raise AudioServiceError(
            "invalid_clip_range",
            "裁切范围超出音频时长",
            details={"start": start, "end": end, "duration": decoded.duration},
        )
    samples = np.array(decoded.samples[start_frame:end_frame], copy=True)
    frame_count = int(samples.shape[0])
    fade_in_frames = min(frame_count, max(0, int(float(fade_in) * decoded.sample_rate)))
    fade_out_frames = min(frame_count, max(0, int(float(fade_out) * decoded.sample_rate)))
    if fade_in_frames:
        samples[:fade_in_frames] *= np.linspace(
            0.0, 1.0, fade_in_frames, dtype=np.float32
        )[:, None]
    if fade_out_frames:
        samples[-fade_out_frames:] *= np.linspace(
            1.0, 0.0, fade_out_frames, dtype=np.float32
        )[:, None]
    exported = service.export_wav(output_path, samples, decoded.sample_rate)
    return {
        "path": exported,
        "duration": float(frame_count / decoded.sample_rate),
    }


def _raise_audio_edit_error(exc: Exception) -> None:
    from core.audio_service import AudioServiceError

    if isinstance(exc, AudioServiceError):
        raise SoundBotAPIError(
            exc.code,
            exc.message,
            status_code=400 if exc.code == "invalid_clip_range" else 422,
            retryable=exc.retryable,
            details=exc.details,
        )
    raise exc

@app.post("/api/export/clip", response_model=schemas.ClipResponse)
async def export_clip(request: schemas.ClipRequest):
    """
    裁切音频片段

    - **path**: 源音频文件路径
    - **start**: 裁切起始时间（秒）
    - **end**: 裁切结束时间（秒）
    - **output**: 输出文件路径（可选，默认在原文件同目录添加 _clip 后缀）
    - **temp_file**: 是否创建临时文件（用于拖拽导出，会在系统临时目录创建）
    """
    logger.info(f"[裁切请求] path={request.path}, start={request.start}, end={request.end}, temp_file={request.temp_file}")

    source_file = config.validate_audio_path(request.path)

    if request.start >= request.end:
        raise HTTPException(status_code=400, detail="起始时间必须小于结束时间")

    try:
        if request.temp_file:
            import uuid
            temp_name = f"clip_{int(time.time())}_{uuid.uuid4().hex[:8]}.wav"
            output_path = Path(config.get_temp_clip_dir()) / temp_name
        elif request.output:
            if not validate_path(request.output):
                raise HTTPException(status_code=400, detail="输出路径包含非法字符")
            output_path = Path(request.output)
        else:
            output_path = source_file.parent / f"{source_file.stem}_clip.wav"
        result = await _run_audio_work(
            _render_audio_edit,
            source_file,
            output_path,
            start=request.start,
            end=request.end,
        )

        return schemas.ClipResponse(
            success=True,
            output_path=str(result["path"]),
            duration=result["duration"],
            message=f"成功裁切 {request.start:.2f}s - {request.end:.2f}s"
        )
    except (HTTPException, SoundBotAPIError):
        raise
    except Exception as e:
        logger.error(f"裁切失败: {e}")
        _raise_audio_edit_error(e)


# ==================== 删除临时文件 ====================

@app.delete("/api/temp/{file_path:path}")
async def delete_temp_file(file_path: str):
    """
    删除临时文件

    - **file_path**: 临时文件路径（URL编码）
    """
    import os

    try:
        # 安全检查：确保文件在临时目录内
        abs_path = Path(file_path).resolve()
        temp_dir_path = Path(config.get_temp_clip_dir()).resolve()
        try:
            abs_path.relative_to(temp_dir_path)
        except ValueError:
            raise HTTPException(status_code=400, detail="只能删除临时目录中的文件")

        # 删除文件
        if abs_path.exists():
            abs_path.unlink()
            return {"success": True, "message": f"已删除临时文件: {abs_path.name}"}
        else:
            return {"success": True, "message": "文件不存在"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除临时文件失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 音频淡入淡出 ====================

@app.post("/api/audio/fade", response_model=schemas.FadeResponse)
async def audio_fade(request: schemas.FadeRequest):
    """
    音频淡入淡出
    
    - **path**: 音频文件路径
    - **fade_in**: 淡入时长（秒）
    - **fade_out**: 淡出时长（秒）
    - **output**: 输出文件路径（可选，默认在原文件同目录添加 _fade 后缀）
    """
    source_file = config.validate_audio_path(request.path)
    
    try:
        if request.output:
            if not validate_path(request.output):
                raise HTTPException(status_code=400, detail="输出路径包含非法字符")
            output_path = Path(request.output)
        else:
            output_path = source_file.parent / f"{source_file.stem}_fade.wav"
        result = await _run_audio_work(
            _render_audio_edit,
            source_file,
            output_path,
            fade_in=request.fade_in,
            fade_out=request.fade_out,
        )
        
        return schemas.FadeResponse(
            success=True,
            output_path=str(result["path"]),
            message=f"淡入: {request.fade_in}s, 淡出: {request.fade_out}s"
        )
    except (HTTPException, SoundBotAPIError):
        raise
    except Exception as e:
        logger.error(f"淡入淡出处理失败: {e}")
        _raise_audio_edit_error(e)


# ==================== 裁切并淡入淡出 ====================

@app.post("/api/export/clip_with_fade", response_model=schemas.ClipResponse)
async def export_clip_with_fade(request: schemas.ClipWithFadeRequest):
    """
    裁切音频片段并应用淡入淡出

    - **path**: 源音频文件路径
    - **start**: 裁切起始时间（秒）
    - **end**: 裁切结束时间（秒）
    - **fade_in**: 淡入时长（秒）
    - **fade_out**: 淡出时长（秒）
    - **temp_file**: 是否创建临时文件
    """
    import uuid

    source_file = config.validate_audio_path(request.path)

    if request.start >= request.end:
        raise HTTPException(status_code=400, detail="起始时间必须小于结束时间")

    try:
        if request.temp_file:
            temp_name = f"clip_fade_{int(time.time())}_{uuid.uuid4().hex[:8]}.wav"
            output_path = Path(config.get_temp_clip_dir()) / temp_name
        else:
            output_path = source_file.parent / f"{source_file.stem}_clip_fade.wav"
        result = await _run_audio_work(
            _render_audio_edit,
            source_file,
            output_path,
            start=request.start,
            end=request.end,
            fade_in=request.fade_in,
            fade_out=request.fade_out,
        )

        return schemas.ClipResponse(
            success=True,
            output_path=str(result["path"]),
            duration=result["duration"],
            message=f"裁切 {request.start:.2f}s - {request.end:.2f}s, 淡入 {request.fade_in}s, 淡出 {request.fade_out}s"
        )
    except (HTTPException, SoundBotAPIError):
        raise
    except Exception as e:
        logger.error(f"裁切并淡入淡出失败: {e}")
        _raise_audio_edit_error(e)


# ==================== 验证临时文件 ====================

@app.get("/api/clip/verify")
async def verify_clip(file_path: str = Query(..., description="临时文件路径")):
    """
    验证临时文件是否存在

    - **file_path**: 临时文件路径（URL编码）
    """
    abs_path = Path(file_path).resolve()
    temp_dir_path = Path(config.get_temp_clip_dir()).resolve()
    try:
        abs_path.relative_to(temp_dir_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="只能验证临时目录中的文件")

    exists = abs_path.exists() and abs_path.is_file()

    return {
        "exists": exists,
        "path": str(abs_path),
        "size": abs_path.stat().st_size if exists else 0
    }


# ==================== 临时文件路径配置 ====================

@app.get("/api/v1/config/temp-dir", response_model=schemas.TempDirResponse)
async def get_temp_dir():
    """
    获取当前临时文件存放目录
    """
    return schemas.TempDirResponse(
        temp_dir=config.get_temp_clip_dir(),
        default_dir=config.DEFAULT_TEMP_CLIP_DIR
    )


@app.post("/api/v1/config/temp-dir", response_model=schemas.TempDirResponse)
async def set_temp_dir(request: schemas.TempDirRequest):
    """
    设置临时文件存放目录
    
    - **temp_dir**: 新的临时文件目录路径
    """
    import json
    
    new_dir = request.temp_dir
    
    # 验证路径安全性，防止路径遍历攻击
    if not validate_path(new_dir):
        raise HTTPException(status_code=400, detail="路径包含非法字符")
    
    # 验证路径是否存在
    if not os.path.exists(new_dir):
        raise HTTPException(status_code=400, detail="指定的目录不存在")
    
    if not os.path.isdir(new_dir):
        raise HTTPException(status_code=400, detail="指定的路径不是目录")
    
    # 保存到用户数据目录；config.get_temp_clip_dir() 也从这里读取
    config_path = config.get_user_data_dir() / "user_config.json"
    
    try:
        # 确保配置目录存在
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 读取现有配置或创建新配置
        if config_path.exists():
            with config_path.open('r', encoding='utf-8') as f:
                current_config = json.load(f)
        else:
            current_config = {}
        
        # 更新临时文件目录
        current_config['tempClipDir'] = new_dir
        
        # 保存配置
        with config_path.open('w', encoding='utf-8') as f:
            json.dump(current_config, f, ensure_ascii=False, indent=2)

        config.TEMP_CLIP_DIR = config.get_temp_clip_dir()
        
        logger.info(f"临时文件目录已更新: {new_dir}")
        
        return schemas.TempDirResponse(
            temp_dir=new_dir,
            default_dir=config.DEFAULT_TEMP_CLIP_DIR,
            message="临时文件目录设置成功"
        )
        
    except Exception as e:
        logger.error(f"设置临时文件目录失败: {e}")
        raise HTTPException(status_code=500, detail=f"设置失败: {str(e)}")


@app.get("/api/v1/disk-space")
async def get_disk_space():
    """
    获取临时文件目录所在磁盘的空间信息
    """
    try:
        import shutil
        temp_dir = config.get_temp_clip_dir()
        
        # 获取磁盘使用情况
        usage = shutil.disk_usage(temp_dir)
        
        return {
            "success": True,
            "path": temp_dir,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free
        }
    except Exception as e:
        logger.error(f"获取磁盘空间失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取磁盘空间失败: {str(e)}")


@app.post("/api/v1/temp-clips/clear")
async def clear_temp_clips():
    """
    清理所有临时裁切文件
    """
    try:
        temp_dir = config.get_temp_clip_dir()
        
        if not os.path.exists(temp_dir):
            return {
                "success": True,
                "deleted_count": 0,
                "freed_space": 0,
                "message": "临时文件目录不存在"
            }
        
        deleted_count = 0
        freed_space = 0
        
        # 遍历并删除所有文件
        for item in os.listdir(temp_dir):
            item_path = os.path.join(temp_dir, item)
            try:
                if os.path.isfile(item_path):
                    file_size = os.path.getsize(item_path)
                    os.remove(item_path)
                    deleted_count += 1
                    freed_space += file_size
                elif os.path.isdir(item_path):
                    # 递归删除子目录
                    import shutil
                    dir_size = sum(os.path.getsize(os.path.join(dirpath, filename)) 
                                  for dirpath, dirnames, filenames in os.walk(item_path) 
                                  for filename in filenames)
                    shutil.rmtree(item_path)
                    deleted_count += 1
                    freed_space += dir_size
            except Exception as e:
                logger.warning(f"删除文件失败 {item_path}: {e}")
        
        logger.info(f"清理临时文件完成: 删除 {deleted_count} 项, 释放 {freed_space} 字节")
        
        return {
            "success": True,
            "deleted_count": deleted_count,
            "freed_space": freed_space,
            "message": f"已清理 {deleted_count} 个文件，释放 {freed_space / (1024*1024):.2f} MB"
        }
    except Exception as e:
        logger.error(f"清理临时文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}")


# ==================== 工程管理 API ====================

@app.post("/api/v1/projects")
async def create_project(request: schemas.CreateProjectRequest):
    """
    创建新工程

    - **name**: 工程名称
    - **description**: 工程描述
    - **temp_dir**: 工程特定的临时文件目录
    """
    try:
        import uuid
        project_id = str(uuid.uuid4())

        db_manager = get_db_manager()
        success = db_manager.create_project(
            project_id=project_id,
            name=request.name,
            description=request.description,
            temp_dir=request.temp_dir
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
            temp_dir=request.temp_dir,
            settings=request.settings
        )

        if not success:
            raise HTTPException(status_code=400, detail="更新工程失败")

        if getattr(config, 'CURRENT_PROJECT_ID', 'default') == project_id:
            active_temp_dir = request.temp_dir if request.temp_dir is not None else existing.get('temp_dir')
            config.set_project_temp_clip_dir(active_temp_dir)

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
    """
    删除工程（会级联删除所有相关文件、向量数据库和缓存）
    """
    if project_id in _projects_deleting:
        raise SoundBotAPIError("project_deleting", "工程正在删除", status_code=409)
    _projects_deleting.add(project_id)
    try:
        if project_id == "default":
            raise SoundBotAPIError(
                "default_project_protected", "默认工程不能删除", status_code=400
            )
        if not is_safe_project_id(project_id):
            raise SoundBotAPIError("invalid_project_id", "工程 ID 非法", status_code=400)
        db_manager = get_db_manager()

        # 检查工程是否存在
        existing = db_manager.get_project(project_id)
        if not existing:
            raise SoundBotAPIError("project_not_found", "工程不存在", status_code=404)

        # Stop all accepted writers before waiting for the per-project lock.
        cancelled_jobs = db_manager.request_project_job_cancellation(project_id)
        for job_id in cancelled_jobs:
            get_ws_manager().cancel_task(job_id)

        # 检查是否是当前工程
        current_project_id = getattr(config, 'CURRENT_PROJECT_ID', None)
        is_current_project = (current_project_id == project_id)

        async with _get_project_index_lock(project_id):
            # Catch jobs that were accepted just before the deletion flag was set.
            for job_id in db_manager.request_project_job_cancellation(project_id):
                get_ws_manager().cancel_task(job_id)

            # SQLite is authoritative. If this fails, retain the searchable
            # collections; if it succeeds, an orphan index directory is harmless
            # and can be cleaned on the next attempt/startup.
            success = db_manager.delete_project(project_id)
            if not success:
                raise SoundBotAPIError(
                    "project_delete_failed",
                    "工程数据删除失败，原索引仍保留",
                    status_code=503,
                    retryable=True,
                )

            from core.search_engine import reset_optimized_searcher
            from core.ai_chat_service import reset_ai_chat_service
            from core.indexer import delete_project_index

            reset_optimized_searcher(project_id)
            reset_ai_chat_service(project_id)
            reset_searcher(project_id)
            index_deleted = await _run_model_work(delete_project_index, project_id)
            if not index_deleted:
                logger.warning(f"工程 {project_id} 已从 SQLite 删除；孤立索引目录稍后清理")

        # 如果删除的是当前工程，清理缓存并切换到默认工程
        if is_current_project:
            logger.info(f"删除的是当前工程 {project_id}，清理缓存并切换到默认工程")

            # 先切换到默认工程
            config.CURRENT_PROJECT_ID = 'default'
            config.set_project_temp_clip_dir(None)
            logger.info("已切换到默认工程")

            # 重置 Searcher
            reset_optimized_searcher('default')
            logger.info("Searcher 已重置")

        return {
            "success": True,
            "message": "工程已删除",
            "index_deleted": index_deleted,
            "was_current_project": is_current_project,
            "switched_to_default": is_current_project
        }
    except SoundBotAPIError:
        raise
    except Exception as e:
        logger.error(f"删除工程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _projects_deleting.discard(project_id)


@app.post("/api/v1/projects/{project_id}/switch")
async def switch_project(project_id: str):
    """
    切换到指定工程

    会将工程添加到最近工程列表，同时切换向量数据库和清理缓存
    """
    try:
        if not is_safe_project_id(project_id):
            raise SoundBotAPIError("invalid_project_id", "工程 ID 非法", status_code=400)
        # Validate before mutating any global state or clearing working caches.
        db_manager = get_db_manager()
        project = db_manager.get_project(project_id)
        if not project:
            raise SoundBotAPIError("project_not_found", "工程不存在", status_code=404)

        # 获取当前工程ID（用于判断是否真的切换了）
        old_project_id = getattr(config, 'CURRENT_PROJECT_ID', None)

        # 如果确实切换了工程，先更新当前工程ID，然后清理缓存
        if old_project_id and old_project_id != project_id:
            logger.info(f"切换工程: {old_project_id} -> {project_id}，开始清理缓存")

            # Project-scoped caches include the project/revision in their keys.
            # Only discard the two affected project views; live jobs for other
            # projects keep their Chroma clients untouched.
            from core.search_engine import reset_optimized_searcher
            reset_optimized_searcher(old_project_id)
            reset_optimized_searcher(project_id)
            logger.info("Searcher 已重置")

            from core.ai_chat_service import reset_ai_chat_service
            reset_ai_chat_service(old_project_id)
            reset_ai_chat_service(project_id)
            logger.info("AI Chat 工程上下文已重置")

        # 添加到最近工程
        db_manager.add_to_recent_projects(project_id)

        # 更新全局配置中的当前工程
        config.CURRENT_PROJECT_ID = project_id

        # 切换工程特定的临时目录。为空时回退到用户全局配置或默认目录。
        active_temp_dir = config.set_project_temp_clip_dir(project.get('temp_dir'))
        logger.info(f"当前临时文件目录: {active_temp_dir}")

        # 获取该工程的向量数据库信息
        from core.embedder import is_embedder_available

        indexer = _get_active_audio_indexer(project_id)
        indexed_count = indexer.get_indexed_count()

        embedder_available = is_embedder_available()

        return {
            "success": True,
            "project_id": project_id,
            "project_name": project['name'],
            "message": f"已切换到工程 '{project['name']}'",
            "cache_cleared": old_project_id != project_id,
            "vector_db": {
                "indexed_count": indexed_count,
                "embedder_available": embedder_available
            }
        }
    except SoundBotAPIError:
        raise
    except Exception as e:
        logger.error(f"切换工程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 工程文件和文件夹管理 ====================

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
async def update_user_folder(
    project_id: str,
    folder_id: str,
    request: UpdateFolderRequest,
    background_tasks: BackgroundTasks,
):
    """
    更新用户自定义文件夹
    """
    try:
        async with _get_project_index_lock(project_id):
            db_manager = get_db_manager()
            if project_id in _projects_deleting:
                raise HTTPException(status_code=409, detail="工程正在删除")
            folder = db_manager.get_user_folder(folder_id)
            if not folder or folder['project_id'] != project_id:
                raise HTTPException(status_code=404, detail="文件夹不存在")
            affected_paths = [
                mapping["folder_path"]
                for mapping in db_manager.get_imported_folder_mappings(
                    project_id, folder_id
                )
            ]
            success = db_manager.update_user_folder(
                folder_id=folder_id,
                name=request.name,
                description=request.description,
                color=request.color,
                sort_order=request.sort_order
            )
            if not success:
                raise HTTPException(status_code=400, detail="更新文件夹失败")
            invalidation = (
                _invalidate_text_metadata_for_paths_locked(
                    project_id, affected_paths, background_tasks
                )
                if request.name is not None or request.description is not None
                else {"affected_files": 0, "reindex_job_id": None}
            )
            return {
                "success": True,
                "message": "文件夹更新成功",
                **invalidation,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新用户文件夹失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/v1/projects/{project_id}/folders/{folder_id}")
async def delete_user_folder(
    project_id: str,
    folder_id: str,
    background_tasks: BackgroundTasks,
):
    """
    删除用户自定义文件夹

    删除后，该文件夹下的导入文件夹将变为未分类状态
    """
    try:
        async with _get_project_index_lock(project_id):
            db_manager = get_db_manager()
            if project_id in _projects_deleting:
                raise HTTPException(status_code=409, detail="工程正在删除")
            folder = db_manager.get_user_folder(folder_id)
            if not folder or folder['project_id'] != project_id:
                raise HTTPException(status_code=404, detail="文件夹不存在")
            affected_paths = [
                mapping["folder_path"]
                for mapping in db_manager.get_imported_folder_mappings(
                    project_id, folder_id
                )
            ]
            if not db_manager.delete_user_folder(folder_id):
                raise HTTPException(status_code=400, detail="删除文件夹失败")
            invalidation = _invalidate_text_metadata_for_paths_locked(
                project_id, affected_paths, background_tasks
            )
            return {
                "success": True,
                "message": f"文件夹 '{folder['name']}' 已删除",
                **invalidation,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除用户文件夹失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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


@app.post("/api/v1/projects/{project_id}/folder-mappings/{folder_path:path}")
async def update_folder_mapping(
    project_id: str,
    folder_path: str,
    background_tasks: BackgroundTasks,
    user_folder_id: Optional[str] = None,
):
    """
    更新导入文件夹的用户文件夹关联

    - **user_folder_id**: 用户文件夹ID，为空表示取消关联（变为未分类）
    """
    try:
        async with _get_project_index_lock(project_id):
            db_manager = get_db_manager()
            if project_id in _projects_deleting:
                raise HTTPException(status_code=409, detail="工程正在删除")
            if not db_manager.get_project(project_id):
                raise HTTPException(status_code=404, detail="工程不存在")
            if user_folder_id:
                target_folder = db_manager.get_user_folder(user_folder_id)
                if not target_folder or target_folder["project_id"] != project_id:
                    raise HTTPException(status_code=404, detail="目标文件夹不存在")
            mapping = _find_mapping_by_path(
                db_manager.get_imported_folder_mappings(project_id), folder_path
            )
            if not mapping:
                raise HTTPException(status_code=404, detail="导入文件夹映射不存在")
            stored_path = str(mapping["folder_path"])
            if not db_manager.update_imported_folder_mapping(
                project_id, stored_path, user_folder_id
            ):
                raise HTTPException(status_code=400, detail="更新文件夹映射失败")
            changed = (mapping.get("user_folder_id") or None) != (user_folder_id or None)
            invalidation = (
                _invalidate_text_metadata_for_paths_locked(
                    project_id, [stored_path], background_tasks
                )
                if changed
                else {"affected_files": 0, "reindex_job_id": None}
            )
            return {
                "success": True,
                "message": "文件夹分类已更新",
                **invalidation,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新文件夹映射失败: {e}")
        raise HTTPException(status_code=500, detail="更新文件夹映射失败，请检查日志")


# ==================== AI Chat API ====================

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


def _text_embedding_config_signature(provider: str, provider_config: Dict[str, Any]) -> str:
    """Fingerprint only settings that can change persisted text vectors."""
    import hashlib

    relevant = {
        "provider": provider,
        "type": provider_config.get("type"),
        "base_url": str(provider_config.get("base_url", "")).rstrip("/"),
        "model": provider_config.get("model"),
        "model_name": provider_config.get("model_name"),
        "dimension": provider_config.get("dimension"),
        "revision": provider_config.get("revision"),
        "preprocessing_version": provider_config.get("preprocessing_version"),
    }
    encoded = json.dumps(relevant, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
async def save_ai_config(
    request: schemas.AIConfigRequest,
    background_tasks: BackgroundTasks,
):
    """
    保存 AI 配置
    
    - **llm_provider**: LLM 提供者 (lm_studio/ollama/external)
    - **llm_config**: LLM 提供者配置
    - **embedding_provider**: Embedding 提供者 (default/local/external)
    - **embedding_config**: Embedding 提供者配置
    """
    try:
        from core.llm_config_manager import get_llm_config_manager
        from core.llm_client import reset_llm_client
        from core.ai_chat_service import reset_ai_chat_service
        from core.search_engine import reset_optimized_searcher
        
        config_manager = get_llm_config_manager()
        old_embedding = config_manager.get_embedding_config()
        old_provider = config_manager.get_embedding_provider()
        old_signature = _text_embedding_config_signature(
            old_provider, old_embedding.get(old_provider, {})
        )
        
        config_manager.save_full_config(
            llm_provider=request.llm_provider,
            llm_config=request.llm_config,
            embedding_provider=request.embedding_provider,
            embedding_config=request.embedding_config
        )

        new_embedding = config_manager.get_embedding_config()
        new_provider = config_manager.get_embedding_provider()
        new_signature = _text_embedding_config_signature(
            new_provider, new_embedding.get(new_provider, {})
        )
        stale_projects = []
        if old_signature != new_signature:
            try:
                db_manager = get_db_manager()
                for project in db_manager.get_all_projects():
                    project_id = project["id"]
                    changed = db_manager.mark_project_artifacts(
                        project_id, ["text_vector"], "stale"
                    )
                    db_manager.upsert_index_manifest(
                        project_id,
                        "text_vector",
                        engine_fingerprint=new_signature,
                        state="stale",
                        revision_increment=1,
                    )
                    reset_optimized_searcher(project_id)
                    job_id = db_manager.create_job(
                        project_id, "index_rebuild", changed
                    )
                    background_tasks.add_task(
                        _repair_index_task,
                        job_id,
                        project_id,
                        ["text_vector"],
                        "rebuild",
                    )
                    stale_projects.append({
                        "project_id": project_id,
                        "files": changed,
                        "job_id": job_id,
                    })
            except Exception as index_error:
                # Metadata/credential saving already succeeded.  Do not make
                # Electron roll back its secure-store transaction; report the
                # index invalidation problem without including config values.
                logger.exception("文本索引失效标记失败: %s", index_error)
                stale_projects.append({
                    "project_id": None,
                    "files": 0,
                    "error": "text_index_invalidation_failed",
                })
        
        # 重置 LLM 客户端和 AI Chat 服务以应用新配置
        reset_llm_client()
        reset_ai_chat_service()
        
        return {
            "success": True,
            "message": "配置已保存",
            "text_index_stale": stale_projects,
        }
    except Exception as e:
        logger.error(f"保存 AI 配置失败: {e}")
        raise HTTPException(status_code=500, detail="保存配置失败，请检查日志")


@app.post("/api/v1/ai/config/test")
async def test_ai_config(request: schemas.AIConfigRequest):
    """
    测试 AI 配置连接
    
    - **llm_provider**: LLM 提供者
    - **llm_config**: LLM 提供者配置
    - **embedding_provider**: Embedding 提供者
    - **embedding_config**: Embedding 提供者配置
    """
    try:
        from core.llm_config_manager import get_llm_config_manager
        
        config_manager = get_llm_config_manager()
        
        # 测试 LLM 连接
        llm_result = await config_manager.test_llm_connection(
            provider=request.llm_provider,
            provider_config=request.llm_config
        )
        
        # 测试 Embedding 连接
        embedding_result = await config_manager.test_embedding_connection(
            provider=request.embedding_provider,
            provider_config=request.embedding_config
        )
        
        return {
            "success": llm_result.get("success", False) and embedding_result.get("success", False),
            "llm": llm_result,
            "embedding": embedding_result
        }
    except Exception as e:
        logger.error(f"测试 AI 配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/ai/status")
async def get_ai_status():
    """
    获取 AI 服务状态
    
    返回 LLM 和 Embedding 的可用状态
    """
    try:
        from core.llm_config_manager import get_llm_config_manager
        from core.llm_client import get_llm_client
        from core.embedder import is_embedder_available
        
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
        embedding_available = is_embedder_available()
        
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
