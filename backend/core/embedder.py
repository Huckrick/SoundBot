# -*- coding: utf-8 -*-
# SoundBot - AI 音效管理器
# Copyright (C) 2026 Nagisa_Huckrick (胡杨)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""CLAP audio/text embeddings and pluggable metadata-text embeddings.

The audio index always uses CLAP so audio and text live in the same vector
space.  A separate metadata index may use CLAP text features or an
OpenAI-compatible embedding endpoint.  Keeping these two responsibilities
explicit prevents a text-only model from accidentally replacing the audio
encoder.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import httpx
import numpy as np
import torch

import config

logger = logging.getLogger(__name__)

if hasattr(config, "HF_ENDPOINT"):
    os.environ.setdefault("HF_ENDPOINT", config.HF_ENDPOINT)
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")

CLAP_PREPROCESSING_VERSION = "clap-deterministic-windows-v2"
_MODEL_HASH_CHUNK_SIZE = 1024 * 1024


def normalize_embedding(value: Any) -> np.ndarray:
    """Return a finite, unit-length float32 vector."""
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    if vector.size == 0 or not np.all(np.isfinite(vector)):
        raise ValueError("embedding 必须是非空有限向量")
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 1e-12:
        raise ValueError("embedding 范数必须大于 0")
    return vector / norm


def _local_model_descriptor(model_path: Any) -> Dict[str, str]:
    """Return a path-independent identity for one installed local model.

    Release model bundles are verified before their atomic installation, so a
    valid package manifest is the authoritative, cheap identity source. Local
    development directories without such a manifest fall back to streaming
    the actual contents of every model file; filenames and sizes alone are not
    sufficient because weights can change without changing their byte count.
    """
    model_dir = Path(os.fspath(model_path)).expanduser().resolve(strict=False)
    if not model_dir.is_dir():
        raise FileNotFoundError(f"CLAP 本地模型目录不存在: {model_dir}")

    manifest_payload: Dict[str, Any] = {}
    for manifest_path in (
        model_dir.parent / "model-manifest.json",
        model_dir / "model-manifest.json",
    ):
        if not manifest_path.is_file():
            continue
        try:
            candidate = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(candidate, dict):
                manifest_payload = candidate
                break
        except (OSError, UnicodeDecodeError, ValueError, TypeError):
            logger.warning("CLAP model manifest 无法读取: %s", manifest_path)

    model_id = str(manifest_payload.get("model_id") or "local/clap")
    manifest_revision = str(manifest_payload.get("revision") or "").strip()
    declared_files = manifest_payload.get("files")
    verified_declarations: Dict[str, str] = {}
    if manifest_revision and isinstance(declared_files, dict):
        for raw_relative, raw_checksum in declared_files.items():
            relative = str(raw_relative).replace("\\", "/").strip("/")
            parts = tuple(part for part in relative.split("/") if part)
            checksum = str(raw_checksum or "").strip().lower()
            if (
                not parts
                or any(part in {".", ".."} for part in parts)
                or len(checksum) != 64
                or any(character not in "0123456789abcdef" for character in checksum)
            ):
                continue
            if parts[0] == model_dir.name:
                local_parts = parts[1:]
                canonical_relative = "/".join(parts)
            else:
                local_parts = parts
                canonical_relative = "/".join((model_dir.name, *parts))
            local_file = model_dir.joinpath(*local_parts)
            if local_parts and local_file.is_file():
                verified_declarations[canonical_relative] = checksum

    if verified_declarations:
        identity_payload = {
            "model_id": model_id,
            "revision": manifest_revision,
            "files": dict(sorted(verified_declarations.items())),
        }
        identity_digest = hashlib.sha256(
            json.dumps(identity_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return {
            "identity": f"local-clap:{identity_digest}",
            "model_id": model_id,
            "revision": manifest_revision,
            "identity_source": "model-manifest",
        }

    content_digest = hashlib.sha256()
    files = sorted(
        (candidate for candidate in model_dir.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(model_dir).as_posix(),
    )
    for candidate in files:
        if candidate.is_symlink():
            raise ValueError(f"CLAP 模型目录不允许符号链接: {candidate}")
        relative = candidate.relative_to(model_dir).as_posix()
        content_digest.update(relative.encode("utf-8"))
        content_digest.update(b"\0")
        with candidate.open("rb") as source:
            for chunk in iter(lambda: source.read(_MODEL_HASH_CHUNK_SIZE), b""):
                content_digest.update(chunk)
        content_digest.update(b"\0")
    content_sha256 = content_digest.hexdigest()
    identity_payload = {
        "model_id": model_id,
        "revision": manifest_revision or f"content:{content_sha256}",
        "content_sha256": content_sha256,
    }
    identity_digest = hashlib.sha256(
        json.dumps(identity_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "identity": f"local-clap:{identity_digest}",
        "model_id": model_id,
        "revision": manifest_revision or f"content:{content_sha256}",
        "identity_source": "content-sha256",
    }


class CLIPEmbedder:
    """Thread-safe singleton wrapper around LAION CLAP."""

    _instance: Optional["CLIPEmbedder"] = None
    _instance_lock = threading.RLock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._initialized = False
                instance._initializing = False
                instance._init_lock = threading.RLock()
                instance._inference_lock = threading.RLock()
                cls._instance = instance
            return cls._instance

    def __init__(self):
        if self._initialized:
            return

        with self._init_lock:
            if self._initialized:
                return
            self._initializing = True
            try:
                from transformers import ClapModel, ClapProcessor

                self.device = self._get_device()
                model_path = Path(config.get_clap_model_name()).expanduser()
                descriptor = _local_model_descriptor(model_path)
                logger.info("加载 CLAP 模型 %s 到 %s", model_path, self.device)
                self.model = ClapModel.from_pretrained(
                    str(model_path),
                    low_cpu_mem_usage=True,
                    local_files_only=True,
                )
                self.model.to(self.device)
                self.model.eval()
                self.processor = ClapProcessor.from_pretrained(
                    str(model_path), local_files_only=True
                )

                feature_extractor = getattr(self.processor, "feature_extractor", None)
                self.sample_rate = int(getattr(feature_extractor, "sampling_rate", 48000))
                self.max_samples = self._processor_max_samples(feature_extractor)
                self._model_identity = descriptor["identity"]
                # Preserve the historical private name for compatibility with
                # old diagnostics while exposing the package model ID below.
                self._model_name = self._model_identity
                self._model_id = descriptor["model_id"]
                self._model_revision = descriptor["revision"]
                self._fingerprint = self._build_fingerprint()
                self._initialized = True
                logger.info(
                    "CLAP 模型加载完成（窗口 %.2fs，fingerprint=%s）",
                    self.max_samples / self.sample_rate,
                    self._fingerprint[:12],
                )
            except Exception:
                # A failed singleton must be retryable after reset_embedder().
                logger.exception("CLAP 模型加载失败")
                raise
            finally:
                self._initializing = False

    def _get_device(self) -> torch.device:
        if torch.cuda.is_available():
            return torch.device("cuda")
        try:
            if torch.backends.mps.is_available():
                return torch.device("mps")
        except AttributeError:
            pass
        return torch.device("cpu")

    def _processor_max_samples(self, feature_extractor: Any) -> int:
        max_samples = getattr(feature_extractor, "nb_max_samples", None)
        if isinstance(max_samples, (int, float)) and max_samples > 0:
            return int(max_samples)
        max_length_s = getattr(feature_extractor, "max_length_s", None)
        if isinstance(max_length_s, (int, float)) and max_length_s > 0:
            return int(round(max_length_s * self.sample_rate))
        # LAION CLAP's published feature extractor uses a 10 second window.
        return 10 * self.sample_rate

    def _build_fingerprint(self) -> str:
        payload = {
            "model": getattr(self, "_model_identity", self._model_name),
            "revision": getattr(self, "_model_revision", "local"),
            "dimension": self.get_embedding_dim(),
            "sample_rate": self.sample_rate,
            "max_samples": self.max_samples,
            "preprocessing": CLAP_PREPROCESSING_VERSION,
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _stable_model_identity(model_path: Any) -> str:
        """Identify local model contents without including their install path."""
        return _local_model_descriptor(model_path)["identity"]

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    @property
    def engine_manifest(self) -> Dict[str, Any]:
        """Describe the exact audio-vector engine without exposing install paths."""
        return {
            "model_id": getattr(self, "_model_id", self._model_name),
            "model_revision": getattr(self, "_model_revision", "local"),
            "dimensions": self.get_embedding_dim(),
            "preprocessing_version": CLAP_PREPROCESSING_VERSION,
            "sample_rate": self.sample_rate,
            "window_samples": self.max_samples,
            "engine_fingerprint": self.fingerprint,
        }

    def audio_to_embedding(self, audio_path: str) -> np.ndarray:
        """Encode an audio file using deterministic processor-sized windows."""
        try:
            from core.audio_service import get_audio_service

            decoded = get_audio_service().decode(
                audio_path,
                target_sample_rate=self.sample_rate,
                mono=True,
            )
            audio = decoded.samples
            audio = np.asarray(audio, dtype=np.float32).reshape(-1)
            if audio.size == 0:
                raise ValueError("音频为空")
            if audio.size <= self.max_samples:
                return self._process_audio_segment(audio)
            return self._process_long_audio(audio)
        except Exception as exc:
            raise RuntimeError(f"[Embedder] 处理 {audio_path} 失败: {exc}") from exc

    def _processor_inputs(self, audio: np.ndarray):
        # Segments are never longer than max_samples, so rand_trunc cannot pick
        # a random crop. Padding is deterministic for the final short segment.
        kwargs = {
            "sampling_rate": self.sample_rate,
            "max_length": self.max_samples,
            "truncation": "rand_trunc",
            "padding": "repeatpad",
            "return_tensors": "pt",
        }
        try:
            return self.processor(audio=[audio], **kwargs).to(self.device)
        except TypeError:
            return self.processor(audios=[audio], **kwargs).to(self.device)

    @staticmethod
    def _output_vector(outputs: Any) -> np.ndarray:
        tensor = outputs.pooler_output if hasattr(outputs, "pooler_output") else outputs
        return normalize_embedding(tensor.detach().cpu().numpy()[0])

    def _process_audio_segment(self, audio: np.ndarray) -> np.ndarray:
        segment = np.asarray(audio[: self.max_samples], dtype=np.float32)
        with self._inference_lock:
            inputs = self._processor_inputs(segment)
            with torch.inference_mode():
                outputs = self.model.get_audio_features(**inputs)
        return self._output_vector(outputs)

    def _process_long_audio(
        self,
        audio: np.ndarray,
        window_size: Optional[int] = None,
        hop_size: Optional[int] = None,
    ) -> np.ndarray:
        """Encode every deterministic window and mean-pool normalized vectors.

        ``window_size`` and ``hop_size`` remain accepted for compatibility and
        are interpreted as seconds when supplied by older callers.
        """
        window_samples = (
            int(window_size * self.sample_rate) if window_size else self.max_samples
        )
        window_samples = min(max(1, window_samples), self.max_samples)
        hop_samples = int(hop_size * self.sample_rate) if hop_size else window_samples
        hop_samples = max(1, hop_samples)

        embeddings: List[np.ndarray] = []
        for start in range(0, len(audio), hop_samples):
            segment = audio[start : start + window_samples]
            if segment.size == 0:
                continue
            embeddings.append(self._process_audio_segment(segment))
            if start + window_samples >= len(audio):
                break

        if not embeddings:
            raise RuntimeError("无法提取有效的音频片段")
        return normalize_embedding(np.mean(np.stack(embeddings), axis=0))

    def text_to_embedding(self, text: str) -> np.ndarray:
        text = str(text or "").strip()
        if not text:
            raise ValueError("文本不能为空")
        try:
            with self._inference_lock:
                inputs = self.processor(text=[text], return_tensors="pt").to(self.device)
                with torch.inference_mode():
                    outputs = self.model.get_text_features(**inputs)
            return self._output_vector(outputs)
        except Exception as exc:
            raise RuntimeError(f"[Embedder] 文本嵌入失败: {exc}") from exc

    def get_embedding_dim(self) -> int:
        config_dim = getattr(getattr(self, "model", None), "config", None)
        dim = getattr(config_dim, "projection_dim", 512)
        return int(dim or 512)


@runtime_checkable
class TextEmbeddingProvider(Protocol):
    """Embedding interface for the separate metadata-text index."""

    @property
    def fingerprint(self) -> str: ...

    async def embed_texts(self, texts: List[str]) -> List[np.ndarray]: ...


class CLAPTextEmbeddingProvider:
    def __init__(self, embedder: Optional[CLIPEmbedder] = None):
        # Provider construction happens on FastAPI request/background-task
        # coroutines. Model loading is owned exclusively by ModelPreloader's
        # worker, so never perform a synchronous load from this async path.
        self.embedder = embedder or peek_embedder()
        if self.embedder is None:
            raise RuntimeError("CLAP 模型不可用")

    @property
    def fingerprint(self) -> str:
        return f"clap-text:{self.embedder.fingerprint}"

    async def embed_texts(self, texts: List[str]) -> List[np.ndarray]:
        # CLAP has one shared inference lock; sequential calls avoid scheduling
        # competing model forwards while still keeping the event loop free.
        result: List[np.ndarray] = []
        for text in texts:
            vector = await asyncio.to_thread(self.embedder.text_to_embedding, text)
            result.append(normalize_embedding(vector))
        return result


class OpenAICompatibleTextEmbeddingProvider:
    """Metadata embeddings from LM Studio, Ollama or an OpenAI-compatible API."""

    def __init__(self, config_data: Dict[str, Any], client: Optional[httpx.AsyncClient] = None):
        self.base_url = str(config_data.get("base_url", "")).rstrip("/")
        self.model = str(config_data.get("model", ""))
        self.api_key = str(config_data.get("api_key", ""))
        if not self.base_url:
            raise ValueError("Embedding API 地址未配置")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    @property
    def fingerprint(self) -> str:
        payload = {"kind": "openai-compatible", "url": self.base_url, "model": self.model}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return f"openai-text:{digest}"

    async def embed_texts(self, texts: List[str]) -> List[np.ndarray]:
        if not texts:
            return []
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = await self._client.post(
            f"{self.base_url}/embeddings",
            headers=headers,
            json={"model": self.model, "input": texts},
        )
        response.raise_for_status()
        data = response.json().get("data", [])
        ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
        vectors = [normalize_embedding(item["embedding"]) for item in ordered]
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding 服务返回的向量数量不匹配")
        return vectors

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def get_text_embedding_config_fingerprint(
    config_override: Optional[Dict[str, Any]] = None,
) -> str:
    """Hash provider/model settings while explicitly excluding credentials."""
    if config_override is None:
        from core.llm_config_manager import get_llm_config_manager

        config_override = get_llm_config_manager().get_embedding_config()
    safe = json.loads(json.dumps(config_override, default=str))
    for section in safe.values():
        if isinstance(section, dict):
            section.pop("api_key", None)
            section.pop("headers", None)
    digest = hashlib.sha256(
        json.dumps(safe, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"text-config:{digest}"


def get_text_embedding_provider(
    config_override: Optional[Dict[str, Any]] = None,
) -> TextEmbeddingProvider:
    """Build the configured provider for metadata text only."""
    if config_override is None:
        from core.llm_config_manager import get_llm_config_manager

        config_override = get_llm_config_manager().get_embedding_config()
    provider = str(config_override.get("provider", "default"))
    if provider == "default":
        return CLAPTextEmbeddingProvider()
    if provider not in {"local", "external"}:
        raise ValueError(f"不支持的文本 Embedding 提供者: {provider}")
    return OpenAICompatibleTextEmbeddingProvider(config_override.get(provider, {}))


_embedder: Optional[CLIPEmbedder] = None
_embedder_loading_failed = False
_embedder_lock = threading.RLock()


def peek_embedder() -> Optional[CLIPEmbedder]:
    """Return an already-loaded CLAP embedder without starting any load.

    This is the only accessor suitable for event-loop request paths.  It does
    not instantiate the model preloader and never calls ``CLIPEmbedder()``.
    Synchronous indexing workers should continue to use :func:`get_embedder`.
    """
    with _embedder_lock:
        if _embedder is not None and getattr(_embedder, "_initialized", True):
            return _embedder

    # A preloader may own the shared instance without publishing it through
    # this module-level slot.  Inspect only an existing preloader: calling
    # get_preloader() here would create lifecycle state on a request path.
    try:
        from core import model_preloader as preloader_module

        preloader = getattr(preloader_module, "_preloader", None)
        if preloader is not None:
            preloaded = preloader.get_embedder()
            if preloaded is not None and getattr(preloaded, "_initialized", True):
                return preloaded
    except (ImportError, AttributeError):
        pass

    # Cover callers that constructed the singleton directly (including the
    # preloader while it is publishing its result) without constructing a new
    # model or waiting on initialization.
    instance = CLIPEmbedder._instance
    if instance is not None and getattr(instance, "_initialized", False):
        return instance
    return None


def get_embedder() -> Optional[CLIPEmbedder]:
    """Get the shared embedder, preferring the preloaded instance."""
    global _embedder, _embedder_loading_failed
    try:
        from core.model_preloader import get_preloader

        preloaded = get_preloader().get_embedder()
        if preloaded is not None:
            return preloaded
    except ImportError:
        pass

    with _embedder_lock:
        if _embedder is None and not _embedder_loading_failed:
            try:
                _embedder = CLIPEmbedder()
            except Exception as exc:
                logger.error("无法加载模型: %s", exc)
                _embedder_loading_failed = True
        return _embedder


def is_embedder_available() -> bool:
    """Report readiness without constructing or synchronously loading CLAP."""
    return peek_embedder() is not None


def is_embedder_loaded() -> bool:
    return peek_embedder() is not None


def get_embedder_fingerprint(load: bool = False) -> str:
    embedder = get_embedder() if load else peek_embedder()
    return embedder.fingerprint if embedder is not None else "clap:unavailable"


def get_clap_engine_manifest(load: bool = False) -> Dict[str, Any]:
    """Return manifest fields for SQLite index compatibility checks.

    A missing optional model is represented explicitly and never triggers a
    network download. When the model is already loaded, runtime processor and
    dimension values take precedence over package metadata.
    """
    embedder = get_embedder() if load else peek_embedder()
    if embedder is not None:
        return dict(embedder.engine_manifest)

    model_path = Path(config.get_clap_model_name()).expanduser()
    if not model_path.is_dir():
        return {
            "model_id": None,
            "model_revision": "unavailable",
            "preprocessing_version": CLAP_PREPROCESSING_VERSION,
            "dimensions": None,
            "engine_fingerprint": "clap:unavailable",
        }
    try:
        descriptor = _local_model_descriptor(model_path)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("CLAP 本地模型身份读取失败: %s", exc)
        return {
            "model_id": None,
            "model_revision": "unavailable",
            "preprocessing_version": CLAP_PREPROCESSING_VERSION,
            "dimensions": None,
            "engine_fingerprint": "clap:unavailable",
        }
    fingerprint_payload = {
        "model_identity": descriptor["identity"],
        "model_id": descriptor["model_id"],
        "model_revision": descriptor["revision"],
        "preprocessing_version": CLAP_PREPROCESSING_VERSION,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "model_id": descriptor["model_id"],
        "model_revision": descriptor["revision"],
        "preprocessing_version": CLAP_PREPROCESSING_VERSION,
        "dimensions": None,
        "engine_fingerprint": fingerprint,
    }


def reset_embedder() -> None:
    """Fully reset both module and class-level singleton state."""
    global _embedder, _embedder_loading_failed
    with _embedder_lock, CLIPEmbedder._instance_lock:
        _embedder = None
        _embedder_loading_failed = False
        CLIPEmbedder._instance = None
