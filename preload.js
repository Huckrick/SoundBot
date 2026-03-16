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
  }
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