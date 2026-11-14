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
TAIL_LIMIT = 30
TAIL_LEVEL = 0.0001  # -80 dBFS


def _tail_budget(effects):
    return min(TAIL_LIMIT, sum(12 if effect.kind == 'vst3' else
                               8 if effect.kind == 'reverb' and effect.values['mix'] else 0
                               for effect in effects))


def _audible_tracks(tracks):
    solo = any(track.solo and not track.mute for track in tracks)
    return tuple(track for track in tracks if not track.mute and (track.solo or not solo))


def tail_budget(tracks, master_effects=()):
    upstream = max((_tail_budget(track.effects) + max((_tail_budget(clip.effects) for clip in track.clips), default=0)
                    for track in _audible_tracks(tracks) if track.clips), default=0)
    return min(TAIL_LIMIT, upstream + _tail_budget(master_effects))


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
    fade_in_curve: str = "linear"
    fade_out_curve: str = "linear"
    pan: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class Track:
    clips: tuple[Clip, ...]
    gain: float = 0
    mute: bool = False
    solo: bool = False
    channels: int | None = None
    effects: tuple[Effect, ...] = ()
    auto_crossfade: bool = False
    crossfade_curve: str = "linear"
    crossfade_overrides: tuple[tuple[int, int, str], ...] = ()


def read_region(path, offset, frames, rate, channels):
    from core.audio_service import _av

    service = get_audio_service()
    service._require_pyav()
    with _av.open(str(service._validate_source(path)), mode="r", metadata_errors="replace") as container:
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


def _fade(progress, curve):
    if curve == "equal_power":
        return np.sin(progress * (math.pi / 2))
    if curve == "ease_in":
        return progress ** 2
    if curve == "ease_out":
        return progress * (2 - progress)
    if curve == "smooth":
        return progress ** 2 * (3 - 2 * progress)
    return progress


def _clip_blocks(clip, channels, rate, start, stop, on_plugin_error):
    def read(first):
        position = first
        gain = 10 ** (clip.gain / 20)
        length = round(clip.duration * rate)
        dry_frames = max(0, min(stop, length) - first)
        source = read_region(clip.path, clip.offset + first / rate, dry_frames, rate, channels) if dry_frames else ()
        for block in source:
            if not clip.fade_in and not clip.fade_out:
                yield block if gain == 1 else block * gain
                continue
            frames = np.arange(position, position + len(block))
            envelope = np.full(len(block), gain, dtype=np.float32)
            if clip.fade_in:
                envelope *= _fade(np.clip(frames / max(1, round(clip.fade_in * rate)), 0, 1), clip.fade_in_curve)
            if clip.fade_out:
                progress = np.clip((length - 1 - frames) / max(1, round(clip.fade_out * rate)), 0, 1)
                envelope *= _fade(progress, clip.fade_out_curve)
            position += len(block)
            yield block * envelope[:, None]
        for frame in range(first + dry_frames, stop, BLOCK):
            yield np.zeros((min(BLOCK, stop - frame), channels), dtype=np.float32)

    if not clip.effects:
        return read(start)
    preroll = rate * max(1, _tail_budget(clip.effects))
    warmup = max(0, min(start, round(clip.duration * rate)) - preroll) if on_plugin_error is not None else 0
    processed = apply_effects(read(warmup), rate, channels, clip.effects, measure=lambda: read(0),
                              on_plugin_error=on_plugin_error)
    return _skip_frames(processed, start - warmup)


def _crossfades(regions, default_curve, overrides):
    """Find boundary overlaps between consecutive clips on a track."""
    transitions = [[] for _ in regions]
    curves = {(left, right): curve for left, right, curve in overrides}
    ordered = sorted(enumerate(regions), key=lambda item: item[1][0])
    for (a, (a_start, a_end)), (b, (b_start, b_end)) in zip(ordered, ordered[1:]):
        if a_start < b_start < a_end < b_end:
            curve = curves.get((a, b), default_curve)
            if curve != "off":
                transitions[a].append((b_start, a_end, False, curve))
                transitions[b].append((b_start, a_end, True, curve))
    return transitions


def _track_blocks(track, rate, channels, origin, stop, pan_enabled, on_plugin_error, tails):
    source_channels = track.channels or channels
    active = []
    regions = [(round(clip.start * rate), round(clip.start * rate) + round(clip.duration * rate))
               for clip in track.clips]
    crossfades = (_crossfades(regions, track.crossfade_curve, track.crossfade_overrides)
                  if track.auto_crossfade else [()] * len(regions))
    try:
        for clip, (left, right), fades in zip(track.clips, regions, crossfades):
            last = min(stop, right + (rate * _tail_budget(clip.effects) if tails else 0))
            if last <= origin or left >= stop:
                continue
            blocks = _clip_blocks(clip, source_channels, rate, max(0, origin-left), last-left, on_plugin_error)
            active.append((left, right, last, _RegionReader(blocks, source_channels), fades, clip.pan))
        gain = 10 ** (track.gain / 20)
        for position in range(origin, stop, BLOCK):
            end = min(position + BLOCK, stop)
            block = np.zeros((end-position, channels), dtype=np.float32)
            for left, right, last, reader, fades, pan in active:
                begin, finish = max(position, left), min(end, last)
                if begin >= finish:
                    continue
                samples = _map_channels(reader.take(finish-begin), channels)
                for fade_start, fade_end, fade_in, curve in fades:
                    if finish <= fade_start or begin >= fade_end:
                        continue
                    first = max(begin, fade_start)
                    last = min(finish, fade_end)
                    blend = (np.arange(first, last) - fade_start) / max(1, fade_end - fade_start - 1)
                    shaped = _fade(blend, curve)
                    envelope = shaped if fade_in else (
                        _fade(1 - blend, curve) if curve == "equal_power" else 1 - shaped
                    )
                    samples[first-begin:last-begin] *= envelope[:, None]
                if pan_enabled and pan:
                    positions, values = zip(*pan)
                    progress = (np.arange(begin, finish) - left) / max(1, right - left - 1)
                    balance = np.interp(progress, positions, values)
                    samples = samples * np.sqrt(1 - np.maximum(np.column_stack((balance, -balance)), 0))
                block[begin-position:finish-position] += samples
            yield block * gain
    finally:
        for _, _, _, reader, _, _ in active:
            reader.blocks.close()


def _render_track(track, rate, channels, origin, stop, pan_enabled, on_plugin_error, tails):
    def read(first):
        return _track_blocks(track, rate, channels, first, stop, pan_enabled, on_plugin_error, tails)

    if not track.effects:
        return read(origin)
    preroll = rate * max(1, _tail_budget(track.effects))
    warmup = max(0, origin - preroll) if on_plugin_error is not None else 0
    processed = apply_effects(read(warmup), rate, channels, track.effects, measure=lambda: read(0),
                              on_plugin_error=on_plugin_error)
    return _skip_frames(processed, origin - warmup)


def _sum_tracks(tracks, rate, channels, duration, start, on_plugin_error, tails):
    origin, stop = round(start * rate), round(duration * rate)
    readers = []
    try:
        for track in _audible_tracks(tracks):
            pan_enabled = channels == 2 and any(clip.pan for clip in track.clips)
            count = 2 if pan_enabled else track.channels or channels
            blocks = _render_track(track, rate, count, origin, stop, pan_enabled, on_plugin_error, tails)
            readers.append(_RegionReader(blocks, count))
        for position in range(origin, stop, BLOCK):
            frames = min(BLOCK, stop - position)
            block = np.zeros((frames, channels), dtype=np.float32)
            for reader in readers:
                block += _map_channels(reader.take(frames), channels)
            yield block
    finally:
        for reader in readers:
            reader.blocks.close()


def mix(tracks, rate, channels, duration, *, start=0, master_effects=(), on_plugin_error=None, tails=False):
    """Mix in float, process the master once, then limit the output format."""
    budget = tail_budget(tracks, master_effects) if tails else 0

    def read(first):
        return _sum_tracks(tracks, rate, channels, duration + budget, first, on_plugin_error, bool(budget))

    if master_effects:
        origin = round(start * rate)
        warmup = max(0, origin - rate * (budget or 1)) if on_plugin_error is not None else 0
        processed = apply_effects(read(warmup / rate), rate, channels, master_effects,
                                  measure=lambda: read(0), on_plugin_error=on_plugin_error)
        blocks = _skip_frames(processed, origin - warmup)
    else:
        blocks = read(start)
    try:
        output = _trim_tail(blocks, round(duration * rate) - round(start * rate), channels) if budget else blocks
        for block in output:
            writable = np.nan_to_num(block, copy=not block.flags.writeable)
            yield np.clip(writable, -1, 1, out=writable)
    finally:
        blocks.close()


def _trim_tail(blocks, nominal_frames, channels):
    """Hold quiet tail frames until a later echo justifies emitting them."""
    remaining = max(0, nominal_frames)
    quiet = 0
    for block in blocks:
        if remaining:
            count = min(remaining, len(block))
            yield block[:count]
            remaining -= count
            block = block[count:]
        audible = np.flatnonzero(np.any(np.abs(block) >= TAIL_LEVEL, axis=1))
        if not len(audible):
            quiet += len(block)
            continue
        for frame in range(0, quiet, BLOCK):
            yield np.zeros((min(BLOCK, quiet-frame), channels), dtype=np.float32)
        last = int(audible[-1]) + 1
        yield block[:last]
        quiet = len(block) - last


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
    width = bits // 8
    with path.open("wb") as output, wave.open(output, "wb") as writer:
        writer.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        frames = 0
        blocks = mix(tracks, rate, channels, duration, tails=True)
        try:
            for samples in blocks:
                frames += len(samples)
                size = frames * channels * width
                if size + (size & 1) > 0xffffffff - 36:
                    raise ValueError("音频长度无效或超出 WAV 大小限制")
                writer.writeframesraw(pcm_bytes(samples, bits))
        finally:
            blocks.close()
    # PCM RIFF chunks are word-aligned. Pad without counting an audio frame.
    size = frames * channels * width
    if size & 1:
        with path.open("r+b") as output:
            output.seek(4)
            output.write(struct.pack("<I", size + 37))
            output.seek(0, 2)
            output.write(b"\0")
    with wave.open(str(path), "rb") as check:
        if (check.getnframes(), check.getframerate(), check.getnchannels(), check.getsampwidth()) != (
            frames, rate, channels, bits//8
        ):
            raise ValueError("渲染规格校验失败")
