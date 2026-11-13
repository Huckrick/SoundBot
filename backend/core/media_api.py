"""Desktop audio preview and trusted host delivery endpoints."""

from __future__ import annotations

import asyncio
import hmac
import os
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from config import AUDIO_FORMAT_CAPABILITIES
from core import delivery, middleware, vst, wwise
from core.audio_service import AudioServiceError, get_audio_service
from core.database import get_db_manager
from core.editing import Clip, Track, mix, read_region, wave_stream
from core.effects import EQBand, Effect, analyze, validate_effect

router = APIRouter(prefix="/api/v1")
Time = Annotated[float, Field(ge=0, le=86400, allow_inf_nan=False)]
Gain = Annotated[float, Field(ge=-96, le=24, allow_inf_nan=False)]


class EQBandRequest(BaseModel):
    shape: Literal["bell", "low_shelf", "high_shelf", "low_cut", "high_cut"]
    frequency: Annotated[float, Field(ge=20, le=20000)]
    gain: Annotated[float, Field(ge=-24, le=24)] = 0
    q: Annotated[float, Field(ge=0.1, le=18)] = 0.707
    enabled: bool = True


class EffectRequest(BaseModel):
    kind: Literal["normalize", "eq", "compressor", "reverb", "vst3"]
    values: dict[str, Annotated[float, Field(allow_inf_nan=False)]] = Field(default_factory=dict, max_length=128)
    plugin_id: str | None = Field(default=None, max_length=64)
    plugin_state: str | None = Field(default=None, max_length=vst.MAX_STATE_TOKEN)
    bands: list[EQBandRequest] = Field(default_factory=list, max_length=8)


Curve = Literal["linear", "equal_power", "ease_in", "ease_out", "smooth"]
PanPosition = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
PanValue = Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]


class ClipRequest(BaseModel):
    file_id: str | None = None
    wwise_token: str | None = Field(default=None, max_length=64)
    middleware_token: str | None = Field(default=None, max_length=64)
    start: Time = 0
    offset: Time = 0
    duration: Annotated[float, Field(gt=0, le=86400, allow_inf_nan=False)]
    gain: Gain = 0
    fade_in: Time = 0
    fade_out: Time = 0
    fade_in_curve: Curve = "linear"
    fade_out_curve: Curve = "linear"
    pan: list[tuple[PanPosition, PanValue]] = Field(default_factory=list, max_length=32)
    effects: list[EffectRequest] = Field(default_factory=list, max_length=12)


    @field_validator("pan")
    @classmethod
    def validate_pan_order(cls, points):
        if any(left[0] >= right[0] for left, right in zip(points, points[1:])):
            raise ValueError("声像点的位置必须按顺序排列且不能重复")
        return points


ClipIndex = Annotated[int, Field(ge=0, lt=128)]


class TrackRequest(BaseModel):
    clips: list[ClipRequest] = Field(max_length=128)
    gain: Gain = 0
    mute: bool = False
    solo: bool = False
    channels: Literal[1, 2] | None = None
    effects: list[EffectRequest] = Field(default_factory=list, max_length=12)
    auto_crossfade: bool = False
    crossfade_curve: Curve = "linear"
    crossfade_overrides: list[tuple[ClipIndex, ClipIndex, Curve | Literal["off"]]] = Field(default_factory=list, max_length=127)


class MixRequest(BaseModel):
    project_id: str
    tracks: list[TrackRequest] = Field(min_length=1, max_length=32)
    rate: int = Field(default=48000, ge=8000, le=384000)
    channels: int = Field(default=2, ge=1, le=32)
    start: Time = 0


class DAWRequest(BaseModel):
    project_id: str
    directory: str
    clip: ClipRequest
    rate: int | None = Field(default=None, ge=8000, le=384000)
    channels: Literal[1, 2] | None = None
    bits: Literal[16, 24] = 24
    name: str | None = Field(default=None, max_length=120)


class WwiseConnection(BaseModel):
    url: str = "ws://127.0.0.1:8080/waapi"


class HostMixRequest(MixRequest):
    token: str = Field(max_length=64)


class MiddlewareConnection(BaseModel):
    host: middleware.Host
    url: str = Field(default="ws://127.0.0.1:8080/waapi", max_length=256)
    project_path: str = Field(default="", max_length=32768)
    source_directory: str = Field(default="", max_length=32768)


class CleanupRequest(BaseModel):
    paths: list[str] = Field(max_length=2048)


def _trusted(token):
    expected = os.environ.get("SOUNDBOT_IPC_TOKEN")
    if not expected or not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(403, "此操作需要从 SoundBot 桌面发起")


def _record(project_id, file_id):
    row = get_db_manager().get_file_by_uuid(file_id, project_id)
    if not row:
        raise HTTPException(404, "素材不存在")
    if not Path(row.path).is_file():
        raise HTTPException(404, "音频源不可用，请重新定位")
    return row


def _clip(project_id, request, *, allow_host=True):
    if sum(map(bool, (request.file_id, request.wwise_token, request.middleware_token))) != 1:
        raise ValueError("剪辑需要一个音频源")
    if request.file_id:
        row = _record(project_id, request.file_id)
        source = Path(row.path)
        info = get_audio_service().probe(source)
        rate, channels, duration = info.sample_rate, info.channels, info.duration or row.duration
    elif allow_host:
        chosen = (middleware.selection(request.middleware_token) if request.middleware_token
                  else wwise.selection(request.wwise_token))
        source, rate, channels, duration = chosen.target, chosen.rate, chosen.channels, chosen.duration
    else:
        raise ValueError("请选择库内素材")
    available = duration - request.offset
    if available <= 0 or request.duration > available + 0.005:
        raise ValueError("裁剪区间超出音频范围")
    clip = Clip(source, request.start, min(request.duration, available), request.offset,
                request.gain, request.fade_in, request.fade_out, _effects(request.effects),
                request.fade_in_curve, request.fade_out_curve, tuple(request.pan))
    return clip, rate, channels


def _effects(requests):
    effects = tuple(validate_effect(Effect(item.kind, item.values, item.plugin_id,
                                          tuple(EQBand(**band.model_dump()) for band in item.bands), item.plugin_state))
                    for item in requests)
    if any(effect.kind == "normalize" for effect in effects[1:]):
        raise ValueError("响度归一化需置于处理链首位")
    return effects


def _mix_request(request):
    tracks = []
    for track in request.tracks:
        clips = tuple(_clip(request.project_id, clip)[0] for clip in track.clips)
        tracks.append(Track(clips, track.gain, track.mute, track.solo, track.channels,
                            _effects(track.effects), track.auto_crossfade, track.crossfade_curve,
                            tuple(track.crossfade_overrides)))
    if sum(len(track.clips) for track in tracks) > 128:
        raise ValueError("单次预览或写回最多支持 128 个剪辑")
    duration = max((clip.start + clip.duration for track in tracks for clip in track.clips), default=0)
    if duration <= request.start:
        raise ValueError("选区没有可播放的音频")
    return tracks, duration


def _stream(blocks, rate, channels, frames):
    if frames < 1 or frames * channels * 2 > 0xffffffff - 36:
        raise ValueError("预览时长超出 WAV 限制")
    return StreamingResponse(wave_stream(blocks, rate, channels, frames), media_type="audio/wav",
                             headers={"Cache-Control": "no-store"})


@router.get("/files/{file_id}/source")
def native_source(file_id: str, project_id: str):
    path = Path(_record(project_id, file_id).path)
    capability = AUDIO_FORMAT_CAPABILITIES.get(path.suffix.lower())
    if not capability:
        raise HTTPException(415, "不支持的音频格式")
    return FileResponse(path, media_type=capability["mime_type"])


@router.get("/files/{file_id}/stream")
def stream_source(file_id: str, project_id: str, offset: float = Query(0, ge=0, le=86400)):
    try:
        row = _record(project_id, file_id)
        service = get_audio_service()
        info = service.probe(row.path)
        if offset >= info.duration:
            raise ValueError("起点超出音频范围")
        frames = round((info.duration-offset)*info.sample_rate)

        def blocks():
            with service.playback_cache_lease(Path(row.path)) as cached:
                yield from read_region(cached or row.path, offset, frames, info.sample_rate, info.channels)

        return _stream(blocks(), info.sample_rate, info.channels, frames)
    except (ValueError, AudioServiceError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/files/{file_id}/loudness")
def file_loudness(file_id: str, project_id: str):
    try:
        row = _record(project_id, file_id)
        info = get_audio_service().probe(row.path)
        frames = round(info.duration * info.sample_rate)
        if frames < 1:
            raise ValueError("该文件没有可分析的音频")
        return analyze(read_region(row.path, 0, frames, info.sample_rate, info.channels),
                       info.sample_rate, info.channels)
    except (ValueError, AudioServiceError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/preview")
def preview(request: MixRequest):
    try:
        tracks, duration = _mix_request(request)
        blocks = mix(tracks, request.rate, request.channels, duration, start=request.start)
        if any(track.effects or any(clip.effects for clip in track.clips) for track in tracks):
            first = next(blocks, None)
            if first is None:
                raise ValueError("选区没有可播放的音频")

            def prepared():
                try:
                    yield first
                    yield from blocks
                finally:
                    blocks.close()

            source = prepared()
        else:
            source = blocks
        return _stream(source,
                       request.rate, request.channels,
                       round(duration*request.rate) - round(request.start*request.rate))
    except (ValueError, AudioServiceError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/effects/plugins")
def discover_plugins():
    return vst.discover()


@router.post("/effects/plugins/{plugin_id}/load")
def load_plugin(plugin_id: str, x_soundbot_ipc: str | None = Header(None)):
    _trusted(x_soundbot_ipc)
    try:
        return vst.load_discovered(plugin_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, "无法加载本地 VST3" if isinstance(exc, OSError) else str(exc)) from exc


class PluginEditorRequest(BaseModel):
    state: str | None = Field(default=None, max_length=vst.MAX_STATE_TOKEN)
    values: dict[str, Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]] = Field(default_factory=dict, max_length=128)


@router.post("/effects/editors/close")
def close_editors(x_soundbot_ipc: str | None = Header(None)):
    _trusted(x_soundbot_ipc)
    vst.close_editors()
    return {"closed": True}


@router.post("/effects/plugins/{plugin_id}/edit")
def edit_plugin(plugin_id: str, request: PluginEditorRequest, x_soundbot_ipc: str | None = Header(None)):
    _trusted(x_soundbot_ipc)
    try:
        return vst.edit(plugin_id, request.state, request.values if request.state is None else None)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, "无法打开 VST3 编辑窗口" if isinstance(exc, OSError) else str(exc)) from exc


@router.post("/delivery")
def prepare_delivery(request: DAWRequest, x_soundbot_ipc: str | None = Header(None)):
    _trusted(x_soundbot_ipc)
    try:
        clip, rate, channels = _clip(request.project_id, request.clip, allow_host=False)
        if clip.start:
            raise ValueError("单个导出剪辑的时间线必须从零开始")
        target = delivery.publish_daw(request.directory, (request.name or "").strip() or clip.path.stem, [Track((clip,))],
                                      request.rate or rate, request.channels or channels, clip.duration, request.bits)
        return {"path": str(target), "name": target.name}
    except (OSError, ValueError, AudioServiceError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/cleanup")
def list_cleanup():
    return delivery.cleanup()


@router.post("/cleanup")
def cleanup(request: CleanupRequest, x_soundbot_ipc: str | None = Header(None)):
    _trusted(x_soundbot_ipc)
    return delivery.cleanup(execute=True, paths=set(request.paths))


@router.post("/middleware/connect")
async def middleware_connect(request: MiddlewareConnection, x_soundbot_ipc: str | None = Header(None)):
    _trusted(x_soundbot_ipc)
    try:
        return await middleware.connect(request.host, url=request.url, project_path=request.project_path,
                                        source_directory=request.source_directory)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/middleware/disconnect")
async def middleware_disconnect(x_soundbot_ipc: str | None = Header(None)):
    _trusted(x_soundbot_ipc)
    await middleware.disconnect()
    return {"connected": False}


@router.post("/middleware/sources/{source_id}/select")
async def middleware_select(source_id: str, x_soundbot_ipc: str | None = Header(None)):
    _trusted(x_soundbot_ipc)
    try:
        return await middleware.select(source_id)
    except (OSError, ValueError, AudioServiceError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/middleware/selection/{token}/waveform")
def middleware_waveform(token: str):
    try:
        item = middleware.selection(token)
        return {"peaks": get_audio_service().waveform(item.target).peaks}
    except (OSError, ValueError, AudioServiceError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/middleware/deliver")
async def middleware_deliver(request: HostMixRequest, x_soundbot_ipc: str | None = Header(None)):
    _trusted(x_soundbot_ipc)
    try:
        if any(clip.wwise_token or (clip.middleware_token and clip.middleware_token != request.token)
               for track in request.tracks for clip in track.clips):
            raise ValueError("请重新选择中间件音频源")
        tracks, duration = await asyncio.to_thread(_mix_request, request)
        return await middleware.deliver(request.token, tracks, duration)
    except (OSError, ValueError, AudioServiceError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/wwise/connect")
async def wwise_connect(request: WwiseConnection, x_soundbot_ipc: str | None = Header(None)):
    _trusted(x_soundbot_ipc)
    try:
        return await wwise.connect(request.url)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/wwise/disconnect")
async def wwise_disconnect(x_soundbot_ipc: str | None = Header(None)):
    _trusted(x_soundbot_ipc)
    await wwise.disconnect()
    return {"connected": False}


@router.post("/wwise/sources/{source_id}/select")
async def wwise_select(source_id: str, x_soundbot_ipc: str | None = Header(None)):
    _trusted(x_soundbot_ipc)
    try:
        return await wwise.select(source_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/wwise/selection/{token}/waveform")
def wwise_waveform(token: str):
    try:
        item = wwise.selection(token)
        return {"peaks": get_audio_service().waveform(item.target).peaks}
    except (OSError, ValueError, AudioServiceError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/wwise/writeback")
async def wwise_writeback(request: HostMixRequest, x_soundbot_ipc: str | None = Header(None)):
    _trusted(x_soundbot_ipc)
    try:
        tracks, duration = await asyncio.to_thread(_mix_request, request)
        return await wwise.writeback(request.token, tracks, duration)
    except (OSError, ValueError, AudioServiceError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/wwise/backups/{backup_id}/restore")
async def restore_wwise(backup_id: str, x_soundbot_ipc: str | None = Header(None)):
    _trusted(x_soundbot_ipc)
    try:
        await wwise.restore(backup_id)
        return {"restored": True}
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
