const { app, BrowserWindow, Menu, ipcMain, dialog, protocol, net, session, Notification } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

// 自定义协议：用于在渲染进程中安全加载本地音频（避免 file:// 跨源限制）
const AUDIO_PROTOCOL = 'soundmind-audio';

// 必须在 app.ready 之前调用
protocol.registerSchemesAsPrivileged([
  { scheme: AUDIO_PROTOCOL, privileges: { standard: true, secure: true, supportFetchAPI: true } }
]);

let mainWindow;
let backendProcess = null;
const BACKEND_PORT = 8000;
const API_BASE_URL = `http://127.0.0.1:${BACKEND_PORT}/api/v1`;

function createWindow() {
  // 创建浏览器窗口
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 1280,
    minHeight: 800,
    title: 'SoundMind - AI 音效管理器',
    icon: path.join(__dirname, 'assets/icon.png'), // 可选：应用图标
    webPreferences: {
      nodeIntegration: false, // 禁用 Node.js 集成，确保安全
      contextIsolation: true, // 启用上下文隔离
      preload: path.join(__dirname, 'preload.js'), // 预加载脚本
      webSecurity: true, // 启用 Web 安全
      allowRunningInsecureContent: false // 禁止运行不安全内容
    },
    titleBarStyle: 'default',
    show: false, // 先隐藏窗口，等加载完成再显示
    backgroundColor: '#0a0a0a' // 深色主题背景色
  });

  // 加载本地 index.html 文件
  mainWindow.loadFile('index.html');

  // 窗口准备好后显示
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    
    // 开发模式下自动打开开发者工具
    if (process.argv.includes('--dev')) {
      mainWindow.webContents.openDevTools();
    }
  });

  // 处理窗口关闭事件
  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // 设置菜单栏（可选，简化菜单）
  createMenu();

  // 处理来自渲染进程的消息
  setupIpcHandlers();
}

function createMenu() {
  const template = [
    {
      label: '文件',
      submenu: [
        {
          label: '新建项目',
          accelerator: 'CmdOrCtrl+N',
          click: () => {
            mainWindow.webContents.send('menu-new-project');
          }
        },
        {
          label: '导入文件',
          accelerator: 'CmdOrCtrl+O',
          click: () => {
            mainWindow.webContents.send('menu-import-file');
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
    },
    {
      label: '窗口',
      submenu: [
        { role: 'minimize' },
        { role: 'close' }
      ]
    }
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

function setupIpcHandlers() {
  // 处理窗口控制消息
  ipcMain.handle('window-control', (event, action) => {
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

  // 处理文件操作
  ipcMain.handle('file-operation', async (event, operation, data) => {
    // 这里可以添加文件操作逻辑
    console.log('File operation:', operation, data);
    return { success: true };
  });

  // 处理对话框
  ipcMain.handle('dialog-open', async (event, options) => {
    const result = await dialog.showOpenDialog(mainWindow, options);
    return result;
  });

  ipcMain.handle('dialog-save', async (event, options) => {
    const result = await dialog.showSaveDialog(mainWindow, options);
    return result;
  });

  ipcMain.handle('dialog-message', async (event, options) => {
    const result = await dialog.showMessageBox(mainWindow, options);
    return result;
  });

  // 处理应用设置
  ipcMain.handle('app-settings', async (event, settings) => {
    // 这里可以保存应用设置
    console.log('App settings:', settings);
    return { success: true };
  });

  // 获取应用路径
  ipcMain.handle('get-app-path', () => {
    return app.getAppPath();
  });

  // 检查完全磁盘访问权限（macOS）
  ipcMain.handle('check-full-disk-access', () => {
    if (process.platform !== 'darwin') {
      return true; // 非 macOS 系统直接返回 true
    }
    // macOS 上无法直接检查权限，返回 null 让前端决定是否提示
    return null;
  });

  // 打开隐私设置（macOS）
  ipcMain.handle('open-privacy-settings', () => {
    if (process.platform === 'darwin') {
      // 打开系统偏好设置中的安全性与隐私
      const { shell } = require('electron');
      shell.openExternal('x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles');
      return { success: true };
    }
    return { success: false, error: '仅支持 macOS' };
  });

  // 处理文件导入
  ipcMain.handle('file-import', async (event, action, data) => {
    console.log('[IPC] file-import 收到请求:', action, data);
    try {
      switch (action) {
        case 'select-audio': {
          console.log('[IPC] 打开文件选择对话框...');
          if (!mainWindow) {
            console.error('[IPC] mainWindow 为空');
            return { success: false, error: '主窗口未初始化' };
          }
          if (!dialog) {
            console.error('[IPC] dialog 为空');
            return { success: false, error: 'dialog 未定义' };
          }
          const result = await dialog.showOpenDialog(mainWindow, {
            title: '选择音频文件',
            filters: [
              { name: '音频文件', extensions: ['wav', 'mp3', 'aac', 'flac', 'ogg', 'm4a'] },
              { name: '所有文件', extensions: ['*'] }
            ],
            properties: ['openFile', 'multiSelections']
          });

          if (!result.canceled && result.filePaths.length > 0) {
            const fileInfo = await Promise.all(
              result.filePaths.map(async (filePath) => {
                const stats = fs.statSync(filePath);
                return {
                  path: filePath,
                  name: path.basename(filePath),
                  size: stats.size,
                  type: path.extname(filePath).toLowerCase(),
                  lastModified: stats.mtime
                };
              })
            );

            // 发送文件选择结果到渲染进程
            mainWindow.webContents.send('files-selected', fileInfo);
            return { success: true, files: fileInfo };
          }
          return { success: false, canceled: true };
        }

        case 'select-folder': {
          const result = await dialog.showOpenDialog(mainWindow, {
            title: '选择文件夹',
            properties: ['openDirectory']
          });

          if (!result.canceled && result.filePaths.length > 0) {
            return { success: true, folder: result.filePaths[0] };
          }
          return { success: false, canceled: true };
        }

        case 'validate-type': {
          const supportedTypes = ['.wav', '.mp3', '.aac', '.flac', '.ogg', '.m4a'];
          const ext = path.extname(data).toLowerCase();
          return { success: supportedTypes.includes(ext), type: ext };
        }

        default:
          return { success: false, error: '未知操作' };
      }
    } catch (error) {
      console.error('文件导入错误:', error);
      return { success: false, error: error.message };
    }
  });

  // 读取本地音频文件（返回 ArrayBuffer，用于前端播放）
  ipcMain.handle('read-audio-file', async (event, filePath) => {
    console.log('[IPC] read-audio-file called with:', filePath);
    try {
      if (!filePath || !path.isAbsolute(filePath)) {
        console.error('[IPC] Invalid path:', filePath);
        return { success: false, error: '无效的文件路径' };
      }
      if (!fs.existsSync(filePath)) {
        console.error('[IPC] File not exists:', filePath);
        return { success: false, error: '文件不存在' };
      }
      const buffer = fs.readFileSync(filePath);
      // 返回 ArrayBuffer（通过 buffer.buffer.slice 转换）
      const arrayBuffer = buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
      const result = { success: true, data: Array.from(new Uint8Array(arrayBuffer)) };
      console.log('[IPC] File read success, size:', result.data.length);
      return result;
    } catch (error) {
      console.error('[IPC] Read audio file error:', error);
      return { success: false, error: error.message };
    }
  });

  // 处理后端 API 请求
  ipcMain.handle('start-drag', async (event, filePath) => {
    console.log('[IPC] start-drag called with:', filePath);
    try {
      if (!filePath || !fs.existsSync(filePath)) {
        console.error('[IPC] Drag file not exists:', filePath);
        return { success: false, error: '文件不存在' };
      }

      // 使用默认音频图标或创建临时图标
      // 注意：音频文件本身不能作为拖拽图标，需要使用图片格式
      const iconPath = path.join(__dirname, 'assets', 'audio-icon.png');
      
      // 如果图标文件不存在，使用一个默认的空白图标
      const finalIconPath = fs.existsSync(iconPath) ? iconPath : undefined;

      mainWindow.webContents.startDrag({
        file: filePath,
        icon: finalIconPath
      });

      return { success: true };
    } catch (error) {
      console.error('[IPC] start-drag error:', error);
      return { success: false, error: error.message };
    }
  });

  ipcMain.handle('backend-api', async (event, action, data) => {
    try {
      switch (action) {
        case 'health': {
          const response = await fetch(`${API_BASE_URL}/health`);
          return await response.json();
        }

        case 'scan': {
          const response = await fetch(`${API_BASE_URL}/scan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              folder_path: data.folderPath,
              recursive: data.recursive
            })
          });
          return await response.json();
        }

        case 'scan-only': {
          const response = await fetch(`${API_BASE_URL}/scan-only`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              folder_path: data.folderPath,
              recursive: data.recursive
            })
          });
          return await response.json();
        }

        case 'search': {
          const response = await fetch(`${API_BASE_URL}/search`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              query: data.query,
              top_k: data.topK,
              threshold: data.threshold
            })
          });
          return await response.json();
        }

        case 'index-status': {
          const response = await fetch(`${API_BASE_URL}/index/status`);
          return await response.json();
        }

        case 'indexed-files': {
          const response = await fetch(`${API_BASE_URL}/files`);
          return await response.json();
        }

        case 'db-files': {
          // 从 SQLite 获取所有文件
          const response = await fetch(`${API_BASE_URL}/db/files`);
          return await response.json();
        }

        case 'db-file': {
          // 获取单个文件详情
          const encodedPath = encodeURIComponent(data);
          const response = await fetch(`${API_BASE_URL}/db/file/${encodedPath}`);
          return await response.json();
        }

        case 'db-file-tags': {
          // 更新文件标签
          const { path, tags } = data;
          const encodedPath = encodeURIComponent(path);
          const response = await fetch(`${API_BASE_URL}/db/file/${encodedPath}/tags`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(tags)
          });
          return await response.json();
        }

        case 'db-file-delete': {
          // 从数据库删除文件
          const encodedPath = encodeURIComponent(data);
          const response = await fetch(`${API_BASE_URL}/db/file/${encodedPath}`, {
            method: 'DELETE'
          });
          return await response.json();
        }

        case 'db-stats': {
          // 获取数据库统计
          const response = await fetch(`${API_BASE_URL}/db/stats`);
          return await response.json();
        }

        case 'import-async': {
          // 异步导入（带进度推送）
          let url = `${API_BASE_URL}/import/async`;
          if (data.clientId) {
            url += `?client_id=${encodeURIComponent(data.clientId)}`;
          }
          console.log('[Electron] import-async 请求 URL:', url);
          console.log('[Electron] import-async 请求体:', JSON.stringify({
            folder_path: data.folderPath,
            recursive: data.recursive
          }));
          const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              folder_path: data.folderPath,
              recursive: data.recursive
            })
          });
          console.log('[Electron] import-async 响应状态:', response.status);
          const result = await response.json();
          console.log('[Electron] import-async 响应内容:', result);
          return result;
        }

        case 'audio-url': {
          // 返回音频文件的 API URL
          const encodedPath = encodeURIComponent(data);
          return `${API_BASE_URL}/audio/${encodedPath}`;
        }

        case 'start-server': {
          return await startBackendServer();
        }

        case 'stop-server': {
          return await stopBackendServer();
        }

        case 'waveform': {
          // 获取音频波形数据
          const response = await fetch(`http://127.0.0.1:${BACKEND_PORT}/api/waveform?path=${encodeURIComponent(data)}`);
          return await response.json();
        }

        case 'export-clip': {
          // 裁切音频
          const { filePath, start, end, tempFile } = data;
          console.log('[Electron] export-clip:', { filePath, start, end, tempFile });

          try {
            // 强制 temp_file 为 true，确保使用临时目录
            const requestBody = {
              path: filePath,
              start: start,
              end: end,
              temp_file: true  // 强制使用临时目录
            };
            console.log('[Electron] export-clip request:', JSON.stringify(requestBody));

            const clipResponse = await fetch(`http://127.0.0.1:${BACKEND_PORT}/api/export/clip`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(requestBody)
            });
            
            if (!clipResponse.ok) {
              const errorData = await clipResponse.json();
              console.error('[Electron] export-clip HTTP error:', clipResponse.status, errorData);
              return { success: false, error: errorData.detail || `HTTP ${clipResponse.status}` };
            }
            
            const result = await clipResponse.json();
            console.log('[Electron] export-clip result:', result);
            return result;
          } catch (error) {
            console.error('[Electron] export-clip error:', error);
            return { success: false, error: error.message };
          }
        }

        case 'audio-fade': {
          // 音频淡入淡出
          const { filePath, fadeIn, fadeOut } = data;
          const fadeResponse = await fetch(`http://127.0.0.1:${BACKEND_PORT}/api/audio/fade`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: filePath, fade_in: fadeIn, fade_out: fadeOut })
          });
          return await fadeResponse.json();
        }

        case 'get-temp-dir': {
          // 获取临时文件目录
          try {
            const response = await fetch(`http://127.0.0.1:${BACKEND_PORT}/api/v1/config/temp-dir`);
            return await response.json();
          } catch (error) {
            console.error('[IPC] get-temp-dir error:', error);
            return { success: false, error: error.message };
          }
        }

        case 'set-temp-dir': {
          // 设置临时文件目录
          try {
            const { tempDir } = data;
            const response = await fetch(`http://127.0.0.1:${BACKEND_PORT}/api/v1/config/temp-dir`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ temp_dir: tempDir })
            });
            return await response.json();
          } catch (error) {
            console.error('[IPC] set-temp-dir error:', error);
            return { success: false, error: error.message };
          }
        }

        case 'disk-space': {
          // 获取磁盘空间信息
          try {
            const response = await fetch(`http://127.0.0.1:${BACKEND_PORT}/api/v1/disk-space`);
            return await response.json();
          } catch (error) {
            console.error('[IPC] disk-space error:', error);
            return { success: false, error: error.message };
          }
        }

        case 'clear-temp-clips': {
          // 清理临时裁切文件
          try {
            const response = await fetch(`http://127.0.0.1:${BACKEND_PORT}/api/v1/temp-clips/clear`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' }
            });
            return await response.json();
          } catch (error) {
            console.error('[IPC] clear-temp-clips error:', error);
            return { success: false, error: error.message };
          }
        }

        case 'audio-processing': {
          console.log('[IPC] audio-processing:', data.action);
          switch (data.action) {
            case 'analyze': {
              try {
                const fs = require('fs');
                const pathModule = require('path');
                const filePath = data.filePath;
                if (!fs.existsSync(filePath)) {
                  return { success: false, error: '文件不存在' };
                }
                const stats = fs.statSync(filePath);
                return {
                  success: true,
                  data: {
                    path: filePath,
                    name: pathModule.basename(filePath),
                    size: stats.size,
                    created: stats.birthtime,
                    modified: stats.mtime,
                    format: pathModule.extname(filePath).toLowerCase()
                  }
                };
              } catch (error) {
                return { success: false, error: error.message };
              }
            }
            case 'metadata': {
              try {
                const fs = require('fs');
                const pathModule = require('path');
                const filePath = data.filePath;
                if (!fs.existsSync(filePath)) {
                  return { success: false, error: '文件不存在' };
                }
                const stats = fs.statSync(filePath);
                return {
                  success: true,
                  data: {
                    path: filePath,
                    filename: pathModule.basename(filePath),
                    size: stats.size,
                    format: pathModule.extname(filePath).toLowerCase(),
                    lastModified: stats.mtime
                  }
                };
              } catch (error) {
                return { success: false, error: error.message };
              }
            }
            default:
              return { success: false, error: '未知的 audio-processing 操作' };
          }
        }

        case 'project-manager': {
          console.log('[IPC] project-manager:', data.action);
          switch (data.action) {
            case 'create': {
              return { success: true, message: '项目创建功能待实现', data: null };
            }
            case 'open': {
              return { success: true, message: '项目打开功能待实现', data: null };
            }
            case 'save': {
              return { success: true, message: '项目保存功能待实现' };
            }
            case 'close': {
              return { success: true, message: '项目关闭功能待实现' };
            }
            default:
              return { success: false, error: '未知的 project-manager 操作' };
          }
        }

        case 'theme-manager': {
          console.log('[IPC] theme-manager:', data);
          switch (data.action) {
            case 'get': {
              const isDark = document.documentElement.classList.contains('dark');
              return { success: true, theme: isDark ? 'dark' : 'light' };
            }
            case 'set': {
              if (data.theme === 'dark') {
                document.documentElement.classList.add('dark');
              } else {
                document.documentElement.classList.remove('dark');
              }
              return { success: true };
            }
            case 'toggle': {
              document.documentElement.classList.toggle('dark');
              const isDark = document.documentElement.classList.contains('dark');
              return { success: true, theme: isDark ? 'dark' : 'light' };
            }
            default:
              return { success: false, error: '未知的 theme-manager 操作' };
          }
        }

        case 'shortcuts-register': {
          return { success: true, id: Date.now().toString(), message: '快捷键注册功能待实现' };
        }

        case 'shortcuts-unregister': {
          return { success: true, message: '快捷键注销功能待实现' };
        }

        case 'dialog-message': {
          const result = await dialog.showMessageBox(mainWindow, data);
          return result;
        }

        case 'dialog-open': {
          const result = await dialog.showOpenDialog(mainWindow, data);
          return result;
        }

        case 'dialog-save': {
          const result = await dialog.showSaveDialog(mainWindow, data);
          return result;
        }

        case 'notification-show': {
          if (process.platform === 'darwin' && app.dock) {
            const notification = new Notification({
              title: data.title,
              body: data.body
            });
            notification.show();
            return { success: true };
          }
          return { success: false, error: '通知功能在此平台不可用' };
        }

        default:
          return { success: false, error: '未知操作' };
      }
    } catch (error) {
      console.error('后端 API 错误:', error);
      return { success: false, error: error.message };
    }
  });
}

// 启动后端服务器
async function startBackendServer() {
  if (backendProcess) {
    return { success: true, message: '后端服务已在运行' };
  }

  const maxRetries = 3;
  let lastError = null;

  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      console.log(`[Backend] 启动尝试 ${attempt}/${maxRetries}...`);

      // 查找后端路径
      const backendPath = path.join(__dirname, 'backend');
      const mainPy = path.join(backendPath, 'main.py');

      // 检查后端文件是否存在
      if (!fs.existsSync(mainPy)) {
        const files = fs.readdirSync(backendPath);
        console.log('Backend files:', files);
        return { success: false, error: '后端文件不存在' };
      }

      // 优先使用 backend/venv 中的 Python 解释器
      let pythonCmd = 'python';
      const venvPython = path.join(backendPath, 'venv', 'bin', 'python');
      const venvPython3 = path.join(backendPath, 'venv', 'bin', 'python3');

      if (fs.existsSync(venvPython)) {
        pythonCmd = venvPython;
      } else if (fs.existsSync(venvPython3)) {
        pythonCmd = venvPython3;
      } else {
        // 尝试系统 Python3
        pythonCmd = 'python3';
      }

      console.log(`[Backend] 使用 Python: ${pythonCmd}`);

      // 如果有之前的进程残留，先清理
      if (backendProcess) {
        try {
          backendProcess.kill('SIGTERM');
          await new Promise(r => setTimeout(r, 500));
        } catch (e) {}
        backendProcess = null;
      }

      // 启动后端进程
      backendProcess = spawn(pythonCmd, [mainPy], {
        cwd: backendPath,
        env: { ...process.env, PYTHONUNBUFFERED: '1' },
        stdio: ['ignore', 'pipe', 'pipe']
      });

      // 收集启动日志
      let startupOutput = '';
      let errorOutput = '';

      backendProcess.stdout.on('data', (data) => {
        const text = data.toString();
        startupOutput += text;
        console.log('[Backend]', text);
      });

      backendProcess.stderr.on('data', (data) => {
        const text = data.toString();
        errorOutput += text;
        // 只在启动阶段记录错误
        if (attempt <= maxRetries) {
          console.error('[Backend Warning]', text);
        }
      });

      backendProcess.on('error', (error) => {
        console.error('后端进程启动失败:', error);
        lastError = error;
        backendProcess = null;
      });

      backendProcess.on('exit', (code, signal) => {
        console.log(`后端进程退出，代码: ${code}, 信号: ${signal}`);
        if (code !== 0 && code !== null) {
          console.error(`[Backend] 非正常退出，输出: ${startupOutput}`);
          console.error(`[Backend] 错误输出: ${errorOutput}`);
        }
        backendProcess = null;
      });

      // 等待服务启动
      await new Promise((resolve, reject) => {
        let retries = 0;
        const maxHealthChecks = 15;

        const checkServer = setInterval(() => {
          fetch(`${API_BASE_URL}/health`)
            .then((response) => {
              if (response.ok) {
                clearInterval(checkServer);
                console.log('[Backend] 服务健康检查通过');
                resolve();
              } else {
                throw new Error(`健康检查失败: ${response.status}`);
              }
            })
            .catch((err) => {
              retries++;
              console.log(`[Backend] 等待服务启动... (${retries}/${maxHealthChecks})`);
              if (retries >= maxHealthChecks) {
                clearInterval(checkServer);
                reject(new Error(`服务启动超时\n启动输出: ${startupOutput}\n错误输出: ${errorOutput}`));
              }
            });
        }, 1000);
      });

      console.log(`[Backend] 第 ${attempt} 次尝试成功启动`);
      return { success: true, message: '后端服务已启动' };

    } catch (error) {
      lastError = error;
      console.error(`[Backend] 第 ${attempt} 次启动失败:`, error.message);

      // 如果还没达到最大重试次数，尝试清理后等待重试
      if (attempt < maxRetries) {
        console.log('[Backend] 等待 2 秒后重试...');

        // 清理可能残留的进程
        if (backendProcess) {
          try {
            backendProcess.kill('SIGKILL');
          } catch (e) {}
          backendProcess = null;
        }

        // 等待后重试
        await new Promise(r => setTimeout(r, 2000));
      }
    }
  }

  // 所有重试都失败了
  console.error('[Backend] 所有启动尝试均失败');
  return {
    success: false,
    error: `后端服务启动失败，已尝试 ${maxRetries} 次\n最后错误: ${lastError?.message || '未知错误'}\n请检查：\n1. Python 依赖是否已安装 (cd backend && ./venv/bin/pip install -r requirements.txt)\n2. 端口 8000 是否被占用\n3. 数据库路径是否有写入权限`
  };
}

// 停止后端服务器
async function stopBackendServer() {
  if (!backendProcess) {
    return { success: true, message: '后端服务未在运行' };
  }

  return new Promise((resolve) => {
    backendProcess.once('exit', () => {
      backendProcess = null;
      resolve({ success: true, message: '后端服务已停止' });
    });

    backendProcess.kill('SIGTERM');
    setTimeout(() => {
      if (backendProcess) {
        backendProcess.kill('SIGKILL');
      }
      resolve({ success: true, message: '后端服务已强制停止' });
    }, 5000);
  });
}

// 应用准备就绪时创建窗口
app.whenReady().then(async () => {
  // 注册自定义协议到默认 session，使渲染进程可安全加载本地音频
  const defaultSession = session.defaultSession;
  
  defaultSession.protocol.handle(AUDIO_PROTOCOL, (request) => {
    try {
      const u = new URL(request.url);
      // u.pathname 包含开头的 /，如 "/Volumes/Studio%20Hub/..."
      let filePath = decodeURIComponent(u.pathname);
      
      console.log('[soundmind-audio] Request:', request.url, '-> File:', filePath);
      
      if (!filePath || !path.isAbsolute(filePath)) {
        console.error('[soundmind-audio] Invalid path:', filePath);
        return new Response('Invalid path', { status: 400 });
      }
      return net.fetch('file://' + filePath);
    } catch (e) {
      console.error('soundmind-audio protocol error:', e);
      return new Response('Error', { status: 500 });
    }
  });

  createWindow();
  
  // 自动启动后端服务
  try {
    const result = await startBackendServer();
    if (result.success) {
      console.log('后端服务启动成功');
    } else {
      console.warn('后端服务启动失败:', result.error);
    }
  } catch (error) {
    console.error('启动后端服务时出错:', error);
  }
});

// 所有窗口关闭时退出应用（macOS 除外）
app.on('window-all-closed', async () => {
  // 停止后端服务
  if (backendProcess) {
    await stopBackendServer();
  }
  
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  // macOS 上重新创建窗口
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

// 安全设置：阻止新窗口创建
app.on('web-contents-created', (event, contents) => {
  contents.on('new-window', (event, navigationUrl) => {
    event.preventDefault();
    console.log('Blocked new window to:', navigationUrl);
  });
});