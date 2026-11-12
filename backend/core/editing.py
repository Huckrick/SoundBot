"""Bounded PCM decoding, fades, mixing and WAV delivery."""

from __future__ import annotations

import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.audio_service import get_audio_service
from core.effects import Effect, process as apply_effects

BLOCK = 8192


@dataclass(frozen=True)
class Clip:
    path: Path
    start: float
    duration: float
    offset: float = 0
    gain: float = 0
    fade_in: float = 0
    fade_out: float = 0
    effects: tuple[Effect, ...] = ()


@dataclass(frozen=True)
class Track:
    clips: tuple[Clip, ...]
    gain: float = 0
    mute: bool = False
    solo: bool = False
    channels: int | None = None
    effects: tuple[Effect, ...] = ()
    auto_crossfade: bool = False


def read_region(path, offset, frames, rate, channels):
    from core.audio_service import _av

    service = get_audio_service()
    service._require_pyav()
    with _av.open(str(service._validate_source(path)), mode="r") as container:
        stream = container.streams.audio[0]
        origin = float((stream.start_time or 0) * stream.time_base) if stream.time_base else 0.0
        if offset > 2 and stream.time_base:
            try:
                container.seek(int((origin + offset - 1) / stream.time_base), stream=stream)
            except (ValueError, _av.FFmpegError):
                container.seek(0)
        layout = {1: "mono", 2: "stereo"}.get(channels) or stream.layout.name
        resampler = _av.AudioResampler(format="fltp", layout=layout, rate=rate)
        start = round(offset * rate)
        stop = start + frames
        position = 0
        emitted = start

        def resampled():
            for source in container.decode(stream):
                yield from resampler.resample(source)
            yield from resampler.resample(None)

        for frame in resampled():
            if frame.pts is not None and frame.time_base is not None:
                position = round((float(frame.pts * frame.time_base) - origin) * rate)
            end = position + frame.samples
            first, last = max(start, position, emitted), min(stop, end)
            while emitted < min(first, stop):
                size = min(BLOCK, min(first, stop) - emitted)
                yield np.zeros((size, channels), dtype=np.float32)
                emitted += size
            if first < last:
                yield frame.to_ndarray()[:, first - position:last - position].T.copy()
                emitted = last
            position = end
            if emitted >= stop:
                return
        while emitted < stop:
            size = min(BLOCK, stop - emitted)
            yield np.zeros((size, channels), dtype=np.float32)
            emitted += size


class _RegionReader:
    def __init__(self, blocks, channels):
        self.blocks = iter(blocks)
        self.buffer = np.empty((0, channels), dtype=np.float32)

    def take(self, count):
        parts = []
        while count:
            if not len(self.buffer):
                self.buffer = next(self.blocks, None)
                if self.buffer is None:
                    raise ValueError("效果器返回的音频长度不足")
            take = min(count, len(self.buffer))
            parts.append(self.buffer[:take])
            self.buffer = self.buffer[take:]
            count -= take
        return parts[0] if len(parts) == 1 else np.concatenate(parts)


def _map_channels(samples, channels):
    if samples.shape[1] == channels:
        return samples
    if channels == 1:
        return samples.mean(axis=1, keepdims=True)
    return np.repeat(samples, channels, axis=1)


def _skip_frames(blocks, frames):
    try:
        for block in blocks:
            if frames >= len(block):
                frames -= len(block)
            else:
                yield block[frames:]
                frames = 0
    finally:
        blocks.close()


def _clip_blocks(clip, channels, rate, start, stop):
    def read(first):
        position = first
        gain = 10 ** (clip.gain / 20)
        length = round(clip.duration * rate)
        for block in read_region(clip.path, clip.offset + first / rate, stop - first, rate, channels):
            if not clip.fade_in and not clip.fade_out:
                yield block if gain == 1 else block * gain
                continue
            frames = np.arange(position, position + len(block))
            envelope = np.full(len(block), gain, dtype=np.float32)
            if clip.fade_in:
                envelope *= np.clip(frames / max(1, round(clip.fade_in * rate)), 0, 1)
            if clip.fade_out:
                envelope *= np.clip((length - 1 - frames) / max(1, round(clip.fade_out * rate)), 0, 1)
            position += len(block)
            yield block * envelope[:, None]

    if not clip.effects:
        return read(start)
    return _skip_frames(apply_effects(read(0), rate, channels, clip.effects, measure=lambda: read(0)), start)


def _crossfades(regions):
    """Find boundary overlaps between consecutive clips on a track."""
    transitions = [[] for _ in regions]
    ordered = sorted(enumerate(regions), key=lambda item: item[1][0])
    for (a, (a_start, a_end)), (b, (b_start, b_end)) in zip(ordered, ordered[1:]):
        if a_start < b_start < a_end < b_end:
            transitions[a].append((b_start, a_end, False))
            transitions[b].append((b_start, a_end, True))
    return transitions


def _track_blocks(track, rate, channels, origin, stop):
    active = []
    regions = [(round(clip.start * rate), round(clip.start * rate) + round(clip.duration * rate))
               for clip in track.clips]
    crossfades = _crossfades(regions) if track.auto_crossfade else [()] * len(regions)
    try:
        for clip, (left, right), fades in zip(track.clips, regions, crossfades):
            if right <= origin or left >= stop:
                continue
            blocks = _clip_blocks(clip, channels, rate, max(0, origin-left), min(stop, right)-left)
            active.append((left, right, _RegionReader(blocks, channels), fades))
        gain = 10 ** (track.gain / 20)
        for position in range(origin, stop, BLOCK):
            end = min(position + BLOCK, stop)
            block = np.zeros((end-position, channels), dtype=np.float32)
            for left, right, reader, fades in active:
                begin, finish = max(position, left), min(end, right)
                if begin >= finish:
                    continue
                samples = reader.take(finish-begin)
                for fade_start, fade_end, fade_in in fades:
                    if finish <= fade_start or begin >= fade_end:
                        continue
                    first = max(begin, fade_start)
                    last = min(finish, fade_end)
                    blend = (np.arange(first, last) - fade_start) / max(1, fade_end - fade_start - 1)
                    samples[first-begin:last-begin] *= (blend if fade_in else 1 - blend)[:, None]
                block[begin-position:finish-position] += samples
            yield block * gain
    finally:
        for _, _, reader, _ in active:
            reader.blocks.close()


def _render_track(track, rate, channels, origin, stop):
    def read(first):
        return _track_blocks(track, rate, channels, first, stop)

    if not track.effects:
        return read(origin)
    return _skip_frames(apply_effects(read(0), rate, channels, track.effects, measure=lambda: read(0)), origin)


def mix(tracks, rate, channels, duration, *, start=0):
    """Yield the same float PCM for preview and final render."""
    origin, stop = round(start * rate), round(duration * rate)
    solo = any(track.solo and not track.mute for track in tracks)
    readers = []
    try:
        for track in tracks:
            if track.mute or (solo and not track.solo):
                continue
            count = track.channels or channels
            readers.append(_RegionReader(_render_track(track, rate, count, origin, stop), count))
        for position in range(origin, stop, BLOCK):
            frames = min(BLOCK, stop - position)
            block = np.zeros((frames, channels), dtype=np.float32)
            for reader in readers:
                block += _map_channels(reader.take(frames), channels)
            yield np.clip(np.nan_to_num(block, copy=False), -1, 1, out=block)
    finally:
        for reader in readers:
            reader.blocks.close()


def pcm_bytes(samples, bits):
    if bits == 16:
        return np.rint(samples * 32767).astype("<i2").tobytes()
    if bits == 24:
        data = np.rint(samples * 8388607).astype("<i4").view(np.uint8).reshape(-1, 4)
        return data[:, :3].tobytes()
    raise ValueError("仅支持 16-bit 或 24-bit PCM")


def wave_stream(blocks, rate, channels, frames, bits=16):
    width = bits // 8
    size = frames * channels * width
    pad = size % 2
    if not 0 <= size <= 0xffffffff - 36 - pad:
        raise ValueError("预览超出 WAV 大小限制")
    yield struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", size+36+pad, b"WAVE", b"fmt ", 16, 1,
                      channels, rate, rate*channels*width, channels*width, bits, b"data", size)
    written = 0
    try:
        for samples in blocks:
            chunk = pcm_bytes(samples, bits)
            written += len(chunk)
            yield chunk
    finally:
        close = getattr(blocks, "close", None)
        if close:
            close()
    if written != size:
        raise ValueError("渲染样本数与预期不一致")
    if pad:
        yield b"\0"


def write_wave(path, tracks, rate, channels, duration, bits):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("音频长度无效或超出 WAV 大小限制")
    frames = round(duration * rate)
    if frames < 1:
        raise ValueError("音频长度无效")
    with path.open("wb") as output:
        output.writelines(wave_stream(mix(tracks, rate, channels, duration), rate, channels, frames, bits))
    with wave.open(str(path), "rb") as check:
        if (check.getnframes(), check.getframerate(), check.getnchannels(), check.getsampwidth()) != (
            frames, rate, channels, bits//8
        ):
            raise ValueError("渲染规格校验失败")
