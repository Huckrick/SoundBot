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

// 自定义协议：用于在渲染进程中安全加载本地音频
const AUDIO_PROTOCOL = 'soundmind-audio';

// 必须在 app.ready 之前调用
protocol.registerSchemesAsPrivileged([
  { scheme: AUDIO_PROTOCOL, privileges: { standard: true, secure: true, supportFetchAPI: true } }
]);

let mainWindow;
let backendProcess = null;
const BACKEND_PORT = 8000;
const API_BASE_URL = `http://127.0.0.1:${BACKEND_PORT}/api/v1`;

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
    // 1. 生产环境 - afterPack 复制后的路径
    path.join(process.resourcesPath, 'backend', 'soundbot-backend', exeName),

    // 2. 生产环境 - extraResources 路径
    path.join(process.resourcesPath, 'backend', 'soundbot-backend', exeName),

    // 3. 开发环境
    path.join(__dirname, 'dist', 'backend', 'soundbot-backend', exeName),
    path.join(__dirname, 'backend', 'dist', 'backend', 'soundbot-backend', exeName),

    // 4. 应用目录（便携模式）
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
    'lib',
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

  if (foundItems.length === 0) {
    console.error('[Backend] ✗ No required items found in backend directory');
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
 * 启动后端服务
 */
async function startBackend() {
  if (backendProcess) {
    console.log('[Backend] Backend service already running');
    return { success: true };
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
    dialog.showErrorBox('错误', '未找到后端可执行文件，请重新安装应用');
    return { success: false, error: '未找到后端可执行文件' };
  }

  // 验证后端目录完整性
  const backendDir = path.dirname(backendExe);
  if (!verifyBackendIntegrity(backendDir)) {
    dialog.showErrorBox('错误', '后端文件不完整，请重新安装应用');
    return { success: false, error: '后端文件不完整' };
  }

  // 设置环境变量
  const env = {
    ...process.env,
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
    });

    backendProcess.on('exit', (code, signal) => {
      console.log(`[Backend] Process exited, code: ${code}, signal: ${signal}`);
      backendProcess = null;
    });

    // 等待服务启动
    return new Promise((resolve) => {
      let retries = 0;
      const maxRetries = 60; // 60秒超时

      const interval = setInterval(async () => {
        try {
          const res = await fetch(`${API_BASE_URL}/health`);
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
          "script-src 'self' 'unsafe-inline' https://unpkg.com; " +
          "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; " +
          "font-src 'self' https://fonts.gstatic.com; " +
          "img-src 'self' data: blob:; " +
          "media-src 'self' blob: soundmind-audio:; " +
          "connect-src 'self' http://127.0.0.1:8000 ws://127.0.0.1:8000 " +
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

  // 窗口准备好后显示
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();

    if (process.argv.includes('--dev')) {
      mainWindow.webContents.openDevTools();
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  createMenu();
  setupIpcHandlers();
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
      const url = `${API_BASE_URL}/${action}`;
      const options = {
        method: data?.method || 'GET',
        headers: { 'Content-Type': 'application/json' }
      };

      if (data?.body) {
        options.body = JSON.stringify(data.body);
      }

      const response = await fetch(url, options);
      return await response.json();
    } catch (error) {
      console.error('[IPC] Backend API error:', error);
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
}

// ==================== 应用生命周期 ====================

app.whenReady().then(async () => {
  createWindow();

  // 启动后端
  const result = await startBackend();
  if (!result.success) {
    console.error('[App] Backend startup failed:', result.error);
  }
});

app.on('window-all-closed', async () => {
  await stopBackend();
  app.quit();
});

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});

app.on('before-quit', async (event) => {
  event.preventDefault();
  await stopBackend();
  app.exit(0);
});
