#!/usr/bin/env python3
"""Exercise a packaged SoundBot Electron/backend chain.

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
import plistlib
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request
import wave

from websockets.sync.client import connect


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


def _write_loudness_fixture(path: Path) -> None:
    rate = 48_000
    gain = 32767 * 10 ** (-23 / 20)
    period = b"".join(struct.pack("<h", round(gain * math.sin(2 * math.pi * frame / 48)))
                      for frame in range(48))
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(period * (rate * 6 // 48))


def _discover_main_target(port: int, deadline: float) -> tuple[str, list[str]]:
    observed_urls: list[str] = []
    endpoint = f"http://127.0.0.1:{port}/json/list"
    while time.monotonic() < deadline:
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(endpoint, timeout=2) as response:
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
        self._connection = connect(url, open_timeout=timeout, max_size=16 * 1024 * 1024, proxy=None)
        self._timeout = timeout
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
            payload = json.loads(self._connection.recv(timeout=self._timeout))
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


def _enumerate_native_windows(root_pid: int) -> list[dict]:
    """List visible windows in the installed Electron process tree."""
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
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    windows: list[dict] = []

    def visit(window, _parameter):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
        if int(process_id.value) not in process_ids or not user32.IsWindowVisible(window):
            return True
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(window, class_name, len(class_name))
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(window, title, len(title))
        owner = user32.GetWindow(window, 4)  # GW_OWNER
        owner_pid = wintypes.DWORD()
        if owner:
            user32.GetWindowThreadProcessId(owner, ctypes.byref(owner_pid))
        bounds = wintypes.RECT()
        user32.GetWindowRect(window, ctypes.byref(bounds))
        windows.append(
            {
                "handle": int(window),
                "pid": int(process_id.value),
                "class": class_name.value,
                "title": title.value,
                "owner_pid": int(owner_pid.value),
                "rect": (bounds.left, bounds.top, bounds.right, bounds.bottom),
                "style": user32.GetWindowLongPtrW(window, -16),
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
    return windows


def _enumerate_native_dialogs(root_pid: int) -> list[dict]:
    return [window for window in _enumerate_native_windows(root_pid) if window["class"] == "#32770"]


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
  if (api.checkFullDiskAccess || api.openPrivacySettings) {{
    throw new Error('renderer still exposes a full disk access request');
  }}
  const assistant = await api.aiConfig.get();
  if (!assistant?.llm?.provider || !(await api.aiConfig.prepareChat())?.success) {{
    throw new Error('the optional assistant bridge is unavailable without saved keys');
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
  if (health.model_loaded !== false) throw new Error('core backend must run without a model');
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
  const parentFolder = {json.dumps(str(folder_fixture.parent.parent.resolve()), ensure_ascii=False)};
  const parentSource = await api.backendAPI.importFolderAsync(parentFolder, true, 'installed-overlap-smoke', 'default');
  await waitForJob(parentSource.job_id);

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
  const search = await api.backendAPI.searchAudio('tone', 20, 0.0, 1, 20, 'default');
  const resultById = new Map(
    (search?.results || []).map((item) => [item?.audio_file?.id, item])
  );
  for (const record of records) {{
    const match = resultById.get(record.id);
    if (!match) {{
      throw new Error(`keyword search did not return ${{record.filename}}: ${{JSON.stringify(search)}}`);
    }}
    if (!Number.isFinite(match?.metadata?.keyword_score)) {{
      throw new Error(`keyword search scores are incomplete for ${{record.filename}}: ${{JSON.stringify(match)}}`);
    }}
  }}

  const wmaRecord = records.find((item) => item.filename.toLowerCase().endsWith('.wma'));
  const until = async (check, description) => {{
    const deadline = Date.now() + 12000;
    while (Date.now() < deadline) {{
      if (check()) return;
      await new Promise(resolve => setTimeout(resolve, 12));
    }}
    throw new Error(`workspace did not reach ${{description}}. ${{document.getElementById('toast')?.textContent}}`);
  }};
  const workspace = globalThis.SoundBotWorkspace;
  await until(() => workspace?.state.library?.id === 'default', 'active library');
  const dawDirectory = {json.dumps(str(file_fixture.parent.parent / 'DAW 工程媒体'), ensure_ascii=False)};
  const actionDialog = document.getElementById('actionDialog');
  await until(() => actionDialog.open && document.getElementById('actionMessage').textContent ===
    SoundBotI18n.t('是否更改上次使用的剪辑存储目录？'), 'saved DAW folder prompt');
  if (workspace.folderKey(workspace.state.dawDirectory) !== workspace.folderKey(dawDirectory)) {{
    throw new Error('saved DAW folder was not restored');
  }}
  document.getElementById('actionCancel').click();
  await until(() => !actionDialog.open, 'keeping saved DAW folder');
  const searchInput = document.getElementById('searchInput');
  searchInput.value = 'tone';
  searchInput.dispatchEvent(new Event('input', {{bubbles: true}}));
  await until(() => document.getElementById('rowCount').textContent === SoundBotI18n.t('结果：{{count}}', {{count: 3}}), 'keyword results');
  const language = document.getElementById('languageSelect');
  const heading = document.getElementById('activeViewLabel');
  const viewIcons = [...document.querySelectorAll('#viewTabs button')];
  const position = element => [element.offsetLeft, element.offsetTop, element.offsetWidth, element.offsetHeight];
  const sidebarGeometry = JSON.stringify([heading, ...viewIcons].map(position));
  const originalLocale = language.value;
  const authorHeading = document.querySelector('#settings-about .credit-author h4');
  const miSansHeading = document.querySelector('#settings-about details summary');
  const fontSamples = {{
    'zh-CN': ['MiSans', '音效库'],
    'zh-TW': ['MiSans TC', '音效庫'],
    ja: ['MiSans', 'ライブラリ'],
    en: ['MiSans Latin', 'Library'],
    'pt-BR': ['MiSans Latin', 'Importação'],
    es: ['MiSans Latin', 'Edición']
  }};
  const folders = {{ 'zh-CN': '文件夹', 'zh-TW': '資料夾', ja: 'フォルダー', en: 'Folders', 'pt-BR': 'Pastas', es: 'Carpetas' }};
  const languages = [...language.options].map(option => option.value);
  if (languages.length !== 6 || new Set(languages).size !== 6) throw new Error('locale choices are incomplete');
  for (const id of languages) {{
    language.value = id;
    language.dispatchEvent(new Event('change', {{ bubbles: true }}));
    if (document.documentElement.lang !== id || heading.textContent !== folders[id] ||
        viewIcons[0].getAttribute('aria-label') !== folders[id] ||
        document.getElementById('rowCount').textContent !== SoundBotI18n.t('结果：{{count}}', {{count: 3}}) ||
        JSON.stringify([heading, ...viewIcons].map(position)) !== sidebarGeometry) {{
      throw new Error(`sidebar or localization failed for ${{id}}`);
    }}
    if (authorHeading?.textContent !== SoundBotI18n.t('作者与许可') ||
        miSansHeading?.textContent !== SoundBotI18n.t('界面字体 · MiSans')) {{
      throw new Error(`credits localization failed for ${{id}}`);
    }}
    const [family, sample] = fontSamples[id];
    const primaryFont = getComputedStyle(document.body).fontFamily.split(',')[0].replaceAll('"', '').trim();
    const faces = await document.fonts.load(`650 14px "${{family}}"`, sample);
    if (primaryFont !== family || !faces.some(face => face.status === 'loaded')) {{
      throw new Error(`embedded font ${{family}} did not load for ${{id}}`);
    }}
  }}
  viewIcons[2].click();
  if (viewIcons[2].getAttribute('aria-pressed') !== 'true' ||
      heading.textContent !== SoundBotI18n.t('分组') ||
      JSON.stringify([heading, ...viewIcons].map(position)) !== sidebarGeometry) {{
    throw new Error('changing library views moved or failed to update the sidebar caption');
  }}
  viewIcons[0].click();
  language.value = originalLocale;
  language.dispatchEvent(new Event('change', {{ bubbles: true }}));
  const thumbnailReady = canvas => {{
    if (!canvas) return false;
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    return pixels.some((value, index) => index % 4 === 3 && value > 0);
  }};
  await until(() => {{
    const rows = [...document.querySelectorAll('#rows .row')];
    return rows.length === records.length && rows.every(row =>
      row.querySelector('.asset-cover') && thumbnailReady(row.querySelector('canvas'))
    );
  }}, 'automatic visible thumbnails before selection');

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
  workspace.playback.setVolume(0.25, false);
  const audition = async (filename) => {{
    const row = [...document.querySelectorAll('#rows .row')].find(
      item => item.title.split(/[\\\\/]/).at(-1) === filename
    );
    if (!row) throw new Error(`missing visible row: ${{filename}}`);
    const start = performance.now();
    row.click();
    await until(() => workspace.playback.currentTime() > 0.01, `playing ${{filename}}`);
    const ms = Math.round(performance.now() - start);
    const equalizer = document.querySelector('#rack .fx-card .eq-plot')?.closest('.fx-card');
    if (equalizer?.querySelector('[role="switch"]')?.getAttribute('aria-checked') !== 'false') {{
      throw new Error('equalizer must start bypassed');
    }}
    await until(() => workspace.playback.levels().some(level => level > 0.01) &&
      Number.isFinite(workspace.playback.meterReading().values.truePeak), `live meter for ${{filename}}`);
    document.getElementById('btnStop').click();
    if (workspace.playback.levels().some(Boolean) ||
        !Number.isFinite(workspace.playback.meterReading().values.truePeak)) {{
      throw new Error('Stopping must clear the live level bars and hold the measured loudness');
    }}
    return ms;
  }};
  const auditionMs = {{ native: await audition(expectedNames[0]), streamed: await audition(wmaRecord.filename) }};
  const searchBox = document.getElementById('searchInput');
  const space = (target, repeat = false) => target.dispatchEvent(new KeyboardEvent('keydown', {{
    key: ' ', code: 'Space', bubbles: true, cancelable: true, repeat
  }}));
  if (!space(searchBox) || workspace.playback.active) throw new Error('Space is reserved for text in search');
  const focusedTab = document.getElementById('tabDetail');
  focusedTab.focus();
  if (space(focusedTab) || !workspace.playback.active || space(focusedTab, true) || !workspace.playback.active) {{
    throw new Error('Space must play once without activating a focused control');
  }}
  if (space(focusedTab) || workspace.playback.active) throw new Error('Space did not pause playback');
  const folderFilter = {folder_path};
  await workspace.setFilter({{ folder: folderFilter }});
  const wwiseTab = document.querySelector('[data-ws="wwise"]');
  wwiseTab.click();
  await until(() => wwiseTab.getAttribute('aria-selected') === 'true', 'middleware workspace');
  if (wwiseTab.textContent !== SoundBotI18n.t('中间件工作台')) throw new Error('middleware workspace label missing');
  const hostSelector = document.getElementById('middlewareHost');
  if ([...hostSelector.options].map(option => option.value).join() !== 'wwise,fmod,cri') {{
    throw new Error('middleware host choices are incomplete');
  }}
  for (const host of ['fmod', 'cri', 'wwise']) {{
    hostSelector.value = host;
    hostSelector.dispatchEvent(new Event('change', {{ bubbles: true }}));
    await until(() => document.getElementById('middlewareSettingsHost').value === host && !hostSelector.disabled,
      `middleware host ${{host}}`);
    const exportLabel = SoundBotI18n.t(host === 'wwise' ? '渲染并导回 Wwise' : '渲染新 WAV');
    if (document.getElementById('btnExportWwise').textContent !== exportLabel) {{
      throw new Error(`incorrect delivery mode for ${{host}}`);
    }}
  }}
  const dock = document.getElementById('miniRows');
  await until(() => dock.querySelectorAll('.mini-row').length === 1 &&
    dock.querySelector('.mini-row').title.endsWith(expectedNames[1]), 'docked library inherits main folder');
  const dockFolder = document.getElementById('miniFolder');
  await until(() => dockFolder.value === folderFilter, 'shared folder selector');
  dockFolder.value = '';
  dockFolder.dispatchEvent(new Event('change', {{ bubbles: true }}));
  await until(() => dock.querySelectorAll('.mini-row').length === records.length && !workspace.state.filter.folder,
    'dock folder selection updates the shared filter');
  await until(() => thumbnailReady(dock.querySelector('canvas')), 'docked waveform previews');
  if (!document.getElementById('tabAi').classList.contains('hidden') ||
      document.getElementById('tabVst').classList.contains('hidden')) {{
    throw new Error('unexpected Wwise inspector tabs');
  }}
  dock.querySelector('.mini-name').click();
  await until(() => workspace.state.dockEditing && workspace.state.waveform, 'shared dock waveform editor');
  document.getElementById('btnBackDock').click();
  if (workspace.state.dockEditing) throw new Error('dock editor failed to close');
  const wmaPlayback = await requestJson(
    `/files/${{encodeURIComponent(wmaRecord.id)}}/playback-source?project_id=default`
  );
  if (wmaPlayback?.mode !== 'transcoded_wav' || !wmaPlayback?.path?.toLowerCase().endsWith('.wav')) {{
    throw new Error(`installed WMA playback transcode failed: ${{JSON.stringify(wmaPlayback)}}`);
  }}

  await workspace.switchWorkspace('library');
  const looseFolder = {json.dumps(str(wma_fixture.parent.resolve()), ensure_ascii=False)};
  const treeEntry = (path, scope = document.getElementById('tree')) => [...scope.querySelectorAll('[data-folder]')].find(
    row => workspace.folderKey(row.dataset.folder) === workspace.folderKey(path)
  );
  const confirmRemoval = async () => {{
    await until(() => actionDialog.open, 'removal confirmation');
    const originalFetch = globalThis.fetch;
    let requestHadProgress = false;
    globalThis.fetch = (url, options) => {{
      if (options?.method === 'DELETE' || String(url).endsWith('/catalog/dismiss-folder')) {{
        requestHadProgress = !document.getElementById('jobTray').hidden;
      }}
      return originalFetch(url, options);
    }};
    try {{
      document.getElementById('actionConfirm').click();
      await until(() => requestHadProgress && document.getElementById('jobTray').hidden,
                  'visible removal progress and completion');
    }} finally {{
      globalThis.fetch = originalFetch;
    }}
  }};
  const dismissTreeEntry = row => {{
    row.dispatchEvent(new MouseEvent('contextmenu', {{ bubbles: true, cancelable: true }}));
    const remove = [...document.querySelectorAll('#folderMenu [role="menuitem"]')].find(
      node => node.textContent === SoundBotI18n.t('移除文件夹…')
    );
    if (!remove) throw new Error('Folder context menu has no remove action');
    remove.click();
  }};
  await until(() => treeEntry(parentFolder) && treeEntry(folderFilter), 'both source registrations');
  treeEntry(parentFolder).querySelector('.tree-expand').click();
  const beneathParent = () => treeEntry(parentFolder)?.parentElement.querySelector('.tree-children');
  await until(() => beneathParent() && treeEntry(folderFilter, beneathParent()), 'overlapping subfolder');
  const standalone = () => [...document.querySelectorAll('#tree > div > [data-folder]')].find(row =>
    workspace.folderKey(row.dataset.folder) === workspace.folderKey(folderFilter));
  dismissTreeEntry(standalone());
  await until(() => !standalone() && beneathParent() && treeEntry(folderFilter, beneathParent()),
              'only the duplicate standalone entry is hidden');
  if ((await workspace.browseCatalog(20)).files.length !== 3 || actionDialog.open) {{
    throw new Error('Hiding a shared folder changed searchable files or prompted for removal');
  }}
  document.getElementById('btnSettings').click();
  document.querySelector('[data-settings="library"]').click();
  await until(() => document.querySelector('#libraryHiddenFolders button'), 'hidden folder in library settings');
  const indexStatus = document.getElementById('libraryIndexSummary');
  await until(() => indexStatus.textContent === SoundBotI18n.t('素材 {{total}} 条，已建立关键词索引 {{indexed}} 条', {{total: 3, indexed: 3}}), 'catalog status');
  for (const [id, kind] of [['btnRefreshMetadata', 'metadata_refresh'], ['btnRebuild', 'keyword_rebuild']]) {{
    document.getElementById(id).click();
    if (document.getElementById('jobTray').hidden) throw new Error(`Missing maintenance progress for ${{id}}`);
    await until(() => document.getElementById('jobTray').hidden, `${{id}} completion`);
    const jobs = await requestJson('/projects/default/jobs');
    if (!jobs.some(job => job.kind === kind && job.state === 'completed')) {{
      throw new Error(`Maintenance job was not completed for ${{id}}`);
    }}
  }}
  document.querySelector('#libraryHiddenFolders button').click();
  await until(standalone, 'shared folder restored');
  const detach = () => [...document.querySelectorAll('#librarySources .library-settings-entry')]
    .find(row => workspace.folderKey(row.querySelector('.library-settings-path').title) === workspace.folderKey(folderFilter));
  await until(detach, 'source maintenance settings');
  [...detach().querySelectorAll('button')].find(button =>
    button.textContent === SoundBotI18n.t('撤销此来源目录的导入…')).click();
  await confirmRemoval();
  document.querySelector('[data-close="ovSettings"]').click();
  await until(() => beneathParent() && treeEntry(folderFilter, beneathParent()), 'only parent registration remains');
  dismissTreeEntry(treeEntry(folderFilter, beneathParent()));
  await confirmRemoval();
  await until(() => treeEntry(parentFolder) && !treeEntry(folderFilter), 'exclusive nested folder unimported');
  for (const path of [parentFolder, looseFolder]) {{
    await until(() => treeEntry(path), 'catalog folder row');
    dismissTreeEntry(treeEntry(path));
    await confirmRemoval();
    await until(() => !treeEntry(path), 'catalog folder removed');
  }}
  const remaining = await workspace.browseCatalog(20);
  if (remaining.files.length !== 1 || remaining.files[0].id !== records[0].id) {{
    throw new Error('Removing folders did not preserve the unrelated item');
  }}
  const disposableName = 'Library removal progress fixture';
  document.getElementById('btnNewLib').click();
  const textDialog = document.getElementById('textDialog');
  await until(() => textDialog.open, 'new library dialog');
  document.getElementById('textValue').value = disposableName;
  textDialog.querySelector('button[value="ok"]').click();
  await until(() => workspace.state.library?.name === disposableName, 'new active library');
  document.getElementById('btnRemoveLib').click();
  await confirmRemoval();
  if (workspace.state.library?.id !== 'default' || workspace.state.libraries.length !== 1) {{
    throw new Error('Library removal did not return to the remaining library');
  }}

  return {{
    bridge: true,
    decoder: health.audio_decoder_available,
    capabilities: capabilities.extensions.length,
    fileJob: fileJobResult.state,
    folderJob: folderJobResult.state,
    modelLoaded: health.model_loaded,
    imported: records.length,
    searched: resultById.size,
    wmaPlayback: wmaPlayback.mode,
    auditionMs,
    folderRemoval: true,
    languages,
    waveforms: waveformSummaries,
  }};
}})()
"""


def _exercise_catalog_meter(client: _DevToolsClient, directory: Path, source: Path) -> dict:
    directory.mkdir()
    for index in range(131):
        shutil.copyfile(source, directory / f"Footstep_{index:03d}.wav")
    _write_loudness_fixture(directory / "Meter reference mono.wav")
    return client.evaluate(r"""
(async () => {
  const workspace = window.SoundBotWorkspace;
  const { $, playback, state } = workspace;
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const until = async (check, label) => {
    const end = Date.now() + 15000;
    while (Date.now() < end) {
      if (await check()) return;
      await delay(50);
    }
    throw Error(`Timed out waiting for ${label}. ${$('toast').textContent}`);
  };
  const job = await workspace.desktop.backendAPI.importFolderAsync(
    FIXTURE_DIR, true, 'installed-catalog-meter', state.library.id
  );
  if (!job?.job_id) throw Error('Search and meter fixture folder did not import');
  await until(async () => {
    const progress = await workspace.api.get('/api/v1/jobs/' + job.job_id);
    if (['failed', 'cancelled'].includes(progress.state)) throw Error('Fixture import failed');
    return progress.state === 'completed';
  }, 'fixture catalog import');

  const search = $('searchInput');
  search.value = '脚步声';
  search.dispatchEvent(new InputEvent('input', { isComposing: true }));
  if (state.search === search.value) throw Error('Unconfirmed IME input changed the query');
  search.dispatchEvent(new CompositionEvent('compositionend', { data: search.value }));
  await until(() => state.search === '脚步声' && state.total === 131 && state.items.length === 120,
              'IME search results');
  $('tabAi').click();
  $('aiInput').value = '腳步聲';
  $('aiSend').click();
  await until(() => state.search === '腳步聲' && state.total === 131 &&
    $('aiMsgs').lastElementChild?.textContent === SoundBotI18n.t('相关素材：{count}', { count: 131 }),
    'assistant results in the central catalog');
  if ($('aiMsgs').querySelector('button')) throw Error('Assistant duplicated results in the chat panel');
  const rows = $('rows');
  rows.scrollTop = rows.scrollHeight;
  rows.dispatchEvent(new Event('scroll'));
  await until(() => state.items.length === 131 && !state.cursor, 'catalog cursor continuation');
  const [firstResult, secondResult] = state.items;

  search.value = 'Meter reference';
  search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
  await until(() => state.total === 1 && state.items[0]?.filename === 'Meter reference mono.wav',
              'immediate search on Enter');
  const meterSource = state.items[0];
  const originalFetch = globalThis.fetch;
  const analyzed = [];
  let finishFirstAnalysis;
  const remainPending = new Promise(resolve => { finishFirstAnalysis = resolve; });
  globalThis.fetch = async (url, options) => {
    if (String(url).includes('/loudness?')) {
      analyzed.push(String(url));
      if (analyzed.length === 1) await remainPending;
    }
    return originalFetch(url, options);
  };
  const number = id => Number($(id).textContent);
  const closeTo = (value, expected) => Math.abs(value - expected) < 0.3;
  try {
    $('tabVst').click();
    await workspace.select(firstResult);
    await until(() => analyzed.length !== 0, 'first source-analysis request');
    if (analyzed.length !== 1) throw Error('Source analysis did not start');
    void workspace.select(secondResult);
    await workspace.select(meterSource);
    finishFirstAnalysis();
    await until(() => closeTo(number('lufsI'), -26) && closeTo(number('lufsTp'), -23) &&
      closeTo(number('lufsLra'), 0), 'source analysis without opening a disclosure');
    if (analyzed.length !== 2 || !analyzed[1].includes(meterSource.id)) {
      throw Error('Source analysis queued stale selections');
    }
  } finally {
    finishFirstAnalysis();
    globalThis.fetch = originalFetch;
  }
  playback.setVolume(0, true);
  $('clipGain').value = '6';
  workspace.togglePlay();
  await until(() => closeTo(number('liveIntegrated'), -20) && closeTo(number('liveTruePeak'), -17) &&
    closeTo(number('liveLra'), 0), 'live mono metrics after applying +6 dB');
  workspace.stop(true);
  await delay(120);
  if (!closeTo(number('liveIntegrated'), -20) || !closeTo(number('lufsI'), -26)) {
    throw Error('Stopping changed held or source measurements');
  }
  const stopped = performance.now();
  try {
    Object.defineProperty(performance, 'now', { configurable: true, value: () => stopped + 59000 });
    await delay(100);
    if (!closeTo(number('liveIntegrated'), -20)) throw Error('Loudness hold ended early');
    Object.defineProperty(performance, 'now', { configurable: true, value: () => stopped + 61000 });
    await until(() => $('liveIntegrated').textContent === '—', 'loudness hold expiry');
  } finally {
    delete performance.now;
  }
  $('clipGain').value = '24';
  workspace.togglePlay();
  const clipped = () => $('truePeakMetric').classList.contains('overload');
  await until(clipped, 'peak warning on full-scale audition');
  $('peakWarning').click();
  await until(() => !clipped() && $('peakWarning').disabled, 'click clears the warning');
  await delay(200);
  if (clipped()) throw Error('Warning returned during the same overload');
  workspace.stop(true);
  return { keywordResults: 131, assistantPagination: true, sourceIntegrated: -26, wetIntegrated: -20,
           truePeakReset: true, holdSeconds: 60 };
})()
""".replace("FIXTURE_DIR", json.dumps(str(directory.resolve()), ensure_ascii=False)))


def _exercise_vst(client: _DevToolsClient, root_pid: int, fixture: Path) -> dict:
    candidate = json.dumps(fixture.stem)
    opened = client.evaluate(r"""
(async () => {
  const w = window.SoundBotWorkspace;
  await w.switchWorkspace('library');
  const name = VST_FIXTURE;
  const catalog = await w.api.get('/api/v1/effects/plugins');
  const plugin = catalog.plugins.find(item => item.name.toLowerCase() === name.toLowerCase());
  if (!plugin) throw Error('installed VST3 fixture not found');
  const confirm = w.$('actionDialog');
  const instance = crypto.randomUUID().replaceAll('-', '');
  window.vstSmoke = { job: { instance } };
  w.desktop.desktopMedia('load-plugin-editor', { ...plugin, instance, dark: true }).then(
    result => { window.vstSmoke.result = result; },
    error => { window.vstSmoke.error = error.message; });
  for (let i = 0; !confirm.open && i < 100; i++) await new Promise(r => setTimeout(r, 50));
  if (!confirm.open) throw Error('VST3 trust prompt did not appear');
  w.$('actionConfirm').click();
  let loaded;
  for (let i = 0; i < 140 && !loaded; i++) {
    if (window.vstSmoke.error) throw Error(window.vstSmoke.error);
    loaded = (await w.api.get('/api/v1/effects/editors/' + instance)).plugin;
    if (!loaded) await new Promise(r => setTimeout(r, 200));
  }
  if (!loaded) throw Error('VST3 editor readiness was not reported');
  const gain = loaded.parameters.find(parameter => /gain/i.test(parameter.name));
  if (!gain) throw Error('VST3 gain parameter is unavailable');
  window.vstSmoke.job = { id: loaded.id, instance, values: { [gain.key]: 0.2 } };
  return { name: loaded.name };
})()
""".replace("VST_FIXTURE", candidate))

    def wait_for_editor() -> dict:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            visible = _enumerate_native_windows(root_pid)
            assert not any(window["class"] == "ConsoleWindowClass" for window in visible), visible
            editor = next((window for window in visible if window["title"] == f"{opened['name']} · SoundBot"), None)
            if editor:
                assert editor["owner_pid"] == root_pid, editor
                assert editor["style"] & 0x00CA0000 == 0x00CA0000, editor  # caption, system menu, minimize
                host = next((window for window in visible if window["pid"] == root_pid and
                             window["class"].startswith("Chrome_WidgetWin") and window["title"] == "SoundBot"), None)
                if host:
                    x, y, right, bottom = editor["rect"]
                    left, top, host_right, host_bottom = host["rect"]
                    assert x < host_right and right > left and y < host_bottom and bottom > top, (host, editor)
                return editor
            status = client.evaluate("({error: window.vstSmoke.error || null})")
            if status["error"]:
                raise RuntimeError(status["error"])
            time.sleep(0.15)
        raise RuntimeError("SoundBot-owned VST3 window did not appear")

    editor = wait_for_editor()
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32")
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindowAsync(editor["handle"], 6)  # SW_MINIMIZE
        deadline = time.monotonic() + 4
        while not user32.IsIconic(editor["handle"]) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert user32.IsIconic(editor["handle"]), editor
        client.evaluate("window.electronAPI.desktopMedia('focus-plugin-editor', { instance: window.vstSmoke.job.instance })")
        deadline = time.monotonic() + 4
        while user32.IsIconic(editor["handle"]) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not user32.IsIconic(editor["handle"]), editor
        result = client.evaluate(r"""
(async () => {
  const w = window.SoundBotWorkspace;
  const {job} = window.vstSmoke;
  const file = w.state.items.find(item => item.duration >= 0.9);
  const session = { project_id: w.state.library.id, rate: 44100, channels: 1,
    tracks: [{ clips: [{ file_id: file.id, duration: 0.8 }] }] };
  const pcm = async (effects) => {
    const request_id = crypto.randomUUID();
    const response = await w.api.fetch('/api/v1/preview', { method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ ...session, include_tail: true, master_effects: effects, request_id }) });
    if (!response.ok) throw Error('VST3 preview: ' + await response.text());
    const data = await response.arrayBuffer();
    const { plugins } = await w.api.get('/api/v1/preview/' + request_id + '/warnings');
    if (Object.keys(plugins).length) throw Error('VST3 was bypassed: ' + JSON.stringify(plugins));
    const header = response.headers.get('X-SoundBot-PCM') ? 0 : 44;
    if (data.byteLength !== header + 35280 * 2) throw Error('Gain plug-in added a silent tail');
    const view = new DataView(data);
    let peak = 0;
    for (let i = header; i < view.byteLength; i += 2) peak = Math.max(peak, Math.abs(view.getInt16(i, true)));
    return peak;
  };
  const dry = await pcm([]);
  const wet = await pcm([{ kind: 'vst3', plugin_id: job.id, plugin_instance: job.instance, values: job.values }]);
  if (!(wet > 0 && wet < dry * 0.85)) throw Error(`Expected processed gain, got dry=${dry} wet=${wet}`);
  if (window.vstSmoke.error || window.vstSmoke.result) throw Error('Editor closed while auditioning');
  await window.electronAPI.desktopMedia('close-plugin-editor', { instance: job.instance });
  for (let i = 0; i < 100 && !window.vstSmoke.result && !window.vstSmoke.error; i++) {
    await new Promise(r => setTimeout(r, 50));
  }
  const editor = window.vstSmoke.result;
  if (!editor?.changed || !editor.state) throw Error(window.vstSmoke.error || 'Live gain did not reach the editor instance');
  const saved = await pcm([{ kind: 'vst3', plugin_id: job.id, plugin_state: editor.state, values: {} }]);
  if (Math.abs(saved - wet) > 1) throw Error(`Saved plug-in state diverged from audition: wet=${wet} saved=${saved}`);
  return { dry, wet, saved };
})()
""")
        return result
    finally:
        client.evaluate(r"""
(async () => {
  await window.electronAPI.desktopMedia('close-plugin-editor', { instance: window.vstSmoke.job.instance });
  for (let i = 0; i < 100 && !window.vstSmoke.result && !window.vstSmoke.error; i++) {
    await new Promise(r => setTimeout(r, 50));
  }
  if (!window.vstSmoke.result) throw Error(window.vstSmoke.error || 'VST3 editor did not close');
  return { closed: true };
})()
""")


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


def _check_macos_vst_worker(executable: Path, backend: Path) -> None:
    worker = backend.with_name("soundbot-vst-worker")
    if not os.access(worker, os.X_OK):
        raise FileNotFoundError("packaged VST3 worker is missing or not executable")

    def entitlements(binary: Path) -> dict:
        payload = subprocess.check_output(
            ["/usr/bin/codesign", "--display", "--entitlements", "-", "--xml", str(binary)],
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return plistlib.loads(payload)

    memory_exception = "com.apple.security.cs.allow-unsigned-executable-memory"
    for binary in (executable, backend, worker):
        rights = entitlements(binary)
        if bool(rights.get(memory_exception)) != (binary == worker):
            raise ValueError(f"wrong executable-memory entitlement scope: {binary.name}")
        if rights.get("com.apple.security.cs.disable-executable-page-protection"):
            raise ValueError(f"executable page protection is disabled: {binary.name}")

    standalone = subprocess.run([str(worker)], capture_output=True, timeout=10)
    if standalone.returncode != 2:
        raise ValueError(f"VST3 worker accepted an unscoped startup: {standalone.returncode}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument("--debug-port", type=int, default=0)
    parser.add_argument("--vst-fixture", type=Path)
    parser.add_argument("--timeout", type=int, default=480)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    executable = args.executable.resolve(strict=True)
    fixture_root = args.fixture_root.resolve()
    fixture_root.mkdir(parents=True, exist_ok=True)
    windows = os.name == "nt"
    resources = executable.parent / "resources" if windows else executable.parent.parent / "Resources"
    for relative in (
        "SoundBot.txt", "SoundBot-CREDITS.md", "MiSans-License.pdf",
        "THIRD_PARTY_AUDIO_NOTICES.txt", "electron/LICENSE",
        "electron/LICENSES.chromium.html", "ffmpeg/COPYING.LGPLv3", "ffmpeg/COPYING.GPLv3",
    ):
        notice = resources / "licenses" / relative
        if not notice.is_file() or notice.stat().st_size == 0:
            raise FileNotFoundError(f"installed license notice is missing: {relative}")
    backend_executable = resources / "backend" / "soundbot-backend" / (
        "soundbot-backend.exe" if windows else "soundbot-backend"
    )
    if not backend_executable.is_file():
        raise FileNotFoundError(f"installed backend is missing: {backend_executable}")
    if sys.platform == "darwin":
        _check_macos_vst_worker(executable, backend_executable)
    if (resources / "models").exists():
        raise ValueError("core installer contains a model directory")

    file_fixture = (
        fixture_root / "单文件 导入 % # + (A)" / "tone 单文件 % # + (A).wav"
    )
    folder_fixture = (
        fixture_root / "来源目录 (B)" / "文件夹 导入 % # + (B)" / "tone 文件夹 % # + (B).wav"
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
    user_data = fixture_root / "user-data"
    daw_directory = fixture_root / "DAW 工程媒体"
    user_data.mkdir(parents=True, exist_ok=True)
    daw_directory.mkdir(parents=True, exist_ok=True)
    (user_data / "daw-directory.json").write_text(json.dumps({"directory": str(daw_directory.resolve())}), encoding="utf-8")
    appdata.mkdir(parents=True, exist_ok=True)
    local_appdata.mkdir(parents=True, exist_ok=True)
    stdout_log = fixture_root / "installed-electron.stdout.log"
    stderr_log = fixture_root / "installed-electron.stderr.log"
    electron_log = fixture_root / "installed-electron.chromium.log"
    port = args.debug_port or _free_loopback_port()
    env = dict(os.environ)
    env.pop("SOUNDBOT_MODELS_PATH", None)
    env.update(
        {
            "APPDATA": str(appdata),
            "LOCALAPPDATA": str(local_appdata),
            "SOUNDBOT_USER_DATA_DIR": str(user_data),
            "ELECTRON_ENABLE_LOGGING": "true",
            "ELECTRON_LOG_FILE": str(electron_log),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    if args.vst_fixture:
        if not windows:
            raise ValueError("The VST3 window ownership check requires Windows")
        fixture = args.vst_fixture.resolve(strict=True)
        fixture_dir = local_appdata / "Programs/Common/VST3" / fixture.name
        shutil.copytree(fixture, fixture_dir)

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
                    "--autoplay-policy=no-user-gesture-required",
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
            ] if windows else []
            result = client.evaluate(
                _renderer_smoke_expression(
                    file_fixture,
                    folder_fixture,
                    wma_fixture,
                    timeout_seconds=args.timeout,
                )
            )
            result["nativeDialogs"] = native_dialogs
            if args.vst_fixture:
                result["vst3"] = _exercise_vst(client, process.pid, args.vst_fixture)
            result["catalogMeter"] = _exercise_catalog_meter(client, fixture_root / "检索与响度", file_fixture)
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
            or result.get("modelLoaded") is not False
            or result.get("wmaPlayback") != "transcoded_wav"
            or result.get("folderRemoval") is not True
            or not all(path.is_file() for path in (file_fixture, folder_fixture, wma_fixture))
            or len(result.get("nativeDialogs") or []) != (2 if windows else 0)
            or not all(item.get("appeared") for item in result.get("nativeDialogs", []))
        ):
            raise AssertionError(f"installed Electron smoke was incomplete: {result}")
        print(
            json.dumps(
                {
                    "executable": str(executable),
                    "resources": str(resources),
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
        for backend_log in sorted(fixture_root.rglob("soundbot_*.log")):
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
