const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

exports.default = async function(context) {
  const { electronPlatformName, appOutDir } = context;

  console.log(`[afterPack] ========================================`);
  console.log(`[afterPack] Platform: ${electronPlatformName}`);
  console.log(`[afterPack] App output dir: ${appOutDir}`);
  console.log(`[afterPack] Working directory: ${process.cwd()}`);

  // 后端源目录 - 使用更可靠的路径解析
  const backendSourceDir = path.resolve(process.cwd(), 'dist', 'backend', 'soundbot-backend');

  // 目标目录（根据平台不同）
  let backendTargetDir;
  if (electronPlatformName === 'win32') {
    backendTargetDir = path.join(appOutDir, 'resources', 'backend', 'soundbot-backend');
  } else if (electronPlatformName === 'darwin') {
    backendTargetDir = path.join(appOutDir, 'SoundBot.app', 'Contents', 'Resources', 'backend', 'soundbot-backend');
  } else {
    backendTargetDir = path.join(appOutDir, 'resources', 'backend', 'soundbot-backend');
  }

  console.log(`[afterPack] Backend source: ${backendSourceDir}`);
  console.log(`[afterPack] Backend target: ${backendTargetDir}`);

  // 检查源目录是否存在
  if (!fs.existsSync(backendSourceDir)) {
    console.error(`[afterPack] ERROR: Backend source directory does not exist: ${backendSourceDir}`);
    throw new Error(`Backend source directory does not exist: ${backendSourceDir}`);
  }

  // 计算源目录大小
  const sourceSize = getDirectorySize(backendSourceDir);
  console.log(`[afterPack] Source backend size: ${(sourceSize / 1024 / 1024).toFixed(1)} MB`);

  if (sourceSize < 100 * 1024 * 1024) {
    console.error(`[afterPack] ERROR: Source backend is too small: ${(sourceSize / 1024 / 1024).toFixed(1)} MB`);
    throw new Error(`Source backend size is too small: ${(sourceSize / 1024 / 1024).toFixed(1)} MB`);
  }

  // 清理目标目录（如果存在）
  if (fs.existsSync(backendTargetDir)) {
    console.log(`[afterPack] Cleaning existing target directory...`);
    fs.rmSync(backendTargetDir, { recursive: true, force: true });
  }

  // 创建目标目录
  fs.mkdirSync(backendTargetDir, { recursive: true });
  console.log(`[afterPack] Created target directory: ${backendTargetDir}`);

  // 复制后端文件
  console.log(`[afterPack] Copying backend files...`);
  copyRecursive(backendSourceDir, backendTargetDir);

  // 验证复制结果
  const targetSize = getDirectorySize(backendTargetDir);
  console.log(`[afterPack] Backend copied successfully. Size: ${(targetSize / 1024 / 1024).toFixed(1)} MB`);

  if (targetSize < 100 * 1024 * 1024) {
    console.error(`[afterPack] ERROR: Backend size is too small: ${(targetSize / 1024 / 1024).toFixed(1)} MB`);
    throw new Error(`Backend size is too small: ${(targetSize / 1024 / 1024).toFixed(1)} MB`);
  }

  // 设置可执行权限（macOS/Linux）
  if (electronPlatformName !== 'win32') {
    const exeName = 'soundbot-backend';
    const exePath = path.join(backendTargetDir, exeName);
    if (fs.existsSync(exePath)) {
      try {
        fs.chmodSync(exePath, 0o755);
        console.log(`[afterPack] Set executable permission for: ${exePath}`);
      } catch (err) {
        console.warn(`[afterPack] Warning: Could not set executable permission: ${err.message}`);
      }
    }
  }

  // 列出关键文件验证
  console.log(`[afterPack] Verifying key files...`);
  const keyFiles = [
    'soundbot-backend',
    'soundbot-backend.exe',
    '_internal',  // PyInstaller 新版本使用 _internal
    'lib',        // 旧版本使用 lib
    'base_library.zip'
  ];

  let foundCount = 0;
  for (const file of keyFiles) {
    const filePath = path.join(backendTargetDir, file);
    if (fs.existsSync(filePath)) {
      const stats = fs.statSync(filePath);
      console.log(`[afterPack] ✓ ${file} (${stats.isDirectory() ? 'dir' : 'file'})`);
      foundCount++;
    }
  }

  if (foundCount < 2) {
    console.error(`[afterPack] ERROR: Too few key files found (${foundCount})`);
    throw new Error(`Backend verification failed: only ${foundCount} key files found`);
  }

  console.log('[afterPack] Done!');
  console.log(`[afterPack] ========================================`);
};

function copyRecursive(src, dest) {
  const stats = fs.statSync(src);

  if (stats.isDirectory()) {
    if (!fs.existsSync(dest)) {
      fs.mkdirSync(dest, { recursive: true });
    }

    const entries = fs.readdirSync(src);
    for (const entry of entries) {
      const srcPath = path.join(src, entry);
      const destPath = path.join(dest, entry);
      copyRecursive(srcPath, destPath);
    }
  } else {
    fs.copyFileSync(src, dest);
  }
}

function getDirectorySize(dir) {
  let size = 0;
  const entries = fs.readdirSync(dir);

  for (const entry of entries) {
    const fullPath = path.join(dir, entry);
    const stats = fs.statSync(fullPath);

    if (stats.isDirectory()) {
      size += getDirectorySize(fullPath);
    } else {
      size += stats.size;
    }
  }

  return size;
}
