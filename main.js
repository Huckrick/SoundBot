/**
 * SoundBot - AI 音效管理器 (PyInstaller 一体化版本)
 * Copyright (C) 2026 Nagisa_Huckrick (胡杨)
 *
 * 前后端一体化打包，无需单独安装 Python 环境
 */

const { app, BrowserWindow, Menu, ipcMain, dialog, protocol, shell, safeStorage } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const net = require('net');
const audioCapabilityManifest = require('./config/audio_capabilities.json');
const supportedAudioExtensions = new Set(Object.keys(audioCapabilityManifest.formats || {}));
const supportedAudioFilterExtensions = [...supportedAudioExtensions].map((value) => value.slice(1));

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
let secureSecretsCache = null;
let backendSecretsHydrated = false;
let audioProtocolRegistered = false;
let rendererBridgeReady = false;
let rendererPreloadFailure = null;
const activeBackendRequests = new Map();
let quitInProgress = false;
let pendingSplashState = {
  status: '正在启动服务…',
  progress: '初始化中',
  isError: false
};
let nativeOpenDialogActive = false;
const BACKEND_ERROR_SENTINEL = '__soundbotBackendError';
const SECRET_KEY_PATTERN = /^(llm|embedding)\.[a-z0-9_-]+\.api_key$/;
const MAIN_LOG_MAX_BYTES = 5 * 1024 * 1024;
let mainLogPath = null;

function redactDiagnosticText(value) {
  return String(value)
    .replace(
      /((?:["']?api[_-]?key["']?|["']?access[_-]?token["']?|["']?secret["']?)\s*[=:]\s*["']?)[^"',;\s}&\]]+/gi,
      '$1[REDACTED]'
    )
    .replace(
      /(["']?authorization["']?\s*[=:]\s*["']?bearer\s+)[^"',;\s}&\]]+/gi,
      '$1[REDACTED]'
    )
    .replace(/\b(sk-[A-Za-z0-9_-]{12,}|github_pat_[A-Za-z0-9_]{20,})\b/g, '[REDACTED]');
}

function formatDiagnosticValue(value) {
  if (value instanceof Error) {
    return `${value.name}: ${value.message}${value.stack ? `\n${value.stack}` : ''}`;
  }
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch (_error) {
    return String(value);
  }
}

function initializeMainLogging() {
  const original = {
    log: console.log.bind(console),
    warn: console.warn.bind(console),
    error: console.error.bind(console)
  };
  try {
    app.setAppLogsPath();
    const logDir = app.getPath('logs');
    fs.mkdirSync(logDir, { recursive: true });
    mainLogPath = path.join(logDir, 'soundbot-main.log');
    if (fs.existsSync(mainLogPath) && fs.statSync(mainLogPath).size > MAIN_LOG_MAX_BYTES) {
      const previous = `${mainLogPath}.1`;
      if (fs.existsSync(previous)) fs.unlinkSync(previous);
      fs.renameSync(mainLogPath, previous);
    }
    const stream = fs.createWriteStream(mainLogPath, { flags: 'a', encoding: 'utf8' });
    for (const level of ['log', 'warn', 'error']) {
      console[level] = (...args) => {
        original[level](...args);
        const message = args.map(formatDiagnosticValue).join(' ');
        stream.write(
          `${new Date().toISOString()} | ${level.toUpperCase()} | ${redactDiagnosticText(message)}\n`
        );
      };
    }
    process.on('uncaughtExceptionMonitor', error => {
      console.error('[Main] Uncaught exception:', error);
    });
    console.log(`[Main] Persistent diagnostics enabled: ${mainLogPath}`);
  } catch (error) {
    original.error('[Main] Failed to initialize persistent diagnostics:', error);
  }
}

// ==================== 路径辅助函数 ====================

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

function getSecureSecretsPath() {
  return path.join(getUserDataDir(), 'secure_secrets.json');
}

function validateSecretKey(key) {
  if (typeof key !== 'string' || !SECRET_KEY_PATTERN.test(key)) {
    throw new Error('无效的密钥标识');
  }
}

function readSecureSecrets() {
  if (secureSecretsCache) return secureSecretsCache;

  try {
    const secretsPath = getSecureSecretsPath();
    if (fs.existsSync(secretsPath)) {
      const parsed = JSON.parse(fs.readFileSync(secretsPath, 'utf-8'));
      secureSecretsCache = parsed && typeof parsed === 'object' ? parsed : {};
      return secureSecretsCache;
    }
  } catch (error) {
    console.warn('[Secrets] Failed to read encrypted secret store:', error);
  }

  secureSecretsCache = {};
  return secureSecretsCache;
}

function writeSecureSecrets(secrets) {
  const secretsPath = getSecureSecretsPath();
  const temporaryPath = `${secretsPath}.tmp-${process.pid}`;
  fs.mkdirSync(getUserDataDir(), { recursive: true });
  fs.writeFileSync(temporaryPath, JSON.stringify(secrets, null, 2), { encoding: 'utf-8', mode: 0o600 });
  fs.renameSync(temporaryPath, secretsPath);
  secureSecretsCache = secrets;
}

function getSafeStorageStatus() {
  const encryptionAvailable = safeStorage.isEncryptionAvailable();
  let backend = null;
  if (encryptionAvailable && typeof safeStorage.getSelectedStorageBackend === 'function') {
    try {
      backend = safeStorage.getSelectedStorageBackend();
    } catch (error) {
      console.warn('[Secrets] Failed to inspect safeStorage backend:', error);
    }
  }
  // Linux's basic_text backend is obfuscation, not OS-backed encryption.  It
  // is outside the supported release targets and must not be advertised as a
  // secure credential store.
  const available = encryptionAvailable && backend !== 'basic_text';
  return { available, backend };
}

function isSecureStorageAvailable() {
  return getSafeStorageStatus().available;
}

function secretKeyFor(scope, provider) {
  const key = `${scope}.${String(provider || '').toLowerCase()}.api_key`;
  validateSecretKey(key);
  return key;
}

function hasEncryptedSecret(key) {
  validateSecretKey(key);
  const secrets = readSecureSecrets();
  return typeof secrets[key] === 'string' && secrets[key].length > 0;
}

function decryptStoredSecret(key) {
  validateSecretKey(key);
  if (!isSecureStorageAvailable()) return null;
  const encoded = readSecureSecrets()[key];
  if (!encoded) return null;
  return safeStorage.decryptString(Buffer.from(encoded, 'base64'));
}

function setStoredSecret(key, value) {
  validateSecretKey(key);
  if (!isSecureStorageAvailable()) {
    const error = new Error('系统安全存储当前不可用');
    error.code = 'ENCRYPTION_UNAVAILABLE';
    throw error;
  }
  if (typeof value !== 'string' || value.length === 0) {
    const error = new Error('密钥值不能为空');
    error.code = 'INVALID_SECRET';
    throw error;
  }
  const secrets = { ...readSecureSecrets() };
  secrets[key] = safeStorage.encryptString(value).toString('base64');
  writeSecureSecrets(secrets);
}

function deleteStoredSecret(key) {
  validateSecretKey(key);
  const secrets = { ...readSecureSecrets() };
  const existed = Object.prototype.hasOwnProperty.call(secrets, key);
  if (existed) {
    delete secrets[key];
    writeSecureSecrets(secrets);
  }
  return existed;
}

function redactLegacyConfigSecrets(configData) {
  const migrated = [];
  const sanitized = JSON.parse(JSON.stringify(configData || {}));
  const llm = sanitized.llm && typeof sanitized.llm === 'object' ? sanitized.llm : {};
  for (const [provider, providerConfig] of Object.entries(llm)) {
    if (!providerConfig || typeof providerConfig !== 'object' || Array.isArray(providerConfig)) continue;
    const value = typeof providerConfig.api_key === 'string' ? providerConfig.api_key : '';
    if (value && value !== '***') {
      migrated.push({ key: secretKeyFor('llm', provider), value });
    }
    delete providerConfig.api_key;
    if (providerConfig.headers && typeof providerConfig.headers === 'object') {
      for (const header of Object.keys(providerConfig.headers)) {
        if (/(authorization|api[-_]?key|access[-_]?token|secret)/i.test(header)) {
          delete providerConfig.headers[header];
        }
      }
    }
  }

  const embedding = sanitized.embedding && typeof sanitized.embedding === 'object'
    ? sanitized.embedding
    : {};
  for (const [provider, providerConfig] of Object.entries(embedding)) {
    if (!providerConfig || typeof providerConfig !== 'object' || Array.isArray(providerConfig)) continue;
    const value = typeof providerConfig.api_key === 'string' ? providerConfig.api_key : '';
    if (value && value !== '***') {
      migrated.push({ key: secretKeyFor('embedding', provider), value });
    }
    delete providerConfig.api_key;
  }
  return { sanitized, migrated };
}

function migrateLegacyPlaintextSecrets() {
  const configPath = path.join(getUserDataDir(), 'ai_config.json');
  if (!fs.existsSync(configPath)) return { success: true, migrated: 0 };
  if (!isSecureStorageAvailable()) {
    console.warn('[Secrets] Legacy plaintext migration pending: OS secure storage is unavailable');
    return { success: false, pending: true, migrated: 0 };
  }

  let parsed;
  try {
    parsed = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
  } catch (error) {
    console.warn('[Secrets] Legacy plaintext migration skipped: config is unreadable');
    return { success: false, pending: true, migrated: 0 };
  }

  const { sanitized, migrated } = redactLegacyConfigSecrets(parsed);
  const hasApiKeyFields = JSON.stringify(parsed) !== JSON.stringify(sanitized);
  if (!hasApiKeyFields) return { success: true, migrated: 0 };

  const originalSecrets = { ...readSecureSecrets() };
  try {
    for (const { key, value } of migrated) {
      if (!hasEncryptedSecret(key)) {
        setStoredSecret(key, value);
      }
    }
    const temporaryPath = `${configPath}.tmp-${process.pid}`;
    fs.writeFileSync(temporaryPath, JSON.stringify(sanitized, null, 2), {
      encoding: 'utf-8',
      mode: 0o600
    });
    fs.renameSync(temporaryPath, configPath);
    console.log(`[Secrets] Migrated ${migrated.length} legacy credential(s) into OS secure storage`);
    return { success: true, migrated: migrated.length };
  } catch (error) {
    // Keep the legacy file untouched and restore the previous secure store so
    // a partial migration never loses or changes the user's credential.
    try {
      writeSecureSecrets(originalSecrets);
    } catch (_) {
    }
    console.warn('[Secrets] Legacy plaintext migration failed; original config was preserved');
    return { success: false, pending: true, migrated: 0 };
  }
}

function toBackendError(error, context = {}) {
  const rawDetails = error && Object.prototype.hasOwnProperty.call(error, 'details')
    ? error.details
    : (error && Object.prototype.hasOwnProperty.call(error, 'detail') ? error.detail : {});
  const details = rawDetails && typeof rawDetails === 'object'
    ? rawDetails
    : (rawDetails == null ? {} : { detail: rawDetails });
  return {
    [BACKEND_ERROR_SENTINEL]: true,
    error: {
      code: error?.code || context.code || 'BACKEND_REQUEST_FAILED',
      message: error?.message || context.message || '后端请求失败',
      retryable: Boolean(error?.retryable ?? context.retryable ?? false),
      details,
      // v0.1 renderer compatibility; new code should use `details`.
      detail: details,
      status: Number.isInteger(error?.status) ? error.status : null,
      action: context.action || null,
      url: context.url || null
    }
  };
}

function registerAudioProtocol() {
  if (audioProtocolRegistered) return;

  protocol.registerFileProtocol(AUDIO_PROTOCOL, (request, callback) => {
    try {
      const parsed = new URL(request.url);
      const encodedPath = parsed.pathname.startsWith('/')
        ? parsed.pathname.slice(1)
        : parsed.pathname;
      const filePath = decodeURIComponent(encodedPath);

      if (!filePath || !path.isAbsolute(filePath) || !fs.existsSync(filePath)) {
        callback({ error: -6 });
        return;
      }

      const stats = fs.statSync(filePath);
      if (!stats.isFile()) {
        callback({ error: -6 });
        return;
      }

      callback({ path: filePath });
    } catch (error) {
      console.error('[AudioProtocol] Failed to resolve audio URL:', error);
      callback({ error: -2 });
    }
  });

  audioProtocolRegistered = true;
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

  const exeName = process.platform === 'win32'
    ? 'soundbot-backend.exe'
    : 'soundbot-backend';

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

  const executablePath = path.join(backendDir, exeName);
  const executableFound = fs.existsSync(executablePath) && fs.statSync(executablePath).isFile();
  const runtimeFound = foundItems.some((item) => ['_internal', 'lib', 'base_library.zip'].includes(item));

  if (!executableFound || !runtimeFound) {
    console.error('[Backend] ✗ Backend executable or runtime files are missing');
    return false;
  }

  const executableSize = fs.statSync(executablePath).size;
  if (executableSize < 100 * 1024) {
    console.error('[Backend] ✗ Backend executable is unexpectedly small');
    return false;
  }

  return true;
}

/**
 * 自动检索模型目录
 * 优先级：环境变量 > 应用目录 > 用户数据目录 > 开发目录
 */
function findModelsDir() {
  // An explicit override is authoritative, including when it intentionally
  // points at a missing directory for diagnostics. Falling through to the
  // bundled model would make configuration mistakes and missing-model tests
  // look healthy.
  const envPath = process.env.SOUNDBOT_MODELS_PATH;
  if (envPath) {
    const explicitPath = path.resolve(envPath);
    console.log(`[Models] Using explicit model directory: ${explicitPath}`);
    return explicitPath;
  }

  const possiblePaths = [];

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

function updateBackendEndpoint(port) {
  const parsed = Number(port);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
    throw new Error(`Invalid backend port: ${port}`);
  }
  backendPort = parsed;
  backendOrigin = `http://127.0.0.1:${backendPort}`;
  backendWsOrigin = `ws://127.0.0.1:${backendPort}`;
  apiBaseUrl = `${backendOrigin}/api/v1`;
}

/**
 * 启动后端服务
 */
async function startBackend() {
  migrateLegacyPlaintextSecrets();
  if (backendProcess) {
    console.log('[Backend] Backend service already running');
    return { success: true };
  }

  // 找可用端口，避免 "Address already in use" 错误
  const freePort = await findFreePort(backendPort);
  if (freePort !== backendPort) {
    console.log(`[Backend] Port ${backendPort} busy, switching to ${freePort}`);
    updateBackendEndpoint(freePort);
  }

  // 检查模型
  const modelStatus = checkModels();
  if (!modelStatus.exists) {
    console.warn('[Backend] Model files not found:', modelStatus.path);
    console.warn('[Backend] Starting without CLAP; management and keyword search remain available.');
  }

  // 获取后端可执行文件
  const backendExe = getBackendExecutable();
  if (!backendExe) {
    return { success: false, error: '未找到后端可执行文件' };
  }

  // 验证后端目录完整性
  const backendDir = path.dirname(backendExe);
  if (!verifyBackendIntegrity(backendDir)) {
    return { success: false, error: '后端文件不完整' };
  }

  // 设置环境变量
  const env = {
    ...process.env,
    SOUNDBOT_PORT: String(backendPort),
    SOUNDBOT_MODELS_PATH: modelStatus.path,
    SOUNDBOT_USER_DATA_DIR: getUserDataDir(),
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
      detached: false,
      // Keep the console-mode PyInstaller executable (stdout carries the
      // authoritative BOUND_PORT handshake) without flashing a separate
      // console window in the installed Windows application.
      windowsHide: true
    });

    // The Python process performs a final bind check and announces the actual
    // port before starting Uvicorn. Treat that handshake as authoritative so
    // a process racing for the Electron-probed port cannot split the app.
    let resolvePortHandshake;
    let portHandshakeSettled = false;
    let stdoutHandshakeBuffer = '';
    const portHandshake = new Promise((resolve) => {
      resolvePortHandshake = (value) => {
        if (portHandshakeSettled) return;
        portHandshakeSettled = true;
        resolve(value);
      };
    });

    // 日志处理
    backendProcess.stdout.on('data', (data) => {
      const rawText = data.toString();
      const text = rawText.trim();
      if (text) console.log(`[Backend] ${text}`);
      stdoutHandshakeBuffer = `${stdoutHandshakeBuffer}${rawText}`;
      const match = stdoutHandshakeBuffer.match(/BOUND_PORT=(\d{1,5})/);
      if (match) {
        try {
          updateBackendEndpoint(Number(match[1]));
          console.log(`[Backend] Confirmed bound port ${backendPort}`);
          resolvePortHandshake(backendPort);
        } catch (error) {
          console.error('[Backend] Invalid port handshake:', error);
          resolvePortHandshake(null);
        }
      }
      stdoutHandshakeBuffer = stdoutHandshakeBuffer.slice(-4096);
    });

    backendProcess.stderr.on('data', (data) => {
      const text = data.toString().trim();
      if (text) console.error(`[Backend] ${text}`);
    });

    backendProcess.on('error', (error) => {
      console.error('[Backend] Process error:', error);
      backendProcess = null;
      backendStartupPromise = null;
      backendSecretsHydrated = false;
      resolvePortHandshake(null);
    });

    backendProcess.on('exit', (code, signal) => {
      console.log(`[Backend] Process exited, code: ${code}, signal: ${signal}`);
      backendProcess = null;
      backendStartupPromise = null;
      backendSecretsHydrated = false;
      resolvePortHandshake(null);
    });

    let handshakeTimer;
    const announcedPort = await Promise.race([
      portHandshake,
      new Promise((resolve) => {
        handshakeTimer = setTimeout(() => resolve(null), 10000);
      })
    ]);
    clearTimeout(handshakeTimer);
    if (!announcedPort) {
      console.error('[Backend] ✗ Port handshake timeout');
      if (backendProcess && backendProcess.exitCode === null) backendProcess.kill();
      return { success: false, error: '后端端口握手失败' };
    }

    const health = await waitForBackendHealth(120000);
    if (health.success) {
      console.log('[Backend] ✓ Backend service started successfully');
    } else {
      console.error(`[Backend] ✗ ${health.error}`);
    }
    return health;

  } catch (error) {
    console.error('[Backend] Startup failed:', error);
    return { success: false, error: error.message };
  }
}

async function waitForBackendHealth(timeoutMs = 120000) {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    if (!backendProcess || backendProcess.exitCode !== null) {
      const code = backendProcess ? backendProcess.exitCode : 'unknown';
      return { success: false, error: `后端进程意外退出 (code: ${code})` };
    }
    const controller = new AbortController();
    const requestTimer = setTimeout(() => controller.abort(), 2000);
    try {
      const res = await fetch(`${apiBaseUrl}/health`, { signal: controller.signal });
      if (res.ok) {
        const payload = await res.json().catch(() => null);
        if (payload?.status === 'degraded' && payload?.audio_decoder_available === false) {
          return {
            success: false,
            error: '安装包内置音频解码器不可用',
            code: 'AUDIO_RUNTIME_UNAVAILABLE'
          };
        }
        if (
          payload?.status === 'healthy'
          && payload?.audio_decoder_available === true
          && payload?.version === app.getVersion()
        ) {
          return { success: true };
        }
      }
    } catch (error) {
    } finally {
      clearTimeout(requestTimer);
    }

    await new Promise((resolve) => setTimeout(resolve, 1000));
  }

  return { success: false, error: '启动超时' };
}

async function ensureBackendStarted() {
  if (backendProcess) {
    const result = await waitForBackendHealth();
    if (result.success) await hydrateBackendAISecrets();
    return result;
  }

  if (backendStartupPromise) {
    return await backendStartupPromise;
  }

  backendStartupPromise = startBackend()
    .then(async (result) => {
      if (result.success) await hydrateBackendAISecrets();
      return result;
    })
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
      const detailError = data?.detail && typeof data.detail === 'object' ? data.detail : null;
      const structuredError = data?.error && typeof data.error === 'object'
        ? data.error
        : (detailError || data);
      const message = typeof data.detail === 'string'
        ? data.detail
        : (typeof data.error === 'string'
          ? data.error
          : (structuredError?.message || `HTTP ${response.status}`));
      return {
        code: structuredError?.code || data.code || `HTTP_${response.status}`,
        message,
        retryable: Boolean(
          structuredError?.retryable
          ?? [408, 425, 429, 502, 503, 504].includes(response.status)
        ),
        details: structuredError?.details && typeof structuredError.details === 'object'
          ? structuredError.details
          : {},
        status: response.status
      };
    }

    const text = await response.text();
    return {
      code: `HTTP_${response.status}`,
      message: text || `HTTP ${response.status}`,
      retryable: [408, 425, 429, 502, 503, 504].includes(response.status),
      details: text ? { response: text } : {},
      status: response.status
    };
  } catch (error) {
    return {
      code: `HTTP_${response.status}`,
      message: `HTTP ${response.status}`,
      retryable: [408, 425, 429, 502, 503, 504].includes(response.status),
      details: {},
      status: response.status
    };
  }
}

async function throwBackendResponseError(response) {
  const parsed = await parseBackendError(response);
  const error = new Error(parsed.message);
  error.code = parsed.code;
  error.retryable = parsed.retryable;
  error.details = parsed.details;
  error.detail = parsed.details;
  error.status = parsed.status;
  throw error;
}

async function requestBackendJson(url, options = {}) {
  const response = await fetch(url, options);

  if (!response.ok) {
    await throwBackendResponseError(response);
  }

  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return await response.json();
  }

  return { success: true, data: await response.text() };
}

function cloneConfig(value) {
  return JSON.parse(JSON.stringify(value || {}));
}

function stripRendererSecretFields(providerConfig) {
  const sanitized = cloneConfig(providerConfig);
  delete sanitized.api_key;
  delete sanitized.has_api_key;
  delete sanitized.secret_action;
  return sanitized;
}

function normalizeSecretUpdate(value) {
  const action = value?.action || 'keep';
  if (!['keep', 'set', 'clear'].includes(action)) {
    const error = new Error('无效的密钥更新操作');
    error.code = 'INVALID_SECRET_ACTION';
    throw error;
  }
  if (action === 'set') {
    if (typeof value?.value !== 'string' || value.value.trim().length === 0) {
      const error = new Error('新密钥不能为空');
      error.code = 'INVALID_SECRET';
      throw error;
    }
    return { action, value: value.value.trim() };
  }
  return { action, value: null };
}

function resolveSecretForRequest(scope, provider, update) {
  const key = secretKeyFor(scope, provider);
  const normalized = normalizeSecretUpdate(update);
  if (normalized.action === 'set') {
    return { key, ...normalized, resolved: normalized.value };
  }
  if (normalized.action === 'clear') {
    return { key, ...normalized, resolved: '' };
  }
  if (hasEncryptedSecret(key) && !isSecureStorageAvailable()) {
    const error = new Error('已保存密钥当前无法由系统安全存储解密');
    error.code = 'ENCRYPTION_UNAVAILABLE';
    throw error;
  }
  return { key, ...normalized, resolved: decryptStoredSecret(key) || '' };
}

function prepareAIConfigRequest(payload = {}) {
  const llmProvider = String(payload.llm_provider || '');
  const embeddingProvider = String(payload.embedding_provider || '');
  // Deriving the key also validates provider identifiers before any store or
  // backend operation occurs.
  const llmSecret = resolveSecretForRequest('llm', llmProvider, payload.llm_secret);
  const embeddingSecret = resolveSecretForRequest(
    'embedding', embeddingProvider, payload.embedding_secret
  );
  const llmConfig = stripRendererSecretFields(payload.llm_config);
  const embeddingConfig = stripRendererSecretFields(payload.embedding_config);
  llmConfig.api_key = llmSecret.resolved;
  embeddingConfig.api_key = embeddingSecret.resolved;
  return {
    body: {
      llm_provider: llmProvider,
      llm_config: llmConfig,
      embedding_provider: embeddingProvider,
      embedding_config: embeddingConfig
    },
    updates: [llmSecret, embeddingSecret]
  };
}

function overlaySecureSecretStatus(responseData) {
  const result = cloneConfig(responseData);
  const groups = [
    ['llm', result?.llm?.config],
    ['embedding', result?.embedding?.config]
  ];
  for (const [scope, configs] of groups) {
    if (!configs || typeof configs !== 'object') continue;
    for (const [provider, providerConfig] of Object.entries(configs)) {
      if (!providerConfig || typeof providerConfig !== 'object' || Array.isArray(providerConfig)) continue;
      delete providerConfig.api_key;
      try {
        providerConfig.has_api_key = hasEncryptedSecret(secretKeyFor(scope, provider));
      } catch (_) {
        providerConfig.has_api_key = false;
      }
    }
  }
  result.secure_storage = getSafeStorageStatus();
  return result;
}

function snapshotSecretStore() {
  return { ...readSecureSecrets() };
}

function restoreSecretStore(snapshot) {
  writeSecureSecrets({ ...(snapshot || {}) });
}

function applyPersistentSecretUpdates(updates) {
  for (const update of updates) {
    if (update.action === 'set') {
      setStoredSecret(update.key, update.value);
    } else if (update.action === 'clear') {
      deleteStoredSecret(update.key);
    }
  }
}

async function hydrateBackendAISecrets() {
  if (backendSecretsHydrated) return;
  if (!isSecureStorageAvailable()) {
    console.warn('[Secrets] Backend credential hydration skipped: secure storage unavailable');
    return;
  }
  try {
    const current = await requestBackendJson(`${apiBaseUrl}/ai/config`);
    const llmProvider = current?.llm?.provider;
    const embeddingProvider = current?.embedding?.provider;
    if (!llmProvider || !embeddingProvider) return;
    const prepared = prepareAIConfigRequest({
      llm_provider: llmProvider,
      llm_config: current?.llm?.config?.[llmProvider] || {},
      llm_secret: { action: 'keep' },
      embedding_provider: embeddingProvider,
      embedding_config: current?.embedding?.config?.[embeddingProvider] || {},
      embedding_secret: { action: 'keep' }
    });
    await requestBackendJson(`${apiBaseUrl}/ai/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(prepared.body)
    });
    backendSecretsHydrated = true;
    console.log('[Secrets] Backend runtime credentials hydrated');
  } catch (error) {
    console.warn('[Secrets] Backend credential hydration failed:', error?.code || error?.message);
  }
}

function requireProjectId(value) {
  const projectId = typeof value === 'string' ? value.trim() : '';
  if (!projectId) {
    const error = new Error('projectId is required');
    error.code = 'PROJECT_ID_REQUIRED';
    throw error;
  }
  return projectId;
}

function createBackendRequest(action, data = {}) {
  const filePath = typeof data === 'string' ? data : (data.filePath || data.path || '');
  const encodedPath = encodeURIComponent(filePath);

  switch (action) {
    case 'health':
      return { url: `${apiBaseUrl}/health` };
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
        url: `${apiBaseUrl}/projects/${encodeURIComponent(requireProjectId(data.projectId))}/imports`,
        options: {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            folder_path: data.folderPath,
            recursive: data.recursive ?? true,
            client_id: data.clientId || 'default'
          })
        }
      };
    case 'import-files':
      return {
        url: `${apiBaseUrl}/projects/${encodeURIComponent(requireProjectId(data.projectId))}/imports`,
        options: {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            file_paths: Array.isArray(data.filePaths) ? data.filePaths : [],
            client_id: data.clientId || 'default'
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
            page_size: data.page_size || 50,
            project_id: requireProjectId(data.projectId)
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
    case 'waveform-by-id':
      return {
        url: `${apiBaseUrl}/files/${encodeURIComponent(data.fileId)}/waveform?project_id=${encodeURIComponent(data.projectId || 'default')}`
      };
    case 'waveforms-batch':
      return {
        url: `${apiBaseUrl}/waveforms/batch`,
        options: {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            file_ids: Array.isArray(data.fileIds) ? data.fileIds : [],
            project_id: data.projectId || 'default'
          })
        }
      };
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
  for (const controller of activeBackendRequests.values()) controller.abort();
  activeBackendRequests.clear();
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
  rendererBridgeReady = false;
  rendererPreloadFailure = null;
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1200,
    minHeight: 700,
    title: 'SoundBot - AI 音效管理器',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
      webSecurity: true,
      allowRunningInsecureContent: false
    },
    titleBarStyle: 'default',
    show: false,
    backgroundColor: '#0a0a0a'
  });

  // A preload failure removes every desktop API from the renderer while the
  // HTML can still look healthy. Capture it explicitly instead of allowing a
  // browser-only fallback to masquerade as a working installed application.
  mainWindow.webContents.on('preload-error', (_event, preloadPath, error) => {
    rendererPreloadFailure = {
      preloadPath,
      message: error?.message || 'Unknown preload error'
    };
    console.error('[Renderer] Preload failed:', preloadPath, error);
  });
  mainWindow.webContents.on('ipc-message', (_event, channel, payload) => {
    if (channel === 'renderer-bridge-ready' && payload?.version === 1) {
      rendererBridgeReady = true;
    }
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
          `media-src 'self' blob: soundmind-audio: ${backendOrigin}; ` +
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

async function showOwnedOpenDialog(event, options = {}) {
  if (nativeOpenDialogActive) {
    const error = new Error('已有文件选择窗口正在打开');
    error.code = 'DIALOG_BUSY';
    throw error;
  }

  const owner = BrowserWindow.fromWebContents(event.sender) || mainWindow;
  if (!owner || owner.isDestroyed()) {
    const error = new Error('主窗口当前不可用');
    error.code = 'WINDOW_UNAVAILABLE';
    throw error;
  }

  nativeOpenDialogActive = true;
  try {
    if (owner.isMinimized()) owner.restore();
    if (!owner.isVisible()) owner.show();
    owner.focus();
    return await dialog.showOpenDialog(owner, options);
  } finally {
    nativeOpenDialogActive = false;
    if (!owner.isDestroyed()) owner.focus();
  }
}

// ==================== IPC 处理 ====================

function setupIpcHandlers() {
  ipcMain.handle('audio-capabilities', () => ({
    version: audioCapabilityManifest.version || 1,
    formats: audioCapabilityManifest.formats || {},
    extensions: [...supportedAudioExtensions]
  }));

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
    return await showOwnedOpenDialog(event, options);
  });

  ipcMain.handle('dialog-save', async (event, options) => {
    return await dialog.showSaveDialog(mainWindow, options);
  });

  ipcMain.handle('dialog-message', async (event, options) => {
    return await dialog.showMessageBox(mainWindow, options);
  });

  // 后端 API 代理
  ipcMain.handle('backend-api', async (event, action, data) => {
    let requestConfig = null;
    let requestKey = null;
    let requestController = null;
    try {
      if (action === 'start-server') {
        const result = await ensureBackendStarted();
        return result?.success === false
          ? toBackendError(new Error(result.error || '后端启动失败'), { action, code: 'BACKEND_START_FAILED' })
          : result;
      }

      if (action === 'stop-server') {
        return await stopBackend();
      }

      if (action === 'runtime-config') {
        return getRuntimeConfig();
      }

      const startupResult = await ensureBackendStarted();
      if (!startupResult.success) {
        return toBackendError(new Error(startupResult.error || '后端未就绪'), {
          action,
          code: 'BACKEND_NOT_READY'
        });
      }

      requestConfig = createBackendRequest(action, data);
      if (!requestConfig) {
        return toBackendError(new Error(`未知操作: ${action}`), {
          action,
          code: 'UNKNOWN_BACKEND_ACTION'
        });
      }

      if (requestConfig.localOnly) {
        return requestConfig.result;
      }

      const requestId = data && typeof data === 'object'
        ? String(data.requestId || '').trim()
        : '';
      if (requestId) {
        requestKey = `${event.sender.id}:${requestId}`;
        const previous = activeBackendRequests.get(requestKey);
        if (previous) previous.abort();
        requestController = new AbortController();
        activeBackendRequests.set(requestKey, requestController);
        requestConfig.options = {
          ...(requestConfig.options || {}),
          signal: requestController.signal
        };
      }

      return await requestBackendJson(requestConfig.url, requestConfig.options);
    } catch (error) {
      if (error?.name === 'AbortError') {
        const cancelledError = new Error('请求已取消');
        cancelledError.code = 'REQUEST_CANCELLED';
        cancelledError.retryable = true;
        cancelledError.details = { cancelled: true };
        return toBackendError(cancelledError, {
          action,
          url: requestConfig?.url || null
        });
      }
      console.error('[IPC] Backend API error:', error);
      return toBackendError(error, { action, url: requestConfig?.url || null });
    } finally {
      if (requestKey && activeBackendRequests.get(requestKey) === requestController) {
        activeBackendRequests.delete(requestKey);
      }
    }
  });

  ipcMain.handle('backend-api-cancel', (event, requestId) => {
    const normalized = String(requestId || '').trim();
    if (!normalized) return { success: false, cancelled: false };
    const requestKey = `${event.sender.id}:${normalized}`;
    const controller = activeBackendRequests.get(requestKey);
    if (!controller) return { success: true, cancelled: false };
    controller.abort();
    activeBackendRequests.delete(requestKey);
    return { success: true, cancelled: true };
  });

  // Credential-aware AI config proxy.  Plaintext keys never cross back from
  // the Electron main process into the renderer and are never persisted by
  // the Python backend.
  ipcMain.handle('ai-config', async (event, action, payload = {}) => {
    try {
      const startupResult = await ensureBackendStarted();
      if (!startupResult.success) {
        return {
          success: false,
          code: 'BACKEND_NOT_READY',
          error: startupResult.error || '后端未就绪'
        };
      }

      if (action === 'get') {
        const result = await requestBackendJson(`${apiBaseUrl}/ai/config`);
        return overlaySecureSecretStatus(result);
      }

      if (!['save', 'test'].includes(action)) {
        return { success: false, code: 'UNKNOWN_AI_CONFIG_ACTION', error: '未知 AI 配置操作' };
      }

      const prepared = prepareAIConfigRequest(payload);
      const snapshot = action === 'save' ? snapshotSecretStore() : null;
      try {
        if (action === 'save') {
          // Persist first, then roll back if the metadata/backend update fails.
          // This makes the secure store and runtime configuration one logical
          // transaction from the renderer's perspective.
          applyPersistentSecretUpdates(prepared.updates);
        }
        const result = await requestBackendJson(
          `${apiBaseUrl}/ai/config${action === 'test' ? '/test' : ''}`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(prepared.body)
          }
        );
        if (action === 'save') backendSecretsHydrated = true;
        return result;
      } catch (error) {
        if (action === 'save') {
          try {
            restoreSecretStore(snapshot);
          } catch (_) {
          }
        }
        throw error;
      }
    } catch (error) {
      console.error('[AI Config] Secure proxy request failed:', error?.code || error?.message);
      return {
        success: false,
        code: error?.code || 'AI_CONFIG_FAILED',
        error: error?.message || 'AI 配置请求失败',
        status: Number.isInteger(error?.status) ? error.status : null
      };
    }
  });

  ipcMain.handle('secure-secret', async (event, action, payload = {}) => {
    try {
      if (action === 'status') {
        return { success: true, ...getSafeStorageStatus() };
      }

      validateSecretKey(payload.key);
      if (!isSecureStorageAvailable()) {
        return { success: false, code: 'ENCRYPTION_UNAVAILABLE', error: '系统安全存储当前不可用' };
      }

      if (action === 'set') {
        setStoredSecret(payload.key, payload.value);
        return { success: true };
      }

      if (action === 'get' || action === 'has') {
        // Deliberately expose existence only.  Renderer code must never be
        // able to retrieve an already stored plaintext credential.
        return { success: true, exists: hasEncryptedSecret(payload.key) };
      }

      if (action === 'delete') {
        const existed = deleteStoredSecret(payload.key);
        return { success: true, existed };
      }

      return { success: false, code: 'UNKNOWN_SECRET_ACTION', error: `未知密钥操作: ${action}` };
    } catch (error) {
      console.error('[Secrets] IPC error:', error);
      return { success: false, code: 'SECRET_STORE_ERROR', error: error.message };
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
  ipcMain.handle('open-log-directory', async () => {
    const logDir = app.getPath('logs');
    const error = await shell.openPath(logDir);
    return { success: !error, error: error || null, path: logDir };
  });
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

  function toImportFileInfo(filePath) {
    const stats = fs.statSync(filePath);
    const extension = path.extname(filePath).toLowerCase();

    return {
      path: filePath,
      name: path.basename(filePath),
      size: stats.size,
      type: extension,
      lastModified: stats.mtimeMs
    };
  }

  ipcMain.handle('file-import', async (event, action, payload) => {
    try {
      switch (action) {
        case 'select-audio': {
          const result = await showOwnedOpenDialog(event, {
            ...payload,
            properties: ['openFile', 'multiSelections'],
            filters: [
              { name: 'Audio Files', extensions: supportedAudioFilterExtensions }
            ]
          });

          if (result.canceled) {
            return { success: false, canceled: true, filePaths: [] };
          }

          const files = (result.filePaths || [])
            .filter((filePath) => supportedAudioExtensions.has(path.extname(filePath).toLowerCase()))
            .filter((filePath) => fs.existsSync(filePath))
            .map(toImportFileInfo);

          return {
            success: true,
            canceled: false,
            filePaths: result.filePaths || [],
            files
          };
        }
        case 'select-folder': {
          const result = await showOwnedOpenDialog(event, {
            ...payload,
            properties: ['openDirectory']
          });

          if (result.canceled) {
            return { success: false, canceled: true, filePaths: [] };
          }

          const folder = (result.filePaths || [])[0] || '';
          return {
            success: Boolean(folder),
            canceled: false,
            filePaths: result.filePaths || [],
            folder
          };
        }
        case 'handle-drop':
          return {
            success: true,
            files: (payload || [])
              .filter((filePath) => typeof filePath === 'string' && fs.existsSync(filePath))
              .map(toImportFileInfo)
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
      const code = error?.code || 'FILE_IMPORT_FAILED';
      const message = error?.message || '文件导入操作失败';
      console.error('[FileImport] IPC action failed:', { action, code, message });
      return {
        success: false,
        error: message,
        code,
        retryable: code === 'DIALOG_BUSY',
        details: { action }
      };
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

// 创建启动窗口（显示加载状态）
let splashWindow = null;

function createSplashWindow() {
  splashWindow = new BrowserWindow({
    width: 400,
    height: 240,
    frame: false,
    alwaysOnTop: true,
    transparent: false,
    resizable: false,
    backgroundColor: '#0a0a0a',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true
    }
  });

  splashWindow.loadFile(path.join(__dirname, 'splash.html'));
  splashWindow.webContents.on('will-navigate', (event, targetUrl) => {
    if (targetUrl.startsWith('soundbot-action://open-logs')) {
      event.preventDefault();
      shell.openPath(app.getPath('logs')).then((error) => {
        if (error) console.error('[Splash] Failed to open log directory:', error);
      });
    }
  });
  splashWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  splashWindow.webContents.once('did-finish-load', () => {
    applySplashState();
  });
  return splashWindow;
}

function applySplashState() {
  if (!splashWindow || splashWindow.isDestroyed() || splashWindow.webContents.isLoading()) return;
  const payload = JSON.stringify(pendingSplashState);
  splashWindow.webContents.executeJavaScript(
    `window.setSoundBotSplashStatus(${payload})`
  ).catch(() => {});
}

// 更新启动窗口状态
function updateSplashStatus(status, progress, isError = false) {
  pendingSplashState = {
    status: String(status || ''),
    progress: String(progress || ''),
    isError: Boolean(isError)
  };
  applySplashState();
}

// 关闭启动窗口
function closeSplashWindow() {
  if (splashWindow && !splashWindow.isDestroyed()) {
    splashWindow.close();
    splashWindow = null;
  }
}

app.whenReady().then(async () => {
  initializeMainLogging();
  registerAudioProtocol();

  // 创建启动窗口
  createSplashWindow();
  
  // 先启动后端
  updateSplashStatus('正在启动后端服务...', '检查模型文件');
  const result = await ensureBackendStarted();
  
  if (!result.success) {
    console.error('[App] Backend startup failed:', result.error);
    updateSplashStatus(
      '启动失败',
      `${result.error || '未知错误'} · 请重新安装或查看应用日志`,
      true
    );
    return;
  }
  
  updateSplashStatus('服务已就绪', '正在加载界面...');
  
  // 后端启动成功后再创建主窗口
  createWindow();
  
  // 等待主窗口准备好后关闭启动窗口
  mainWindow.once('ready-to-show', () => {
    if (rendererPreloadFailure || !rendererBridgeReady) {
      const reason = rendererPreloadFailure
        ? '桌面桥接组件加载失败'
        : '桌面桥接组件未完成初始化';
      console.error('[Renderer] Bridge startup assertion failed:', {
        rendererBridgeReady,
        rendererPreloadFailure
      });
      updateSplashStatus('界面加载失败', `${reason} · 请重新安装应用`, true);
      mainWindow.destroy();
      return;
    }
    closeSplashWindow();
    mainWindow.show();
    
    // 通知前端后端已就绪
    mainWindow.webContents.send('backend-ready', { success: true });
    
    if (process.argv.includes('--dev')) {
      mainWindow.webContents.openDevTools();
    }
  });
});

app.on('window-all-closed', () => {
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
  if (quitInProgress) {
    return;
  }
  event.preventDefault();
  quitInProgress = true;
  await stopBackend();
  app.exit(0);
});
