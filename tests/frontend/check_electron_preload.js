#!/usr/bin/env node

// Runtime smoke for the real sandboxed Electron preload. `node --check` cannot
// detect unsupported local require() calls because those fail only inside
// Electron's restricted preload environment.
const assert = require('node:assert/strict');
const path = require('node:path');
const { app, BrowserWindow, ipcMain } = require('electron');

const root = path.resolve(__dirname, '..', '..');
let bridgeReady = false;
let preloadFailure = null;

async function run() {
  ipcMain.handle('audio-capabilities', () => ({
    version: 1,
    formats: { '.wav': { native_playback: true } },
    extensions: ['.wav']
  }));

  const window = new BrowserWindow({
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(root, 'preload.js')
    }
  });

  window.webContents.on('preload-error', (_event, preloadPath, error) => {
    preloadFailure = new Error(`${preloadPath}: ${error?.message || error}`);
  });
  window.webContents.on('ipc-message', (_event, channel, payload) => {
    if (channel === 'renderer-bridge-ready' && payload?.version === 1) bridgeReady = true;
  });

  await window.loadURL('data:text/html;charset=utf-8,<html><body>SoundBot preload smoke</body></html>');
  if (preloadFailure) throw preloadFailure;

  const contract = await window.webContents.executeJavaScript(`({
    electronAPI: typeof window.electronAPI,
    selectAudioFiles: typeof window.electronAPI?.fileImport?.selectAudioFiles,
    selectFolder: typeof window.electronAPI?.fileImport?.selectFolder,
    getCapabilities: typeof window.electronAPI?.fileImport?.getCapabilities,
    importFiles: typeof window.electronAPI?.backendAPI?.importFiles,
    importFolderAsync: typeof window.electronAPI?.backendAPI?.importFolderAsync,
    getWaveformById: typeof window.electronAPI?.backendAPI?.getWaveformById,
    legacyNodeAPI: typeof window.nodeAPI
  })`);

  assert.equal(bridgeReady, true, 'preload bridge handshake was not received');
  assert.equal(contract.electronAPI, 'object');
  for (const key of [
    'selectAudioFiles',
    'selectFolder',
    'getCapabilities',
    'importFiles',
    'importFolderAsync',
    'getWaveformById'
  ]) {
    assert.equal(contract[key], 'function', `${key} is not exposed by the sandboxed preload`);
  }
  assert.equal(contract.legacyNodeAPI, 'undefined', 'legacy filesystem bridge is still exposed');

  const capabilities = await window.webContents.executeJavaScript(
    'window.electronAPI.fileImport.getCapabilities()'
  );
  assert.deepEqual(capabilities.extensions, ['.wav']);
  window.destroy();
  console.log('Sandboxed Electron preload bridge smoke passed.');
}

// The first Electron launch on a clean Windows runner may include Defender
// scanning of the unpacked runtime. Keep the timeout bounded but large enough
// that security scanning is not mistaken for a preload failure.
const timeout = setTimeout(() => {
  console.error('Sandboxed Electron preload bridge smoke timed out.');
  app.exit(1);
}, 60_000);

app.whenReady()
  .then(run)
  .then(() => {
    clearTimeout(timeout);
    app.exit(0);
  })
  .catch(error => {
    clearTimeout(timeout);
    console.error(error);
    app.exit(1);
  });
