const { app, BrowserWindow, Menu, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

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

  // 处理应用设置
  ipcMain.handle('app-settings', async (event, settings) => {
    // 这里可以保存应用设置
    console.log('App settings:', settings);
    return { success: true };
  });

  // 处理文件导入
  ipcMain.handle('file-import', async (event, action, data) => {
    try {
      switch (action) {
        case 'select-audio': {
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

  // 处理后端 API 请求
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

  try {
    // 查找后端路径
    const backendPath = path.join(__dirname, 'backend');
    const mainPy = path.join(backendPath, 'main.py');

    // 检查后端文件是否存在
    if (!fs.existsSync(mainPy)) {
      // 尝试找 main.py 或检查是否有 venv
      const files = fs.readdirSync(backendPath);
      console.log('Backend files:', files);
      return { success: false, error: '后端文件不存在' };
    }

    // 确定 Python 解释器
    const venvPython = path.join(backendPath, 'venv', 'bin', 'python');
    const pythonCmd = fs.existsSync(venvPython) ? venvPython : 'python';

    // 启动后端进程
    backendProcess = spawn(pythonCmd, [mainPy], {
      cwd: backendPath,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      stdio: ['ignore', 'pipe', 'pipe']
    });

    backendProcess.stdout.on('data', (data) => {
      console.log('[Backend]', data.toString());
    });

    backendProcess.stderr.on('data', (data) => {
      console.error('[Backend Error]', data.toString());
    });

    backendProcess.on('error', (error) => {
      console.error('后端进程启动失败:', error);
      backendProcess = null;
    });

    backendProcess.on('exit', (code) => {
      console.log(`后端进程退出，代码: ${code}`);
      backendProcess = null;
    });

    // 等待服务启动
    await new Promise((resolve, reject) => {
      let retries = 0;
      const maxRetries = 30;

      const checkServer = setInterval(() => {
        fetch(`${API_BASE_URL}/health`)
          .then(() => {
            clearInterval(checkServer);
            resolve();
          })
          .catch(() => {
            retries++;
            if (retries >= maxRetries) {
              clearInterval(checkServer);
              reject(new Error('服务启动超时'));
            }
          });
      }, 1000);
    });

    return { success: true, message: '后端服务已启动' };
  } catch (error) {
    console.error('启动后端服务失败:', error);
    return { success: false, error: error.message };
  }
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