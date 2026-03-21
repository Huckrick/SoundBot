// SoundBot i18n 国际化模块

const i18n = {
    // 当前语言
    currentLang: 'zh',
    
    // 翻译数据
    translations: {
        zh: {
            // 通用
            'app.name': 'SoundBot',
            'app.title': 'SoundBot - AI 音效管理器',
            'loading': '加载中...',
            'save': '保存',
            'cancel': '取消',
            'confirm': '确认',
            'delete': '删除',
            'edit': '编辑',
            'add': '添加',
            'close': '关闭',
            'search': '搜索',
            'settings': '设置',
            'help': '帮助',
            
            // 语言设置
            'lang.zh': '中文',
            'lang.en': 'English',
            'lang.title': '语言',
            
            // 菜单
            'menu.file': '文件',
            'menu.edit': '编辑',
            'menu.view': '视图',
            'menu.tools': '工具',
            'menu.help': '帮助',
            
            // 文件菜单
            'file.import': '导入',
            'file.import.audio': '导入音频文件',
            'file.import.folder': '导入文件夹',
            'file.export': '导出',
            'file.exit': '退出',
            
            // 编辑菜单
            'edit.select.all': '全选',
            'edit.delete': '删除',
            
            // 视图菜单
            'view.dark.mode': '深色模式',
            'view.light.mode': '浅色模式',
            
            // 工具菜单
            'tools.reindex': '重新索引',
            'tools.ai.search': 'AI 语义搜索',
            'tools.batch.process': '批量处理',
            
            // 搜索
            'search.placeholder': '搜索音效...',
            'search.ai.placeholder': 'AI 语义搜索（输入描述，如：爆炸声）',
            'search.results': '搜索结果',
            'search.no.results': '没有找到匹配的音效',
            'search.loading': '搜索中...',
            
            // AI 搜索
            'ai.search.title': 'AI 智能搜索',
            'ai.search.placeholder': '描述你想要的音效，例如：噼里啪啦的篝火声',
            'ai.search.send': '发送',
            'ai.search.loading': 'AI 思考中...',
            'ai.search.results': 'AI 搜索结果',
            'ai.chat.welcome': '你好！我是 SoundBot AI 助手。\n\n我可以帮你：\n• 用自然语言搜索音效\n• 回答关于音效的问题\n\n试试输入："找个爆炸声" 或 "噼里啪啦的篝火声"',
            'ai.chat.welcome.short': '你好！我是你的音效管理助手。你可以用自然语言描述你需要的音效，比如：',
            'ai.assistant.title': 'AI 助手',
            'ai.example.rain': '"帮我找雨声音效"',
            'ai.example.horror': '"恐怖氛围的"',
            'ai.example.music': '"轻快背景音乐"',
            
            // 播放器
            'player.play': '播放',
            'player.pause': '暂停',
            'player.stop': '停止',
            'player.loop': '循环播放',
            'player.fade.in': '淡入',
            'player.fade.out': '淡出',
            'player.volume': '音量',
            'player.current.time': '当前时间',
            'player.duration': '时长',
            'player.selection.start': '选区起点',
            'player.selection.end': '选区终点',
            'player.play.selection': '播放选区',
            'player.export.selection': '导出选区',
            'player.selection': '选区',
            'player.zoom.in': '放大',
            'player.zoom.out': '缩小',
            'player.zoom.reset': '重置缩放',
            'player.drag.export': '拖拽导出',
            'player.drag.failed': '生成失败',
            
            // 音效列表
            'sound.list.title': '音效列表',
            'sound.list.empty': '暂无音效文件',
            'sound.list.import.hint': '点击"文件"菜单导入音频',
            'sound.name': '名称',
            'sound.duration': '时长',
            'sound.format': '格式',
            'sound.sample.rate': '采样率',
            'sound.bit.depth': '比特深度',
            'sound.channels': '声道',
            'sound.size': '大小',
            'sound.path': '路径',
            'sound.tags': '标签',
            'sound.cover': '封面',
            'sound.waveform': '波形',
            'sound.filename': '文件名',
            'sound.action': '操作',
            
            // 文件夹
            'folder.title': '文件夹',
            'folder.all': '全部文件',
            'folder.uncategorized': '未分类',
            'folder.add': '新建文件夹',
            'folder.rename': '重命名',
            'folder.delete': '删除文件夹',
            'folder.import.here': '导入到此文件夹',
            
            // 分类
            'category.title': '分类',
            
            // 工程管理
            'project.default': '默认工程',
            'project.new': '新建工程',
            'project.switch': '切换工程',
            'project.rename': '重命名工程',
            'project.delete': '删除工程',
            'project.recent': '最近打开',
            
            // 设置
            'settings.title': '设置',
            'settings.general': '常规',
            'settings.ai': 'AI 设置',
            'settings.embedding': '嵌入模型',
            'settings.temp.dir': '临时文件目录',
            'settings.temp.dir.reset': '恢复默认位置',
            'settings.temp.dir.permission': '此目录需要磁盘访问权限，点击重新选择以授权',
            'settings.cache.clear': '清理临时裁切文件',
            
            // AI 设置
            'ai.provider': 'AI 提供商',
            'ai.provider.openai': 'OpenAI',
            'ai.provider.anthropic': 'Anthropic',
            'ai.provider.gemini': 'Google Gemini',
            'ai.provider.custom': '自定义',
            'ai.api.key': 'API 密钥',
            'ai.base.url': '基础 URL',
            'ai.model': '模型',
            'ai.test.connection': '测试连接',
            
            // 嵌入模型设置
            'embedding.provider': '嵌入模型提供商',
            'embedding.provider.local': '本地模型',
            'embedding.provider.openai': 'OpenAI',
            'embedding.local.type': '本地模型类型',
            'embedding.local.url': '本地模型 URL',
            'embedding.local.model': '模型名称',
            
            // 通知
            'notification.success': '成功',
            'notification.error': '错误',
            'notification.warning': '警告',
            'notification.info': '提示',
            
            // 首次启动
            'first.time.title': '欢迎使用 SoundBot',
            'first.time.subtitle': '首次使用设置',
            'first.time.description': '请设置裁切音频文件的临时存放目录。所有裁切后的片段将保存在此目录中，您可以随时在设置中更改。',
            'first.time.select.dir': '选择文件夹',
            'first.time.use.default': '使用默认路径',
            'first.time.confirm': '确认设置',
            'first.time.not.selected': '未选择',
            
            // 错误信息
            'error.load.audio': '加载音频失败',
            'error.play.audio': '播放音频失败',
            'error.import.audio': '导入音频失败',
            'error.delete.audio': '删除音频失败',
            'error.network': '网络错误',
            'error.unknown': '未知错误',
            
            // 确认对话框
            'confirm.delete': '确定要删除吗？',
            'confirm.delete.permanent': '此操作不可恢复，确定要永久删除吗？',
            'confirm.switch.project': '切换工程前请保存当前工作',
            
            // 状态
            'status.ready': '就绪',
            'status.processing': '处理中...',
            'status.scanning': '扫描中...',
            'status.indexing': '索引中...',
            'status.completed': '完成',
            'status.analyzing': '分析波形',
            'status.saving': '保存数据',
        },
        
        en: {
            // General
            'app.name': 'SoundBot',
            'app.title': 'SoundBot - AI Sound Manager',
            'loading': 'Loading...',
            'save': 'Save',
            'cancel': 'Cancel',
            'confirm': 'Confirm',
            'delete': 'Delete',
            'edit': 'Edit',
            'add': 'Add',
            'close': 'Close',
            'search': 'Search',
            'settings': 'Settings',
            'help': 'Help',
            
            // Language settings
            'lang.zh': '中文',
            'lang.en': 'English',
            'lang.title': 'Language',
            
            // Menu
            'menu.file': 'File',
            'menu.edit': 'Edit',
            'menu.view': 'View',
            'menu.tools': 'Tools',
            'menu.help': 'Help',
            
            // File menu
            'file.import': 'Import',
            'file.import.audio': 'Import Audio Files',
            'file.import.folder': 'Import Folder',
            'file.export': 'Export',
            'file.exit': 'Exit',
            
            // Edit menu
            'edit.select.all': 'Select All',
            'edit.delete': 'Delete',
            
            // View menu
            'view.dark.mode': 'Dark Mode',
            'view.light.mode': 'Light Mode',
            
            // Tools menu
            'tools.reindex': 'Reindex',
            'tools.ai.search': 'AI Semantic Search',
            'tools.batch.process': 'Batch Process',
            
            // Search
            'search.placeholder': 'Search sounds...',
            'search.ai.placeholder': 'AI semantic search (describe, e.g., explosion)',
            'search.results': 'Search Results',
            'search.no.results': 'No matching sounds found',
            'search.loading': 'Searching...',
            
            // AI Search
            'ai.search.title': 'AI Smart Search',
            'ai.search.placeholder': 'Describe the sound you want, e.g., crackling campfire',
            'ai.search.send': 'Send',
            'ai.search.loading': 'AI is thinking...',
            'ai.search.results': 'AI Search Results',
            'ai.chat.welcome': 'Hello! I am SoundBot AI Assistant.\n\nI can help you:\n• Search sounds with natural language\n• Answer questions about sounds\n\nTry: "find an explosion sound" or "crackling campfire"',
            'ai.chat.welcome.short': 'Hello! I am your sound management assistant. You can describe the sound you need in natural language, such as:',
            'ai.assistant.title': 'AI Assistant',
            'ai.example.rain': '"Find rain sounds"',
            'ai.example.horror': '"Horror atmosphere"',
            'ai.example.music': '"Upbeat background music"',
            
            // Player
            'player.play': 'Play',
            'player.pause': 'Pause',
            'player.stop': 'Stop',
            'player.loop': 'Loop',
            'player.fade.in': 'Fade In',
            'player.fade.out': 'Fade Out',
            'player.volume': 'Volume',
            'player.current.time': 'Current Time',
            'player.duration': 'Duration',
            'player.selection.start': 'Selection Start',
            'player.selection.end': 'Selection End',
            'player.play.selection': 'Play Selection',
            'player.export.selection': 'Export Selection',
            'player.selection': 'Selection',
            'player.zoom.in': 'Zoom In',
            'player.zoom.out': 'Zoom Out',
            'player.zoom.reset': 'Reset Zoom',
            'player.drag.export': 'Drag to Export',
            'player.drag.failed': 'Generation Failed',
            
            // Sound list
            'sound.list.title': 'Sound List',
            'sound.list.empty': 'No audio files',
            'sound.list.import.hint': 'Click "File" menu to import audio',
            'sound.name': 'Name',
            'sound.duration': 'Duration',
            'sound.format': 'Format',
            'sound.sample.rate': 'Sample Rate',
            'sound.bit.depth': 'Bit Depth',
            'sound.channels': 'Channels',
            'sound.size': 'Size',
            'sound.path': 'Path',
            'sound.tags': 'Tags',
            'sound.cover': 'Cover',
            'sound.waveform': 'Waveform',
            'sound.filename': 'Filename',
            'sound.action': 'Action',
            
            // Folders
            'folder.title': 'Folders',
            'folder.all': 'All Files',
            'folder.uncategorized': 'Uncategorized',
            'folder.add': 'New Folder',
            'folder.rename': 'Rename',
            'folder.delete': 'Delete Folder',
            'folder.import.here': 'Import to this folder',
            
            // Category
            'category.title': 'Categories',
            
            // Project management
            'project.default': 'Default Project',
            'project.new': 'New Project',
            'project.switch': 'Switch Project',
            'project.rename': 'Rename Project',
            'project.delete': 'Delete Project',
            'project.recent': 'Recent',
            
            // Settings
            'settings.title': 'Settings',
            'settings.general': 'General',
            'settings.ai': 'AI Settings',
            'settings.embedding': 'Embedding Model',
            'settings.temp.dir': 'Temp Directory',
            'settings.temp.dir.reset': 'Reset to Default',
            'settings.temp.dir.permission': 'This directory requires disk access permission. Click to reselect and authorize.',
            'settings.cache.clear': 'Clear Temp Clips',
            
            // AI Settings
            'ai.provider': 'AI Provider',
            'ai.provider.openai': 'OpenAI',
            'ai.provider.anthropic': 'Anthropic',
            'ai.provider.gemini': 'Google Gemini',
            'ai.provider.custom': 'Custom',
            'ai.api.key': 'API Key',
            'ai.base.url': 'Base URL',
            'ai.model': 'Model',
            'ai.test.connection': 'Test Connection',
            
            // Embedding settings
            'embedding.provider': 'Embedding Provider',
            'embedding.provider.local': 'Local Model',
            'embedding.provider.openai': 'OpenAI',
            'embedding.local.type': 'Local Model Type',
            'embedding.local.url': 'Local Model URL',
            'embedding.local.model': 'Model Name',
            
            // Notifications
            'notification.success': 'Success',
            'notification.error': 'Error',
            'notification.warning': 'Warning',
            'notification.info': 'Info',
            
            // First time setup
            'first.time.title': 'Welcome to SoundBot',
            'first.time.subtitle': 'First Time Setup',
            'first.time.description': 'Please set a temporary directory for audio clips. All clipped segments will be saved here, and you can change it later in settings.',
            'first.time.select.dir': 'Select Folder',
            'first.time.use.default': 'Use Default Path',
            'first.time.confirm': 'Confirm Settings',
            'first.time.not.selected': 'Not Selected',
            
            // Error messages
            'error.load.audio': 'Failed to load audio',
            'error.play.audio': 'Failed to play audio',
            'error.import.audio': 'Failed to import audio',
            'error.delete.audio': 'Failed to delete audio',
            'error.network': 'Network error',
            'error.unknown': 'Unknown error',
            
            // Confirm dialogs
            'confirm.delete': 'Are you sure you want to delete?',
            'confirm.delete.permanent': 'This action cannot be undone. Are you sure?',
            'confirm.switch.project': 'Please save your work before switching projects',
            
            // Status
            'status.ready': 'Ready',
            'status.processing': 'Processing...',
            'status.scanning': 'Scanning...',
            'status.indexing': 'Indexing...',
            'status.completed': 'Completed',
            'status.analyzing': 'Analyzing Waveform',
            'status.saving': 'Saving Data',
        }
    },
    
    // 初始化
    init() {
        // 从 localStorage 加载语言设置
        const savedLang = localStorage.getItem('soundbot_language');
        if (savedLang && this.translations[savedLang]) {
            this.currentLang = savedLang;
        }
        this.updatePageLang();
        return this;
    },
    
    // 切换语言
    setLang(lang) {
        if (this.translations[lang]) {
            this.currentLang = lang;
            localStorage.setItem('soundbot_language', lang);
            this.updatePageLang();
            this.updateAllText();
            return true;
        }
        return false;
    },
    
    // 获取翻译
    t(key, replacements = {}) {
        const text = this.translations[this.currentLang]?.[key] || 
                     this.translations['en']?.[key] || 
                     key;
        
        // 替换变量
        return text.replace(/\{\{(\w+)\}\}/g, (match, name) => {
            return replacements[name] !== undefined ? replacements[name] : match;
        });
    },
    
    // 更新页面 lang 属性
    updatePageLang() {
        document.documentElement.lang = this.currentLang === 'zh' ? 'zh-CN' : 'en';
    },
    
    // 更新所有带有 data-i18n 属性的元素
    updateAllText() {
        // 处理 data-i18n 属性（文本内容）
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.dataset.i18n;
            const text = this.t(key);
            
            if (el.tagName === 'INPUT' && el.type === 'text') {
                el.placeholder = text;
            } else if (el.tagName === 'INPUT') {
                el.value = text;
            } else {
                el.textContent = text;
            }
        });
        
        // 处理 data-i18n-placeholder 属性（placeholder）
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.dataset.i18nPlaceholder;
            const text = this.t(key);
            el.placeholder = text;
        });
        
        // 处理 data-i18n-title 属性（title/tooltip）
        document.querySelectorAll('[data-i18n-title]').forEach(el => {
            const key = el.dataset.i18nTitle;
            const text = this.t(key);
            el.title = text;
        });
        
        // 更新页面标题
        document.title = this.t('app.title');
        
        // 触发语言切换事件
        window.dispatchEvent(new CustomEvent('languageChanged', { detail: { lang: this.currentLang } }));
    }
};

// 全局翻译函数
function t(key, replacements) {
    return i18n.t(key, replacements);
}

// 初始化
i18n.init();
