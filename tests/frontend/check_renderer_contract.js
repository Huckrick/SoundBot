#!/usr/bin/env node

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..', '..');
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');
const indexHtml = read('index.html');
const mainJs = read('main.js');
const preloadJs = read('preload.js');
const packageJson = JSON.parse(read('package.json'));
const backendVersionMatch = read('backend/config.py').match(/^APP_VERSION\s*=\s*["']([^"']+)["']/m);
const moduleNames = ['api', 'audio', 'waveform'];
const moduleSources = Object.fromEntries(
  moduleNames.map(name => [name, read(`assets/renderer/${name}.js`)])
);

async function main() {
  assert(!/wavesurfer/i.test(indexHtml), 'renderer still references WaveSurfer');
  assert(!/playbackWebSocket|connectPlaybackWebSocket|disconnectPlaybackWebSocket/.test(indexHtml), 'backend playback WebSocket remains');
  assert(!/readAudioFile|read-audio-file/.test(indexHtml + mainJs + preloadJs), 'whole-file IPC reader remains');
  assert(!/requestBackendBinary|audio-stream/.test(mainJs + preloadJs), 'unused whole-file binary playback IPC remains');
  assert.deepStrictEqual(
    [...preloadJs.matchAll(/require\((['"])([^'"]+)\1\)/g)].map(match => match[2]),
    ['electron'],
    'sandboxed preload imports a local or unsupported CommonJS module'
  );
  assert(mainJs.includes('sandbox: true'), 'renderer sandbox is not explicit');
  assert(mainJs.includes("webContents.on('preload-error'"), 'preload failures are not observed');
  assert(mainJs.includes("channel === 'renderer-bridge-ready'"), 'preload bridge handshake is not verified');
  assert(preloadJs.includes("ipcRenderer.send('renderer-bridge-ready'"), 'preload bridge handshake is not sent');
  assert(mainJs.includes('windowsHide: true'), 'Windows backend console window is not hidden');
  assert(mainJs.includes("app.setAppLogsPath()"), 'main-process diagnostics are not persisted');
  assert(mainJs.includes("ipcMain.handle('open-log-directory'"), 'log-directory recovery action is missing');
  assert(preloadJs.includes("openLogs: () => ipcRenderer.invoke('open-log-directory')"), 'renderer cannot open diagnostics');
  const redactorMatch = mainJs.match(/function redactDiagnosticText\(value\) \{([\s\S]*?)\n\}/);
  assert(redactorMatch, 'diagnostic credential redactor is missing');
  const redactDiagnosticText = vm.runInNewContext(`(function(value) {${redactorMatch[1]}\n})`);
  for (const sample of [
    '{"api_key":"secret-value-123"}',
    "api-key=secret-value-123",
    '{"Authorization":"Bearer secret-value-123"}',
    'https://example.test?q=1&' + 'access_token=secret-value-123&next=2'
  ]) {
    assert(!redactDiagnosticText(sample).includes('secret-value-123'), 'diagnostic log leaks a credential');
  }
  assert(mainJs.includes("payload?.audio_decoder_available === false"), 'startup does not reject a missing audio runtime');
  assert(mainJs.includes('BrowserWindow.fromWebContents(event.sender)'), 'native dialogs are not owned by their requesting window');
  assert(mainJs.includes('nativeOpenDialogActive'), 'native open dialogs are not serialized');
  assert(mainJs.includes('if (owner.isMinimized()) owner.restore()'), 'native dialog owner is not restored before opening');
  assert(mainJs.includes("loadFile(path.join(__dirname, 'splash.html'),"), 'splash is not loaded from the packaged local page');
  assert(mainJs.includes('pendingSplashState') && mainJs.includes('applySplashState()'), 'early splash state is not queued until load');
  const rendererEntries = ['api', 'audio', 'waveform', 'playback', 'workspace', 'equalizer', 'effects', 'timeline'];
  for (const name of rendererEntries) {
    assert(indexHtml.includes(`<script src="./assets/renderer/${name}.js"></script>`), `${name} is not loaded`);
    new vm.Script(read(`assets/renderer/${name}.js`), { filename: `${name}.js` });
  }
  const workspace = read('assets/renderer/workspace.js');
  const timeline = read('assets/renderer/timeline.js');
  for (const element of ['rows', 'wwiseView', 'wwiseTree', 'wave', 'waveformStatus', 'btnExportWwise', 'dragRange',
    'moveRange', 'tabVst', 'groupsPanel', 'jobsList']) {
    assert(indexHtml.includes(`id="${element}"`), `${element} is missing`);
  }
  assert(workspace.includes('desktop.fileImport.selectFolder'), 'native folder selection is missing');
  assert(workspace.includes("route('/imports', libraryId)"), 'imports are not scoped to the chosen library');
  assert(workspace.includes("api.get(route('/jobs', library))") && workspace.includes("api.get('/api/v1/jobs/active')"),
    'library history and global active job polling are missing');
  assert(workspace.includes('state.cursor = result.next_cursor'), 'cursor continuation is missing');
  assert(workspace.includes('waveform.renderMain(canvas'), 'main waveform is not wired to the canvas');
  assert(workspace.includes('visiblePreviews()') && workspace.includes('[...waves].slice(0, 2)'),
    'thumbnail generation is not bounded to visible assets');
  assert(timeline.includes('ui.openDockEditor(item)') && timeline.includes('ui.miniCanvas(item)'),
    'Wwise dock does not share the asset waveform workflow');
  assert(workspace.includes('desktop.startDrag(state.delivery)'), 'native drag does not use a prepared delivery');
  assert(timeline.includes("desktop.desktopMedia('writeback', session)"), 'Wwise writeback is not wired');
  assert(mainJs.includes('event.sender.startDrag({ file,'), 'Electron native drag is missing');
  assert(!/https?:\/\//.test(indexHtml.match(/<script[^>]*src[^>]*>/gi).join('')), 'offline renderer depends on remote scripts');
  assert(!packageJson.build.extraResources.some(entry => entry.to === 'models'), 'core ships with model resources');
  assert(moduleSources.waveform.includes('value.length > 0 && value.every(Number.isFinite)'), 'strict waveform validation is missing');
  assert(moduleSources.waveform.includes('const layerCache = new WeakMap()'), 'offscreen waveform cache is missing');
  assert(mainJs.includes("const BACKEND_ERROR_SENTINEL = '__soundbotBackendError'"), 'structured backend error sentinel is missing');
  assert(preloadJs.includes('throw createRendererError(result.error)'), 'preload does not reject structured backend errors');
  assert(mainJs.includes('retryable: Boolean(') && mainJs.includes('details,'), 'structured backend error fields are incomplete');
  assert(mainJs.includes("ipcMain.handle('backend-api-cancel'"), 'backend request cancellation IPC is missing');
  assert(mainJs.includes('match(/BOUND_PORT='), 'backend bound-port handshake is not parsed');
  assert(mainJs.includes('updateBackendEndpoint(Number(match[1]))'), 'announced backend port is not authoritative');
  assert(mainJs.includes("payload?.status === 'healthy'"), 'health polling can accept an unrelated HTTP service');
  assert(preloadJs.includes("cancelRequest: (requestId) => ipcRenderer.invoke('backend-api-cancel'"), 'preload cancellation API is missing');
  assert(workspace.includes('peakRequest?.abort()'), 'main waveform does not cancel stale HTTP requests');
  assert(mainJs.includes('safeStorage.encryptString'), 'safeStorage secret encryption is missing');
  assert(!preloadJs.includes('secrets: {'), 'preload exposes a general-purpose credential bridge');
  assert(mainJs.includes('devTools: false') && !mainJs.includes('openDevTools('), 'developer tools are enabled');
  assert(mainJs.includes('X-SoundBot-IPC') && !preloadJs.includes('X-SoundBot-IPC'), 'desktop token escaped the main process');

  assert(/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(packageJson.version), 'package version is not SemVer');
  assert(backendVersionMatch, 'backend APP_VERSION is missing');
  assert.strictEqual(packageJson.version, backendVersionMatch[1], 'frontend/backend versions drifted');
  assert(packageJson.build.files.includes('assets/renderer/*.js'), 'renderer modules are not included in packaging');
  assert(packageJson.build.files.includes('splash.html'), 'the app-styled splash page is not packaged');
  const splashHtml = read('splash.html');
  assert(splashHtml.includes('id="closeButton"'), 'fatal splash state has no exit control');
  assert(splashHtml.includes('window.close()'), 'fatal splash exit control is not wired');
  assert(!packageJson.build.files.some(entry => /wavesurfer/i.test(entry) && !entry.startsWith('!')), 'WaveSurfer is still packaged');

  const calls = [];
  const fakeContext = {
    console,
    URLSearchParams,
    setTimeout,
    clearTimeout,
    devicePixelRatio: 2,
    fetch: async (url, options = {}) => {
      calls.push({ url, options });
      let payload = { success: true };
      if (url.includes('/playback-source')) payload = { path: '/tmp/rendered.wav', mode: 'transcoded_wav' };
      if (url.includes('/api/v1/files?')) payload = { files: [], total: 0, next_cursor: 42 };
      if (url.includes('/api/v1/jobs/')) payload = { id: 'job-1', state: 'completed', processed: 3, total: 3 };
      if (url.includes('/index/status')) payload = {
        artifacts: {
          audio_vector: { ready: 5, pending: 2, stale: 1, failed: 1 },
          text_vector: { ready: 4, pending: 3, stale: 0, failed: 2 }
        }
      };
      return {
        ok: true,
        status: 200,
        headers: { get: () => 'application/json' },
        json: async () => payload,
        text: async () => JSON.stringify(payload)
      };
    }
  };
  fakeContext.window = fakeContext;
  fakeContext.globalThis = fakeContext;
  vm.createContext(fakeContext);
  for (const name of moduleNames) vm.runInContext(moduleSources[name], fakeContext, { filename: `${name}.js` });
  const modules = fakeContext.SoundBotRenderer;

  modules.api.configure({ apiBase: 'http://127.0.0.1:19000/', wsBase: 'ws://127.0.0.1:19000/' });
  assert.strictEqual(modules.api.url('/api/v1/health'), 'http://127.0.0.1:19000/api/v1/health');
  assert.strictEqual(modules.api.wsUrl('/ws/scan/x'), 'ws://127.0.0.1:19000/ws/scan/x');

  const normalSource = await modules.audio.resolvePlaybackSource({ filePath: '/tmp/test.wav', fileId: 'wav-id' });
  assert(normalSource.startsWith('soundbot-audio://local/'));
  const soundA = { id: 'sound-a', fileId: 'sound-a' };
  const soundB = { id: 'sound-b', fileId: 'sound-b' };
  assert.strictEqual(modules.audio.playbackAction(soundA, soundA, true), 'pause');
  // selectSound(B) has already replaced currentSound while the old playing
  // flag may still describe A. One AI-result click must nevertheless play B.
  assert.strictEqual(
    modules.audio.playbackAction(soundB, soundB, true, { forcePlay: true }),
    'play',
    'AI select-and-play was mistaken for a pause because of stale isPlaying state'
  );
  const callCountBeforeWma = calls.length;
  const wmaSource = await modules.audio.resolvePlaybackSource(
    { filePath: 'C:\\Audio\\test.wma', fileId: 'wma-id' },
    { api: modules.api, projectId: 'project-a' }
  );
  assert.strictEqual(wmaSource, 'soundbot-audio://local/%2Ftmp%2Frendered.wav');
  assert.strictEqual(calls.length, callCountBeforeWma + 1);
  assert(calls.at(-1).url.includes('/api/v1/files/wma-id/playback-source?project_id=project-a'));
  await modules.audio.resolvePlaybackSource(
    { filePath: 'C:\\Audio\\test.wma', fileId: 'wma-id' },
    { api: modules.api, projectId: 'project-a' }
  );
  assert.strictEqual(calls.length, callCountBeforeWma + 2, 'renderer retained an evictable backend WAV path');

  assert.strictEqual(modules.waveform.isValidData([]), false);
  assert.strictEqual(modules.waveform.isValidData([0, 0.25, -0.5]), true);
  assert.strictEqual(modules.waveform.isValidData([0, NaN]), false);

  let createdLayers = 0;
  const context2d = () => ({
    setTransform() {}, fillRect() {}, save() {}, beginPath() {}, rect() {}, clip() {}, translate() {},
    moveTo() {}, lineTo() {}, closePath() {}, fill() {}, restore() {}, clearRect() {}, drawImage() {},
    stroke() {}, arc() {}
  });
  const ownerDocument = {
    createElement: () => {
      createdLayers += 1;
      return { width: 0, height: 0, getContext: context2d };
    }
  };
  const mainCanvas = {
    width: 0,
    height: 0,
    ownerDocument,
    getBoundingClientRect: () => ({ width: 300, height: 80 }),
    getContext: context2d
  };
  const peaks = [0.1, 0.4, 0.2, 0.8];
  modules.waveform.renderMain(mainCanvas, { waveformData: peaks, duration: 1000, progress: 0.1 });
  modules.waveform.renderMain(mainCanvas, { waveformData: peaks, duration: 1000, progress: 0.8 });
  assert.strictEqual(createdLayers, 2, 'static waveform layers were rebuilt for a progress-only frame');

  const inlineScripts = [...indexHtml.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)];
  assert.strictEqual(inlineScripts.length, 0, 'workspace logic should be packaged in renderer modules');
  for (const [index, match] of inlineScripts.entries()) {
    new vm.Script(match[1], { filename: `index.inline.${index}.js` });
  }

  console.log('Renderer module and integration contract checks passed.');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
