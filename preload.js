const { contextBridge, ipcRenderer } = require('electron');

// 安全地暴露 API 给前端
contextBridge.exposeInMainWorld('electronAPI', {
  // 窗口控制
  windowControl: {
    minimize: () => ipcRenderer.invoke('window-control', 'minimize'),
    maximize: () => ipcRenderer.invoke('window-control', 'maximize'),
    close: () => ipcRenderer.invoke('window-control', 'close'),
    isMaximized: () => ipcRenderer.invoke('window-control', 'isMaximized')
  },

  // 文件操作
  fileOperation: {
    openFile: (options) => ipcRenderer.invoke('file-operation', 'open', options),
    saveFile: (data, options) => ipcRenderer.invoke('file-operation', 'save', { data, options }),
    importFiles: (options) => ipcRenderer.invoke('file-operation', 'import', options),
    exportProject: (data, options) => ipcRenderer.invoke('file-operation', 'export', { data, options })
  },

  // 文件导入（专门为导入按钮设计）
  fileImport: {
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

  // 应用设置
  appSettings: {
    get: (key) => ipcRenderer.invoke('app-settings', { action: 'get', key }),
    set: (key, value) => ipcRenderer.invoke('app-settings', { action: 'set', key, value }),
    getAll: () => ipcRenderer.invoke('app-settings', { action: 'getAll' })
  },

  // 系统信息
  systemInfo: {
    platform: process.platform,
    version: process.versions.electron,
    isDev: process.env.NODE_ENV === 'development'
  },

  // 菜单事件监听
  onMenuEvent: (callback) => {
    ipcRenderer.on('menu-new-project', callback);
    ipcRenderer.on('menu-import-file', callback);
  },

  // 移除事件监听器
  removeAllListeners: (channel) => {
    ipcRenderer.removeAllListeners(channel);
  },

  // 音频文件处理（增强功能）
  audioProcessing: {
    analyzeFile: (filePath) => ipcRenderer.invoke('audio-processing', 'analyze', filePath),
    convertFormat: (filePath, format) => ipcRenderer.invoke('audio-processing', 'convert', { filePath, format }),
    extractMetadata: (filePath) => ipcRenderer.invoke('audio-processing', 'metadata', filePath)
  },

  // 项目管理
  projectManager: {
    createProject: (name, settings) => ipcRenderer.invoke('project-manager', 'create', { name, settings }),
    openProject: (path) => ipcRenderer.invoke('project-manager', 'open', path),
    saveProject: (data) => ipcRenderer.invoke('project-manager', 'save', data),
    closeProject: () => ipcRenderer.invoke('project-manager', 'close')
  },

  // 主题管理
  themeManager: {
    getTheme: () => ipcRenderer.invoke('theme-manager', 'get'),
    setTheme: (theme) => ipcRenderer.invoke('theme-manager', 'set', theme),
    toggleTheme: () => ipcRenderer.invoke('theme-manager', 'toggle')
  },

  // 快捷键管理
  shortcuts: {
    register: (accelerator, callback) => {
      const id = ipcRenderer.invoke('shortcuts-register', accelerator);
      ipcRenderer.on(`shortcut-${id}`, callback);
      return id;
    },
    unregister: (id) => ipcRenderer.invoke('shortcuts-unregister', id)
  },

  // 对话框
  dialogs: {
    showMessageBox: (options) => ipcRenderer.invoke('dialog-message', options),
    showOpenDialog: (options) => ipcRenderer.invoke('dialog-open', options),
    showSaveDialog: (options) => ipcRenderer.invoke('dialog-save', options)
  },

  // 通知
  notifications: {
    show: (title, body, options) => ipcRenderer.invoke('notification-show', { title, body, options })
  },

  // 原生拖拽导出（用于拖拽文件到 DAW）
  startDrag: (filePath) => ipcRenderer.invoke('start-drag', filePath),

  // 读取本地音频文件并返回 ArrayBuffer（用于播放）
  readAudioFile: (filePath) => ipcRenderer.invoke('read-audio-file', filePath),

    // 后端 API（与 FastAPI 服务通信）
    backendAPI: {
        // 健康检查
        healthCheck: () => ipcRenderer.invoke('backend-api', 'health'),

        // 扫描并索引音频文件夹
        scanFolder: (folderPath, recursive = true) =>
          ipcRenderer.invoke('backend-api', 'scan', { folderPath, recursive }),

        // 仅扫描文件不建索引（用于没有模型的情况）
        scanOnly: (folderPath, recursive = true) =>
          ipcRenderer.invoke('backend-api', 'scan-only', { folderPath, recursive }),

        // 异步导入文件夹（带进度推送）
        importFolderAsync: (folderPath, recursive = true, clientId = 'default') =>
          ipcRenderer.invoke('backend-api', 'import-async', { folderPath, recursive, clientId }),

        // 语义搜索音频
        searchAudio: (query, topK = 1000, threshold = 0.15) =>
          ipcRenderer.invoke('backend-api', 'search', { query, topK, threshold }),

        // 获取索引状态
        getIndexStatus: () => ipcRenderer.invoke('backend-api', 'index-status'),

        // 获取已索引的文件列表
        getIndexedFiles: () => ipcRenderer.invoke('backend-api', 'indexed-files'),

        // 从 SQLite 获取所有文件（启动时加载）
        getAllDbFiles: () => ipcRenderer.invoke('backend-api', 'db-files'),

        // 获取单个文件详情
        getDbFile: (path) => ipcRenderer.invoke('backend-api', 'db-file', path),

        // 更新文件标签
        updateFileTags: (path, tags) => ipcRenderer.invoke('backend-api', 'db-file-tags', { path, tags }),

        // 从数据库删除文件
        deleteDbFile: (path) => ipcRenderer.invoke('backend-api', 'db-file-delete', path),

        // 获取数据库统计
        getDbStats: () => ipcRenderer.invoke('backend-api', 'db-stats'),

        // 获取音频文件 URL
        getAudioUrl: (filePath) => ipcRenderer.invoke('backend-api', 'audio-url', filePath),

        // 启动后端服务
        startServer: () => ipcRenderer.invoke('backend-api', 'start-server'),

        // 停止后端服务
        stopServer: () => ipcRenderer.invoke('backend-api', 'stop-server'),

        // 获取音频波形数据
        getWaveform: (filePath) => ipcRenderer.invoke('backend-api', 'waveform', filePath),

        // 预加载音频到 LRU 缓存（用于加速后续播放）
        preloadAudio: (filePath) => ipcRenderer.invoke('backend-api', 'audio-preload', filePath),

        // 获取已解码的音频数据（使用 LRU 缓存）
        getDecodedAudio: (filePath) => ipcRenderer.invoke('backend-api', 'audio-decoded', filePath),

        // 从 LRU 缓存流式获取音频（WAV 格式，用于前端播放）
        streamAudio: (filePath) => ipcRenderer.invoke('backend-api', 'audio-stream', filePath),

        // 裁切音频片段
        exportClip: (filePath, start, end, tempFile = true) => ipcRenderer.invoke('backend-api', 'export-clip', { filePath, start, end, tempFile }),

        // 音频淡入淡出
        applyFade: (filePath, fadeIn, fadeOut) => ipcRenderer.invoke('backend-api', 'audio-fade', { filePath, fadeIn, fadeOut }),

        // 获取临时文件目录
        getTempDir: () => ipcRenderer.invoke('backend-api', 'get-temp-dir'),

        // 设置临时文件目录
        setTempDir: (tempDir) => ipcRenderer.invoke('backend-api', 'set-temp-dir', { tempDir }),

        // 获取磁盘空间信息
        getDiskSpace: () => ipcRenderer.invoke('backend-api', 'disk-space'),

        // 清理临时裁切文件
        clearTempClips: () => ipcRenderer.invoke('backend-api', 'clear-temp-clips')
    },

  // 平台信息
  platform: process.platform,

  // 获取应用路径
  getAppPath: () => ipcRenderer.invoke('get-app-path'),

  // 检查完全磁盘访问权限（macOS）
  checkFullDiskAccess: () => ipcRenderer.invoke('check-full-disk-access'),

  // 打开隐私设置（macOS）
  openPrivacySettings: () => ipcRenderer.invoke('open-privacy-settings')
});

// 暴露一些常用的 Node.js 功能（安全版本）
contextBridge.exposeInMainWorld('nodeAPI', {
  path: {
    basename: (path) => require('path').basename(path),
    dirname: (path) => require('path').dirname(path),
    extname: (path) => require('path').extname(path),
    join: (...paths) => require('path').join(...paths)
  },
  fs: {
    readFile: (path, encoding) => {
      return new Promise((resolve, reject) => {
        require('fs').readFile(path, encoding, (err, data) => {
          if (err) reject(err);
          else resolve(data);
        });
      });
    },
    exists: (path) => {
      return new Promise((resolve) => {
        require('fs').access(path, (err) => {
          resolve(!err);
        });
      });
    }
  },
  os: {
    homedir: () => require('os').homedir(),
    tmpdir: () => require('os').tmpdir()
  }
});

// 为前端提供一些工具函数
contextBridge.exposeInMainWorld('utils', {
  // 深拷贝对象
  deepClone: (obj) => {
    if (obj === null || typeof obj !== 'object') return obj;
    if (obj instanceof Date) return new Date(obj.getTime());
    if (obj instanceof Array) return obj.map(item => utils.deepClone(item));
    
    const cloned = {};
    for (const key in obj) {
      if (obj.hasOwnProperty(key)) {
        cloned[key] = utils.deepClone(obj[key]);
      }
    }
    return cloned;
  },

  // 防抖函数
  debounce: (func, wait) => {
    let timeout;
    return function executedFunction(...args) {
      const later = () => {
        clearTimeout(timeout);
        func(...args);
      };
      clearTimeout(timeout);
      timeout = setTimeout(later, wait);
    };
  },

  // 节流函数
  throttle: (func, limit) => {
    let inThrottle;
    return function(...args) {
      if (!inThrottle) {
        func.apply(this, args);
        inThrottle = true;
        setTimeout(() => inThrottle = false, limit);
      }
    };
  },

  // 格式化文件大小
  formatFileSize: (bytes) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  },

  // 格式化时间
  formatTime: (seconds) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  }
});

// 控制台日志（仅在开发模式下）
if (process.env.NODE_ENV === 'development') {
  contextBridge.exposeInMainWorld('debug', {
    log: (...args) => console.log('[Electron]', ...args),
    warn: (...args) => console.warn('[Electron]', ...args),
    error: (...args) => console.error('[Electron]', ...args)
  });
}