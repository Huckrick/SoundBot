/**
 * SoundBot - AI 音效管理器
 * Copyright (C) 2026 Nagisa_Huckrick (胡杨)
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

// Electron 20+ sandboxes renderer processes by default. Sandboxed preload
// scripts only have a small, polyfilled `require` surface, so this file must
// stay self-contained and only import Electron's renderer APIs. In
// particular, requiring local JSON/CommonJS files here prevents the entire
// contextBridge from being installed in packaged builds.
const { contextBridge, ipcRenderer } = require('electron');

const BACKEND_ERROR_SENTINEL = '__soundbotBackendError';

function createRendererError(payload = {}) {
  const error = new Error(payload.message || payload.error || '请求失败');
  error.code = payload.code || 'REQUEST_FAILED';
  error.retryable = Boolean(payload.retryable);
  error.details = payload.details && typeof payload.details === 'object'
    ? payload.details
    : (payload.detail && typeof payload.detail === 'object' ? payload.detail : {});
  error.detail = error.details;
  error.status = Number.isInteger(payload.status) ? payload.status : null;
  error.action = payload.action || null;
  error.url = payload.url || null;
  return error;
}

async function invokeBackend(action, data) {
  const result = await ipcRenderer.invoke('backend-api', action, data);
  if (result?.[BACKEND_ERROR_SENTINEL]) {
    throw createRendererError(result.error);
  }
  return result;
}

async function invokeSecret(action, payload) {
  const result = await ipcRenderer.invoke('secure-secret', action, payload);
  if (result?.success === false) {
    throw createRendererError(result);
  }
  return result;
}

async function invokeAIConfig(action, payload) {
  const result = await ipcRenderer.invoke('ai-config', action, payload);
  if (result?.success === false) {
    throw createRendererError(result);
  }
  return result;
}

// 安全地暴露 API 给前端
contextBridge.exposeInMainWorld('electronAPI', {
  // 窗口控制
  windowControl: {
    minimize: () => ipcRenderer.invoke('window-control', 'minimize'),
    maximize: () => ipcRenderer.invoke('window-control', 'maximize'),
    close: () => ipcRenderer.invoke('window-control', 'close'),
    isMaximized: () => ipcRenderer.invoke('window-control', 'isMaximized')
  },

  // 文件导入（专门为导入按钮设计）
  fileImport: {
    // The main process owns the canonical capability manifest. Returning it
    // over IPC avoids duplicating the table or loading local modules here.
    getCapabilities: () => ipcRenderer.invoke('audio-capabilities'),
    // 打开音频文件选择对话框
    selectAudioFiles: (options = {}) => ipcRenderer.invoke('file-import', 'select-audio', options),
    // 打开文件夹选择对话框
    selectFolder: (options = {}) => ipcRenderer.invoke('file-import', 'select-folder', options),
    // 处理拖放的文件
    handleDropFiles: (files) => ipcRenderer.invoke('file-import', 'handle-drop', files),
    // 获取文件信息
    getFileInfo: (filePath) => ipcRenderer.invoke('file-import', 'get-info', filePath),
    // 验证文件类型
    validateFileType: (filePath) => ipcRenderer.invoke('file-import', 'validate-type', filePath)
  },

  // 菜单事件监听
  onMenuEvent: (callback) => {
    ipcRenderer.on('menu-new-project', (event, ...args) => callback(event, 'menu-new-project', ...args));
    ipcRenderer.on('menu-import-file', (event, ...args) => callback(event, 'menu-import-file', ...args));
    ipcRenderer.on('menu-import-folder', (event, ...args) => callback(event, 'menu-import-folder', ...args));
  },

  // 后端就绪事件监听
  onBackendReady: (callback) => {
    ipcRenderer.on('backend-ready', callback);
  },

  // 对话框
  dialogs: {
    showMessageBox: (options) => ipcRenderer.invoke('dialog-message', options),
    showOpenDialog: (options) => ipcRenderer.invoke('dialog-open', options),
    showSaveDialog: (options) => ipcRenderer.invoke('dialog-save', options)
  },

  // 原生拖拽导出（用于拖拽文件到 DAW）
  startDrag: (filePath) => ipcRenderer.invoke('start-drag', filePath),

  // 后端 API（与 FastAPI 服务通信；失败时 Promise 会 reject，并保留结构化错误字段）
  backendAPI: {
        // 健康检查
        healthCheck: () => invokeBackend('health'),

        // 仅扫描文件不建索引（用于没有模型的情况）
        scanOnly: (folderPath, recursive = true) =>
          invokeBackend('scan-only', { folderPath, recursive }),

        // 异步导入文件夹（带进度推送）
        importFolderAsync: (folderPath, recursive = true, clientId = 'default', projectId) =>
          invokeBackend('import-async', { folderPath, recursive, clientId, projectId }),

        // 将文件路径交给后端导入任务；不再通过 IPC 搬运整文件字节数组
        importFiles: (filePaths, clientId = 'default', projectId) =>
          invokeBackend('import-files', { filePaths, clientId, projectId }),

        // 语义搜索音频（支持分页）
        searchAudio: (query, topK = 400, threshold = 0.15, page = 1, pageSize = 50, projectId) =>
          invokeBackend('search', { query, topK, threshold, page, page_size: pageSize, projectId }),

        // 获取索引状态
        getIndexStatus: () => invokeBackend('index-status'),

        // 获取已索引的文件列表
        getIndexedFiles: () => invokeBackend('indexed-files'),

        // 从 SQLite 获取所有文件（启动时加载）
        getAllDbFiles: () => invokeBackend('db-files'),

        // 获取单个文件详情
        getDbFile: (path) => invokeBackend('db-file', path),

        // 更新文件标签
        updateFileTags: (path, tags) => invokeBackend('db-file-tags', { path, tags }),

        // 从数据库删除文件
        deleteDbFile: (path) => invokeBackend('db-file-delete', path),

        // 获取数据库统计
        getDbStats: () => invokeBackend('db-stats'),

        // 获取音频文件 URL
        getAudioUrl: (filePath) => invokeBackend('audio-url', filePath),

        // 启动后端服务
        startServer: () => invokeBackend('start-server'),

        // 停止后端服务
        stopServer: () => invokeBackend('stop-server'),

        // 获取音频波形数据
        getWaveform: (filePath, requestId = null) =>
          invokeBackend('waveform', { filePath, requestId }),

        // 数据库文件优先使用稳定 file_id，避免路径编码和工程歧义
        getWaveformById: (fileId, projectId = 'default', requestId = null) =>
          invokeBackend('waveform-by-id', { fileId, projectId, requestId }),

        // Abort a renderer-owned in-flight backend request (used by waveform selection).
        cancelRequest: (requestId) => ipcRenderer.invoke('backend-api-cancel', requestId),

        // 批量获取可见列表项波形
        getWaveformsBatch: (fileIds, projectId = 'default') =>
          invokeBackend('waveforms-batch', { fileIds, projectId }),

        // 裁切音频片段
        exportClip: (filePath, start, end, tempFile = true) => invokeBackend('export-clip', { filePath, start, end, tempFile }),

        // 音频淡入淡出
        applyFade: (filePath, fadeIn, fadeOut) => invokeBackend('audio-fade', { filePath, fadeIn, fadeOut }),

        // 获取临时文件目录
        getTempDir: () => invokeBackend('get-temp-dir'),

        // 设置临时文件目录
        setTempDir: (tempDir) => invokeBackend('set-temp-dir', { tempDir }),

        // 获取磁盘空间信息
        getDiskSpace: () => invokeBackend('disk-space'),

        // 清理临时裁切文件
        clearTempClips: () => invokeBackend('clear-temp-clips')
    },

  // OS-backed encrypted secrets. Legacy plaintext is migrated by Electron
  // before the backend starts; stored plaintext is never exposed here.
  secrets: {
    isAvailable: () => invokeSecret('status'),
    set: (key, value) => invokeSecret('set', { key, value }),
    has: (key) => invokeSecret('has', { key }),
    // Backward-compatible alias; returns existence only, never plaintext.
    get: (key) => invokeSecret('has', { key }),
    delete: (key) => invokeSecret('delete', { key })
  },

  aiConfig: {
    get: () => invokeAIConfig('get'),
    save: (config) => invokeAIConfig('save', config),
    test: (config) => invokeAIConfig('test', config)
  },

  backendStatus: {
    waitUntilReady: (timeoutMs = 60000) => ipcRenderer.invoke('wait-backend-ready', timeoutMs),
    openLogs: () => ipcRenderer.invoke('open-log-directory')
  },

  runtime: {
    getConfig: () => ipcRenderer.invoke('get-runtime-config')
  },

  // 平台信息
  platform: process.platform,

  // 获取应用路径
  getAppPath: () => ipcRenderer.invoke('get-app-path'),

  // 检查完全磁盘访问权限（macOS）
  checkFullDiskAccess: () => ipcRenderer.invoke('check-full-disk-access'),

  // 打开隐私设置（macOS）
  openPrivacySettings: () => ipcRenderer.invoke('open-privacy-settings'),

  // Keep the renderer surface deliberately small; unsupported and unused IPC
  // contracts were removed in v0.2.0.
});

// Main uses this one-way handshake as a packaged-runtime assertion that the
// whole bridge was installed. A syntax-only check cannot catch preload sandbox
// violations.
ipcRenderer.send('renderer-bridge-ready', { version: 1 });
