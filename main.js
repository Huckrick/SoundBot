/**
 * SoundBot - AI 音效管理器 (PyInstaller 一体化版本)
 * Copyright (C) 2026 Nagisa_Huckrick (胡杨)
 *
 * 前后端一体化打包，无需单独安装 Python 环境
 */

const { app, BrowserWindow, Menu, ipcMain, dialog, protocol, shell, Notification, globalShortcut } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const net = require('net');

// 自定义协议：用于在渲染进程中安全加载本地音频
const AUDIO_PROTOCOL = 'soundmind-audio';

// 必须在 app.ready 之前调用
protocol.registerSchemesAsPrivileged([
  { scheme: AUDIO_PROTOCOL, privileges: { standard: true, secure: true, supportFetchAPI: true } }
]);

let mainWindow;
let backendProcess = null;
let backendStartupPromise = null;
let ipcHandlersInitialized = false;
let backendPort = Number(process.env.SOUNDBOT_PORT || 8000);
let backendOrigin = `http://127.0.0.1:${backendPort}`;
let backendWsOrigin = `ws://127.0.0.1:${backendPort}`;
let apiBaseUrl = `${backendOrigin}/api/v1`;

// GitHub 仓库配置
const GITHUB_REPO = 'Huckrick/SoundBot';

// ==================== 路径辅助函数 ====================

/**
 * 获取应用资源路径
 * 开发环境：项目根目录
 * 生产环境：app.asar 解压后的资源目录
 */
function getAppPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'app.asar.unpacked');
  }
  return __dirname;
}

/**
 * 获取应用根目录
 */
function getAppRootDir() {
  if (app.isPackaged) {
    return path.dirname(process.execPath);
  }
  return __dirname;
}

/**
 * 获取用户数据目录
 */
function getUserDataDir() {
  return app.getPath('userData');
}

/**
 * 获取后端可执行文件路径
 * onedir 模式：resources/backend/soundbot-backend/soundbot-backend
 */
function getBackendExecutable() {
  const exeName = process.platform === 'win32'
    ? 'soundbot-backend.exe'
    : 'soundbot-backend';

  // 可能的路径（按优先级）
  const possiblePaths = [
    // 1. 生产环境 - extraResources 路径
    path.join(process.resourcesPath, 'backend', 'soundbot-backend', exeName),

    // 2. 开发环境
    path.join(__dirname, 'dist', 'backend', 'soundbot-backend', exeName),
    path.join(__dirname, 'backend', 'dist', 'backend', 'soundbot-backend', exeName),

    // 3. 应用目录（便携模式）
    path.join(getAppRootDir(), 'backend', 'soundbot-backend', exeName),
    path.join(getAppRootDir(), 'resources', 'backend', 'soundbot-backend', exeName),
  ];

  console.log('[Backend] Searching for backend executable...');
  for (const p of possiblePaths) {
    console.log(`[Backend] Checking: ${p}`);
    if (fs.existsSync(p)) {
      console.log(`[Backend] ✓ Found backend executable: ${p}`);
      return p;
    }
  }

  console.error('[Backend] ✗ Backend executable not found. Tried paths:');
  possiblePaths.forEach(p => console.error(`  - ${p}`));
  return null;
}

/**
 * 验证后端目录完整性
 */
function verifyBackendIntegrity(backendDir) {
  console.log(`[Backend] Verifying backend integrity: ${backendDir}`);

  const requiredItems = [
    'soundbot-backend',
    'soundbot-backend.exe',
    '_internal',  // PyInstaller 新版本使用 _internal
    'lib',        // 旧版本使用 lib
    'base_library.zip'
  ];

  const foundItems = [];
  for (const item of requiredItems) {
    const itemPath = path.join(backendDir, item);
    if (fs.existsSync(itemPath)) {
      foundItems.push(item);
      const stats = fs.statSync(itemPath);
      console.log(`[Backend] ✓ ${item} (${stats.isDirectory() ? 'dir' : 'file'})`);
    }
  }

  if (foundItems.length < 2) {
    console.error('[Backend] ✗ Too few required items found in backend directory');
    return false;
  }

  // 检查目录大小
  const getDirSize = (dir) => {
    let size = 0;
    const files = fs.readdirSync(dir);
    for (const file of files) {
      const filePath = path.join(dir, file);
      const stats = fs.statSync(filePath);
      if (stats.isDirectory()) {
        size += getDirSize(filePath);
      } else {
        size += stats.size;
      }
    }
    return size;
  };

  const totalSize = getDirSize(backendDir);
  console.log(`[Backend] Total backend size: ${(totalSize / 1024 / 1024).toFixed(1)} MB`);

  if (totalSize < 50 * 1024 * 1024) {
    console.error('[Backend] ✗ Backend size is too small, may be incomplete');
    return false;
  }

  return true;
}

/**
 * 自动检索模型目录
 * 优先级：环境变量 > 应用目录 > 用户数据目录 > 开发目录
 */
function findModelsDir() {
  const possiblePaths = [];

  // 1. 环境变量（最高优先级）
  const envPath = process.env.SOUNDBOT_MODELS_PATH;
  if (envPath) {
    possiblePaths.push(envPath);
  }

  // 2. 应用资源目录
  possiblePaths.push(path.join(process.resourcesPath, 'models'));
  possiblePaths.push(path.join(getAppRootDir(), 'models'));

  // 3. 用户数据目录
  possiblePaths.push(path.join(getUserDataDir(), 'models'));

  // 4. 开发环境
  possiblePaths.push(path.join(__dirname, 'models'));
  possiblePaths.push(path.join(__dirname, '..', 'models'));

  console.log('[Models] Searching for models directory...');

  // 查找第一个包含 clap 子目录的路径
  for (const modelsPath of possiblePaths) {
    const clapDir = path.join(modelsPath, 'clap');
    console.log(`[Models] Checking: ${modelsPath}`);
    if (fs.existsSync(clapDir) && fs.statSync(clapDir).isDirectory()) {
      console.log(`[Models] ✓ Found models directory: ${modelsPath}`);
      return modelsPath;
    }
  }

  // 没找到，返回第一个路径（用于错误提示）
  const defaultPath = possiblePaths[0] || path.join(getUserDataDir(), 'models');
  console.log(`[Models] ✗ Models not found, using default: ${defaultPath}`);
  return defaultPath;
}

/**
 * 检查模型是否存在
 */
function checkModels() {
  const modelsDir = findModelsDir();
  const clapDir = path.join(modelsDir, 'clap');

  return {
    exists: fs.existsSync(clapDir) && fs.statSync(clapDir).isDirectory(),
    path: modelsDir
  };
}

// ==================== 后端管理 ====================

/**
 * 显示模型缺失提示
 */
async function showModelMissingDialog(modelsPath) {
  const result = await dialog.showMessageBox(mainWindow, {
    type: 'warning',
    title: '需要 AI 模型文件',
    message: '未找到 AI 模型文件',
    detail: `请在以下位置放置模型文件:\n\n${modelsPath}\n\n` +
            `目录结构应为:\n${path.join(modelsPath, 'clap', '...')}\n\n` +
            `您也可以将 models 文件夹放在应用安装目录。`,
    buttons: ['打开下载页面', '打开模型目录', '退出'],
    defaultId: 0,
    cancelId: 2
  });

  if (result.response === 0) {
    shell.openExternal(`https://github.com/${GITHUB_REPO}/releases`);
  } else if (result.response === 1) {
    // 创建目录并打开
    fs.mkdirSync(modelsPath, { recursive: true });
    shell.openPath(modelsPath);
  }

  app.quit();
}

/**
 * 查找可用端口（从 startPort 开始递增）
 */
function findFreePort(startPort) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(findFreePort(startPort + 1)));
    server.once('listening', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
    server.listen(startPort, '127.0.0.1');
  });
}

/**
 * 启动后端服务
 */
async function startBackend() {
  if (backendProcess) {
    console.log('[Backend] Backend service already running');
    return { success: true };
  }

  // 找可用端口，避免 "Address already in use" 错误
  const freePort = await findFreePort(backendPort);
  if (freePort !== backendPort) {
    console.log(`[Backend] Port ${backendPort} busy, switching to ${freePort}`);
    backendPort = freePort;
    backendOrigin = `http://127.0.0.1:${backendPort}`;
    backendWsOrigin = `ws://127.0.0.1:${backendPort}`;
    apiBaseUrl = `${backendOrigin}/api/v1`;
  }

  // 检查模型
  const modelStatus = checkModels();
  if (!modelStatus.exists) {
    console.warn('[Backend] Model files not found:', modelStatus.path);
    await showModelMissingDialog(modelStatus.path);
    return { success: false, error: '缺少模型文件' };
  }

  // 获取后端可执行文件
  const backendExe = getBackendExecutable();
  if (!backendExe) {
    dialog.showErrorBox(
      '错误', 
      '未找到后端可执行文件，请重新安装应用。\n\n【重要提示】：如果您使用的是 Windows 系统，这很可能是因为 Windows Defender 或其他杀毒软件误报并将核心文件(soundbot-backend.exe)隔离或删除了。请尝试将软件安装目录加入杀毒软件白名单后，重新安装。'
    );
    return { success: false, error: '未找到后端可执行文件' };
  }

  // 验证后端目录完整性
  const backendDir = path.dirname(backendExe);
  if (!verifyBackendIntegrity(backendDir)) {
    dialog.showErrorBox(
      '错误', 
      '后端文件不完整，请重新安装应用。\n\n【重要提示】：如果您使用的是 Windows 系统，这很可能是因为 Windows Defender 或其他杀毒软件误报并将部分核心文件隔离或删除了。请尝试将软件安装目录加入杀毒软件白名单后，重新安装。'
    );
    return { success: false, error: '后端文件不完整' };
  }

  // 设置环境变量
  const env = {
    ...process.env,
    SOUNDBOT_PORT: String(backendPort),
    SOUNDBOT_MODELS_PATH: modelStatus.path,
    PYTHONUNBUFFERED: '1',
    PYTHONIOENCODING: 'utf-8'
  };

  console.log(`[Backend] Starting backend: ${backendExe}`);
  console.log(`[Backend] Working directory: ${backendDir}`);
  console.log(`[Backend] Model path: ${env.SOUNDBOT_MODELS_PATH}`);

  try {
    // 启动后端进程
    backendProcess = spawn(backendExe, [], {
      env,
      cwd: backendDir,
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: false
    });

    // 日志处理
    backendProcess.stdout.on('data', (data) => {
      const text = data.toString().trim();
      if (text) console.log(`[Backend] ${text}`);
    });

    backendProcess.stderr.on('data', (data) => {
      const text = data.toString().trim();
      if (text) console.error(`[Backend] ${text}`);
    });

    backendProcess.on('error', (error) => {
      console.error('[Backend] Process error:', error);
      backendProcess = null;
      backendStartupPromise = null;
    });

    backendProcess.on('exit', (code, signal) => {
      console.log(`[Backend] Process exited, code: ${code}, signal: ${signal}`);
      backendProcess = null;
      backendStartupPromise = null;
    });

    // 等待服务启动
    return new Promise((resolve) => {
      let retries = 0;
      const maxRetries = 120; // 120秒超时（torch冷启动在Windows上可能较慢）

      const interval = setInterval(async () => {
        // 快速失败：进程已提前退出
        if (!backendProcess || backendProcess.exitCode !== null) {
          clearInterval(interval);
          const code = backendProcess ? backendProcess.exitCode : 'unknown';
          console.error(`[Backend] ✗ Process exited before becoming healthy (code: ${code})`);
          resolve({ success: false, error: `后端进程意外退出 (code: ${code})` });
          return;
        }
        try {
          const res = await fetch(`${apiBaseUrl}/health`);
          if (res.ok) {
            clearInterval(interval);
            console.log('[Backend] ✓ Backend service started successfully');
            resolve({ success: true });
          }
        } catch (e) {
          retries++;
          if (retries >= maxRetries) {
            clearInterval(interval);
            console.error('[Backend] ✗ Startup timeout');
            resolve({ success: false, error: '启动超时' });
          }
        }
      }, 1000);
    });

  } catch (error) {
    console.error('[Backend] Startup failed:', error);
    return { success: false, error: error.message };
  }
}

async function waitForBackendHealth(timeoutMs = 120000) {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    try {
      const res = await fetch(`${apiBaseUrl}/health`);
      if (res.ok) {
        return { success: true };
      }
    } catch (error) {
    }

    await new Promise((resolve) => setTimeout(resolve, 1000));
  }

  return { success: false, error: '启动超时' };
}

async function ensureBackendStarted() {
  if (backendProcess) {
    return await waitForBackendHealth();
  }

  if (backendStartupPromise) {
    return await backendStartupPromise;
  }

  backendStartupPromise = startBackend()
    .finally(() => {
      if (!backendProcess) {
        backendStartupPromise = null;
      }
    });

  return await backendStartupPromise;
}

function getRuntimeConfig() {
  return {
    port: backendPort,
    apiBase: backendOrigin,
    apiV1Base: apiBaseUrl,
    wsBase: backendWsOrigin
  };
}

async function parseBackendError(response) {
  const contentType = response.headers.get('content-type') || '';

  try {
    if (contentType.includes('application/json')) {
      const data = await response.json();
      return data.detail || data.error || JSON.stringify(data);
    }

    const text = await response.text();
    return text || `HTTP ${response.status}`;
  } catch (error) {
    return `HTTP ${response.status}`;
  }
}

async function requestBackendJson(url, options = {}) {
  const response = await fetch(url, options);

  if (!response.ok) {
    throw new Error(await parseBackendError(response));
  }

  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return await response.json();
  }

  return { success: true, data: await response.text() };
}

async function requestBackendBinary(url, options = {}) {
  const response = await fetch(url, options);

  if (!response.ok) {
    throw new Error(await parseBackendError(response));
  }

  const buffer = await response.arrayBuffer();
  return {
    success: true,
    data: Array.from(new Uint8Array(buffer)),
    headers: {
      cached: response.headers.get('X-Cached'),
      duration: response.headers.get('X-Duration')
    }
  };
}

function createBackendRequest(action, data = {}) {
  const filePath = typeof data === 'string' ? data : (data.filePath || data.path || '');
  const encodedPath = encodeURIComponent(filePath);

  switch (action) {
    case 'health':
      return { url: `${apiBaseUrl}/health` };
    case 'scan':
      return {
        url: `${apiBaseUrl}/scan`,
        options: {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            folder_path: data.folderPath,
            recursive: data.recursive ?? true
          })
        }
      };
    case 'scan-only':
      return {
        url: `${apiBaseUrl}/scan-only`,
        options: {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            folder_path: data.folderPath,
            recursive: data.recursive ?? true
          })
        }
      };
    case 'import-async':
      return {
        url: `${apiBaseUrl}/import/async?client_id=${encodeURIComponent(data.clientId || 'default')}`,
        options: {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            folder_path: data.folderPath,
            recursive: data.recursive ?? true
          })
        }
      };
    case 'search':
      return {
        url: `${apiBaseUrl}/search`,
        options: {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            query: data.query,
            top_k: data.topK,
            threshold: data.threshold,
            page: data.page || 1,
            page_size: data.page_size || 50
          })
        }
      };
    case 'index-status':
      return { url: `${apiBaseUrl}/index/status` };
    case 'indexed-files':
      return { url: `${apiBaseUrl}/files` };
    case 'db-files':
      return { url: `${apiBaseUrl}/db/files` };
    case 'db-file':
      return { url: `${apiBaseUrl}/db/file/${encodedPath}` };
    case 'db-file-tags':
      return {
        url: `${apiBaseUrl}/db/file/${encodeURIComponent(data.path)}/tags`,
        options: {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tags: data.tags || [] })
        }
      };
    case 'db-file-delete':
      return {
        url: `${apiBaseUrl}/db/file/${encodedPath}`,
        options: { method: 'DELETE' }
      };
    case 'db-stats':
      return { url: `${apiBaseUrl}/db/stats` };
    case 'audio-url':
      return { localOnly: true, result: { success: true, url: `${apiBaseUrl}/audio/${encodedPath}` } };
    case 'waveform':
      return { url: `${backendOrigin}/api/waveform?path=${encodedPath}` };
    case 'audio-preload':
      return {
        url: `${apiBaseUrl}/audio/preload/${encodedPath}`,
        options: { method: 'POST' }
      };
    case 'audio-decoded':
      return { url: `${apiBaseUrl}/audio/decoded/${encodedPath}` };
    case 'audio-stream':
      return { url: `${apiBaseUrl}/audio/stream/${encodedPath}`, responseType: 'binary' };
    case 'export-clip':
      return {
        url: `${backendOrigin}/api/export/clip`,
        options: {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            path: data.filePath,
            start: data.start,
            end: data.end,
            temp_file: data.tempFile
          })
        }
      };
    case 'audio-fade':
      return {
        url: `${backendOrigin}/api/audio/fade`,
        options: {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            path: data.filePath,
            fade_in: data.fadeIn,
            fade_out: data.fadeOut
          })
        }
      };
    case 'get-temp-dir':
      return { url: `${apiBaseUrl}/config/temp-dir` };
    case 'set-temp-dir':
      return {
        url: `${apiBaseUrl}/config/temp-dir`,
        options: {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ temp_dir: data.tempDir })
        }
      };
    case 'disk-space':
      return { url: `${apiBaseUrl}/disk-space` };
    case 'clear-temp-clips':
      return {
        url: `${apiBaseUrl}/temp-clips/clear`,
        options: { method: 'POST' }
      };
    default:
      return null;
  }
}

/**
 * 停止后端服务
 */
async function stopBackend() {
  if (!backendProcess) {
    return { success: true };
  }

  console.log('[Backend] Stopping backend service');

  return new Promise((resolve) => {
    // 发送 SIGTERM
    if (process.platform === 'win32') {
      backendProcess.kill();
    } else {
      backendProcess.kill('SIGTERM');
    }

    // 等待进程退出
    const timeout = setTimeout(() => {
      console.warn('[Backend] Force killing backend process');
      backendProcess.kill('SIGKILL');
      backendProcess = null;
      resolve({ success: true });
    }, 5000);

    backendProcess.on('exit', () => {
      clearTimeout(timeout);
      backendProcess = null;
      resolve({ success: true });
    });
  });
}

// ==================== 窗口管理 ====================

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1200,
    minHeight: 700,
    title: 'SoundBot - AI 音效管理器',
    icon: path.join(__dirname, 'assets', 'icon.png'),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
      webSecurity: true,
      allowRunningInsecureContent: false
    },
    titleBarStyle: 'default',
    show: false,
    backgroundColor: '#0a0a0a'
  });

  // 设置 CSP
  mainWindow.webContents.session.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          "default-src 'self'; " +
          "script-src 'self' 'unsafe-inline'; " +
          "style-src 'self' 'unsafe-inline'; " +
          "font-src 'self'; " +
          "img-src 'self' data: blob:; " +
          "media-src 'self' blob: soundmind-audio:; " +
          `connect-src 'self' ${backendOrigin} ${backendWsOrigin} ` +
          "https://api.openai.com https://api.moonshot.cn https://api.anthropic.com " +
          "https://api.deepseek.com https://api.siliconflow.cn " +
          "https://generativelanguage.googleapis.com;"
        ]
      }
    });
  });

  // 加载页面
  const indexPath = path.join(__dirname, 'index.html');
  mainWindow.loadFile(indexPath);

  // 窗口关闭处理
  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  createMenu();
  if (!ipcHandlersInitialized) {
    setupIpcHandlers();
    ipcHandlersInitialized = true;
  }
}

function createMenu() {
  const template = [
    {
      label: '文件',
      submenu: [
        {
          label: '导入文件夹',
          accelerator: 'CmdOrCtrl+O',
          click: () => {
            mainWindow.webContents.send('menu-import-folder');
          }
        },
        { type: 'separator' },
        {
          label: '退出',
          accelerator: process.platform === 'darwin' ? 'Cmd+Q' : 'Ctrl+Q',
          click: () => {
            app.quit();
          }
        }
      ]
    },
    {
      label: '编辑',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' }
      ]
    },
    {
      label: '视图',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools', accelerator: 'F12' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' }
      ]
    }
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

// ==================== IPC 处理 ====================

function setupIpcHandlers() {
  // 窗口控制
  ipcMain.handle('window-control', (event, action) => {
    if (!mainWindow) return;

    switch (action) {
      case 'minimize':
        mainWindow.minimize();
        break;
      case 'maximize':
        if (mainWindow.isMaximized()) {
          mainWindow.unmaximize();
        } else {
          mainWindow.maximize();
        }
        break;
      case 'close':
        mainWindow.close();
        break;
      case 'isMaximized':
        return mainWindow.isMaximized();
    }
  });

  // 文件对话框
  ipcMain.handle('dialog-open', async (event, options) => {
    return await dialog.showOpenDialog(mainWindow, options);
  });

  ipcMain.handle('dialog-save', async (event, options) => {
    return await dialog.showSaveDialog(mainWindow, options);
  });

  ipcMain.handle('dialog-message', async (event, options) => {
    return await dialog.showMessageBox(mainWindow, options);
  });

  // 后端 API 代理
  ipcMain.handle('backend-api', async (event, action, data) => {
    try {
      if (action === 'start-server') {
        return await ensureBackendStarted();
      }

      if (action === 'stop-server') {
        return await stopBackend();
      }

      if (action === 'runtime-config') {
        return getRuntimeConfig();
      }

      const startupResult = await ensureBackendStarted();
      if (!startupResult.success) {
        return startupResult;
      }

      const requestConfig = createBackendRequest(action, data);
      if (!requestConfig) {
        return { success: false, error: `未知操作: ${action}` };
      }

      if (requestConfig.localOnly) {
        return requestConfig.result;
      }

      if (requestConfig.responseType === 'binary') {
        return await requestBackendBinary(requestConfig.url, requestConfig.options);
      }

      return await requestBackendJson(requestConfig.url, requestConfig.options);
    } catch (error) {
      console.error('[IPC] Backend API error:', error);
      return { success: false, error: error.message };
    }
  });

  ipcMain.handle('wait-backend-ready', async (event, timeoutMs = 60000) => {
    const startupResult = await ensureBackendStarted();
    if (!startupResult.success) {
      return startupResult;
    }

    return await waitForBackendHealth(timeoutMs);
  });

  ipcMain.handle('get-runtime-config', () => getRuntimeConfig());
  ipcMain.handle('get-app-path', () => getAppRootDir());
  ipcMain.handle('check-full-disk-access', async () => {
    if (process.platform !== 'darwin') {
      return true;
    }

    try {
      fs.readdirSync(app.getPath('documents'));
      return true;
    } catch (error) {
      return false;
    }
  });
  ipcMain.handle('open-privacy-settings', async () => {
    if (process.platform !== 'darwin') {
      return { success: false, error: '仅支持 macOS' };
    }

    await shell.openExternal('x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles');
    return { success: true };
  });

  const supportedAudioExtensions = new Set(['.wav', '.mp3', '.flac', '.aiff', '.aif', '.ogg', '.m4a', '.aac', '.wma']);
  ipcMain.handle('file-import', async (event, action, payload) => {
    try {
      switch (action) {
        case 'select-audio': {
          const result = await dialog.showOpenDialog(mainWindow, {
            properties: ['openFile', 'multiSelections'],
            filters: [
              { name: 'Audio Files', extensions: ['wav', 'mp3', 'flac', 'aiff', 'aif', 'ogg', 'm4a', 'aac', 'wma'] }
            ],
            ...payload
          });
          return result;
        }
        case 'select-folder':
          return await dialog.showOpenDialog(mainWindow, {
            properties: ['openDirectory'],
            ...payload
          });
        case 'handle-drop':
          return {
            success: true,
            files: (payload || []).filter((filePath) => typeof filePath === 'string' && fs.existsSync(filePath))
          };
        case 'get-info': {
          if (!payload || !fs.existsSync(payload)) {
            return { success: false, error: '文件不存在' };
          }

          const stats = fs.statSync(payload);
          return {
            success: true,
            path: payload,
            name: path.basename(payload),
            size: stats.size,
            isDirectory: stats.isDirectory(),
            extension: path.extname(payload).toLowerCase()
          };
        }
        case 'validate-type': {
          const extension = path.extname(payload || '').toLowerCase();
          return {
            success: true,
            valid: supportedAudioExtensions.has(extension),
            extension
          };
        }
        default:
          return { success: false, error: `未知文件导入操作: ${action}` };
      }
    } catch (error) {
      return { success: false, error: error.message };
    }
  });

  ipcMain.handle('notification-show', async (event, { title, body, options } = {}) => {
    try {
      const notification = new Notification({
        title: title || 'SoundBot',
        body: body || '',
        ...options
      });
      notification.show();
      return { success: true };
    } catch (error) {
      return { success: false, error: error.message };
    }
  });

  // 获取路径
  ipcMain.handle('get-paths', () => {
    return {
      appRoot: getAppRootDir(),
      userData: getUserDataDir(),
      models: findModelsDir()
    };
  });

  // 检查资源
  ipcMain.handle('check-resources', () => {
    const modelStatus = checkModels();
    return {
      models: modelStatus.exists,
      modelsPath: modelStatus.path
    };
  });

  // 打开下载页面
  ipcMain.handle('open-download-page', () => {
    shell.openExternal(`https://github.com/${GITHUB_REPO}/releases`);
  });

  // 读取音频文件
  ipcMain.handle('read-audio-file', async (event, filePath) => {
    try {
      if (!filePath || !path.isAbsolute(filePath)) {
        return { success: false, error: '无效的文件路径' };
      }
      if (!fs.existsSync(filePath)) {
        return { success: false, error: '文件不存在' };
      }

      const buffer = fs.readFileSync(filePath);
      return {
        success: true,
        data: Array.from(new Uint8Array(buffer))
      };
    } catch (error) {
      return { success: false, error: error.message };
    }
  });

  // 拖拽文件
  ipcMain.handle('start-drag', async (event, filePath) => {
    try {
      if (!filePath || !fs.existsSync(filePath)) {
        return { success: false, error: '文件不存在' };
      }

      const iconPath = path.join(__dirname, 'assets', 'audio-icon.png');
      const finalIconPath = fs.existsSync(iconPath) ? iconPath : undefined;

      mainWindow.webContents.startDrag({
        file: filePath,
        icon: finalIconPath
      });

      return { success: true };
    } catch (error) {
      return { success: false, error: error.message };
    }
  });

  // 调试信息
  ipcMain.handle('get-backend-status', () => {
    return {
      isRunning: backendProcess !== null,
      pid: backendProcess ? backendProcess.pid : null,
      port: backendPort
    };
  });

  ipcMain.handle('get-app-paths', () => {
    return {
      appRoot: getAppRootDir(),
      userData: getUserDataDir(),
      resourcesPath: process.resourcesPath,
      backendExecutable: getBackendExecutable(),
      modelsPath: findModelsDir()
    };
  });

  ipcMain.handle('open-dev-tools', () => {
    if (mainWindow) {
      mainWindow.webContents.openDevTools();
    }
  });
}

// ==================== 应用生命周期 ====================

// 创建启动窗口（显示加载状态）
let splashWindow = null;

function createSplashWindow() {
  splashWindow = new BrowserWindow({
    width: 400,
    height: 300,
    frame: false,
    alwaysOnTop: true,
    transparent: true,
    backgroundColor: '#0a0a0a',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true
    }
  });

  // 加载简单的启动页面
  const splashHtml = `
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8">
      <style>
        body {
          margin: 0;
          padding: 0;
          width: 400px;
          height: 300px;
          background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          color: #e5e5e5;
        }
        .logo {
          font-size: 28px;
          font-weight: 600;
          margin-bottom: 20px;
          background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
        }
        .status {
          font-size: 14px;
          color: #a3a3a3;
          margin-bottom: 30px;
        }
        .spinner {
          width: 40px;
          height: 40px;
          border: 3px solid rgba(255,255,255,0.1);
          border-top-color: #667eea;
          border-radius: 50%;
          animation: spin 1s linear infinite;
        }
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
        .progress {
          margin-top: 20px;
          font-size: 12px;
          color: #666;
        }
      </style>
    </head>
    <body>
      <div class="logo">SoundBot</div>
      <div class="status" id="status">正在启动服务...</div>
      <div class="spinner"></div>
      <div class="progress" id="progress">初始化中</div>
    </body>
    </html>
  `;
  
  splashWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(splashHtml)}`);
  return splashWindow;
}

// 更新启动窗口状态
function updateSplashStatus(status, progress) {
  if (splashWindow && !splashWindow.isDestroyed()) {
    splashWindow.webContents.executeJavaScript(`
      document.getElementById('status').textContent = '${status}';
      document.getElementById('progress').textContent = '${progress}';
    `).catch(() => {});
  }
}

// 关闭启动窗口
function closeSplashWindow() {
  if (splashWindow && !splashWindow.isDestroyed()) {
    splashWindow.close();
    splashWindow = null;
  }
}

app.whenReady().then(async () => {
  // 创建启动窗口
  createSplashWindow();
  
  // 先启动后端
  updateSplashStatus('正在启动后端服务...', '检查模型文件');
  const result = await ensureBackendStarted();
  
  if (!result.success) {
    console.error('[App] Backend startup failed:', result.error);
    updateSplashStatus('启动失败', result.error || '未知错误');
    
    // 显示错误对话框
    dialog.showErrorBox(
      '启动失败',
      `无法启动后端服务：${result.error || '未知错误'}\n\n请检查：\n1. 模型文件是否正确放置\n2. 端口 8000 是否被占用\n3. 重新安装应用`
    );
    
    closeSplashWindow();
    app.quit();
    return;
  }
  
  updateSplashStatus('服务已就绪', '正在加载界面...');
  
  // 后端启动成功后再创建主窗口
  createWindow();
  
  // 等待主窗口准备好后关闭启动窗口
  mainWindow.once('ready-to-show', () => {
    closeSplashWindow();
    mainWindow.show();
    
    // 通知前端后端已就绪
    mainWindow.webContents.send('backend-ready', { success: true });
    
    if (process.argv.includes('--dev')) {
      mainWindow.webContents.openDevTools();
    }
  });
});

app.on('window-all-closed', async () => {
  await stopBackend();
  app.quit();
});

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }

  if (!backendProcess) {
    ensureBackendStarted().catch((error) => {
      console.error('[App] Backend restart failed:', error);
    });
  }
});

app.on('before-quit', async (event) => {
  event.preventDefault();
  await stopBackend();
  app.exit(0);
});
