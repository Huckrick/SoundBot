/**
 * SoundBot - AI 音效管理器
 * Copyright (C) 2026 Huckrick and SoundBot contributors
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
const { contextBridge, ipcRenderer, webUtils } = require('electron');

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
    handleDropFiles: (files) => ipcRenderer.invoke('file-import', 'handle-drop',
      files.map(file => webUtils.getPathForFile(file)).filter(Boolean)),
    // 获取文件信息
    getFileInfo: (filePath) => ipcRenderer.invoke('file-import', 'get-info', filePath),
    // 验证文件类型
    validateFileType: (filePath) => ipcRenderer.invoke('file-import', 'validate-type', filePath)
  },

  // 菜单事件监听
  onMenuEvent: (callback) => {
    for (const action of ['menu-new-project', 'menu-import-file', 'menu-import-folder']) {
      ipcRenderer.on(action, () => callback(action));
    }
  },

  // 对话框
  dialogs: {
    showMessageBox: (options) => ipcRenderer.invoke('dialog-message', options)
  },

  // 原生拖拽导出（用于拖拽文件到 DAW）
  startDrag: (deliveryId) => ipcRenderer.send('start-drag', deliveryId),
  chooseDawDirectory: () => ipcRenderer.invoke('choose-daw-directory'),
  onDeliveryError: (callback) => ipcRenderer.on('delivery-error', (_event, message) => callback(message)),
  editingDirty: (dirty) => ipcRenderer.send('editing-dirty', dirty),
  setLanguage: (language) => ipcRenderer.send('interface-language', language),
  revealFile: (file) => ipcRenderer.invoke('reveal-file', file),
  desktopMedia: async (action, data) => {
    const result = await ipcRenderer.invoke('desktop-media', action, data);
    if (result?.[BACKEND_ERROR_SENTINEL]) throw createRendererError(result.error);
    return result;
  },

  backendAPI: {
    healthCheck: () => invokeBackend('health'),
    importFolderAsync: (folderPath, recursive, clientId, projectId) =>
      invokeBackend('import-async', { folderPath, recursive, clientId, projectId }),
    importFiles: (filePaths, clientId, projectId) =>
      invokeBackend('import-files', { filePaths, clientId, projectId }),
    searchAudio: (query, topK, threshold, page, pageSize, projectId) =>
      invokeBackend('search', { query, topK, threshold, page, page_size: pageSize, projectId }),
    getWaveformById: (fileId, projectId, requestId) =>
      invokeBackend('waveform-by-id', { fileId, projectId, requestId }),
    cancelRequest: (requestId) => ipcRenderer.invoke('backend-api-cancel', requestId)
  },

  aiConfig: {
    get: () => invokeAIConfig('get'),
    save: (config) => invokeAIConfig('save', config),
    test: (config) => invokeAIConfig('test', config),
    prepareChat: () => invokeAIConfig('prepare-chat')
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

  // Keep the renderer surface deliberately small; unsupported and unused IPC
  // contracts were removed in v0.2.0.
});

// Main uses this one-way handshake as a packaged-runtime assertion that the
// whole bridge was installed. A syntax-only check cannot catch preload sandbox
// violations.
ipcRenderer.send('renderer-bridge-ready', { version: 1 });
