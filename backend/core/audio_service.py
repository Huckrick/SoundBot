# -*- coding: utf-8 -*-
"""Cross-platform audio probing, decoding, waveform and playback cache.

The service is deliberately synchronous and has no event-loop ownership. API
handlers should call it through a bounded worker executor (for example
``await asyncio.to_thread(service.waveform, path)``).

PyAV is the only decoder boundary because its pinned wheels carry the tested
FFmpeg libraries. No operation depends on a system FFmpeg executable or an
unrelated libsndfile codec matrix.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Dict, Iterable, Mapping, Optional

import numpy as np

try:  # Backend is launched with backend/ on sys.path in production.
    from config import (
        AUDIO_FORMAT_CAPABILITIES,
        PLAYBACK_WAV_CACHE_MAX_BYTES,
        PLAYBACK_WAV_CACHE_MAX_FILES,
        WAVEFORM_PEAK_COUNT,
        WAVEFORM_VERSION,
        get_temp_dir,
    )
except ModuleNotFoundError:  # Namespace-package import used by tests/tools.
    from backend.config import (  # type: ignore
        AUDIO_FORMAT_CAPABILITIES,
        PLAYBACK_WAV_CACHE_MAX_BYTES,
        PLAYBACK_WAV_CACHE_MAX_FILES,
        WAVEFORM_PEAK_COUNT,
        WAVEFORM_VERSION,
        get_temp_dir,
    )

_AV_IMPORT_ERROR: Optional[Exception] = None
try:
    import av as _av
except Exception as exc:  # Missing wheel, unavailable native DLL, or ABI mismatch.
    _av = None
    _AV_IMPORT_ERROR = exc


logger = logging.getLogger(__name__)


class AudioServiceError(RuntimeError):
    """Stable, serialisable error raised by the audio boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'message': self.message,
            'retryable': self.retryable,
            'details': self.details,
        }


@dataclass(frozen=True)
class FileFingerprint:
    size: int
    mtime_ns: int
    waveform_version: str = WAVEFORM_VERSION

    @property
    def source_key(self) -> str:
        """Fingerprint of source bytes metadata, independent from peak algorithm version."""
        payload = f'{self.size}:{self.mtime_ns}'
        return sha256(payload.encode('utf-8')).hexdigest()

    @property
    def key(self) -> str:
        payload = f'{self.source_key}:{self.waveform_version}'
        return sha256(payload.encode('utf-8')).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result['source_key'] = self.source_key
        result['key'] = self.key
        return result


@dataclass(frozen=True)
class AudioMetadata:
    duration: float
    sample_rate: int
    channels: int
    format: str
    codec: str = ''
    tags: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DecodedAudio:
    """Float32 samples with shape ``(frames, channels)``."""

    samples: np.ndarray
    sample_rate: int
    channels: int
    duration: float


@dataclass(frozen=True)
class WaveformResult:
    peaks: list[float]
    duration: float
    sample_rate: int
    fingerprint: FileFingerprint

    def to_dict(self) -> Dict[str, Any]:
        return {
            'peaks': self.peaks,
            'duration': self.duration,
            'sample_rate': self.sample_rate,
            'fingerprint': self.fingerprint.to_dict(),
            'waveform_version': self.fingerprint.waveform_version,
        }


class AudioService:
    """Thread-safe facade around stateless decoders and a bounded WAV cache."""

    def __init__(
        self,
        playback_cache_dir: Optional[os.PathLike[str] | str] = None,
        *,
        waveform_work_dir: Optional[os.PathLike[str] | str] = None,
        playback_cache_max_bytes: int = PLAYBACK_WAV_CACHE_MAX_BYTES,
        playback_cache_max_files: int = PLAYBACK_WAV_CACHE_MAX_FILES,
        manage_temporary: bool = False,
    ) -> None:
        if playback_cache_max_bytes < 1 or playback_cache_max_files < 1:
            raise ValueError('playback cache limits must be positive')
        runtime_temp_dir = (
            Path(playback_cache_dir).expanduser().resolve(strict=False).parent
            if playback_cache_dir
            else get_temp_dir()
        )
        base = (
            Path(playback_cache_dir).expanduser().resolve(strict=False)
            if playback_cache_dir
            else runtime_temp_dir / 'playback_wav'
        )
        self.playback_cache_dir = base
        self.waveform_work_dir = (
            Path(waveform_work_dir).expanduser().resolve(strict=False)
            if waveform_work_dir
            else runtime_temp_dir / 'waveform_work'
        )
        self.playback_cache_max_bytes = playback_cache_max_bytes
        self.playback_cache_max_files = playback_cache_max_files
        self._cache_lock = threading.RLock()
        self._active_cache: dict[Path, int] = {}
        self._manage_temporary = manage_temporary

    @staticmethod
    def is_supported(path: os.PathLike[str] | str) -> bool:
        return Path(path).suffix.lower() in AUDIO_FORMAT_CAPABILITIES

    @staticmethod
    def requires_playback_transcode(path: os.PathLike[str] | str) -> bool:
        capability = AUDIO_FORMAT_CAPABILITIES.get(Path(path).suffix.lower())
        return bool(capability and capability['requires_playback_transcode'])

    @staticmethod
    def runtime_status() -> Dict[str, Any]:
        """Return a path-free snapshot of the bundled decoder runtime.

        Importing PyAV loads its wheel-bundled FFmpeg libraries.  A missing or
        quarantined DLL therefore shows up here before an import job is
        accepted, instead of being mistaken for a healthy backend process.
        Exception text is deliberately excluded because native-loader errors
        can contain a user's installation path.
        """
        if _av is None:
            return {
                'available': False,
                'required': True,
                'engine': 'pyav',
                'version': None,
                'ffmpeg_libraries': {},
                'error_code': 'audio_decoder_unavailable',
                'error_type': type(
                    _AV_IMPORT_ERROR or ImportError('PyAV unavailable')
                ).__name__,
            }

        versions: Dict[str, str] = {}
        raw_versions = getattr(_av, 'library_versions', {}) or {}
        if isinstance(raw_versions, Mapping):
            for name, value in sorted(raw_versions.items(), key=lambda item: str(item[0])):
                if isinstance(value, (tuple, list)):
                    rendered = '.'.join(str(part) for part in value)
                else:
                    rendered = str(value)
                versions[str(name)] = rendered

        return {
            'available': True,
            'required': True,
            'engine': 'pyav',
            'version': str(getattr(_av, '__version__', '') or '') or None,
            'ffmpeg_libraries': versions,
            'error_code': None,
            'error_type': None,
        }

    def fingerprint(self, path: os.PathLike[str] | str) -> FileFingerprint:
        source = self._validate_source(path)
        stat = source.stat()
        return FileFingerprint(size=stat.st_size, mtime_ns=stat.st_mtime_ns)

    def probe(self, path: os.PathLike[str] | str) -> AudioMetadata:
        source = self._validate_source(path)
        self._require_pyav()
        try:
            return self._probe_with_pyav(source)
        except Exception as exc:
            raise AudioServiceError(
                'audio_probe_failed',
                f'Unable to read audio metadata: {source.name}',
                details={'path': str(source), 'decoder': 'pyav', 'reason': self._safe_error(exc)},
            ) from exc

    def artwork(self, path: os.PathLike[str] | str):
        """Read a bounded display image from the audio file's embedded artwork."""
        from core.source_metadata import read_artwork

        source = self._validate_source(path)
        self._require_pyav()
        try:
            return read_artwork(source, _av)
        except (OSError, ValueError, _av.FFmpegError) as exc:
            raise AudioServiceError('artwork_unavailable', 'Unable to read embedded artwork') from exc

    def details(self, path: os.PathLike[str] | str) -> Dict[str, Any]:
        """Read original file metadata for the details panel on demand."""
        from core.source_metadata import read_details

        source = self._validate_source(path)
        self._require_pyav()
        try:
            return read_details(source, _av)
        except (OSError, ValueError, _av.FFmpegError) as exc:
            raise AudioServiceError('audio_probe_failed', 'Unable to read audio metadata') from exc

    def decode(
        self,
        path: os.PathLike[str] | str,
        *,
        target_sample_rate: Optional[int] = None,
        mono: bool = False,
    ) -> DecodedAudio:
        source = self._validate_source(path)
        if target_sample_rate is not None and target_sample_rate < 1:
            raise AudioServiceError(
                'invalid_sample_rate',
                'target_sample_rate must be positive',
                details={'target_sample_rate': target_sample_rate},
            )

        self._require_pyav()
        try:
            return self._decode_with_pyav(source, target_sample_rate, mono)
        except Exception as exc:
            raise AudioServiceError(
                'audio_decode_failed',
                f'Unable to decode audio: {source.name}',
                details={'path': str(source), 'decoder': 'pyav'},
            ) from exc

    def waveform(self, path: os.PathLike[str] | str) -> WaveformResult:
        """Return exactly 2,000 deterministic normalised mono peaks."""
        source = self._validate_source(path)
        self._require_pyav()
        try:
            return self._waveform_with_pyav_streaming(source)
        except Exception as exc:
            raise AudioServiceError(
                'waveform_decode_failed',
                f'Unable to build waveform: {source.name}',
                details={'path': str(source), 'decoder': 'pyav'},
            ) from exc

    def _waveform_with_pyav_streaming(self, source: Path) -> WaveformResult:
        """Decode mono frames through a bounded temporary file.

        This keeps waveform memory usage essentially constant for long files
        while retaining exact equal-width bins over the fully decoded stream.
        """
        with self._temporary(self.waveform_work_dir, "f32") as temp_path:
            return self._waveform_from_frames(source, temp_path)

    @contextmanager
    def _temporary(self, directory: Path, extension: str):
        directory.mkdir(parents=True, exist_ok=True)
        if self._manage_temporary:
            from core.delivery import staging

            with staging(directory, extension) as path:
                yield path
            return
        fd, name = tempfile.mkstemp(suffix=f".{extension}", dir=directory)
        os.close(fd)
        path = Path(name)
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)

    def _waveform_from_frames(self, source: Path, temp_path: Path) -> WaveformResult:
        frame_count = 0
        sample_rate = 0
        mapped = None
        try:
            with temp_path.open("wb") as sink, _av.open(str(source), mode="r") as container:
                stream = next(
                    (item for item in container.streams if item.type == "audio"), None
                )
                if stream is None:
                    raise ValueError("container has no audio stream")
                sample_rate = int(
                    getattr(stream.codec_context, "sample_rate", 0)
                    or getattr(stream, "rate", 0)
                    or 0
                )
                if sample_rate < 1:
                    raise ValueError("audio stream has no sample rate")
                resampler = _av.AudioResampler(
                    format="fltp", layout="mono", rate=sample_rate
                )

                def write_frames(value: Any) -> None:
                    nonlocal frame_count
                    for output_frame in self._iter_av_frames(value):
                        values = np.asarray(
                            output_frame.to_ndarray(), dtype=np.float32
                        ).reshape(-1)
                        values = np.nan_to_num(
                            values, copy=False, nan=0.0, posinf=1.0, neginf=-1.0
                        )
                        values.tofile(sink)
                        frame_count += int(values.size)

                for frame in container.decode(stream):
                    write_frames(resampler.resample(frame))
                write_frames(resampler.resample(None))
                sink.flush()

            if frame_count:
                mapped = np.memmap(
                    temp_path, dtype=np.float32, mode="r", shape=(frame_count,)
                )
                peaks = self._fixed_peaks_bounded(mapped, WAVEFORM_PEAK_COUNT)
            else:
                peaks = [0.0] * WAVEFORM_PEAK_COUNT
            return WaveformResult(
                peaks=peaks,
                duration=float(frame_count / sample_rate),
                sample_rate=sample_rate,
                fingerprint=self.fingerprint(source),
            )
        finally:
            if mapped is not None:
                del mapped

    def _cache_path(self, source: Path) -> Path:
        source_key = sha256(str(source).encode('utf-8', errors='surrogatepass')).hexdigest()[:20]
        return self.playback_cache_dir / f'{source_key}-{self.fingerprint(source).key}.wav'

    @contextmanager
    def playback_cache_lease(self, source: Path):
        target = self._cache_path(self._validate_source(source))
        with self._cache_lock:
            if not target.is_symlink() and target.is_file() and target.stat().st_size > 44:
                self._register_cache(target)
                self._active_cache[target] = self._active_cache.get(target, 0) + 1
                self._touch(target)
            else:
                target = None
        try:
            yield target
        finally:
            if target is not None:
                with self._cache_lock:
                    self._active_cache[target] -= 1
                    if not self._active_cache[target]:
                        del self._active_cache[target]

    def _register_cache(self, target: Path) -> None:
        if self._manage_temporary:
            from core.delivery import initialize
            from core.database import get_db_manager

            with get_db_manager().get_cursor() as cur:
                initialize(cur)
                cur.execute("INSERT OR IGNORE INTO render_files(path,state) VALUES (?,'playback')", (str(target),))

    def prepare_playback_wav(self, path: os.PathLike[str] | str) -> Path:
        """Create/reuse an atomic, fingerprinted WAV and enforce disk LRU limits."""
        source = self._validate_source(path)
        target = self._cache_path(source)

        with self._cache_lock:
            self.playback_cache_dir.mkdir(parents=True, exist_ok=True)
            if not target.is_symlink() and target.is_file() and target.stat().st_size > 44:
                if target.stat().st_size <= self.playback_cache_max_bytes:
                    self._register_cache(target)
                    self._touch(target)
                    self._prune_playback_cache(protected=target)
                    return target
                target.unlink(missing_ok=True)

            decoded = self.decode(source, mono=False)
            try:
                with self._temporary(self.playback_cache_dir, "wav") as temp_path:
                    self._write_wav(temp_path, decoded.samples, decoded.sample_rate)
                    self._register_cache(target)
                    os.replace(temp_path, target)
            except AudioServiceError:
                raise
            except Exception as exc:
                raise AudioServiceError(
                    'playback_transcode_failed',
                    f'Unable to create playback WAV: {source.name}',
                    details={'path': str(source), 'reason': self._safe_error(exc)},
                ) from exc
            self._touch(target)
            self._prune_playback_cache(protected=target)
            if target.stat().st_size > self.playback_cache_max_bytes:
                size = target.stat().st_size
                target.unlink(missing_ok=True)
                raise AudioServiceError(
                    'playback_cache_limit_exceeded',
                    'Playback WAV is larger than the configured cache limit',
                    details={
                        'path': str(source),
                        'wav_size': size,
                        'cache_limit': self.playback_cache_max_bytes,
                    },
                )
            return target

    def export_wav(
        self,
        path: os.PathLike[str] | str,
        samples: np.ndarray,
        sample_rate: int,
    ) -> Path:
        """Atomically persist processed float samples as portable PCM WAV."""
        target = Path(path).expanduser().resolve(strict=False)
        if target.suffix.lower() != '.wav':
            target = target.with_suffix('.wav')
        if sample_rate < 1:
            raise AudioServiceError(
                'invalid_sample_rate', 'sample_rate must be positive'
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Optional[Path] = None
        try:
            fd, raw_path = tempfile.mkstemp(
                prefix=f'.{target.stem}-', suffix='.tmp.wav', dir=target.parent
            )
            os.close(fd)
            temp_path = Path(raw_path)
            self._write_wav(
                temp_path, self._sanitise_samples(samples), int(sample_rate)
            )
            os.replace(temp_path, target)
            temp_path = None
            return target
        except AudioServiceError:
            raise
        except Exception as exc:
            raise AudioServiceError(
                'audio_export_failed',
                'Unable to export processed audio',
                details={'path': str(target), 'reason': self._safe_error(exc)},
            ) from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _validate_source(self, path: os.PathLike[str] | str) -> Path:
        try:
            source = Path(path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise AudioServiceError(
                'audio_not_found',
                'Audio file does not exist',
                details={'path': str(path)},
            ) from exc
        if not source.is_file():
            raise AudioServiceError(
                'audio_not_file', 'Audio path is not a file', details={'path': str(source)}
            )
        if not self.is_supported(source):
            raise AudioServiceError(
                'unsupported_audio_format',
                f'Unsupported audio format: {source.suffix or "(none)"}',
                details={
                    'path': str(source),
                    'supported_extensions': list(AUDIO_FORMAT_CAPABILITIES),
                },
            )
        return source

    @staticmethod
    def _require_pyav() -> None:
        if _av is None:
            raise AudioServiceError(
                'audio_decoder_unavailable',
                'Bundled PyAV/FFmpeg runtime is unavailable',
                details={
                    'decoder': 'pyav',
                    'reason': AudioService._safe_error(
                        _AV_IMPORT_ERROR or ImportError('PyAV unavailable')
                    ),
                },
            )

    @staticmethod
    def _probe_with_pyav(source: Path) -> AudioMetadata:
        with _av.open(str(source), mode='r', timeout=(4.0, 4.0)) as container:
            stream = next((item for item in container.streams if item.type == 'audio'), None)
            if stream is None:
                raise ValueError('container has no audio stream')
            context = stream.codec_context
            sample_rate = int(getattr(context, 'sample_rate', 0) or getattr(stream, 'rate', 0) or 0)
            channels = int(getattr(context, 'channels', 0) or 0)
            if not channels and getattr(stream, 'layout', None) is not None:
                channels = len(stream.layout.channels)
            duration = 0.0
            if stream.duration is not None and stream.time_base is not None:
                duration = float(stream.duration * stream.time_base)
            elif container.duration is not None:
                duration = float(container.duration / _av.time_base)
            codec = getattr(context, 'name', '') or ''
            if sample_rate < 1 or channels < 1:
                raise ValueError('audio stream has incomplete channel or sample-rate metadata')
            return AudioMetadata(
                duration=max(0.0, duration),
                sample_rate=sample_rate,
                channels=channels,
                format=source.suffix.lower().lstrip('.'),
                codec=codec,
                tags={**container.metadata, **stream.metadata},
            )

    @staticmethod
    def _decode_with_pyav(
        source: Path, target_sample_rate: Optional[int], mono: bool
    ) -> DecodedAudio:
        chunks: list[np.ndarray] = []
        with _av.open(str(source), mode='r') as container:
            stream = next((item for item in container.streams if item.type == 'audio'), None)
            if stream is None:
                raise ValueError('container has no audio stream')

            source_rate = int(
                getattr(stream.codec_context, 'sample_rate', 0)
                or getattr(stream, 'rate', 0)
                or 0
            )
            output_rate = int(target_sample_rate or source_rate)
            if output_rate < 1:
                raise ValueError('audio stream has no sample rate')
            layout = 'mono' if mono else getattr(getattr(stream, 'layout', None), 'name', None)
            if not layout:
                channel_count = int(getattr(stream.codec_context, 'channels', 0) or 1)
                layout = 'mono' if channel_count == 1 else 'stereo'
            resampler = _av.AudioResampler(format='fltp', layout=layout, rate=output_rate)

            for frame in container.decode(stream):
                converted = resampler.resample(frame)
                for output_frame in AudioService._iter_av_frames(converted):
                    array = np.asarray(output_frame.to_ndarray(), dtype=np.float32)
                    if array.ndim == 1:
                        array = array[np.newaxis, :]
                    chunks.append(array.T.copy())
            for output_frame in AudioService._iter_av_frames(resampler.resample(None)):
                array = np.asarray(output_frame.to_ndarray(), dtype=np.float32)
                if array.ndim == 1:
                    array = array[np.newaxis, :]
                chunks.append(array.T.copy())

        samples = (
            np.concatenate(chunks, axis=0)
            if chunks
            else np.empty((0, 1 if mono else 0), dtype=np.float32)
        )
        samples = AudioService._sanitise_samples(samples)
        channels = int(samples.shape[1]) if samples.ndim == 2 and samples.shape[1] else (1 if mono else 0)
        return DecodedAudio(
            samples=samples,
            sample_rate=output_rate,
            channels=channels,
            duration=float(samples.shape[0] / output_rate),
        )

    @staticmethod
    def _iter_av_frames(value: Any) -> Iterable[Any]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return value
        return (value,)

    @staticmethod
    def _sanitise_samples(samples: np.ndarray) -> np.ndarray:
        result = np.asarray(samples, dtype=np.float32)
        if result.ndim != 2:
            raise ValueError('decoded audio must have shape (frames, channels)')
        return np.nan_to_num(result, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)

    @staticmethod
    def _fixed_peaks(samples: np.ndarray, count: int) -> list[float]:
        mono = np.abs(np.nan_to_num(np.asarray(samples, dtype=np.float32), nan=0.0))
        if mono.size == 0:
            return [0.0] * count
        boundaries = np.linspace(0, mono.size, count + 1, dtype=np.int64)
        peaks = np.zeros(count, dtype=np.float32)
        for index in range(count):
            start, end = int(boundaries[index]), int(boundaries[index + 1])
            if end > start:
                peaks[index] = float(np.max(mono[start:end]))
            else:
                peaks[index] = float(mono[min(start, mono.size - 1)])
        maximum = float(np.max(peaks))
        if math.isfinite(maximum) and maximum > 0.0:
            peaks /= maximum
        peaks = np.nan_to_num(peaks, nan=0.0, posinf=1.0, neginf=0.0)
        return np.clip(peaks, 0.0, 1.0).astype(float).tolist()

    @staticmethod
    def _fixed_peaks_bounded(samples: np.ndarray, count: int) -> list[float]:
        """Compute fixed peaks without materialising an absolute-value copy."""
        size = int(samples.size)
        if size == 0:
            return [0.0] * count
        boundaries = np.linspace(0, size, count + 1, dtype=np.int64)
        peaks = np.zeros(count, dtype=np.float32)
        for index in range(count):
            start, end = int(boundaries[index]), int(boundaries[index + 1])
            if end <= start:
                value = float(samples[min(start, size - 1)])
                peaks[index] = abs(value) if math.isfinite(value) else 0.0
                continue
            segment = np.asarray(samples[start:end], dtype=np.float32)
            finite = np.nan_to_num(
                segment, copy=True, nan=0.0, posinf=1.0, neginf=-1.0
            )
            peaks[index] = float(np.max(np.abs(finite)))
        maximum = float(np.max(peaks))
        if math.isfinite(maximum) and maximum > 0.0:
            peaks /= maximum
        return np.clip(
            np.nan_to_num(peaks, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0
        ).astype(float).tolist()

    @staticmethod
    def _write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
        import wave

        values = AudioService._sanitise_samples(samples)
        pcm = np.clip(values, -1.0, 1.0)
        pcm = np.rint(pcm * 32767.0).astype('<i2', copy=False)
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(int(values.shape[1]))
            output.setsampwidth(2)
            output.setframerate(int(sample_rate))
            output.writeframes(pcm.tobytes(order='C'))
        if not path.is_file() or path.stat().st_size <= 44:
            raise OSError('decoder produced an empty WAV')

    @staticmethod
    def _touch(path: Path) -> None:
        try:
            os.utime(path, None)
        except OSError:
            pass

    def _prune_playback_cache(self, *, protected: Optional[Path] = None) -> None:
        entries = self._playback_entries()
        entries.sort(key=lambda item: item[1])
        total_bytes = sum(item[2] for item in entries)
        total_files = len(entries)
        for entry, _, size in entries:
            if (
                total_files <= self.playback_cache_max_files
                and total_bytes <= self.playback_cache_max_bytes
            ):
                break
            if entry == protected or entry in self._active_cache:
                continue
            try:
                self._remove_cache(entry)
                total_files -= 1
                total_bytes -= size
            except OSError:
                logger.debug('Unable to prune playback cache file %s', entry, exc_info=True)

    def _playback_entries(self):
        if self._manage_temporary:
            from core.database import get_db_manager
            from core.delivery import initialize

            with get_db_manager().get_cursor() as cur:
                initialize(cur)
                paths = [Path(row["path"]) for row in cur.execute("SELECT path FROM render_files WHERE state='playback'")]
        else:
            paths = self.playback_cache_dir.glob("*.wav")
        entries = []
        for entry in paths:
            if entry.parent != self.playback_cache_dir or not re.fullmatch(r"[a-f0-9]{20}-[a-f0-9]{64}\.wav", entry.name):
                continue
            try:
                if not entry.is_symlink():
                    stat = entry.stat()
                    entries.append((entry, stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                self._remove_cache(entry)
        return entries

    def _remove_cache(self, entry):
        entry.unlink(missing_ok=True)
        if self._manage_temporary:
            from core.database import get_db_manager

            with get_db_manager().get_cursor() as cur:
                cur.execute("DELETE FROM render_files WHERE path=? AND state='playback'", (str(entry),))

    def cleanup_playback_cache(self, *, execute, paths=None):
        files = []
        with self._cache_lock:
            for entry, _, size in self._playback_entries():
                active = entry in self._active_cache
                removed = False
                if execute and not active and (paths is None or str(entry) in paths):
                    try:
                        self._remove_cache(entry)
                        removed = True
                    except OSError:
                        logger.debug("Unable to remove playback cache %s", entry, exc_info=True)
                files.append({"path": str(entry), "bytes": size, "removed": removed, "eligible": not active,
                              "reason": "试听使用中" if active else "可再生成的试听缓存"})
        return files

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        # Never include a traceback or arbitrary object repr in an API payload.
        text = str(exc).strip()
        return text[:500] if text else type(exc).__name__


_audio_service: Optional[AudioService] = None
_audio_service_lock = threading.Lock()


def get_audio_service() -> AudioService:
    global _audio_service
    if _audio_service is None:
        with _audio_service_lock:
            if _audio_service is None:
                _audio_service = AudioService(manage_temporary=True)
    return _audio_service
