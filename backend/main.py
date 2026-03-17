# -*- coding: utf-8 -*-
# SoundMind Backend

"""FastAPI 后端服务，用于音效管理器的 AI 语义搜索功能。"""

import os
import time
import asyncio
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Path as PathParam
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import config
from models import schemas
from core.indexer import get_indexer, AudioIndexer
from core.searcher import get_searcher, AudioSearcher
from core.embedder import get_embedder
from utils.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info(f"SoundMind API 启动中...")
    logger.info(f"设备: {config.get_device()}")
    logger.info(f"数据库路径: {config.get_db_path()}")
    
    # 预热 embedder（延迟加载）
    try:
        embedder = get_embedder()
        logger.info("Embedder 预热完成")
    except Exception as e:
        logger.warning(f"Embedder 预热失败: {e}")
    
    yield
    
    logger.info("SoundMind API 关闭中...")


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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    
    # 解码 URL 编码的路径
    file_path = urllib.parse.unquote(path)
    
    audio_file = Path(file_path)
    
    if not audio_file.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_path}")
    
    if not audio_file.is_file():
        raise HTTPException(status_code=400, detail=f"不是有效文件: {file_path}")
    
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
    # 解码 URL 编码的路径
    import urllib.parse
    file_path = urllib.parse.unquote(file_path)
    
    audio_file = Path(file_path)
    
    if not audio_file.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_path}")
    
    if not audio_file.is_file():
        raise HTTPException(status_code=400, detail=f"不是有效文件: {file_path}")
    
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


# ==================== 扫描文件（不建索引）====================

@app.post("/api/v1/scan-only", response_model=schemas.ScanResponse)
async def scan_files_only(request: schemas.ScanRequest):
    """
    仅扫描音频文件，不建立索引（适用于没有 CLAP 模型的情况）
    
    - **folder_path**: 要扫描的文件夹路径
    - **recursive**: 是否递归扫描子文件夹
    """
    from core.scanner import AudioScanner
    
    folder = Path(request.folder_path)
    
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"文件夹不存在: {request.folder_path}")
    
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不是文件夹: {request.folder_path}")
    
    try:
        scanner = AudioScanner()
        audio_files = scanner.scan(str(folder), request.recursive)
        
        files = []
        for f in audio_files:
            files.append(schemas.AudioFile(
                path=f.path,
                filename=f.filename,
                duration=f.duration,
                sample_rate=f.sample_rate,
                channels=f.channels,
                format=f.format,
                size=f.size
            ))
        
        return schemas.ScanResponse(
            total=len(files),
            files=files
        )
        
    except Exception as e:
        logger.error(f"扫描失败: {e}")
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