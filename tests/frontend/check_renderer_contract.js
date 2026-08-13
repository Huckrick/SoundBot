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
const moduleNames = ['api', 'state', 'audio', 'waveform', 'search', 'projects', 'settings'];
const moduleSources = Object.fromEntries(
  moduleNames.map(name => [name, read(`assets/renderer/${name}.js`)])
);

async function main() {
  assert(!/wavesurfer/i.test(indexHtml), 'renderer still references WaveSurfer');
  assert(!/playbackWebSocket|connectPlaybackWebSocket|disconnectPlaybackWebSocket/.test(indexHtml), 'backend playback WebSocket remains');
  assert(!/readAudioFile|read-audio-file/.test(indexHtml + mainJs + preloadJs), 'whole-file IPC reader remains');
  assert(!/requestBackendBinary|audio-stream/.test(mainJs + preloadJs), 'unused whole-file binary playback IPC remains');

  for (const name of moduleNames) {
    const scriptPath = `./assets/renderer/${name}.js`;
    assert(indexHtml.includes(`<script src="${scriptPath}"></script>`), `${name} module is not loaded`);
    assert(indexHtml.includes(`rendererModules.${name}`), `${name} module is not referenced by the page`);
    new vm.Script(moduleSources[name], { filename: `${name}.js` });
  }

  assert(!/function apiUrl|function wsUrl|function drawBarWaveform|new ResizeObserver|window\.devicePixelRatio/.test(indexHtml), 'extracted renderer core remains inline');
  assert(!/function getSecretUpdateForInput|function getAiSecretUpdates/.test(indexHtml), 'secret-intent core remains inline');
  assert(indexHtml.includes('rendererWaveform.renderMain(canvas'), 'main waveform is not rendered by the cached module');
  assert(indexHtml.includes('rendererAudio.resolvePlaybackSource(sound'), 'playback-source selection is not wired');
  assert(indexHtml.includes('r.metadata?.file_id'), 'AI results do not preserve stable file IDs');
  assert(indexHtml.includes('selectAndPlaySound(soundObj);'), 'AI result playback is not an explicit select-and-play action');
  assert(indexHtml.includes('rendererSearch.buildPayload({'), 'search payload module is not wired');
  assert(indexHtml.includes("rendererApi.post('/api/v1/search', searchRequest)"), 'project-scoped search request is not wired');
  assert(indexHtml.includes('rendererProjects.listFiles('), 'cursor file API is not wired');
  assert(indexHtml.includes('loadFilesFromDatabase({ reset: false })'), 'cursor continuation is not wired');
  assert(indexHtml.includes('id="audioIndexStatus"') && indexHtml.includes('id="textIndexStatus"'), 'project index status UI is missing');
  assert(indexHtml.includes("runProjectIndexAction('reconcile')"), 'reconcile action is not wired');
  assert(indexHtml.includes("runProjectIndexAction('rebuild')"), 'rebuild action is not wired');

  assert(moduleSources.waveform.includes('value.length > 0 && value.every(Number.isFinite)'), 'strict waveform validation is missing');
  assert(moduleSources.waveform.includes('const layerCache = new WeakMap()'), 'offscreen waveform cache is missing');
  assert(moduleSources.waveform.includes('inactiveLayer') && moduleSources.waveform.includes('activeLayer'), 'static waveform layers are missing');
  assert(moduleSources.audio.includes("['aif', 'aiff', 'm4a', 'aac', 'wma']"), 'transcode extension list is incomplete');
  assert(moduleSources.audio.includes('/playback-source?project_id='), 'file-ID playback-source endpoint is missing');
  assert(moduleSources.projects.includes('/index/reconcile') && moduleSources.projects.includes('/index/rebuild'), 'project index actions are missing');
  assert(moduleSources.projects.includes('/api/v1/files?'), 'cursor files endpoint is missing');

  assert(indexHtml.includes('id="waveformStatus"'), 'waveform loading/error status is missing');
  assert(indexHtml.includes('id="waveformRetryBtn"'), 'waveform retry control is missing');
  assert(!/伪随机 fallback|备用假数据/.test(indexHtml), 'generated waveform fallback remains');
  assert(indexHtml.includes("importFiles(\n                        filePaths"), 'file import is not routed to the backend job');
  assert(mainJs.includes("case 'import-files':"), 'main-process import-files proxy is missing');
  assert(preloadJs.includes('importFiles: (filePaths'), 'preload import-files API is missing');
  assert(mainJs.includes('/projects/${encodeURIComponent(requireProjectId(data.projectId))}/imports'), 'project-scoped import proxy is missing');
  assert(indexHtml.includes('currentProject?.id\n                            );'), 'folder import does not pass the active project');
  assert(!moduleSources.search.includes("options.projectId || 'default'"), 'renderer search still silently falls back to the default project');
  assert(!/top_k\s*:\s*10000|searchAudio\([^\n]*10000/.test(indexHtml + mainJs + preloadJs), 'legacy top_k=10000 remains');
  assert(mainJs.includes("case 'waveform-by-id':"), 'main-process waveform-by-id proxy is missing');
  assert(mainJs.includes("const BACKEND_ERROR_SENTINEL = '__soundbotBackendError'"), 'structured backend error sentinel is missing');
  assert(preloadJs.includes('throw createRendererError(result.error)'), 'preload does not reject structured backend errors');
  assert(mainJs.includes('retryable: Boolean(') && mainJs.includes('details,'), 'structured backend error fields are incomplete');
  assert(mainJs.includes("ipcMain.handle('backend-api-cancel'"), 'backend request cancellation IPC is missing');
  assert(mainJs.includes('match(/BOUND_PORT='), 'backend bound-port handshake is not parsed');
  assert(mainJs.includes('updateBackendEndpoint(Number(match[1]))'), 'announced backend port is not authoritative');
  assert(mainJs.includes("payload?.status === 'healthy'"), 'health polling can accept an unrelated HTTP service');
  assert(preloadJs.includes("cancelRequest: (requestId) => ipcRenderer.invoke('backend-api-cancel'"), 'preload cancellation API is missing');
  assert(indexHtml.includes('cancelActiveWaveformRequest();'), 'main waveform does not cancel stale HTTP requests');
  assert(mainJs.includes('safeStorage.encryptString'), 'safeStorage secret encryption is missing');
  assert(preloadJs.includes('secrets: {'), 'secret API is not exposed by preload');

  for (const provider of ['azure', 'gemini', 'anthropic', 'kimi_coding']) {
    const pattern = new RegExp(`<label class="[^"]*hidden[^"]*"[^>]*data-llm-provider="${provider}"`);
    assert(pattern.test(indexHtml), `${provider} should be hidden`);
  }

  assert(/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(packageJson.version), 'package version is not SemVer');
  assert(backendVersionMatch, 'backend APP_VERSION is missing');
  assert.strictEqual(packageJson.version, backendVersionMatch[1], 'frontend/backend versions drifted');
  assert(packageJson.build.files.includes('assets/**/*'), 'renderer modules are not included in packaging');
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

  const store = modules.state.createStore({ project: 'default' });
  assert.strictEqual(store.set('project', 'demo'), 'demo');
  assert.strictEqual(store.get('project'), 'demo');
  assert(modules.state.isSameOrChildPath('C:\\Audio\\FX', 'C:\\Audio'));

  assert.throws(() => modules.search.buildPayload({ query: 'hit' }), /projectId is required/);
  const payload = modules.search.buildPayload({
    query: ' hit ', topK: 10000, threshold: 2, page: 0, pageSize: 50, projectId: 'project-a'
  });
  assert.strictEqual(payload.query, 'hit');
  assert.strictEqual(payload.top_k, 1000);
  assert.strictEqual(payload.threshold, 1);
  assert.strictEqual(payload.page, 1);
  assert.strictEqual(payload.project_id, 'project-a');
  assert.strictEqual(
    modules.search.buildChatPayload({ message: 'hit', projectId: 'project-a' }).project_id,
    'project-a'
  );

  const normalSource = await modules.audio.resolvePlaybackSource({ filePath: '/tmp/test.wav', fileId: 'wav-id' });
  assert(normalSource.startsWith('soundmind-audio://local/'));
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
  assert.strictEqual(wmaSource, 'soundmind-audio://local/%2Ftmp%2Frendered.wav');
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

  await modules.projects.listFiles('project-a', { limit: 200, cursor: 9 });
  assert(calls.at(-1).url.includes('/api/v1/files?project_id=project-a&limit=200&cursor=9'));
  const indexStatus = await modules.projects.indexStatus('project-a');
  assert.deepStrictEqual(
    JSON.parse(JSON.stringify(modules.projects.artifactSummary(indexStatus, 'audio_vector'))),
    { ready: 5, pending: 3, failed: 1 }
  );
  await modules.projects.reconcileIndex('project-a', ['audio_vector', 'text_vector']);
  assert(calls.at(-1).url.endsWith('/api/v1/projects/project-a/index/reconcile'));
  assert.deepStrictEqual(JSON.parse(calls.at(-1).options.body).kinds, ['audio_vector', 'text_vector']);
  const completedJob = await modules.projects.waitForJob('job-1');
  assert.strictEqual(completedJob.state, 'completed');

  assert.deepStrictEqual(
    JSON.parse(JSON.stringify(modules.settings.secretIntent({ dataset: { secretAction: 'keep' }, value: '' }))),
    { action: 'keep' }
  );
  assert.deepStrictEqual(
    JSON.parse(JSON.stringify(modules.settings.secretIntent({ dataset: { secretAction: 'set' }, value: ' new-key ' }))),
    { action: 'set', value: 'new-key' }
  );
  assert.deepStrictEqual(
    JSON.parse(JSON.stringify(modules.settings.secretIntent({ dataset: { secretAction: 'clear' }, value: '' }))),
    { action: 'clear' }
  );

  const inlineScripts = [...indexHtml.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)];
  assert(inlineScripts.length > 0, 'no inline renderer script found');
  for (const [index, match] of inlineScripts.entries()) {
    new vm.Script(match[1], { filename: `index.inline.${index}.js` });
  }

  console.log('Renderer module and integration contract checks passed.');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
