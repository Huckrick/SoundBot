#!/usr/bin/env python3
"""Exercise an actual NSIS-installed SoundBot Electron/backend chain.

The release workflow launches the installed application with Chromium's local
debugging endpoint, then evaluates only the same context-bridged APIs available
to the production renderer.  This catches failures that a direct
``win-unpacked`` backend smoke cannot see: missing ``app.asar`` files, a broken
sandboxed preload, incorrect ``process.resourcesPath`` resolution, backend
spawn failures, and installed-runtime PyAV/FFmpeg failures.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request
import wave

import websocket


REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _write_fixture(path: Path, frequency: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 44_100
    frame_count = sample_rate
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames = bytearray()
        for index in range(frame_count):
            envelope = 0.25 + 0.75 * (index / max(1, frame_count - 1))
            value = int(
                20_000
                * envelope
                * math.sin(2.0 * math.pi * frequency * index / sample_rate)
            )
            frames.extend(struct.pack("<h", value))
        output.writeframes(frames)


def _discover_main_target(port: int, deadline: float) -> tuple[str, list[str]]:
    observed_urls: list[str] = []
    endpoint = f"http://127.0.0.1:{port}/json/list"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(endpoint, timeout=2) as response:
                targets = json.loads(response.read().decode("utf-8"))
            observed_urls = [str(item.get("url", "")) for item in targets]
            for target in targets:
                target_url = str(target.get("url", ""))
                websocket_url = target.get("webSocketDebuggerUrl")
                if (
                    target.get("type") == "page"
                    and "index.html" in target_url
                    and isinstance(websocket_url, str)
                ):
                    return websocket_url, observed_urls
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        time.sleep(0.5)
    raise TimeoutError(
        "installed Electron main window did not expose a CDP target; "
        f"observed URLs: {observed_urls}"
    )


class _DevToolsClient:
    def __init__(self, url: str, timeout: float) -> None:
        self._connection = websocket.create_connection(
            url,
            timeout=timeout,
            suppress_origin=True,
        )
        self._next_id = 0

    def close(self) -> None:
        self._connection.close()

    def call(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        request_id = self._next_id
        self._connection.send(
            json.dumps(
                {"id": request_id, "method": method, "params": params or {}},
                separators=(",", ":"),
            )
        )
        while True:
            payload = json.loads(self._connection.recv())
            if payload.get("id") != request_id:
                continue
            if "error" in payload:
                raise RuntimeError(f"CDP {method} failed: {payload['error']}")
            return payload.get("result", {})

    def evaluate(self, expression: str) -> dict:
        payload = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
        )
        if payload.get("exceptionDetails"):
            details = payload["exceptionDetails"]
            exception = details.get("exception", {}).get("description")
            raise RuntimeError(exception or details.get("text") or "renderer evaluation failed")
        result = payload.get("result", {})
        if result.get("subtype") == "error":
            raise RuntimeError(result.get("description") or "renderer returned an error")
        value = result.get("value")
        if not isinstance(value, dict):
            raise RuntimeError(f"renderer smoke returned an invalid value: {value!r}")
        return value


def _windows_process_tree(root_pid: int) -> set[int]:
    """Return the Electron process tree without relying on WMI or PowerShell."""
    if os.name != "nt":
        raise RuntimeError("native dialog enumeration is Windows-only")
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot_fn = kernel32.CreateToolhelp32Snapshot
    snapshot_fn.argtypes = [wintypes.DWORD, wintypes.DWORD]
    snapshot_fn.restype = wintypes.HANDLE
    first_fn = kernel32.Process32FirstW
    first_fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    first_fn.restype = wintypes.BOOL
    next_fn = kernel32.Process32NextW
    next_fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    next_fn.restype = wintypes.BOOL
    close_fn = kernel32.CloseHandle
    close_fn.argtypes = [wintypes.HANDLE]
    close_fn.restype = wintypes.BOOL

    snapshot = snapshot_fn(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    parents: dict[int, int] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if first_fn(snapshot, ctypes.byref(entry)):
            while True:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                if not next_fn(snapshot, ctypes.byref(entry)):
                    break
    finally:
        close_fn(snapshot)

    process_ids = {int(root_pid)}
    changed = True
    while changed:
        changed = False
        for process_id, parent_id in parents.items():
            if parent_id in process_ids and process_id not in process_ids:
                process_ids.add(process_id)
                changed = True
    return process_ids


def _enumerate_native_dialogs(root_pid: int) -> list[dict]:
    """List visible #32770 dialogs owned by the installed Electron tree."""
    import ctypes
    from ctypes import wintypes

    process_ids = _windows_process_tree(root_pid)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    dialogs: list[dict] = []

    def visit(window, _parameter):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
        if int(process_id.value) not in process_ids or not user32.IsWindowVisible(window):
            return True
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(window, class_name, len(class_name))
        if class_name.value != "#32770":
            return True
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(window, title, len(title))
        dialogs.append(
            {
                "handle": int(window),
                "pid": int(process_id.value),
                "class": class_name.value,
                "title": title.value,
            }
        )
        return True

    callback = callback_type(visit)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    if not user32.EnumWindows(callback, 0):
        error = ctypes.get_last_error()
        if error:
            raise ctypes.WinError(error)
    return dialogs


def _wait_for_new_native_dialog(
    root_pid: int,
    previous_handles: set[int],
    timeout: float = 30.0,
) -> dict:
    deadline = time.monotonic() + timeout
    observed: list[dict] = []
    while time.monotonic() < deadline:
        observed = _enumerate_native_dialogs(root_pid)
        created = [item for item in observed if item["handle"] not in previous_handles]
        if created:
            return created[0]
        time.sleep(0.2)
    raise TimeoutError(
        "native #32770 chooser did not appear in the Electron process tree; "
        f"observed dialogs: {observed}"
    )


def _close_native_dialog(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    if not user32.PostMessageW(handle, 0x0010, 0, 0):  # WM_CLOSE
        raise ctypes.WinError(ctypes.get_last_error())


def _native_chooser_expression(method: str, state_key: str) -> str:
    if method not in {"selectAudioFiles", "selectFolder"}:
        raise ValueError(f"unsupported chooser method: {method}")
    rendered_method = json.dumps(method)
    rendered_key = json.dumps(state_key)
    return f"""
(() => {{
  const api = globalThis.electronAPI?.fileImport;
  if (typeof api?.[{rendered_method}] !== 'function') {{
    throw new Error('native chooser bridge method is unavailable');
  }}
  const state = {{ settled: false, result: null, error: null }};
  globalThis[{rendered_key}] = state;
  Promise.resolve(api[{rendered_method}]({{}}))
    .then((result) => {{ state.result = result; }})
    .catch((error) => {{ state.error = String(error?.message || error); }})
    .finally(() => {{ state.settled = true; }});
  return {{ triggered: true, method: {rendered_method} }};
}})()
"""


def _exercise_native_chooser(
    client: _DevToolsClient,
    root_pid: int,
    method: str,
    *,
    timeout: float = 30.0,
) -> dict:
    previous = {item["handle"] for item in _enumerate_native_dialogs(root_pid)}
    state_key = f"__soundbotInstalledSmoke_{method}"
    triggered = client.evaluate(_native_chooser_expression(method, state_key))
    if not triggered.get("triggered"):
        raise RuntimeError(f"failed to trigger native chooser: {method}")
    dialog = _wait_for_new_native_dialog(root_pid, previous, timeout=timeout)
    _close_native_dialog(dialog["handle"])

    rendered_key = json.dumps(state_key)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        settled = client.evaluate(
            f"(() => ({{ settled: Boolean(globalThis[{rendered_key}]?.settled) }}))()"
        )
        if settled.get("settled"):
            return {
                "method": method,
                "pid": dialog["pid"],
                "class": dialog["class"],
                "appeared": True,
                "closed": True,
            }
        time.sleep(0.2)
    raise TimeoutError(f"native chooser did not settle after WM_CLOSE: {method}")


def _renderer_smoke_expression(
    file_fixture: Path,
    folder_fixture: Path,
    wma_fixture: Path,
    timeout_seconds: int,
) -> str:
    file_path = json.dumps(str(file_fixture.resolve()), ensure_ascii=False)
    folder_path = json.dumps(str(folder_fixture.parent.resolve()), ensure_ascii=False)
    wma_path = json.dumps(str(wma_fixture.resolve()), ensure_ascii=False)
    timeout_ms = max(30_000, int(timeout_seconds * 1000))
    return f"""
(async () => {{
  const api = globalThis.electronAPI;
  if (!api || !api.fileImport || !api.backendAPI || !api.backendStatus || !api.runtime) {{
    throw new Error('sandboxed preload bridge is unavailable');
  }}
  for (const method of ['selectAudioFiles', 'selectFolder', 'getCapabilities']) {{
    if (typeof api.fileImport[method] !== 'function') {{
      throw new Error(`preload fileImport.${{method}} is unavailable`);
    }}
  }}

  const capabilities = await api.fileImport.getCapabilities();
  if (!Array.isArray(capabilities?.extensions) || !capabilities.extensions.includes('.wav')) {{
    throw new Error('canonical audio capability IPC is incomplete');
  }}

  const ready = await api.backendStatus.waitUntilReady({timeout_ms});
  if (!ready?.success) {{
    throw new Error(`backend readiness failed: ${{JSON.stringify(ready)}}`);
  }}
  const health = await api.backendAPI.healthCheck();
  if (health?.status !== 'healthy' || health?.audio_decoder_available !== true) {{
    throw new Error(`bundled audio decoder is unavailable: ${{JSON.stringify(health)}}`);
  }}

  const runtime = await api.runtime.getConfig();
  if (!runtime?.apiV1Base?.startsWith('http://127.0.0.1:')) {{
    throw new Error(`invalid runtime config: ${{JSON.stringify(runtime)}}`);
  }}
  const requestJson = async (path) => {{
    const response = await fetch(`${{runtime.apiV1Base}}${{path}}`);
    const payload = await response.json().catch(() => ({{}}));
    if (!response.ok) {{
      throw new Error(`HTTP ${{response.status}} ${{path}}: ${{JSON.stringify(payload)}}`);
    }}
    return payload;
  }};
  const waitForModel = async () => {{
    const deadline = Date.now() + {timeout_ms};
    while (Date.now() < deadline) {{
      const status = await requestJson('/model/status');
      if (status?.model_status?.loaded === true && status?.embedder_available === true) {{
        return status;
      }}
      if (status?.model_status?.error) {{
        throw new Error(`installed CLAP preload failed: ${{status.model_status.error}}`);
      }}
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }}
    throw new Error('installed CLAP model did not become ready');
  }};
  const modelStatus = await waitForModel();
  const waitForJob = async (jobId) => {{
    const deadline = Date.now() + {timeout_ms};
    while (Date.now() < deadline) {{
      const job = await requestJson(`/jobs/${{encodeURIComponent(jobId)}}`);
      if (['completed', 'failed', 'cancelled'].includes(job.state)) {{
        if (job.state !== 'completed') {{
          throw new Error(`import job ${{jobId}} ended as ${{job.state}}: ${{JSON.stringify(job)}}`);
        }}
        return job;
      }}
      await new Promise((resolve) => setTimeout(resolve, 500));
    }}
    throw new Error(`import job ${{jobId}} timed out`);
  }};

  const fileJob = await api.backendAPI.importFiles(
    [{file_path}, {wma_path}], 'installed-file-smoke', 'default'
  );
  if (!fileJob?.job_id) throw new Error(`file import returned no job: ${{JSON.stringify(fileJob)}}`);
  const fileJobResult = await waitForJob(fileJob.job_id);

  const folderJob = await api.backendAPI.importFolderAsync(
    {folder_path}, false, 'installed-folder-smoke', 'default'
  );
  if (!folderJob?.job_id) throw new Error(`folder import returned no job: ${{JSON.stringify(folderJob)}}`);
  const folderJobResult = await waitForJob(folderJob.job_id);

  const listing = await requestJson('/files?project_id=default&limit=20');
  const expectedNames = [
    {json.dumps(file_fixture.name, ensure_ascii=False)},
    {json.dumps(folder_fixture.name, ensure_ascii=False)},
    {json.dumps(wma_fixture.name, ensure_ascii=False)}
  ];
  const records = expectedNames.map((name) => listing.files?.find((item) => item.filename === name));
  if (records.some((item) => !item?.id)) {{
    throw new Error(`installed imports missing from SQLite: ${{JSON.stringify(listing)}}`);
  }}
  for (const record of records) {{
    if (record.audio_index_state !== 'ready' || record.text_index_state !== 'ready') {{
      throw new Error(`installed dual index is not ready for ${{record.filename}}: ${{JSON.stringify(record)}}`);
    }}
  }}

  const search = await api.backendAPI.searchAudio('tone', 20, 0.0, 1, 20, 'default');
  const resultById = new Map(
    (search?.results || []).map((item) => [item?.audio_file?.id, item])
  );
  for (const record of records) {{
    const match = resultById.get(record.id);
    if (!match) {{
      throw new Error(`dual-index search did not return ${{record.filename}}: ${{JSON.stringify(search)}}`);
    }}
    if (!Number.isFinite(match?.metadata?.audio_score) || !Number.isFinite(match?.metadata?.text_score)) {{
      throw new Error(`dual-index search scores are incomplete for ${{record.filename}}: ${{JSON.stringify(match)}}`);
    }}
  }}

  const waveformSummaries = [];
  for (const record of records) {{
    const waveform = await api.backendAPI.getWaveformById(record.id, 'default');
    const peaks = waveform?.peaks;
    if (!Array.isArray(peaks) || peaks.length !== 2000) {{
      throw new Error(`invalid waveform length for ${{record.filename}}: ${{peaks?.length}}`);
    }}
    if (!peaks.every((value) => Number.isFinite(value) && value >= 0 && value <= 1)) {{
      throw new Error(`non-finite or out-of-range waveform for ${{record.filename}}`);
    }}
    if (!peaks.some((value) => value > 0.01)) {{
      throw new Error(`unexpectedly silent waveform for ${{record.filename}}`);
    }}
    waveformSummaries.push({{
      filename: record.filename,
      peaks: peaks.length,
      cached: Boolean(waveform.cached),
      state: record.waveform_state,
    }});
  }}
  const wmaRecord = records.find((item) => item.filename.toLowerCase().endsWith('.wma'));
  const wmaPlayback = await requestJson(
    `/files/${{encodeURIComponent(wmaRecord.id)}}/playback-source?project_id=default`
  );
  if (wmaPlayback?.mode !== 'transcoded_wav' || !wmaPlayback?.path?.toLowerCase().endsWith('.wav')) {{
    throw new Error(`installed WMA playback transcode failed: ${{JSON.stringify(wmaPlayback)}}`);
  }}

  return {{
    bridge: true,
    decoder: health.audio_decoder_available,
    capabilities: capabilities.extensions.length,
    fileJob: fileJobResult.state,
    folderJob: folderJobResult.state,
    modelLoaded: modelStatus.model_status.loaded,
    modelFingerprint: modelStatus.model_status.fingerprint,
    imported: records.length,
    searched: resultById.size,
    dualIndexReady: records.every((item) =>
      item.audio_index_state === 'ready' && item.text_index_state === 'ready'
    ),
    wmaPlayback: wmaPlayback.mode,
    waveforms: waveformSummaries,
  }};
}})()
"""


def _tail(path: Path, line_count: int = 100) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-line_count:])


def _verify_installed_models(
    models_dir: Path,
    *,
    bundle_config_path: Path = REPO_ROOT / "config" / "model_bundle.json",
    source_root: Path = REPO_ROOT,
) -> dict:
    scripts_dir = str(REPO_ROOT / "scripts")
    inserted = scripts_dir not in sys.path
    if inserted:
        sys.path.insert(0, scripts_dir)
    try:
        from download_manager import verify_model_manifest

        manifest = verify_model_manifest(models_dir)
    finally:
        if inserted:
            sys.path.remove(scripts_dir)

    try:
        bundle_config = json.loads(bundle_config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid pinned model bundle config: {bundle_config_path}") from exc
    if manifest.get("model_id") != bundle_config.get("model_id"):
        raise ValueError("installed CLAP model_id does not match the pinned bundle config")
    if str(manifest.get("revision", "")).lower() != str(
        bundle_config.get("revision", "")
    ).lower():
        raise ValueError("installed CLAP revision does not match the pinned bundle config")

    notice_relative = bundle_config.get("notice_file")
    if not isinstance(notice_relative, str) or not notice_relative.strip():
        raise ValueError("pinned model bundle config has no notice_file")
    source_root = source_root.resolve(strict=True)
    notice_source = (source_root / notice_relative).resolve(strict=True)
    try:
        notice_source.relative_to(source_root)
    except ValueError as exc:
        raise ValueError("pinned model notice escapes the repository root") from exc
    notice_target = models_dir / "CLAP_MODEL_NOTICE.txt"
    if not notice_target.is_file() or notice_target.read_bytes() != notice_source.read_bytes():
        raise ValueError("installed CLAP model notice is missing or does not match source")
    return manifest


def _stop_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument("--debug-port", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=480)
    parser.add_argument("--require-models", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if os.name != "nt":
        raise RuntimeError("the installed Electron smoke must run on Windows")

    executable = args.executable.resolve(strict=True)
    fixture_root = args.fixture_root.resolve()
    fixture_root.mkdir(parents=True, exist_ok=True)
    resources = executable.parent / "resources"
    backend_executable = (
        resources / "backend" / "soundbot-backend" / "soundbot-backend.exe"
    )
    if not backend_executable.is_file():
        raise FileNotFoundError(f"installed backend is missing: {backend_executable}")
    model_manifest = None
    if args.require_models:
        model_manifest = _verify_installed_models(resources / "models")

    file_fixture = (
        fixture_root / "单文件 导入 % # + (A)" / "tone 单文件 % # + (A).wav"
    )
    folder_fixture = (
        fixture_root / "文件夹 导入 % # + (B)" / "tone 文件夹 % # + (B).wav"
    )
    wma_fixture = (
        fixture_root / "压缩容器 导入 % # + (WMA)" / "tone 压缩容器 % # + (WMA).wma"
    )
    _write_fixture(file_fixture, 440.0)
    _write_fixture(folder_fixture, 880.0)
    wma_fixture.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / "tests" / "build" / "fixtures" / "tone.wma", wma_fixture)

    appdata = fixture_root / "AppData" / "Roaming"
    local_appdata = fixture_root / "AppData" / "Local"
    appdata.mkdir(parents=True, exist_ok=True)
    local_appdata.mkdir(parents=True, exist_ok=True)
    stdout_log = fixture_root / "installed-electron.stdout.log"
    stderr_log = fixture_root / "installed-electron.stderr.log"
    electron_log = fixture_root / "installed-electron.chromium.log"
    port = args.debug_port or _free_loopback_port()
    env = dict(os.environ)
    # The build job also has a verified workspace model. Never allow that
    # source path to mask a missing/corrupt model in the installed resources.
    env.pop("SOUNDBOT_MODELS_PATH", None)
    env.update(
        {
            "APPDATA": str(appdata),
            "LOCALAPPDATA": str(local_appdata),
            "ENABLE_MODEL_PRELOAD": "true" if args.require_models else "false",
            "ELECTRON_ENABLE_LOGGING": "true",
            "ELECTRON_LOG_FILE": str(electron_log),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )

    process: subprocess.Popen | None = None
    client: _DevToolsClient | None = None
    try:
        with (
            stdout_log.open("w", encoding="utf-8", errors="replace") as stdout,
            stderr_log.open("w", encoding="utf-8", errors="replace") as stderr,
        ):
            process = subprocess.Popen(
                [
                    str(executable),
                    f"--remote-debugging-port={port}",
                    "--remote-debugging-address=127.0.0.1",
                    "--remote-allow-origins=*",
                    "--disable-gpu",
                ],
                cwd=str(executable.parent),
                env=env,
                stdout=stdout,
                stderr=stderr,
            )
            deadline = time.monotonic() + args.timeout
            websocket_url, observed_urls = _discover_main_target(port, deadline)
            client = _DevToolsClient(websocket_url, timeout=(args.timeout * 4) + 60)
            native_dialogs = [
                _exercise_native_chooser(
                    client, process.pid, "selectAudioFiles", timeout=45.0
                ),
                _exercise_native_chooser(
                    client, process.pid, "selectFolder", timeout=45.0
                ),
            ]
            result = client.evaluate(
                _renderer_smoke_expression(
                    file_fixture,
                    folder_fixture,
                    wma_fixture,
                    timeout_seconds=args.timeout,
                )
            )
            result["nativeDialogs"] = native_dialogs
            try:
                client.evaluate(
                    "(async () => { await window.electronAPI.windowControl.close(); "
                    "return {closed: true}; })()"
                )
            except Exception:
                pass

        if (
            not result.get("bridge")
            or result.get("imported") != 3
            or (args.require_models and not result.get("modelLoaded"))
            or (args.require_models and not result.get("dualIndexReady"))
            or result.get("wmaPlayback") != "transcoded_wav"
            or len(result.get("nativeDialogs") or []) != 2
            or not all(item.get("appeared") for item in result.get("nativeDialogs", []))
        ):
            raise AssertionError(f"installed Electron smoke was incomplete: {result}")
        print(
            json.dumps(
                {
                    "executable": str(executable),
                    "resources": str(resources),
                    "model_revision": (
                        model_manifest.get("revision") if model_manifest else None
                    ),
                    "targets": observed_urls,
                    **result,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except Exception:
        print("--- installed Electron stdout (tail) ---", file=sys.stderr)
        print(_tail(stdout_log), file=sys.stderr)
        print("--- installed Electron stderr (tail) ---", file=sys.stderr)
        print(_tail(stderr_log), file=sys.stderr)
        print("--- installed Electron Chromium log (tail) ---", file=sys.stderr)
        print(_tail(electron_log), file=sys.stderr)
        for backend_log in sorted(fixture_root.rglob("soundmind_*.log")):
            print(f"--- frozen backend log: {backend_log} (tail) ---", file=sys.stderr)
            print(_tail(backend_log), file=sys.stderr)
        raise
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if process is not None:
            _stop_process_tree(process)


if __name__ == "__main__":
    raise SystemExit(main())
