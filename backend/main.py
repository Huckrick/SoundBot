# -*- coding: utf-8 -*-
# SoundMind Backend

"""FastAPI 后端服务，用于音效管理器的 AI 语义搜索功能。"""

import os
import time
import asyncio
import json
from pathlib import Path
from typing import Optional, List
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Query, Path as PathParam, BackgroundTasks, WebSocket, WebSocketDisconnect, Body
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

import config
from models import schemas
from core.indexer import get_indexer, AudioIndexer, reset_indexer
from core.searcher import get_searcher, AudioSearcher, reset_searcher
from core.embedder import get_embedder, reset_embedder, is_embedder_available
from core.database import get_db_manager, reset_db_manager, AudioFileRecord
from core.websocket_manager import get_ws_manager, reset_ws_manager
from utils.logger import logger

# 线程池用于 CPU 密集型任务
_executor = ThreadPoolExecutor(max_workers=4)


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
    logger.info(f"SoundMind API 启动中...")
    logger.info(f"设备: {config.get_device()}")
    logger.info(f"数据库路径: {config.get_db_path()}")

    # 初始化 SQLite 数据库
    db_manager = get_db_manager()
    file_count = db_manager.get_file_count()
    logger.info(f"SQLite 数据库已加载，当前文件数: {file_count}")

    # 使用动态获取的临时文件目录
    temp_dir = Path(config.get_temp_clip_dir())
    temp_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"临时文件目录: {temp_dir}")

    logger.info("临时文件由用户自行管理，不执行自动清理")

    yield

    logger.info("SoundMind API 关闭中...")
    logger.info("临时文件由用户自行管理，不执行自动清理")
    reset_embedder()
    reset_indexer()
    reset_searcher()
    reset_db_manager()
    reset_ws_manager()
    logger.info("全局单例状态已清理")


# 创建 FastAPI 应用
app = FastAPI(
    title="SoundMind API",
    description="AI 音效管理器的语义搜索后端",
    version=config.APP_VERSION,
    lifespan=lifespan
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
        device=config.get_device()
    )


# ==================== 扫描与索引 ====================

@app.post("/api/v1/scan", response_model=schemas.IndexResponse)
async def scan_and_index(request: schemas.ScanRequest):
    """
    扫描音频文件并建立索引
    
    - **folder_path**: 要扫描的文件夹路径
    - **recursive**: 是否递归扫描子文件夹
    """
    folder = Path(request.folder_path)
    
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"文件夹不存在: {request.folder_path}")
    
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不是文件夹: {request.folder_path}")
    
    try:
        start_time = time.time()
        
        # 获取或创建索引器
        indexer = get_indexer()
        
        # 执行索引
        result = indexer.index_audio_files(
            folder_path=str(folder),
            recursive=request.recursive
        )
        
        duration = time.time() - start_time
        
        # 返回扫描到的文件数量
        return schemas.IndexResponse(
            indexed=result.get("added", 0) + result.get("updated", 0),
            skipped=result.get("skipped", 0),
            duration=duration
        )
        
    except Exception as e:
        logger.error(f"扫描索引失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 仅扫描文件（不建索引）====================
@app.post("/api/v1/scan-only", response_model=schemas.ScanResponse)
async def scan_only(request: schemas.ScanRequest):
    """
    仅扫描音频文件，不建立索引（用于没有模型的情况）
    """
    folder = Path(request.folder_path)
    
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"文件夹不存在: {request.folder_path}")
    
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不是文件夹: {request.folder_path}")
    
    try:
        from core.scanner import AudioScanner
        scanner = AudioScanner()
        audio_files = scanner.scan(str(folder), request.recursive)
        
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

@app.post("/api/v1/import/async")
async def import_folder_async(
    request: schemas.ScanRequest,
    background_tasks: BackgroundTasks,
    client_id: str = Query(default="default")
):
    """
    异步导入文件夹（带进度推送）

    - **folder_path**: 要导入的文件夹路径
    - **recursive**: 是否递归扫描子文件夹
    - **client_id**: WebSocket 客户端标识（前端生成）

    后台执行扫描和导入，通过 WebSocket 推送进度。
    """
    folder = Path(request.folder_path)

    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=400, detail="无效的文件夹路径")

    task_id = f"scan_{int(time.time() * 1000)}"
    ws_manager = get_ws_manager()
    ws_manager.register_task(task_id, client_id)

    # 后台执行扫描
    background_tasks.add_task(
        _scan_and_import_task,
        task_id=task_id,
        folder_path=str(folder),
        recursive=request.recursive,
        client_id=client_id
    )

    return {"task_id": task_id, "message": "扫描任务已启动"}


async def _scan_and_import_task(
    task_id: str,
    folder_path: str,
    recursive: bool,
    client_id: str
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
    import librosa
    import numpy as np

    # #region agent log
    import json as json_module
    def write_log(msg_type, msg, data=None):
        try:
            log_path = '/Users/huyang/Downloads/SoundMind/.cursor/debug-95ddf7.log'
            log_entry = {
                'sessionId': '95ddf7',
                'id': f'log_{int(time.time()*1000)}',
                'timestamp': int(time.time()*1000),
                'location': 'main.py:_scan_and_import_task',
                'message': msg,
                'data': data or {},
                'runId': 'debug',
                'hypothesisId': 'H1'
            }
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json_module.dumps(log_entry) + '\n')
        except Exception:
            pass
    write_log('info', '任务开始', {'task_id': task_id, 'folder_path': folder_path, 'recursive': recursive, 'client_id': client_id})
    # #endregion
    
    # 初始化管理器
    ws_manager = get_ws_manager()
    db_manager = get_db_manager()
    scanner = AudioScanner()
    
    # 添加详细日志
    logger.info(f"[SCAN_TASK] 任务ID: {task_id}, 文件夹: {folder_path}, 递归: {recursive}, 客户端: {client_id}")
    logger.info(f"[SCAN_TASK] ws_manager 连接数: {ws_manager.get_connection_count()}")
    logger.info(f"[SCAN_TASK] 活跃连接: {list(ws_manager.active_connections.keys())}")

    try:
        # 第一步：扫描所有文件
        logger.info(f"[SCAN_TASK] 发送扫描状态: 正在扫描文件...")
        await ws_manager.send_scan_status(
            client_id, task_id, "scanning", "正在扫描文件..."
        )
        logger.info(f"[SCAN_TASK] 扫描状态已发送，当前连接数: {ws_manager.get_connection_count()}")
        write_log('info', '开始扫描文件夹', {'folder_path': folder_path})

        # 使用 scan_with_structure 获取文件列表和文件夹结构
        audio_files, folder_structure = scanner.scan_with_structure(folder_path, recursive)
        total = len(audio_files)
        logger.info(f"[SCAN_TASK] 扫描完成，找到 {total} 个音频文件")
        write_log('info', '扫描完成', {'total': total, 'files': [f.path for f in audio_files[:5]]})  # 只记录前5个

        # 发送扫描统计日志到前端
        await ws_manager.send_scan_log(
            client_id, task_id, 'info',
            f"扫描完成统计: 找到 {total} 个音频文件",
            {'total': total, 'folder_path': folder_path}
        )

        # 发送文件夹结构到前端
        await ws_manager.send_folder_structure(
            client_id, task_id, folder_structure.dict()
        )

        if total == 0:
            write_log('warning', '未找到音频文件')
            await ws_manager.send_scan_complete(
                client_id, task_id, 0, 0, 0, "未找到音频文件"
            )
            ws_manager.unregister_task(task_id)
            return

        logger.info(f"开始导入 {total} 个文件到 SQLite")
        write_log('info', '开始导入文件', {'total': total})

        # 第二步：逐个处理文件
        added = 0
        skipped = 0

        for i, audio_file in enumerate(audio_files):
            # 检查是否取消
            if ws_manager.is_task_cancelled(task_id):
                write_log('info', '任务被取消', {'processed': i, 'added': added})
                await ws_manager.send_scan_complete(
                    client_id, task_id, total, added, i - added,
                    "用户取消"
                )
                ws_manager.unregister_task(task_id)
                return

            current_file = Path(audio_file.path).name

            # 发送进度
            await ws_manager.send_scan_progress(
                client_id, task_id, i, total, current_file, "processing"
            )

            # 检查是否已存在
            if db_manager.file_exists(audio_file.path):
                skipped += 1
                continue

            # 计算波形峰值
            peaks_json = None
            try:
                # 使用 librosa 加载并计算峰值
                y, sr = librosa.load(audio_file.path, sr=None, mono=True)

                # 降采样到 2000 个点
                target_points = 2000
                samples_per_point = max(1, len(y) // target_points)
                peaks = []
                for j in range(target_points):
                    start = j * samples_per_point
                    end = min((j + 1) * samples_per_point, len(y))
                    if end > start:
                        peak = float(np.max(np.abs(y[start:end])))
                        peaks.append(peak)

                peaks_json = json.dumps(peaks)
            except Exception as e:
                logger.warning(f"计算波形失败 {audio_file.path}: {e}")
                write_log('warning', '计算波形失败', {'path': audio_file.path, 'error': str(e)})

            # 写入数据库（使用当前工程ID）
            record = AudioFileRecord(
                path=audio_file.path,
                filename=audio_file.filename,
                duration=audio_file.duration,
                sample_rate=audio_file.sample_rate,
                channels=audio_file.channels,
                file_size=audio_file.size,
                peaks_json=peaks_json,
                tags='[]'
            )
            success = db_manager.add_file(record, config.CURRENT_PROJECT_ID)
            if success:
                added += 1
                if added % 10 == 0:  # 每10个文件记录一次
                    write_log('info', '已添加文件', {'added': added, 'current': current_file})

        write_log('info', '导入完成', {'total': total, 'added': added, 'skipped': skipped})
        # 完成
        await ws_manager.send_scan_complete(
            client_id, task_id, total, added, skipped
        )
        ws_manager.unregister_task(task_id)
        logger.info(f"导入完成: 新增 {added} 个文件，跳过 {skipped} 个")

    except Exception as e:
        logger.error(f"扫描导入失败: {e}")
        write_log('error', '扫描导入失败', {'error': str(e)})
        await ws_manager.send_scan_error(client_id, task_id, str(e))
        ws_manager.unregister_task(task_id)


# ==================== SQLite 数据库 API ====================

@app.get("/api/v1/db/files")
async def get_all_db_files():
    """
    从 SQLite 获取所有文件列表（启动时加载）

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
    import time
    start_time = time.time()

    # #region agent log
    import json as json_module
    def write_log(msg_type, msg, data=None):
        try:
            log_path = '/Users/huyang/Downloads/SoundMind/.cursor/debug-95ddf7.log'
            log_entry = {
                'sessionId': '95ddf7',
                'id': f'log_{int(time.time()*1000)}',
                'timestamp': int(time.time()*1000),
                'location': 'main.py:get_all_db_files',
                'message': msg,
                'data': data or {},
                'runId': 'debug',
                'hypothesisId': 'H2'
            }
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json_module.dumps(log_entry) + '\n')
        except Exception:
            pass
    write_log('info', '开始获取文件列表')
    # #endregion

    try:
        db_manager = get_db_manager()
        files = db_manager.get_all_files()
        write_log('info', '从数据库读取文件', {'count': len(files)})

        # 构建返回数据
        result_files = []
        total_peaks_size = 0
        long_durations = []

        for f in files:
            peaks = f.get_peaks()
            peaks_len = len(peaks) if peaks else 0
            total_peaks_size += peaks_len

            if f.duration > 60:
                long_durations.append({'filename': f.filename, 'duration': f.duration, 'peaks_len': peaks_len})

            result_files.append({
                "path": f.path,
                "filename": f.filename,
                "duration": f.duration,
                "sample_rate": f.sample_rate,
                "channels": f.channels,
                "size": f.file_size,
                "peaks": peaks,
                "tags": f.get_tags(),
                "created_at": f.created_at
            })

        elapsed = time.time() - start_time
        write_log('info', '构建返回数据完成', {
            'file_count': len(files),
            'total_peaks': total_peaks_size,
            'long_durations_count': len(long_durations),
            'elapsed_sec': round(elapsed, 3),
            'top_long_durations': long_durations[:5]
        })

        return {
            "total": len(files),
            "files": result_files
        }
    except Exception as e:
        logger.error(f"获取文件列表失败: {e}")
        write_log('error', '获取文件列表失败', {'error': str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/db/file/{path:path}")
async def get_db_file(path: str):
    """
    获取单个文件详情

    - **path**: URL 编码的文件路径
    """
    import urllib.parse
    file_path = urllib.parse.unquote(path)

    db_manager = get_db_manager()
    record = db_manager.get_file(file_path)

    if not record:
        raise HTTPException(status_code=404, detail="文件不存在")

    return {
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
async def update_file_tags(path: str, tags: List[str] = Body(...)):
    """
    更新文件标签

    - **path**: URL 编码的文件路径
    - **tags**: 新的标签列表
    """
    import urllib.parse
    file_path = urllib.parse.unquote(path)

    db_manager = get_db_manager()
    success = db_manager.update_tags(file_path, tags)

    if not success:
        raise HTTPException(status_code=404, detail="文件不存在")

    return {"success": True, "message": "标签已更新"}


@app.delete("/api/v1/db/file/{path:path}")
async def delete_db_file(path: str):
    """
    从数据库删除文件记录

    - **path**: URL 编码的文件路径
    """
    import urllib.parse
    file_path = urllib.parse.unquote(path)

    db_manager = get_db_manager()
    success = db_manager.delete_file(file_path)

    if not success:
        raise HTTPException(status_code=404, detail="文件不存在")

    return {"success": True, "message": "文件已删除"}


@app.get("/api/v1/db/stats")
async def get_db_stats():
    """
    获取数据库统计信息
    """
    try:
        db_manager = get_db_manager()
        return {
            "total_files": db_manager.get_file_count(),
            "total_duration": db_manager.get_total_duration()
        }
    except Exception as e:
        logger.error(f"获取统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 语义搜索 ====================

@app.post("/api/v1/search", response_model=schemas.SearchResponse)
async def search_audio(request: schemas.SearchRequest):
    """
    语义搜索音频
    
    - **query**: 自然语言查询（如"清脆的铃铛声"）
    - **top_k**: 返回结果数量（默认 20）
    - **threshold**: 相似度阈值（默认 0.15）
    """
    try:
        # 获取或创建搜索器
        searcher = get_searcher()
        
        # 执行搜索
        results = searcher.search(
            query=request.query,
            top_k=request.top_k,
            min_similarity=request.threshold
        )
        
        # 转换为响应格式
        search_results = []
        for r in results:
            audio_file = schemas.AudioFile(
                path=r.file_path,
                filename=r.filename,
                duration=r.duration,
                sample_rate=r.metadata.get("sample_rate", 0),
                channels=r.metadata.get("channels", 0),
                format=r.format,
                size=r.metadata.get("size", 0)
            )
            search_results.append(schemas.SearchResult(
                audio_file=audio_file,
                score=r.similarity,
                distance=1.0 - r.similarity
            ))
        
        return schemas.SearchResponse(
            query=request.query,
            total=len(search_results),
            results=search_results
        )
        
    except Exception as e:
        logger.error(f"搜索失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 音频波形 ====================

@app.get("/api/waveform")
async def get_waveform(path: str = Query(..., description="音频文件路径")):
    """
    获取音频波形数据

    将原始波形降采样到 2000 个峰值点，用于前端波形显示

    返回格式：
    {
        "peaks": [0.1, 0.4, -0.3, ...],  # 降采样后的峰值数组
        "duration": 12.4,                  # 时长（秒）
        "sample_rate": 48000,               # 采样率
        "channels": 2                       # 声道数
    }
    """
    import urllib.parse
    import librosa
    import numpy as np

    file_path = urllib.parse.unquote(path)

    audio_file = config.validate_audio_path(file_path)
    
    try:
        # 加载音频
        y, sr = librosa.load(str(audio_file), sr=None, mono=False)
        
        # 获取基本信息
        duration = librosa.get_duration(y=y, sr=sr)
        channels = 1 if y.ndim == 1 else y.shape[0]
        
        # 转换为单声道进行波形处理
        y_mono = librosa.to_mono(y)
        
        # 降采样到 2000 个点
        target_points = 2000
        samples_per_point = len(y_mono) // target_points
        
        if samples_per_point > 0:
            # 计算每个区间的峰值（绝对值最大）
            peaks = []
            for i in range(target_points):
                start = i * samples_per_point
                end = min((i + 1) * samples_per_point, len(y_mono))
                segment = y_mono[start:end]
                if len(segment) > 0:
                    peak = np.max(np.abs(segment))
                    peaks.append(float(peak))
                else:
                    peaks.append(0.0)
        else:
            # 如果音频太短，直接返回全部数据
            peaks = y_mono.tolist()[:target_points]
        
        return {
            "peaks": peaks,
            "duration": duration,
            "sample_rate": sr,
            "channels": channels
        }
        
    except Exception as e:
        logger.error(f"获取波形失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 音频文件服务 ====================

@app.get("/api/v1/audio/{file_path:path}")
async def get_audio(file_path: str = PathParam(..., description="音频文件路径")):
    """
    提供音频文件播放服务

    支持范围请求（用于前端波形显示和流式播放）
    """
    import urllib.parse
    file_path = urllib.parse.unquote(file_path)

    audio_file = config.validate_audio_path(file_path)
    
    # 获取文件大小
    file_size = audio_file.stat().st_size
    
    # 获取 MIME 类型
    mime_types = {
        '.wav': 'audio/wav',
        '.mp3': 'audio/mpeg',
        '.flac': 'audio/flac',
        '.aiff': 'audio/aiff',
        '.aif': 'audio/aiff',
        '.ogg': 'audio/ogg',
        '.m4a': 'audio/mp4',
        '.aac': 'audio/aac'
    }
    mime_type = mime_types.get(audio_file.suffix.lower(), 'application/octet-stream')
    
    # 创建文件响应，支持范围请求
    return FileResponse(
        path=str(audio_file),
        media_type=mime_type,
        filename=audio_file.name
    )


# ==================== 索引状态 ====================

@app.get("/api/v1/index/status", response_model=schemas.IndexStatus)
async def get_index_status():
    """获取当前索引状态"""
    try:
        searcher = get_searcher()
        stats = searcher.get_collection_stats()
        
        return schemas.IndexStatus(
            total_files=stats.get("total_count", 0),
            indexed_files=stats.get("total_count", 0)
        )
    except Exception as e:
        logger.error(f"获取索引状态失败: {e}")
        return schemas.IndexStatus(total_files=0, indexed_files=0)


# ==================== 文件列表 ====================

@app.get("/api/v1/files", response_model=schemas.ScanResponse)
async def get_indexed_files():
    """获取所有已索引的文件列表"""
    try:
        searcher = get_searcher()
        files = searcher.get_all_indexed_files()
        
        audio_files = []
        for f in files:
            audio_files.append(schemas.AudioFile(
                path=f.get("file_path", ""),
                filename=f.get("filename", ""),
                duration=f.get("duration", 0.0),
                sample_rate=0,
                channels=0,
                format=f.get("format", ""),
                size=f.get("size", 0)
            ))
        
        return schemas.ScanResponse(
            total=len(audio_files),
            files=audio_files
        )
    except Exception as e:
        logger.error(f"获取文件列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 音频裁切 ====================

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
    import soundfile as sf
    import numpy as np
    import os

    logger.info(f"[裁切请求] path={request.path}, start={request.start}, end={request.end}, temp_file={request.temp_file}")

    source_file = config.validate_audio_path(request.path)

    if request.start >= request.end:
        raise HTTPException(status_code=400, detail="起始时间必须小于结束时间")

    try:
        # 读取音频，获取原始采样率
        audio, sr = sf.read(str(source_file))

        # 获取原始音频的格式信息
        subtype = None
        if hasattr(source_file, 'suffix'):
            # 根据文件格式确定 subtype
            suffix = source_file.suffix.lower()
            if suffix in ['.wav']:
                # WAV 文件保留原始格式
                try:
                    info = sf.info(str(source_file))
                    subtype = info.subtype if hasattr(info, 'subtype') else 'PCM_16'
                except:
                    subtype = 'PCM_16'

        # 计算裁切的样本位置
        start_sample = int(request.start * sr)
        end_sample = int(request.end * sr)

        # 边界检查
        if start_sample >= len(audio):
            raise HTTPException(status_code=400, detail="起始时间超出音频时长")

        end_sample = min(end_sample, len(audio))

        # 裁切
        clipped_audio = audio[start_sample:end_sample]

        # 生成输出路径
        if request.temp_file:
            import uuid
            temp_name = f"clip_{int(time.time())}_{uuid.uuid4().hex[:8]}{source_file.suffix}"
            output_path = Path(config.get_temp_clip_dir()) / temp_name
            logger.info(f"[裁切] 使用临时目录: {output_path}")
        elif request.output:
            output_path = Path(request.output)
            logger.info(f"[裁切] 使用指定输出路径: {output_path}")
        else:
            output_path = source_file.parent / f"{source_file.stem}_clip{source_file.suffix}"
            logger.info(f"[裁切] 使用原目录: {output_path}")

        # 确保输出目录存在
        output_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"[裁切] 保存文件到: {output_path}")

        # 保存 - 保留原始采样率和格式
        if subtype:
            sf.write(str(output_path), clipped_audio, sr, subtype=subtype)
        else:
            sf.write(str(output_path), clipped_audio, sr)

        # 不再自动清理临时文件，由用户自行管理
        # 保留所有裁切后的文件供用户使用

        duration = len(clipped_audio) / sr

        return schemas.ClipResponse(
            success=True,
            output_path=str(output_path),
            duration=duration,
            message=f"成功裁切 {request.start:.2f}s - {request.end:.2f}s"
        )

    except Exception as e:
        logger.error(f"裁切失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 删除临时文件 ====================

@app.delete("/api/temp/{file_path:path}")
async def delete_temp_file(file_path: str):
    """
    删除临时文件

    - **file_path**: 临时文件路径（URL编码）
    """
    import os

    try:
        # 解码URL编码的路径
        from urllib.parse import unquote
        file_path = unquote(file_path)

        # 安全检查：确保文件在临时目录内
        abs_path = os.path.abspath(file_path)

        if not abs_path.startswith(config.get_temp_clip_dir()):
            raise HTTPException(status_code=400, detail="只能删除临时目录中的文件")

        # 删除文件
        if os.path.exists(abs_path):
            os.remove(abs_path)
            return {"success": True, "message": f"已删除临时文件: {os.path.basename(abs_path)}"}
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
    import soundfile as sf
    import numpy as np

    source_file = config.validate_audio_path(request.path)
    
    try:
        # 读取音频
        audio, sr = sf.read(str(source_file))
        duration_samples = len(audio)
        duration_seconds = duration_samples / sr
        
        # 确保 fade 时间不超过音频时长
        fade_in_samples = min(int(request.fade_in * sr), duration_samples)
        fade_out_samples = min(int(request.fade_out * sr), duration_samples)
        
        # 淡入：线性增益从 0 到 1
        if fade_in_samples > 0:
            fade_in_curve = np.linspace(0, 1, fade_in_samples)
            if audio.ndim == 1:
                audio[:fade_in_samples] = audio[:fade_in_samples] * fade_in_curve
            else:
                for ch in range(audio.shape[1]):
                    audio[:fade_in_samples, ch] = audio[:fade_in_samples, ch] * fade_in_curve.reshape(-1, 1)
        
        # 淡出：线性增益从 1 到 0
        if fade_out_samples > 0:
            fade_out_curve = np.linspace(1, 0, fade_out_samples)
            start_idx = duration_samples - fade_out_samples
            if audio.ndim == 1:
                audio[start_idx:] = audio[start_idx:] * fade_out_curve
            else:
                for ch in range(audio.shape[1]):
                    audio[start_idx:, ch] = audio[start_idx:, ch] * fade_out_curve.reshape(-1, 1)
        
        # 生成输出路径
        if request.output:
            output_path = Path(request.output)
        else:
            output_path = source_file.parent / f"{source_file.stem}_fade{source_file.suffix}"
        
        # 保存
        sf.write(str(output_path), audio, sr)
        
        return schemas.FadeResponse(
            success=True,
            output_path=str(output_path),
            message=f"淡入: {request.fade_in}s, 淡出: {request.fade_out}s"
        )
        
    except Exception as e:
        logger.error(f"淡入淡出处理失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
    import soundfile as sf
    import numpy as np
    import uuid

    source_file = config.validate_audio_path(request.path)

    if request.start >= request.end:
        raise HTTPException(status_code=400, detail="起始时间必须小于结束时间")

    try:
        info = sf.info(str(source_file))
        sr = info.samplerate
        channels = info.channels
        subtype = info.subtype if hasattr(info, 'subtype') else 'PCM_16'

        start_sample = int(request.start * sr)
        end_sample = int(request.end * sr)

        if start_sample >= info.frames:
            raise HTTPException(status_code=400, detail="起始时间超出音频时长")

        end_sample = min(end_sample, info.frames)

        with sf.SoundFile(str(source_file)) as f:
            f.seek(start_sample)
            clipped_audio = f.read(frames=end_sample - start_sample, dtype='float32')

        if clipped_audio.ndim > 1 and clipped_audio.shape[1] != channels:
            channels = clipped_audio.shape[1]

        duration_samples = len(clipped_audio)
        fade_in_samples = min(int(request.fade_in * sr), duration_samples)
        fade_out_samples = min(int(request.fade_out * sr), duration_samples)

        if fade_in_samples > 0:
            fade_in_curve = np.linspace(0, 1, fade_in_samples)
            if clipped_audio.ndim == 1:
                clipped_audio[:fade_in_samples] = clipped_audio[:fade_in_samples] * fade_in_curve
            else:
                for ch in range(clipped_audio.shape[1]):
                    clipped_audio[:fade_in_samples, ch] = clipped_audio[:fade_in_samples, ch] * fade_in_curve.reshape(-1, 1)

        if fade_out_samples > 0:
            fade_out_curve = np.linspace(1, 0, fade_out_samples)
            start_idx = duration_samples - fade_out_samples
            if clipped_audio.ndim == 1:
                clipped_audio[start_idx:] = clipped_audio[start_idx:] * fade_out_curve
            else:
                for ch in range(clipped_audio.shape[1]):
                    clipped_audio[start_idx:, ch] = clipped_audio[start_idx:, ch] * fade_out_curve.reshape(-1, 1)

        if request.temp_file:
            temp_name = f"clip_fade_{int(time.time())}_{uuid.uuid4().hex[:8]}{source_file.suffix}"
            output_path = Path(config.get_temp_clip_dir()) / temp_name
        else:
            output_path = source_file.parent / f"{source_file.stem}_clip_fade{source_file.suffix}"

        sf.write(str(output_path), clipped_audio, sr, subtype=subtype)

        duration = len(clipped_audio) / sr

        return schemas.ClipResponse(
            success=True,
            output_path=str(output_path),
            duration=duration,
            message=f"裁切 {request.start:.2f}s - {request.end:.2f}s, 淡入 {request.fade_in}s, 淡出 {request.fade_out}s"
        )

    except Exception as e:
        logger.error(f"裁切并淡入淡出失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 验证临时文件 ====================

@app.get("/api/clip/verify")
async def verify_clip(file_path: str = Query(..., description="临时文件路径")):
    """
    验证临时文件是否存在

    - **file_path**: 临时文件路径（URL编码）
    """
    import urllib.parse

    decoded_path = urllib.parse.unquote(file_path)
    abs_path = Path(decoded_path).resolve()

    if not str(abs_path).startswith(config.get_temp_clip_dir()):
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
    
    # 验证路径是否存在
    if not os.path.exists(new_dir):
        raise HTTPException(status_code=400, detail="指定的目录不存在")
    
    if not os.path.isdir(new_dir):
        raise HTTPException(status_code=400, detail="指定的路径不是目录")
    
    # 保存到配置文件（使用应用目录而不是用户主目录）
    config_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config')
    config_path = os.path.join(config_dir, "user_config.json")
    
    try:
        # 确保配置目录存在
        os.makedirs(config_dir, exist_ok=True)
        
        # 读取现有配置或创建新配置
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                current_config = json.load(f)
        else:
            current_config = {}
        
        # 更新临时文件目录
        current_config['tempClipDir'] = new_dir
        
        # 保存配置
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(current_config, f, ensure_ascii=False, indent=2)
        
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

    - **id**: 工程唯一标识（可选，不传则自动生成）
    - **name**: 工程名称
    - **description**: 工程描述
    - **temp_dir**: 工程特定的临时文件目录
    """
    try:
        import uuid
        project_id = request.id or f"proj_{uuid.uuid4().hex[:8]}"

        db_manager = get_db_manager()
        success = db_manager.create_project(
            project_id=project_id,
            name=request.name,
            description=request.description,
            temp_dir=request.temp_dir
        )

        if not success:
            raise HTTPException(status_code=400, detail="创建工程失败，可能ID已存在")

        return {
            "success": True,
            "project_id": project_id,
            "message": f"工程 '{request.name}' 创建成功"
        }
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


@app.get("/api/v1/projects/{project_id}")
async def get_project(project_id: str):
    """
    获取工程详情
    """
    try:
        db_manager = get_db_manager()
        project = db_manager.get_project(project_id)

        if not project:
            raise HTTPException(status_code=404, detail="工程不存在")

        # 添加文件数量
        project['file_count'] = db_manager.get_project_file_count(project_id)

        return project
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

        return {
            "success": True,
            "message": "工程更新成功"
        }
    except Exception as e:
        logger.error(f"更新工程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/v1/projects/{project_id}")
async def delete_project(project_id: str):
    """
    删除工程（会级联删除所有相关文件和向量数据库）
    """
    try:
        db_manager = get_db_manager()

        # 检查工程是否存在
        existing = db_manager.get_project(project_id)
        if not existing:
            raise HTTPException(status_code=404, detail="工程不存在")

        # 不能删除默认工程
        if project_id == 'default':
            raise HTTPException(status_code=400, detail="不能删除默认工程")

        # 删除工程的向量数据库
        from core.indexer import delete_project_index
        index_deleted = delete_project_index(project_id)
        if not index_deleted:
            logger.warning(f"删除工程 {project_id} 的向量数据库失败，继续删除工程数据")

        # 删除工程数据
        success = db_manager.delete_project(project_id)

        if not success:
            raise HTTPException(status_code=400, detail="删除工程失败")

        return {
            "success": True,
            "message": "工程已删除",
            "index_deleted": index_deleted
        }
    except Exception as e:
        logger.error(f"删除工程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/projects/{project_id}/switch")
async def switch_project(project_id: str):
    """
    切换到指定工程

    会将工程添加到最近工程列表，同时切换向量数据库
    """
    try:
        db_manager = get_db_manager()

        # 检查工程是否存在
        project = db_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="工程不存在")

        # 添加到最近工程
        db_manager.add_to_recent_projects(project_id)

        # 更新全局配置中的当前工程
        config.CURRENT_PROJECT_ID = project_id

        # 如果有工程特定的临时目录，更新配置
        if project.get('temp_dir'):
            config.TEMP_CLIP_DIR = project['temp_dir']

        # 获取该工程的向量数据库信息
        from core.indexer import get_indexer
        from core.embedder import is_embedder_available

        indexer = get_indexer(project_id)
        indexed_count = indexer.get_indexed_count()

        embedder_available = is_embedder_available()

        return {
            "success": True,
            "project_id": project_id,
            "project_name": project['name'],
            "message": f"已切换到工程 '{project['name']}'",
            "vector_db": {
                "indexed_count": indexed_count,
                "embedder_available": embedder_available
            }
        }
    except Exception as e:
        logger.error(f"切换工程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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


@app.get("/api/v1/projects/{project_id}/files")
async def get_project_files(project_id: str):
    """
    获取指定工程的所有文件
    """
    try:
        db_manager = get_db_manager()

        # 检查工程是否存在
        project = db_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="工程不存在")

        files = db_manager.get_files_by_project(project_id)

        return {
            "project_id": project_id,
            "project_name": project['name'],
            "total": len(files),
            "files": [f.to_dict() for f in files]
        }
    except Exception as e:
        logger.error(f"获取工程文件失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
    except Exception as e:
        logger.error(f"获取用户文件夹失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/projects/{project_id}/folders")
async def create_user_folder(project_id: str, request: CreateFolderRequest):
    """
    创建用户自定义文件夹
    """
    try:
        db_manager = get_db_manager()

        # 检查工程是否存在
        project = db_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="工程不存在")

        # 生成文件夹ID
        import uuid
        folder_id = f"folder_{uuid.uuid4().hex[:8]}"

        # 获取当前最大排序号
        existing_folders = db_manager.get_user_folders(project_id)
        sort_order = len(existing_folders)

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
    except Exception as e:
        logger.error(f"创建用户文件夹失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/v1/projects/{project_id}/folders/{folder_id}")
async def update_user_folder(project_id: str, folder_id: str, request: UpdateFolderRequest):
    """
    更新用户自定义文件夹
    """
    try:
        db_manager = get_db_manager()

        # 检查文件夹是否存在
        folder = db_manager.get_user_folder(folder_id)
        if not folder or folder['project_id'] != project_id:
            raise HTTPException(status_code=404, detail="文件夹不存在")

        success = db_manager.update_user_folder(
            folder_id=folder_id,
            name=request.name,
            description=request.description,
            color=request.color,
            sort_order=request.sort_order
        )

        if not success:
            raise HTTPException(status_code=400, detail="更新文件夹失败")

        return {
            "success": True,
            "message": "文件夹更新成功"
        }
    except Exception as e:
        logger.error(f"更新用户文件夹失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/v1/projects/{project_id}/folders/{folder_id}")
async def delete_user_folder(project_id: str, folder_id: str):
    """
    删除用户自定义文件夹

    删除后，该文件夹下的导入文件夹将变为未分类状态
    """
    try:
        db_manager = get_db_manager()

        # 检查文件夹是否存在
        folder = db_manager.get_user_folder(folder_id)
        if not folder or folder['project_id'] != project_id:
            raise HTTPException(status_code=404, detail="文件夹不存在")

        success = db_manager.delete_user_folder(folder_id)

        if not success:
            raise HTTPException(status_code=400, detail="删除文件夹失败")

        return {
            "success": True,
            "message": f"文件夹 '{folder['name']}' 已删除"
        }
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
    except Exception as e:
        logger.error(f"获取导入文件夹映射失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/projects/{project_id}/folder-mappings/{folder_path:path}")
async def update_folder_mapping(project_id: str, folder_path: str, user_folder_id: Optional[str] = None):
    """
    更新导入文件夹的用户文件夹关联

    - **user_folder_id**: 用户文件夹ID，为空表示取消关联（变为未分类）
    """
    try:
        db_manager = get_db_manager()
        import urllib.parse

        decoded_path = urllib.parse.unquote(folder_path)

        # 检查工程是否存在
        project = db_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="工程不存在")

        success = db_manager.update_imported_folder_mapping(project_id, decoded_path, user_folder_id)

        if not success:
            raise HTTPException(status_code=400, detail="更新文件夹映射失败")

        return {
            "success": True,
            "message": "文件夹分类已更新"
        }
    except Exception as e:
        logger.error(f"更新文件夹映射失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 主入口 ====================

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        reload=config.DEBUG,
        log_level="info"
    )