# -*- coding: utf-8 -*-
"""
Pydantic 数据模型

定义 API 请求/响应的数据结构和验证规则。
"""

from typing import Optional, List
from pydantic import BaseModel, Field


# ==================== 请求模型 ====================

class ScanRequest(BaseModel):
    """扫描请求"""
    folder_path: str = Field(..., description="要扫描的文件夹路径")
    recursive: bool = Field(default=True, description="是否递归扫描子文件夹")


class SearchRequest(BaseModel):
    """语义搜索请求"""
    query: str = Field(..., description="搜索查询文本")
    top_k: int = Field(default=20, ge=1, le=100, description="返回结果数量")
    threshold: float = Field(default=0.15, ge=0.0, le=1.0, description="相似度阈值")


class IndexRequest(BaseModel):
    """索引请求"""
    folder_path: str = Field(..., description="要索引的文件夹路径")
    recursive: bool = Field(default=True, description="是否递归扫描")


# ==================== 响应模型 ====================

class AudioFile(BaseModel):
    """音频文件元数据"""
    path: str = Field(..., description="文件完整路径")
    filename: str = Field(..., description="文件名")
    duration: float = Field(..., description="时长（秒）")
    sample_rate: int = Field(..., description="采样率")
    channels: int = Field(..., description="声道数")
    format: str = Field(..., description="音频格式")
    size: int = Field(..., description="文件大小（字节）")


class SearchResult(BaseModel):
    """搜索结果"""
    audio_file: AudioFile
    score: float = Field(..., description="相似度分数")
    distance: float = Field(..., description="距离（越低越相似）")


class ScanResponse(BaseModel):
    """扫描响应"""
    total: int = Field(..., description="扫描到的音频文件总数")
    files: List[AudioFile] = Field(..., description="音频文件列表")


class SearchResponse(BaseModel):
    """搜索响应"""
    query: str = Field(..., description="搜索查询")
    total: int = Field(..., description="结果总数")
    results: List[SearchResult] = Field(..., description="搜索结果列表")


class IndexResponse(BaseModel):
    """索引响应"""
    indexed: int = Field(..., description="已索引的文件数")
    skipped: int = Field(..., description="跳过的文件数")
    duration: float = Field(..., description="耗时（秒）")


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = Field(..., description="服务状态")
    version: str = Field(..., description="版本号")
    device: str = Field(..., description="当前设备")


class ErrorResponse(BaseModel):
    """错误响应"""
    error: str = Field(..., description="错误信息")
    detail: Optional[str] = Field(None, description="详细错误信息")


# ==================== 状态模型 ====================

class IndexStatus(BaseModel):
    """索引状态"""
    total_files: int = Field(default=0, description="总文件数")
    indexed_files: int = Field(default=0, description="已索引文件数")
    last_update: Optional[str] = Field(None, description="最后更新时间")


# ==================== 音频处理请求模型 ====================

class ClipRequest(BaseModel):
    """裁切请求"""
    path: str = Field(..., description="源音频文件路径")
    start: float = Field(..., ge=0, description="裁切起始时间（秒）")
    end: float = Field(..., gt=0, description="裁切结束时间（秒）")
    output: Optional[str] = Field(None, description="输出文件路径，默认在原文件同目录添加 _clip 后缀")
    temp_file: bool = Field(default=True, description="是否创建临时文件（用于拖拽导出）")


class FadeRequest(BaseModel):
    """淡入淡出请求"""
    path: str = Field(..., description="音频文件路径")
    fade_in: float = Field(default=0, ge=0, description="淡入时长（秒）")
    fade_out: float = Field(default=0, ge=0, description="淡出时长（秒）")
    output: Optional[str] = Field(None, description="输出文件路径，默认在原文件同目录添加 _fade 后缀")


class ClipWithFadeRequest(BaseModel):
    """裁切并淡入淡出请求"""
    path: str = Field(..., description="源音频文件路径")
    start: float = Field(..., ge=0, description="裁切起始时间（秒）")
    end: float = Field(..., gt=0, description="裁切结束时间（秒）")
    fade_in: float = Field(default=0, ge=0, description="淡入时长（秒）")
    fade_out: float = Field(default=0, ge=0, description="淡出时长（秒）")
    temp_file: bool = Field(default=True, description="是否创建临时文件")


class ClipResponse(BaseModel):
    """裁切响应"""
    success: bool = Field(..., description="是否成功")
    output_path: Optional[str] = Field(None, description="输出文件路径")
    duration: Optional[float] = Field(None, description="裁切后的时长")
    message: Optional[str] = Field(None, description="消息")


class FadeResponse(BaseModel):
    """淡入淡出响应"""
    success: bool = Field(..., description="是否成功")
    output_path: Optional[str] = Field(None, description="输出文件路径")
    message: Optional[str] = Field(None, description="消息")


# ==================== 临时文件路径配置模型 ====================

class TempDirRequest(BaseModel):
    """临时文件目录设置请求"""
    temp_dir: str = Field(..., description="临时文件存放目录路径")


# ==================== 工程管理请求模型 ====================

class CreateProjectRequest(BaseModel):
    """创建工程请求"""
    id: Optional[str] = Field(None, description="工程唯一标识（可选，不传则自动生成）")
    name: str = Field(..., description="工程名称")
    description: str = Field(default="", description="工程描述")
    temp_dir: Optional[str] = Field(None, description="工程特定的临时文件目录")


class UpdateProjectRequest(BaseModel):
    """更新工程请求"""
    name: Optional[str] = Field(None, description="工程名称")
    description: Optional[str] = Field(None, description="工程描述")
    temp_dir: Optional[str] = Field(None, description="工程特定的临时文件目录")
    settings: Optional[dict] = Field(None, description="工程特定配置（JSON格式）")


class TempDirResponse(BaseModel):
    """临时文件目录响应"""
    temp_dir: str = Field(..., description="当前临时文件目录")
    default_dir: str = Field(..., description="默认临时文件目录")
    message: Optional[str] = Field(None, description="消息")
