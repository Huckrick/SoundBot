# SoundMind - AI 音效管理器桌面版

这是一个基于 Electron 的音效管理器桌面应用，将您的网页版音效管理器转换为原生桌面应用。

## 项目结构

```
SoundMind/
├── package.json          # Electron 项目配置
├── main.js               # Electron 主进程
├── preload.js            # 安全桥接脚本
├── index.html            # 主界面文件
├── assets/               # 静态资源目录
│   └── style.css         # 补充样式文件
├── README.md             # 项目说明
└── test-app.js           # 测试应用（可选）
```

## 功能特性

- ✅ **原生桌面体验** - 窗口化应用，支持最小化、最大化、关闭
- ✅ **深色主题支持** - 完美匹配原网页的深色主题
- ✅ **开发者工具** - F12 快捷键打开开发者工具
- ✅ **安全架构** - 使用预加载脚本安全地暴露 API
- ✅ **菜单栏集成** - 完整的文件、编辑、视图菜单
- ✅ **窗口控制** - 支持窗口最小化、最大化、关闭操作
- ✅ **文件操作 API** - 安全的文件读写接口
- ✅ **系统集成** - 通知、对话框、快捷键支持

## 安装和运行

### 1. 安装依赖

```bash
npm install
```

### 2. 运行应用

**开发模式（带开发者工具）:**
```bash
npm run dev
```

**生产模式:**
```bash
npm start
```

### 3. 构建应用

```bash
npm run build
```

## 快捷键

- `F12` - 打开/关闭开发者工具
- `Cmd/Ctrl + N` - 新建项目
- `Cmd/Ctrl + O` - 导入文件
- `Cmd/Ctrl + Q` - 退出应用

## 安全架构

应用采用 Electron 推荐的安全最佳实践：

- **禁用 Node.js 集成** - 防止渲染进程直接访问 Node.js API
- **启用上下文隔离** - 隔离渲染进程和主进程
- **预加载脚本** - 安全地暴露必要的 API 给前端
- **Web 安全** - 启用 CSP 和内容安全策略

## API 接口

### 窗口控制
```javascript
window.electronAPI.windowControl.minimize();
window.electronAPI.windowControl.maximize();
window.electronAPI.windowControl.close();
```

### 文件操作
```javascript
window.electronAPI.fileOperation.openFile(options);
window.electronAPI.fileOperation.saveFile(data, options);
```

### 应用设置
```javascript
window.electronAPI.appSettings.set('theme', 'dark');
window.electronAPI.appSettings.get('theme');
```

### 系统信息
```javascript
console.log(window.electronAPI.systemInfo.platform);
console.log(window.electronAPI.systemInfo.version);
```

## 故障排除

### 常见问题

1. **应用无法启动（SIGTRAP 错误）**
   - 确保 Electron 版本与系统兼容
   - 检查是否有安全软件阻止应用运行
   - 尝试重新安装依赖：`rm -rf node_modules && npm install`

2. **开发者工具无法打开**
   - 确保使用 `npm run dev` 启动开发模式
   - 检查 F12 快捷键是否被其他应用占用

3. **文件操作权限问题**
   - 确保应用有适当的文件系统权限
   - 检查文件路径是否正确

### 开发调试

1. **启用详细日志**
   ```bash
   DEBUG=* npm run dev
   ```

2. **检查预加载脚本**
   - 在开发者工具中检查 `window.electronAPI` 是否可用
   - 验证 API 接口是否正确暴露

3. **主进程调试**
   - 使用 `--inspect` 参数调试主进程
   - 检查控制台输出是否有错误信息

## 自定义配置

### 修改窗口大小
在 `main.js` 中修改 BrowserWindow 配置：

```javascript
const mainWindow = new BrowserWindow({
  width: 1280,    // 宽度
  height: 800,    // 高度
  minWidth: 1280, // 最小宽度
  minHeight: 800  // 最小高度
});
```

### 添加新的 API 接口
在 `preload.js` 中添加新的 API：

```javascript
contextBridge.exposeInMainWorld('electronAPI', {
  // 现有 API...
  
  // 新 API
  myNewFeature: {
    doSomething: () => ipcRenderer.invoke('my-feature', 'do-something')
  }
});
```

然后在 `main.js` 中添加对应的 IPC 处理程序。

## 构建和分发

### 构建应用
```bash
npm run build
```

### 支持的平台
- **macOS** - `.dmg` 安装包
- **Windows** - `.exe` 安装程序
- **Linux** - `.AppImage` 应用镜像

### 构建配置
在 `package.json` 的 `build` 字段中配置构建选项：

```json
{
  "build": {
    "appId": "com.soundmind.app",
    "productName": "SoundMind",
    "directories": {
      "output": "dist"
    }
  }
}
```

## 技术栈

- **Electron** - 桌面应用框架
- **HTML/CSS/JavaScript** - 前端技术
- **Tailwind CSS** - 样式框架
- **Lucide Icons** - 图标库
- **FastAPI** - 后端 API 框架
- **CLAP** - 音频-文本嵌入模型

## 模型引用 / Model Citation

本项目使用 LAION 的 CLAP 模型进行音频语义理解：

**模型**: `laion/larger_clap_general`

**论文**: Wu, Y., Chen, K., Zhang, T., Hui, Y., Berg-Kirkpatrick, T., & Dubnov, S. (2022). Large-scale Contrastive Language-Audio Pretraining with Feature Fusion and Keyword-to-Caption Augmentation. arXiv preprint arXiv:2211.06687.

**BibTeX**:
```bibtex
@misc{wu2022large,
  doi = {10.48550/ARXIV.2211.06687},
  url = {https://arxiv.org/abs/2211.06687},
  author = {Wu, Yusong and Chen, Ke and Zhang, Tianyu and Hui, Yuchen and Berg-Kirkpatrick, Taylor and Dubnov, Shlomo},
  title = {Large-scale Contrastive Language-Audio Pretraining with Feature Fusion and Keyword-to-Caption Augmentation},
  publisher = {arXiv},
  year = {2022}
}
```

**HuggingFace**: https://huggingface.co/laion/larger_clap_general

**许可证 / License**: 
- 模型基于 MIT License 开源，允许商业使用
- 遵循 LAION 的使用条款，请确保合法合规使用
- 详见: https://huggingface.co/laion/larger_clap_general

## 许可证

本项目采用 MIT License 开源许可证

### 第三方组件许可证

| 组件 | 许可证 | 说明 |
|------|--------|------|
| CLAP Model (laion/larger_clap_general) | MIT | 音频-文本嵌入模型 |
| Electron | MIT | 桌面应用框架 |
| FastAPI | MIT | 后端 API 框架 |
| Transformers | Apache 2.0 | HuggingFace 模型库 |
| ChromaDB | Apache 2.0 | 向量数据库 |

**免责声明**: 
- 本软件仅供学习和研究使用
- 使用 CLAP 模型时请遵守 LAION 的使用条款
- 用户需自行承担使用本软件的风险和责任