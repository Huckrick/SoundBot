"""Block-based loudness analysis and ordered effects for the editing pipeline."""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass
from fractions import Fraction

import numpy as np

from core.audio_service import _av


_LOUDNESS_LOCK = threading.RLock()
_RANGES = {
    "normalize": {"target": (-24, -12), "peak": (-6, 0)},
    "eq": {"low": (-12, 12), "high": (-12, 12)},
    "compressor": {"threshold": (-48, 0), "ratio": (1, 12)},
    "reverb": {"mix": (0, 100), "size": (0, 100), "damping": (0, 100), "width": (0, 100)},
}

EQ_FILTERS = {"bell": "equalizer", "low_shelf": "bass", "high_shelf": "treble",
              "low_cut": "highpass", "high_cut": "lowpass"}


@dataclass(frozen=True)
class EQBand:
    shape: str
    frequency: float
    gain: float
    q: float
    enabled: bool


@dataclass(frozen=True)
class Effect:
    kind: str
    values: dict[str, float]
    plugin_id: str | None = None
    bands: tuple[EQBand, ...] = ()
    plugin_state: str | None = None
    plugin_instance: str | None = None


def validate_effect(effect: Effect) -> Effect:
    if effect.plugin_instance is not None and effect.kind != "vst3":
        raise ValueError("VST3 参数无效")
    if effect.plugin_state is not None and (effect.kind != "vst3" or effect.values):
        raise ValueError("VST3 参数无效")
    if effect.bands:
        if effect.kind != "eq" or effect.values or effect.plugin_id or len(effect.bands) > 8 or any(
            band.shape not in EQ_FILTERS or not 20 <= band.frequency <= 20000
            or not -24 <= band.gain <= 24 or not 0.1 <= band.q <= 18 for band in effect.bands
        ):
            raise ValueError("均衡频段参数无效")
        return effect
    if effect.kind == "vst3":
        if not effect.plugin_id or any(not 0 <= value <= 1 for value in effect.values.values()):
            raise ValueError("VST3 参数无效")
        return effect
    ranges = _RANGES[effect.kind]
    if effect.plugin_id or effect.values.keys() != ranges.keys() or any(
        not low <= effect.values[key] <= high for key, (low, high) in ranges.items()
    ):
        raise ValueError("效果器参数超出范围")
    return effect


def _graph(blocks, rate, channels, filters):
    layout = {1: "mono", 2: "stereo"}.get(channels, f"{channels}c")
    graph = _av.filter.Graph()
    graph.link_nodes(
        graph.add_abuffer(sample_rate=rate, format="fltp", layout=layout,
                          time_base=Fraction(1, rate)),
        *(graph.add(name, options) for name, options in filters),
        graph.add("abuffersink"),
    )
    graph.configure()
    position = 0

    def output():
        while True:
            try:
                frame = graph.pull()
            except (_av.error.BlockingIOError, _av.error.EOFError):
                return
            if frame.format.is_planar:
                yield frame.to_ndarray().T.astype(np.float32, copy=False)
            else:
                yield frame.to_ndarray().reshape(-1, channels).astype(np.float32, copy=False)

    for block in blocks:
        frame = _av.AudioFrame.from_ndarray(np.ascontiguousarray(block.T), format="fltp", layout=layout)
        frame.sample_rate = rate
        frame.time_base = Fraction(1, rate)
        frame.pts = position
        position += len(block)
        graph.push(frame)
        yield from output()
    graph.push(None)
    yield from output()


def analyze(blocks, rate, channels):
    """Return integrated loudness, loudness range, and oversampled true peak."""
    if _av is None:
        raise ValueError("音频解码器不可用")

    def padded():
        frames = 0
        for block in blocks:
            frames += len(block)
            yield block
        minimum = math.ceil(rate * 0.4)
        if frames < minimum:
            yield np.zeros((minimum - frames, channels), dtype=np.float32)
    # Capture the FFmpeg summary on this thread, with per-frame logging disabled.
    with _LOUDNESS_LOCK:
        previous = _av.logging.get_level()
        _av.logging.set_level(_av.logging.INFO)
        try:
            with _av.logging.Capture(local=True) as messages:
                for _ in _graph(padded(), rate, channels, [("ebur128", "peak=true:framelog=quiet")]):
                    pass
        finally:
            _av.logging.set_level(previous)
    summary = "".join(message for _, name, message in messages if name == "ebur128")

    def value(label):
        match = re.search(rf"^\s*{label}:\s+(-?(?:\d+(?:\.\d+)?|inf))", summary, flags=re.M)
        if not match:
            raise ValueError("无法读取响度分析结果")
        number = float(match[1])
        return number if math.isfinite(number) else None

    return {"integrated": value("I"), "range": value("LRA"), "true_peak": value("Peak")}


def process(blocks, rate, channels, effects, *, measure, on_plugin_error=None):
    """Apply effects in their declared order, using bounded audio blocks."""
    from core import vst

    for effect in effects:
        values = effect.values
        if effect.kind == "normalize":
            levels = analyze(measure(), rate, channels)
            integrated, peak = levels["integrated"], levels["true_peak"]
            if peak is not None:
                target_gain = values["target"] - integrated if integrated is not None and integrated > -70 else 0
                gain = 10 ** (min(target_gain, values["peak"] - peak) / 20)
                blocks = _gain(blocks, gain)
        elif effect.kind == "eq":
            filters = _eq_filters(effect, rate)
            if filters:
                blocks = _graph(blocks, rate, channels, filters)
        elif effect.kind == "compressor":
            threshold = 10 ** (values["threshold"] / 20)
            blocks = _graph(blocks, rate, channels, [
                ("acompressor", f"threshold={threshold}:ratio={values['ratio']}:attack=12:release=180"),
            ])
        elif effect.kind == "vst3":
            blocks = vst.process(blocks, rate, channels, effect.plugin_id, values, effect.plugin_state,
                                 instance=effect.plugin_instance, on_error=on_plugin_error)
        elif effect.kind == "reverb" and values["mix"]:
            blocks = _reverb(blocks, rate, channels, values)
    yield from blocks


def _gain(blocks, amount):
    for block in blocks:
        yield block * amount


def _eq_filters(effect, rate):
    if not effect.bands:
        return [
            ("bass", f"f=160:g={effect.values['low']}"),
            ("treble", f"f={min(4200, rate * 0.4)}:g={effect.values['high']}"),
        ]
    filters = []
    for band in effect.bands:
        if not band.enabled:
            continue
        frequency = min(band.frequency, rate * 0.49)
        width = "t=s:w=1" if band.shape.endswith("_shelf") else f"t=q:w={band.q}"
        gain = "" if band.shape.endswith("_cut") else f":g={band.gain}"
        filters.append((EQ_FILTERS[band.shape], f"f={frequency}:{width}{gain}"))
    return filters


def _reverb(blocks, rate, channels, values):
    from pedalboard import Reverb

    wet = values["mix"] / 100
    # Reverb accepts mono or stereo. Preserve the layout for wider audio.
    pairs = [(slice(i, i+2), Reverb(
        room_size=values["size"] / 100, damping=values["damping"] / 100,
        wet_level=1, dry_level=0, width=values["width"] / 100,
    )) for i in range(0, channels, 2)]
    for block in blocks:
        output = np.empty_like(block)
        for pair, plugin in pairs:
            data = np.ascontiguousarray(block[:, pair].T)
            # Avoid ambiguous channel orientation for a final one- or two-sample block.
            count = data.shape[1]
            if count <= data.shape[0]:
                data = np.pad(data, ((0, 0), (0, 3-count)))
            # Keep the DSP block size fixed across decoder and tail blocks.
            # Changing it can reinitialize the reverb and erase its state.
            reverberated = plugin.process(data, rate, buffer_size=512, reset=False)[:, :count].T
            output[:, pair] = block[:, pair] * (1-wet) + reverberated * wet
        yield output
