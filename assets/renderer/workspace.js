/* Library views, real jobs, selection, and DAW delivery. */
(() => {
    const { api, audio, waveform } = window.SoundBotRenderer;
    const i18n = window.SoundBotI18n;
    const { t, number, date, describeError, setText } = i18n;
    const fx = () => window.SoundBotEffects;
    const libraryName = project => project?.id === 'default' && project.name === '默认工程' ? t('默认音效库') : project?.name || '';
    const clipSessions = new Map();
    let libraryEffects;
    const desktop = window.electronAPI;
    const playback = window.SoundBotPlayback;
    const $ = id => document.getElementById(id);
    const state = {
        library: null, libraries: [], workspace: 'library', items: [], cursor: null, total: 0,
        view: 'folder', filter: {}, sort: 'newest', groupByFolder: false, displayRows: [],
        search: '', selected: null, selectedIds: new Set(),
        range: null, waveform: null, zoom: 1, pan: 0, position: 0, loop: false, muted: false,
        dawDirectory: null, jobs: [], facets: null, dockEditing: false
    };
    let toastTimer;
    let listRequest = null;
    let listQueryKey;
    let listWindowStart = null;
    const listPreviousWindows = [];
    const listWindowLimit = 480;
    let peakRequest = null;
    let previewRevision = 0;
    let renderFrame = null;
    let rowWindow;
    let rowNodes = new Map();
    let effectsPreviewTimer;
    let renderedWave;
    const waveDuration = () => Math.max(state.selected?.duration || 0, renderedWave?.duration || 0);
    const auditionEnd = () => Math.max(state.range?.[1] ?? state.selected?.duration ?? 0, renderedWave?.duration || 0);
    let libraryRevision = 0;
    let directoryRevision = 0;
    let treeView;
    const expandedFolders = new Set();
    let groupRevision = 0;
    let libraryEditor;
    let timelinePosition = 0;
    let activeJobs = [];
    const preparedDrags = new Map();
    let preparingDrag;
    let deliveryRevision = 0;
    let audioPress;
    let clipDrag;
    let refreshRowsAfterDrag = false;
    let libraryPanel = 'tabDetail';
    let wwisePanel = 'tabDetail';
    const peakCache = new Map();
    const miniPeaks = new Map();
    const miniLimit = 8192;
    const artCache = new Map();
    const artworkRequests = new Map();
    let sourceDetails;
    let detailsRequest;
    const previewItems = new WeakMap();
    const ROW = 52;
    const route = (part = '', id = state.library?.id) => `/api/v1/projects/${encodeURIComponent(id || 'default')}${part}`;
    const fileRoute = (id, part, params = {}) => `/api/v1/files/${encodeURIComponent(id)}/${part}?${new URLSearchParams({ project_id: state.library.id, ...params })}`;
    const say = message => {
        $('toast').textContent = message;
        $('toast').classList.add('show');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => $('toast').classList.remove('show'), 3500);
    };
    const fmt = seconds => {
        const ms = Math.round(Math.max(0, Number(seconds) || 0) * 1000);
        return `${String(Math.floor(ms / 60000)).padStart(2, '0')}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}.${String(ms % 1000).padStart(3, '0')}`;
    };
    const attempt = async fn => {
        try { return await fn(); }
        catch (error) {
            if (error.name === 'AbortError') return;
            console.warn('[SoundBot]', error);
            say(describeError(error));
        }
    };
    const el = (tag, text, className) => {
        const node = document.createElement(tag);
        if (text != null) node.textContent = text;
        if (className) node.className = className;
        return node;
    };
    const button = (text, fn, className = 'ghost-btn') => {
        const node = el('button', text, className);
        node.type = 'button';
        node.onclick = () => attempt(fn);
        return node;
    };
    const modal = (id, open = true) => $(id).classList.toggle('show', open);
    const isLibrary = () => state.workspace === 'library';
    const isSourceEditing = () => isLibrary() || state.dockEditing;
    const timeline = () => window.SoundBotTimeline;
    const color = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

    function updateDragHandle() {
        const drag = $('dragRange');
        const request = rangeDrag();
        const preparing = Boolean(request && preparingDrag?.signature === deliverySignature(request));
        const label = preparing ? t('正在渲染…') : t('拖出到 DAW');
        $('dragRangeLabel').textContent = label;
        drag.setAttribute('aria-busy', String(preparing));
        drag.classList.toggle('ready', Boolean(request && preparedDrags.has(deliverySignature(request))));
    }

    function invalidateDelivery() {
        deliveryRevision++;
        preparedDrags.clear();
        updateDragHandle();
    }

    function syncTransport() {
        const time = playback.active ? playback.currentTime() : state.position;
        $('tc').textContent = fmt(time);
        $('tcTotal').textContent = `/ ${fmt(isSourceEditing() ? auditionEnd() : timeline()?.auditionEnd())}`;
        $('btnPlay').classList.toggle('playing', playback.active);
        $('btnPlay').setAttribute('aria-pressed', String(playback.active));
        $('playIcon').innerHTML = playback.active
            ? '<path d="M3 2h2v8H3zm4 0h2v8H7z" fill="currentColor"/>'
            : '<path d="M2.5 1.5l8 4.5-8 4.5z" fill="currentColor"/>';
        if (isSourceEditing()) drawWave(time);
        else timeline()?.playhead(time);
    }

    function stop(reset = false) {
        if (playback.active && !reset) state.position = playback.currentTime();
        playback.stop();
        if (reset) { state.position = 0; renderedWave = null; }
        syncTransport();
    }

    function refreshAudition() {
        if (!playback.active) return;
        const generation = playback.generation;
        clearTimeout(effectsPreviewTimer);
        effectsPreviewTimer = setTimeout(() => {
            if (!playback.active || playback.generation !== generation) return;
            stop();
            attempt(isSourceEditing() ? playLibrary : () => timeline()?.play());
        }, 180);
    }

    async function playLibrary() {
        const item = state.selected;
        if (!item || item.missing) return say(t('请选择可用的音频素材'));
        const range = state.range;
        const edits = currentClip();
        const masterEffects = libraryEffects ? fx().enabled(libraryEffects, true) : [];
        const processed = range || edits?.gain || edits?.fade_in || edits?.fade_out || masterEffects.length;
        const offset = processed ? Math.max(range?.[0] || 0, state.position) : state.position;
        const end = auditionEnd();
        const start = offset >= end ? (range?.[0] || 0) : offset;
        const playbackId = ++previewRevision;
        const onEnd = () => {
            state.position = range?.[0] || 0;
            if (state.loop && playbackId === previewRevision && isSourceEditing()) attempt(playLibrary);
            syncTransport();
        };
        if (processed && edits) {
            const startRelative = start - edits.offset;
            const rendered = (start > 0 && renderedWave) || playback.renderedPeaks(item.duration);
            return fx().preview({
                project_id: state.library.id, tracks: [{ clips: [edits] }], master_effects: masterEffects,
                rate: item.sample_rate || 48000, channels: masterEffects.some(effect => effect.kind === 'vst3') ? 2 : item.channels || 2,
                start: startRelative
            }, start, onEnd, (buffer, time) => {
                if (playbackId !== previewRevision) return;
                rendered.append(buffer, time);
                renderedWave = rendered;
            });
        }
        renderedWave = null;
        if (!audio.requiresPlaybackSource({ filePath: item.path, format: item.format })) {
            try { return await playback.playSource(api.url(fileRoute(item.id, 'source')), start, onEnd); }
            catch (error) {
                if (playbackId !== previewRevision || state.selected !== item) return;
                if (error.name === 'NotAllowedError') throw error;
            }
        }
        return playback.playPCM(api.url(fileRoute(item.id, 'stream', { offset: start })), {}, start, onEnd);
    }

    function togglePlay() {
        if (playback.active) {
            previewRevision++;
            stop();
        } else attempt(isSourceEditing() ? playLibrary : () => timeline()?.play());
    }

    function currentClip() {
        const item = state.selected;
        if (!item?.duration || item.missing) return null;
        const [offset, end] = state.range || [0, item.duration];
        const duration = end - offset;
        if (duration <= 0.001) return null;
        return { file_id: item.id, offset, duration, start: 0,
            gain: Math.min(24, Math.max(-96, Number($('clipGain').value) || 0)),
            fade_in: Math.min(duration, Math.max(0, Number($('fadeIn').value) || 0)),
            fade_out: Math.min(duration, Math.max(0, Number($('fadeOut').value) || 0)) };
    }

    function positionRangeHandles() {
        const waveArea = $('wave').closest('.wave-gesture');
        const duration = waveDuration();
        const fraction = time => Math.min(1, Math.max(0, time / duration * state.zoom - state.pan));
        const left = duration && state.range ? fraction(state.range[0]) : 0;
        const right = duration && state.range ? fraction(state.range[1]) : 0;
        waveArea.style.setProperty('--range-left', `${left * 100}%`);
        waveArea.style.setProperty('--range-width', `${(right - left) * 100}%`);
        for (const id of ['dragRange', 'moveRange']) $(id).classList.toggle('hidden', !(right > left));
    }

    function drawWave(position) {
        const item = state.selected;
        const canvas = $('wave');
        const duration = waveDuration();
        if (!item || !state.waveform) {
            canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
            return;
        }
        if (renderedWave && (!renderedWave.data || renderedWave.dirty !== renderedWave.duration)) {
            const original = item.duration;
            const useRendered = time => !state.range || (time >= state.range[0] && time < renderedWave.duration);
            renderedWave.data = Float32Array.from({ length: 2000 }, (_, index) => {
                const time = index / 2000 * duration;
                const wet = useRendered(time) ? renderedWave.peakBetween(time, (index + 1) / 2000 * duration) : -1;
                if (wet >= 0) return wet;
                return time < original ? state.waveform[Math.floor(time / original * state.waveform.length)] || 0 : 0;
            });
            renderedWave.dirty = renderedWave.duration;
            positionRangeHandles();
            showRange();
        }
        waveform.renderMain(canvas, {
            waveformData: renderedWave?.data || state.waveform, duration, zoomLevel: state.zoom,
            scrollOffset: canvas.clientWidth * state.pan, progress: position / (duration || 1),
            activeColor: color('--tx1'), inactiveColor: color('--tx2'), inactiveOpacity: 0.6,
            backgroundColor: color('--bg0'), selectionColor: color('--tx1'),
            highlightSelection: true,
            selectionStart: state.range?.[0] ?? null, selectionEnd: state.range?.[1] ?? null
        });
    }

    function showRange() {
        const range = state.range;
        $('wIn').textContent = `${t('入点')} ${range ? fmt(range[0]) : '—'}`;
        $('wOut').textContent = `${t('出点')} ${range ? fmt(range[1]) : '—'}`;
        $('wSel').textContent = range ? `${t('选区')} ${fmt(range[1] - range[0])}`
            : t('拖动波形框选');
        const tail = Math.max(0, (renderedWave?.duration || 0) - (range?.[1] ?? state.selected?.duration ?? 0));
        if (tail > 0.001) $('wSel').textContent += ` · ${t('效果尾音')} +${fmt(tail)}`;
    }

    function updateRange() {
        renderedWave = null;
        showRange();
        positionRangeHandles();
        updateDragHandle();
        syncTransport();
    }

    function keepClip() {
        if (!currentClip()) return;
        const controls = Object.fromEntries(['fadeIn', 'fadeOut', 'clipGain'].map(id => [id, $(id).value]));
        const name = $('clipName').value.trim();
        if (!state.range && !name && !Object.values(controls).some(Number)) {
            clipSessions.delete(state.selected.id);
            return;
        }
        clipSessions.set(state.selected.id, {
            name,
            range: state.range && [...state.range],
            controls
        });
    }

    function editorClip() {
        const clip = currentClip();
        if (!clip) return null;
        const item = { ...state.selected, filename: $('clipName').value.trim() || state.selected.filename, peaks: state.waveform };
        return { item, clip };
    }

    async function select(item, play = false) {
        keepClip();
        previewRevision++;
        peakRequest?.abort();
        stop(true);
        state.selected = item;
        const saved = item && clipSessions.get(item.id);
        fx()?.resetLoudness();
        if (isLibrary()) showLibraryEffects();
        state.range = saved?.range && saved.range[1] <= item.duration ? [...saved.range] : null;
        state.waveform = null;
        state.zoom = 1;
        state.pan = 0;
        $('zoomLevel').textContent = '100%';
        for (const id of ['fadeIn', 'fadeOut', 'clipGain']) $(id).value = saved?.controls[id] ?? 0;
        if (isLibrary()) updateAssetDetails(item);
        $('editorFileName').title = item?.path || '';
        $('editorFileName').textContent = item?.title || item?.filename || '—';
        $('clipName').value = saved?.name || '';
        $('editorSourceBadge').textContent = libraryName(state.library);
        $('sampleRateInfo').textContent = item?.sample_rate ? `${number(item.sample_rate)} Hz` : '';
        updateRange();
        if (isLibrary()) {
            for (const row of $('rows').querySelectorAll('[data-file-id]')) {
                row.classList.toggle('sel', row.dataset.fileId === item?.id);
            }
            if ($('tabGroups').classList.contains('on')) attempt(renderAssetGroups);
        }
        if (!item || item.missing) {
            setText($('waveformStatus'), item?.missing ? () => t('音频离线 · 可重新定位') : '');
            return;
        }
        const key = previewKey(item);
        const cached = peakCache.get(key);
        if (play) attempt(playLibrary);
        if (cached) {
            state.waveform = cached;
            setText($('waveformStatus'), '');
            syncTransport();
            return;
        }
        const current = peakRequest = new AbortController();
        setText($('waveformStatus'), () => t('正在读取波形…'));
        try {
            const result = await api.get(fileRoute(item.id, 'waveform'), { signal: current.signal });
            if (current.signal.aborted) return;
            state.waveform = result.peaks;
            if (!item.duration && result.duration) item.duration = result.duration;
            rememberPreview(peakCache, key, result.peaks, 512);
            if ((!item.source_fingerprint || result.source_fingerprint === item.source_fingerprint) && !miniPeaks.has(key)) {
                const compact = new Float32Array(256);
                for (let index = 0; index < compact.length; index++) {
                    const lo = Math.floor(index * result.peaks.length / compact.length);
                    const hi = Math.floor((index + 1) * result.peaks.length / compact.length);
                    compact[index] = Math.max(...result.peaks.slice(lo, hi));
                }
                rememberPreview(miniPeaks, key, compact, miniLimit);
            }
            scheduleMiniPeaks();
            setText($('waveformStatus'), '');
            syncTransport();
        } catch (error) {
            if (!current.signal.aborted) setText($('waveformStatus'), () => describeError(error));
        }
    }

    function updateAssetDetails(item) {
        $('inspName').textContent = item?.title || item?.filename || t('选择素材');
        $('inspMeta').textContent = item ? [
            item.format?.toUpperCase(), item.sample_rate ? `${number(item.sample_rate)} Hz` : '',
            item.channels ? `${t('声道数：{count}', { count: item.channels })}` : '', fmt(item.duration)
        ].filter(Boolean).join(' · ') : '';
        $('btnFavorite').textContent = item?.favorite ? t('取消收藏') : t('添加到收藏');
        $('btnRelocate').disabled = !item;
        $('btnAssetInfo').disabled = !item;
        $('btnReveal').disabled = !item;
        $('btnFavorite').disabled = !item;
        $('btnUseWwise').disabled = !item || item.missing;
        $('assetArtwork').classList.toggle('hidden', !item);
        $('assetArtworkImage').replaceChildren(...(item ? [assetCover(item)] : []));
        $('artworkMetadata').textContent = item?.missing ? t('素材离线') : t('正在读取封面…');
        const key = item && previewKey(item);
        if (sourceDetails?.key !== key) sourceDetails = null;
        if (detailsRequest?.key !== key) detailsRequest?.controller.abort();
        $('assetDetailsStatus').hidden = true;
        renderAssetDetails(item, sourceDetails?.data);
        loadAssetDetails();
    }

    function formatBytes(size) {
        if (!Number.isFinite(size)) return '';
        if (size < 1024) return `${number(size)} B`;
        const power = Math.min(3, Math.floor(Math.log(size) / Math.log(1024)));
        return `${number(size / 1024 ** power, { maximumFractionDigits: 1 })} ${['B', 'KB', 'MB', 'GB'][power]}`;
    }

    function renderAssetDetails(item, details) {
        const root = $('assetDetailsSections');
        const open = new Set([...root.querySelectorAll('details[open]')].map(node => node.dataset.section));
        root.replaceChildren();
        if (!item) return;
        const rows = fields => {
            const list = el('dl');
            for (const [name, value] of fields) {
                if (value === null || value === undefined || value === '') continue;
                const row = el('div', null, 'asset-data-row');
                row.append(el('dt', name), el('dd', String(value)));
                list.append(row);
            }
            return list;
        };
        const section = (key, title, fields, expandable = false) => {
            const content = rows(fields);
            if (!content.childElementCount) return;
            const block = el(expandable ? 'details' : 'section', null, 'asset-section');
            block.dataset.section = key;
            if (expandable) block.open = open.has(key);
            block.append(el(expandable ? 'summary' : 'h3', title), content);
            root.append(block);
        };
        const source = details?.audio || {};
        const file = details?.file || {};
        const value = (n, unit) => Number.isFinite(n) && n > 0 ? `${number(n)} ${unit}` : '';
        section('library', t('库内信息'), [
            [t('描述'), item.description],
            [t('库内标签'), item.tags?.join(' · ')],
            ['UCS', item.ucs],
            [t('素材库'), item.library_name],
            [t('关键词'), item.keywords]
        ]);
        const tagNames = {
            title: t('原始标题'), artist: t('艺术家'), album: t('专辑'),
            album_artist: t('专辑艺术家'), composer: t('作曲'), genre: t('流派'),
            date: t('日期'), track: t('曲目'), disc: t('碟号'),
            comment: t('备注'), copyright: t('版权'), publisher: t('发行方')
        };
        const originalTags = new Map(Object.values(details?.tags || {}).flatMap(group =>
            Object.entries(group).map(([key, entry]) => [key.toLowerCase(), entry])));
        section('common', t('原始标签'), Object.entries(tagNames).map(([key, name]) => [name, originalTags.get(key)]));
        section('audio', t('音频规格'), [
            [t('时长'), fmt(source.duration || item.duration)],
            [t('采样率'), value(source.sample_rate || item.sample_rate, 'Hz')],
            [t('声道'), source.channels || item.channels],
            [t('声道布局'), source.channel_layout],
            [t('位深'), value(source.bit_depth, 'bit')],
            [t('码率'), source.bit_rate ? value(Math.round(source.bit_rate / 1000), 'kbps') : ''],
            [t('编码'), source.codec],
            [t('容器'), source.container || item.format?.toUpperCase()],
            [t('音频流数'), source.audio_streams]
        ]);
        section('file', t('文件信息'), [
            [t('文件名'), item.filename],
            [t('大小'), formatBytes(file.size ?? item.size)],
            [t('修改时间'), file.modified_at ? date(file.modified_at * 1000) : ''],
            [t('位置'), item.path]
        ]);
        const bwfNames = new Map([
            ['description', t('描述')], ['originator', t('制作方')],
            ['originator_reference', t('制作方参考编号')],
            ['origination_date', t('录制日期')], ['origination_time', t('录制时间')],
            ['time_reference', t('时间参考（采样点）')], ['version', t('版本')],
            ['umid', 'UMID'], ['coding_history', t('编码历史')],
            ['loudness_value', t('综合响度 LUFS')],
            ['loudness_range', t('响度范围 LU')],
            ['max_true_peak', t('最大真峰值 dBTP')],
            ['max_momentary', t('最大瞬时响度 LUFS')],
            ['max_short_term', t('最大短时响度 LUFS')]
        ]);
        const ixmlNames = new Map([
            ['PROJECT', t('工程')], ['SCENE', t('场次')], ['TAKE', t('拍次')],
            ['TAPE', t('卷号')], ['NOTE', t('备注')]
        ]);
        const groupedTags = [
            ['container', t('容器标签'), details?.tags?.container],
            ['stream', t('音频流标签'), details?.tags?.stream],
            ['embedded', t('内嵌标签'), details?.tags?.embedded],
            ['bwf', t('BWF 广播扩展'), details?.bwf, bwfNames],
            ['ixml', t('iXML 录音信息'), details?.ixml, ixmlNames]
        ];
        for (const [key, title, tags, names] of groupedTags) {
            if (tags) section(key, title, Object.entries(tags).map(([field, entry]) => [names?.get(field) || field, entry]), true);
        }
        const rate = source.sample_rate || item.sample_rate;
        const sampleTime = sample => rate ? `${fmt(sample / rate)} (${number(sample)})` : number(sample);
        section('markers', t('WAV 标记'), (details?.markers || [])
            .map(marker => [`#${marker.id}`, sampleTime(marker.sample)]), true);
        section('loops', t('WAV 循环'), (details?.loops || [])
            .map(loop => [`#${loop.id}`, `${sampleTime(loop.start)} → ${sampleTime(loop.end)} · ${loop.count || '∞'}`]), true);
    }

    function loadAssetDetails() {
        const item = state.selected;
        if (!isLibrary() || !item || $('detailPanel').classList.contains('hidden')) return;
        const status = $('assetDetailsStatus');
        status.hidden = true;
        if (item.missing) {
            status.hidden = false;
            status.textContent = t('素材离线');
            return;
        }
        const key = previewKey(item);
        const cover = $('assetArtworkImage').querySelector('.asset-cover');
        requestArtwork(item).then(artwork => {
            if (cover?.isConnected) showArtwork(cover, artwork);
        }).catch(() => {
            if (cover?.isConnected) $('artworkMetadata').textContent = t('封面暂时无法读取');
        });
        if (sourceDetails?.key === key) return;
        status.hidden = false;
        status.textContent = t('正在读取源文件信息…');
        if (detailsRequest?.key === key) return;
        const controller = new AbortController();
        detailsRequest = { key, controller };
        api.get(fileRoute(item.id, 'details'), { signal: controller.signal }).then(data => {
            if (controller.signal.aborted || !isLibrary() || state.selected !== item) return;
            sourceDetails = { key, data };
            status.hidden = true;
            renderAssetDetails(item, data);
        }).catch(() => {
            if (!controller.signal.aborted && isLibrary() && state.selected === item) {
                status.textContent = t('源文件信息暂时无法读取');
            }
        }).finally(() => {
            if (detailsRequest?.controller === controller) detailsRequest = null;
        });
    }

    async function openDockEditor(item) {
        if (state.workspace !== 'wwise') return;
        if (!state.dockEditing) {
            timelinePosition = playback.active ? playback.currentTime() : state.position;
            state.dockEditing = true;
            syncBottomStage();
        }
        await select(item, true);
    }

    function closeDockEditor() {
        if (!state.dockEditing) return;
        keepClip();
        previewRevision++;
        peakRequest?.abort();
        playback.stop();
        state.dockEditing = false;
        state.selected = null;
        state.waveform = state.range = null;
        state.position = timelinePosition;
        syncBottomStage();
        syncTransport();
    }

    function syncBottomStage() {
        const dockVisible = !isLibrary() && !state.dockEditing;
        $('bottomStage').classList.toggle('dock-editing', state.dockEditing);
        $('sourceEditor').inert = dockVisible;
        $('miniLibrary').inert = !dockVisible;
        $('btnBackDock').classList.toggle('hidden', !state.dockEditing);
        $('clipOptions').open = false;
        showRange();
        positionRangeHandles();
        updateDragHandle();
        scheduleMiniPeaks();
    }

    function bindWave() {
        const canvas = $('wave');
        let gesture;
        let moving;
        const move = $('moveRange');
        const drag = $('dragRange');
        const moveBy = (range, delta) => {
            const length = range[1] - range[0];
            const start = Math.max(0, Math.min(state.selected.duration - length, range[0] + delta));
            state.range = [start, start + length];
            state.position = start;
            updateRange();
        };
        move.onpointerdown = event => {
            if (event.button !== 0 || !state.range) return;
            previewRevision++;
            stop();
            moving = { range: [...state.range], x: event.clientX, pointer: event.pointerId };
            move.setPointerCapture(event.pointerId);
        };
        move.onpointermove = event => {
            if (moving?.pointer !== event.pointerId) return;
            moveBy(moving.range, (event.clientX - moving.x) / canvas.clientWidth * waveDuration() / state.zoom);
        };
        move.onlostpointercapture = () => { moving = null; };
        move.onkeydown = event => {
            if (!state.range || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
            event.preventDefault();
            stop();
            moveBy(state.range, (event.key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 0.1 : 0.01));
        };
        bindAudioDrag(drag, rangeDrag);
        const timeAt = (event, duration = waveDuration()) => {
            const rect = canvas.getBoundingClientRect();
            const fraction = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
            return duration * (fraction + state.pan) / state.zoom;
        };
        canvas.onpointerdown = event => {
            if (!state.selected?.duration || event.button !== 0) return;
            const t = timeAt(event);
            const threshold = waveDuration() / canvas.clientWidth / state.zoom * 9;
            const edge = state.range?.findIndex(value => Math.abs(t - value) < threshold) ?? -1;
            gesture = { x: event.clientX, anchor: edge < 0 ? t : state.range[1 - edge], previous: state.range,
                duration: waveDuration() };
            canvas.setPointerCapture(event.pointerId);
        };
        canvas.onpointermove = event => {
            if (!gesture || (!gesture.moved && Math.abs(event.clientX - gesture.x) < 3)) return;
            if (!gesture.moved) {
                previewRevision++;
                stop();
                gesture.moved = true;
            }
            state.range = [gesture.anchor, timeAt(event, gesture.duration)].map(time => Math.min(state.selected.duration, time)).sort((a, b) => a - b);
            updateRange();
        };
        canvas.onpointerup = event => {
            if (!gesture) return;
            if (!gesture.moved) {
                const wasPlaying = playback.active;
                previewRevision++;
                stop();
                const time = timeAt(event);
                state.position = time;
                if (time <= (state.range?.[1] ?? state.selected.duration)) {
                    state.range = null;
                    updateRange();
                } else if (time < (renderedWave?.duration || 0)) syncTransport();
                else {
                    state.range = null;
                    state.position = Math.min(time, state.selected.duration);
                    updateRange();
                }
                if (wasPlaying) attempt(playLibrary);
            } else {
                state.position = state.range[0];
                if (state.range[1] - state.range[0] <= 0.001) state.range = null;
                updateRange();
            }
            gesture = null;
        };
        canvas.onlostpointercapture = () => {
            if (gesture?.moved) {
                state.range = gesture.previous;
                updateRange();
            }
            gesture = null;
        };
        canvas.onkeydown = event => {
            if (event.key === 'Escape') { state.range = null; updateRange(); }
        };
        $('zoomIn').onclick = () => { state.zoom = Math.min(state.zoom * 1.5, 16); updateZoom(); };
        $('zoomOut').onclick = () => { state.zoom = Math.max(state.zoom / 1.5, 1); updateZoom(); };
        $('zoomReset').onclick = () => { state.zoom = 1; updateZoom(); };
        let wheelFrame = null;
        canvas.closest('.wave-plot').parentElement.addEventListener('wheel', event => {
            const rect = canvas.getBoundingClientRect();
            if (!state.selected?.duration || !rect.width || (!event.deltaY && !event.deltaX)) return;
            event.preventDefault();
            const scale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.width : 1;
            if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
                state.pan += (event.deltaX || event.deltaY) * scale / rect.width;
            } else {
                const pointer = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
                const anchor = (state.pan + pointer) / state.zoom;
                const delta = Math.max(-320, Math.min(320, event.deltaY * scale));
                state.zoom = Math.max(1, Math.min(16, state.zoom * Math.exp(-delta * 0.003)));
                state.pan = anchor * state.zoom - pointer;
            }
            state.pan = Math.max(0, Math.min(state.zoom - 1, state.pan));
            if (wheelFrame === null) wheelFrame = requestAnimationFrame(() => {
                wheelFrame = null;
                refreshZoom();
            });
        }, { passive: false });
        function updateZoom() {
            const duration = waveDuration() || 1;
            const center = state.range ? (state.range[0] + state.range[1]) / 2 : state.position;
            state.pan = Math.max(0, Math.min(state.zoom - 1, center / duration * state.zoom - 0.5));
            refreshZoom();
        }
        function refreshZoom() {
            $('zoomLevel').textContent = `${Math.round(state.zoom * 100)}%`;
            positionRangeHandles();
            syncTransport();
        }
        new ResizeObserver(() => {
            if (isSourceEditing()) drawWave(playback.active ? playback.currentTime() : state.position);
        }).observe(canvas);
        for (const id of ['fadeIn', 'fadeOut', 'clipGain', 'clipName', 'dawRate', 'dawBits', 'dawChannels']) $(id).oninput = () => {
            updateDragHandle();
            if (['fadeIn', 'fadeOut', 'clipGain'].includes(id)) {
                renderedWave = null;
                refreshAudition();
                syncTransport();
            }
        };
    }

    async function loadLibraries(preferred = state.library?.id, expectedRevision = libraryRevision) {
        const { projects } = await api.get('/api/v1/projects');
        if (expectedRevision !== libraryRevision) return;
        state.libraries = projects;
        const active = projects.find(item => item.id === preferred) || projects[0] || null;
        if (active?.id !== state.library?.id) await switchLibrary(active);
        else {
            state.library = active;
            renderLibraryPicker();
            if (!active) { renderRows(); await renderTree(); }
        }
    }

    function renderLibraryPicker() {
        const picker = $('contextPill');
        picker.replaceChildren(...state.libraries.map(project => {
            const option = el('option', `${libraryName(project)} · ${number(project.file_count)}`);
            option.value = project.id;
            return option;
        }));
        if (state.library) picker.value = state.library.id;
        picker.disabled = !state.libraries.length;
        if (picker.disabled) picker.append(el('option', t('暂无音效库')));
        for (const id of ['btnRenameLib', 'btnRemoveLib', 'btnRebuild', 'btnCreateGroup', 'aiSend']) {
            $(id).disabled = !state.library;
        }
    }

    async function switchLibrary(library) {
        if (timeline()?.hasLibraryClips() && !await timeline().discard()) {
            renderLibraryPicker();
            return;
        }
        const revision = ++libraryRevision;
        listRequest?.abort();
        previewBatch?.abort();
        await select(null);
        if (!library || !state.library) {
            state.search = '';
            state.view = 'folder';
            if (isLibrary()) $('searchInput').value = '';
            for (const id of ['filterFormat', 'filterChannels', 'filterDuration']) $(id).value = '';
            renderSortControls();
        }
        state.library = library;
        state.filter = {};
        expandedFolders.clear();
        $('queryEcho').textContent = '';
        state.facets = null;
        state.selectedIds.clear();
        state.jobs = [];
        state.items = [];
        state.displayRows = [];
        listQueryKey = null;
        listPreviousWindows.length = 0;
        state.cursor = null;
        state.total = 0;
        invalidateDelivery();
        state.dockEditing = false;
        libraryEditor = null;
        clipSessions.clear();
        syncBottomStage();
        if (library) localStorage.setItem('soundbot-library', library.id);
        else localStorage.removeItem('soundbot-library');
        renderLibraryPicker();
        renderRows();
        await Promise.all([loadItems(true), renderTree(), refreshJobs(revision), timeline()?.loadDock(true)]);
    }

    function renderSortControls() {
        const hasQuery = Boolean(state.search.trim());
        document.querySelectorAll('[data-sort]').forEach(button => {
            button.disabled = hasQuery || state.groupByFolder;
            button.title = hasQuery ? t('搜索结果按相关性排序') : state.groupByFolder ? t('关闭按目录分组后可调整排序') : '';
        });
        $('groupToggle').disabled = hasQuery;
    }

    function catalogFilters() {
        const grouped = state.groupByFolder && !state.search.trim();
        const params = new URLSearchParams({ q: state.search, sort: grouped ? 'folder' : state.sort, ...state.filter });
        if (state.view === 'fav') params.set('favorite', 'true');
        if ($('filterFormat').value) params.set('format', $('filterFormat').value);
        if ($('filterChannels').value) params.set('channels', $('filterChannels').value);
        const [min, max] = $('filterDuration').value.split(',');
        if (min) params.set('min_duration', min);
        if (max) params.set('max_duration', max);
        return params;
    }

    const catalogKey = () => `${state.library?.id || ''}?${catalogFilters()}`;

    function browseCatalog(limit, cursor, signal, total = false) {
        const params = catalogFilters();
        params.set('limit', limit);
        params.set('include_total', String(total));
        if (cursor) params.set('cursor', JSON.stringify(cursor));
        return api.get(route(`/catalog?${params}`), { signal });
    }

    async function loadItems(reset = false, navigation) {
        if (!isLibrary()) {
            if (reset) listQueryKey = null;
            return;
        }
        if (!state.library || (!reset && (listRequest || (navigation !== 'previous' && !state.cursor)))) return;
        if (reset) {
            listRequest?.abort();
            if (listQueryKey !== catalogKey()) previewBatch?.abort();
        }
        if (!reset && !navigation && state.items.length >= listWindowLimit) return;
        const request = listRequest = new AbortController();
        const key = catalogKey();
        renderSortControls();
        const cursor = navigation === 'previous' ? listPreviousWindows.at(-1) : reset ? null : state.cursor;
        try {
            const result = await browseCatalog(120, cursor, request.signal, reset);
            if (request.signal.aborted || key !== catalogKey()) return;
            if (reset) { listPreviousWindows.length = 0; listWindowStart = null; }
            if (navigation === 'next') listPreviousWindows.push(listWindowStart);
            if (navigation === 'previous') listPreviousWindows.pop();
            if (navigation) listWindowStart = cursor;
            listQueryKey = key;
            state.items = reset || navigation ? result.files : [...state.items, ...result.files];
            state.displayRows = state.groupByFolder && !state.search.trim() ? state.items.flatMap((item, index) =>
                state.items[index - 1]?.folder_key === item.folder_key ? [item] : [{ folder: item.folder_key }, item]
            ) : state.items;
            state.cursor = result.next_cursor;
            state.total = result.total ?? state.total;
            if (reset || navigation) $('rows').scrollTop = 0;
            renderRows(!reset && !navigation);
        } catch (error) {
            if (error.name !== 'AbortError') throw error;
        } finally {
            if (listRequest === request) listRequest = null;
        }
    }

    function renderBulkActions() {
        const count = state.selectedIds.size;
        $('btnBulk').disabled = !count;
        $('btnBulk').textContent = count ? t('批量操作 ({count})', { count }) : t('批量操作');
    }

    function renderRows(scrolling = false) {
        if (clipDrag) { refreshRowsAfterDrag = true; return; }
        refreshRowsAfterDrag = false;
        $('rowCount').textContent = t('结果：{count}', { count: state.total });
        if (!scrolling) renderBulkActions();
        const box = $('rows');
        const first = Math.max(0, Math.floor(box.scrollTop / ROW) - 5);
        const count = Math.max(16, Math.ceil(box.clientHeight / ROW) + 10);
        const last = Math.min(first + count, state.displayRows.length);
        if (scrolling && rowWindow?.first === first && rowWindow?.last === last) {
            scheduleMiniPeaks();
            return;
        }
        rowWindow = { first, last };
        const nextRows = scrolling ? rowNodes : new Map();
        const parts = [];
        if (!first && listPreviousWindows.length) parts.push(button(t('上一页'), () => loadItems(false, 'previous'), 'more'));
        if (first) { const space = el('div'); space.style.height = `${first * ROW}px`; parts.push(space); }
        for (const item of state.displayRows.slice(first, last)) {
            const existing = scrolling && rowNodes.get(item);
            if (existing) {
                nextRows.delete(item);
                nextRows.set(item, existing);
                parts.push(existing);
                continue;
            }
            if (item.folder) {
                const label = item.folder.split('/').filter(Boolean).at(-1) || item.folder;
                const heading = button(label, () => setFilter({ folder: item.folder }), 'folder-hdr');
                heading.title = item.folder;
                nextRows.set(item, heading);
                parts.push(heading);
                continue;
            }
            const row = el('div', null, `row ${state.selected?.id === item.id ? 'sel' : ''}`);
            row.dataset.fileId = item.id;
            const playable = item.duration > 0 && !item.missing;
            const name = el('div', null, 'name');
            const check = el('input');
            check.type = 'checkbox';
            check.checked = state.selectedIds.has(item.id);
            check.setAttribute('aria-label', t('选择 {name}', { name: item.filename }));
            check.onclick = event => event.stopPropagation();
            check.onchange = () => {
                if (check.checked) state.selectedIds.add(item.id); else state.selectedIds.delete(item.id);
                renderBulkActions();
            };
            const thumb = miniCanvas(item);
            let waveHandle = thumb;
            if (playable) {
                const handle = waveHandle = el('button', null, 'wave-drag');
                handle.type = 'button';
                handle.append(thumb);
                bindAudioDrag(handle, () => wholeDrag(item));
            }
            name.append(check, assetCover(item), waveHandle, el('span', item.title || item.filename, 'file-title'));
            row.append(name, el('span', fmt(item.duration), 'meta'),
                el('span', item.sample_rate ? `${item.sample_rate / 1000}k` : '—', 'meta'),
                el('span', item.channels || '—', 'meta'),
                el('span', [item.ucs, ...item.tags].filter(Boolean).join(' · '), 'tags'),
                el('span', t(item.missing ? '离线' : item.probe_error ? '读取异常' : item.favorite ? '已收藏' : ''), 'state'));
            row.title = item.path;
            row.onclick = () => {
                if (state.selected?.id !== item.id) attempt(() => select(item, true));
                else if (!playback.active) attempt(playLibrary);
            };
            nextRows.set(item, row);
            parts.push(row);
        }
        if (state.displayRows.length > last) {
            const space = el('div'); space.style.height = `${(state.displayRows.length-last)*ROW}px`; parts.push(space);
        }
        if (state.cursor) {
            const full = state.items.length >= listWindowLimit;
            parts.push(button(t(full ? '下一页' : '加载更多'), () => loadItems(false, full ? 'next' : undefined), 'more'));
        }
        if (!parts.length) parts.push(state.library
            ? el('div', state.search ? t('没有匹配的素材') : t('将音频或文件夹拖到这里'), 'empty')
            : button(t('新建音效库…'), createLibrary, 'empty empty-library'));
        const offset = box.scrollTop;
        const keep = new Set(parts);
        for (const child of [...box.children]) if (!keep.has(child)) child.remove();
        let current = box.firstElementChild;
        for (const part of parts) {
            if (part === current) current = current.nextElementSibling;
            else box.insertBefore(part, current);
        }
        while (nextRows.size > Math.max(128, count)) nextRows.delete(nextRows.keys().next().value);
        rowNodes = nextRows;
        if (box.scrollTop !== offset) box.scrollTop = offset;
        paintMiniPreviews(parts.flatMap(row => [...row.querySelectorAll('canvas.mini-preview')]));
        scheduleMiniPeaks();
    }

    const previewKey = item => JSON.stringify([state.library?.id, item.id, item.path, item.source_fingerprint, item.missing]);
    const cachedPeaks = item => peakCache.get(previewKey(item));

    const miniFor = item => miniPeaks.get(previewKey(item)) || cachedPeaks(item);

    function paintMiniPreviews(canvases) {
        if (!canvases.length) return;
        const waveColor = color('--tx2');
        for (const node of canvases) {
            const item = previewItems.get(node);
            if (!item || item.missing) continue;
            const peaks = miniFor(item);
            if (peaks) waveform.drawMini(node, { waveformData: peaks, inactiveColor: waveColor });
        }
    }

    function rememberPreview(cache, key, value, limit) {
        if (cache.size >= limit && !cache.has(key)) cache.delete(cache.keys().next().value);
        cache.set(key, value);
    }

    function assetCover(item) {
        const cover = el('span', null, 'asset-cover');
        cover.classList.toggle('pending', !item.missing);
        cover.setAttribute('aria-hidden', 'true');
        cover.innerHTML = '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M3 8v4m3.5-6v8M10 3v14m3.5-11v8M17 8v4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
        previewItems.set(cover, item);
        return cover;
    }

    function miniCanvas(item) {
        const canvas = el('canvas', null, 'mini-preview');
        canvas.setAttribute('aria-hidden', 'true');
        previewItems.set(canvas, item);
        return canvas;
    }

    let miniFrame;
    let previewBatch;
    let loadingArtwork = false;
    const failedPeaks = new Map();
    const failedArtwork = new Map();

    function visiblePreviews() {
        const box = isLibrary() ? $('rows')
            : !state.dockEditing && !$('bottomStage').classList.contains('dock-collapsed') ? $('miniRows') : null;
        if (!box) return [];
        const bounds = box.getBoundingClientRect();
        if (!bounds.width || !bounds.height) return [];
        const previews = [];
        for (const row of box.children) {
            if (!row.matches('.row,.mini-row')) continue;
            const rect = row.getBoundingClientRect();
            if (rect.bottom > bounds.top && rect.top < bounds.bottom && rect.right > bounds.left && rect.left < bounds.right) {
                previews.push(...row.querySelectorAll('.mini-preview,.asset-cover'));
            }
        }
        return previews;
    }

    async function loadArtwork(item) {
        const response = await api.fetch(fileRoute(item.id, 'artwork'));
        if (response.status === 204) return null;
        if (!response.ok) throw new Error('封面暂时无法读取');
        const info = JSON.parse(response.headers.get('X-SoundBot-Artwork') || '{}');
        const blob = await response.blob();
        const formats = { 'image/png': 'PNG', 'image/jpeg': 'JPEG', 'image/webp': 'WEBP', 'image/gif': 'GIF', 'image/bmp': 'BMP' };
        if (!formats[blob.type] || blob.size > 1024 * 1024) throw new Error('无法显示封面');
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve({ format: formats[blob.type], ...info, url: reader.result });
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(blob);
        });
    }

    function requestArtwork(item) {
        const key = previewKey(item);
        if (artCache.has(key)) return Promise.resolve(artCache.get(key));
        if (artworkRequests.has(key)) return artworkRequests.get(key);
        const library = state.library.id;
        const request = loadArtwork(item).then(cover => {
            if (library === state.library?.id) rememberPreview(artCache, key, cover, 48);
            return cover;
        }).finally(() => artworkRequests.delete(key));
        artworkRequests.set(key, request);
        return request;
    }

    function showArtwork(node, cover) {
        const metadata = node.closest('#assetArtwork') ? $('artworkMetadata') : null;
        if (!cover) {
            node.classList.remove('pending');
            if (metadata) metadata.textContent = t('无内嵌封面');
            return;
        }
        if (node.querySelector('img')) return;
        const img = el('img');
        img.alt = '';
        img.onload = () => {
            node.classList.remove('pending');
            if (metadata && node.isConnected) {
                const dimensions = `${cover.width || img.naturalWidth} × ${cover.height || img.naturalHeight}`;
                const size = formatBytes(cover.size);
                const role = { 3: t('正面封面'), 4: t('背面封面'),
                    8: t('艺术家图片') }[cover.role];
                metadata.textContent = [`${cover.format} · ${dimensions}`,
                    [role, size].filter(Boolean).join(' · '), cover.description
                ].filter(Boolean).join('\n');
            }
        };
        img.onerror = () => {
            img.remove();
            if (metadata && node.isConnected) metadata.textContent = t('无法显示封面');
        };
        img.src = cover.url;
        node.append(img);
    }

    function scheduleMiniPeaks() {
        if (miniFrame != null) return;
        miniFrame = requestAnimationFrame(() => {
            miniFrame = null;
            const library = state.library?.id;
            if (!library) return;
            const waves = new Map();
            const covers = new Map();
            const waveColor = color('--tx2');
            for (const node of visiblePreviews()) {
                const item = previewItems.get(node);
                if (!item || item.missing) continue;
                const key = previewKey(item);
                if (node.tagName === 'CANVAS') {
                    const peaks = miniPeaks.get(key) || peakCache.get(key);
                    if (peaks) {
                        waveform.drawMini(node, { waveformData: peaks, inactiveColor: waveColor });
                        if (miniPeaks.has(key)) {
                            miniPeaks.delete(key);
                            miniPeaks.set(key, peaks);
                        }
                    }
                    else if (Date.now() - (failedPeaks.get(key) || 0) > 20000) waves.set(key, item);
                } else if (artCache.has(key)) showArtwork(node, artCache.get(key));
                else if (Date.now() - (failedArtwork.get(key) || 0) > 20000) covers.set(key, item);
            }

            if (!previewBatch && waves.size) {
                const request = previewBatch = new AbortController();
                const waveBatch = [...waves].slice(0, 2);
                api.post('/api/v1/waveforms/batch', {
                    project_id: library, points: 256, file_ids: waveBatch.map(([, item]) => item.id)
                }, { signal: request.signal }).then(result => {
                    if (request.signal.aborted) return;
                    const loaded = new Map(result.items.filter(item => item.ok).map(item => [item.file_id, item]));
                    for (const [key, item] of waveBatch) {
                        const result = loaded.get(item.id);
                        if (result && (!item.source_fingerprint || result.source_fingerprint === item.source_fingerprint)) {
                            rememberPreview(miniPeaks, key, Float32Array.from(result.peaks), miniLimit);
                        } else rememberPreview(failedPeaks, key, Date.now(), 256);
                    }
                }).catch(() => {
                    if (!request.signal.aborted) waveBatch.forEach(([key]) => rememberPreview(failedPeaks, key, Date.now(), 256));
                }).finally(() => { previewBatch = null; scheduleMiniPeaks(); });
            }
            if (!loadingArtwork && covers.size) {
                loadingArtwork = true;
                const [key, item] = covers.entries().next().value;
                requestArtwork(item).catch(() => rememberPreview(failedArtwork, key, Date.now(), 256))
                    .finally(() => { loadingArtwork = false; scheduleMiniPeaks(); });
            }
        });
    }

    $('rows').onscroll = () => {
        if (renderFrame !== null) return;
        renderFrame = requestAnimationFrame(() => {
            renderFrame = null;
            renderRows(true);
            const box = $('rows');
            if (box.scrollHeight - box.scrollTop - box.clientHeight < ROW * 4) attempt(() => loadItems());
        });
    };

    async function setFilter(filter) {
        previewBatch?.abort();
        state.filter = filter;
        $('tree').querySelectorAll('[data-folder]').forEach(row => row.classList.toggle('sel',
            Boolean(filter.folder) && folderKey(row.dataset.folder) === folderKey(filter.folder)));
        updateTagSelection();
        renderFilterSummary();
        if (isLibrary()) return loadItems(true);
        return timeline()?.loadDock();
    }

    const libraryViews = Object.freeze({ folder: '文件夹', tag: '标签', group: '分组', fav: '收藏' });
    const filenameCategories = new Map(Object.entries({
        footsteps: '脚步声', doors: '门和栅栏', ambience: '环境音', weather: '天气',
        water: '水声', vehicles: '车辆', weapons: '武器', impacts: '撞击',
        whooshes: '呼啸音', interface: '界面音效'
    }));
    const filenameCategoryLabel = id => t(filenameCategories.get(id) ?? id);

    function renderFilterSummary() {
        $('queryEcho').textContent = Object.entries(state.filter)
            .map(([type, value]) => type === 'filename_category'
                ? filenameCategoryLabel(value) : type === 'folder' ? String(value).split(/[/\\]/).at(-1) : value)
            .join(' · ');
    }
    const folderKey = path => {
        const normalized = path.replaceAll('\\', '/').replace(/\/$/, '');
        return desktop.platform === 'win32' ? normalized.toLowerCase() : normalized;
    };

    const tagSectionsKey = 'soundbot-tag-sections';
    const tagSectionNames = new Map([
        ['folders', '标签夹'], ['filename_category', '文件名分类'], ['ucs', 'UCS'], ['tag', '自定义标签']
    ]);

    function loadTagLayout() {
        let saved;
        try { saved = JSON.parse(localStorage.getItem(tagSectionsKey)); } catch { /* Use the default layout. */ }
        const keys = [...tagSectionNames.keys()];
        const known = values => Array.isArray(values) ? values.filter(key => tagSectionNames.has(key)) : [];
        return {
            order: [...new Set([...known(saved?.order), ...keys])],
            closed: new Set(known(saved?.closed)),
            countSort: new Set(known(saved?.countSort ?? ['tag'])),
            expandedFolders: new Set(Array.isArray(saved?.expandedFolders)
                ? saved.expandedFolders.filter(id => typeof id === 'string' && /^[0-9a-f]{32}$/.test(id)) : [])
        };
    }
    const tagLayout = loadTagLayout();

    function saveTagLayout() {
        localStorage.setItem(tagSectionsKey, JSON.stringify({
            order: tagLayout.order, closed: [...tagLayout.closed], countSort: [...tagLayout.countSort],
            expandedFolders: [...tagLayout.expandedFolders]
        }));
    }

    function updateTagSelection() {
        for (const entry of $('tree').querySelectorAll('[data-tag-type]')) {
            const selected = state.filter[entry.dataset.tagType] === entry.dataset.tagName;
            entry.classList.toggle('sel', selected);
            entry.setAttribute('aria-pressed', String(selected));
        }
    }

    function tagBranch(label, count, content, expanded, toggle) {
        const switcher = button(null, () => {
            content.hidden = !content.hidden;
            switcher.setAttribute('aria-expanded', String(!content.hidden));
            toggle(!content.hidden);
            saveTagLayout();
        }, 'tag-tree-toggle tree-label');
        const arrow = el('span', null, 'tree-expand');
        arrow.setAttribute('aria-hidden', 'true');
        switcher.title = label;
        switcher.setAttribute('aria-expanded', String(expanded));
        switcher.setAttribute('aria-controls', content.id);
        switcher.append(arrow, el('span', label, 'tree-name'), el('span', number(count), 'tree-count'));
        content.hidden = !expanded;
        return switcher;
    }

    function tagEntry(type, record) {
        const name = type === 'filename_category' ? filenameCategoryLabel(record.name) : record.name;
        const entry = button(null, () => setFilter({ [type]: record.name }), 'tree-row tag-entry');
        entry.title = name;
        entry.dataset.tagType = type;
        entry.dataset.tagName = record.name;
        entry.append(el('span', name, 'tree-name'), el('span', number(record.count), 'tree-count'));
        if (type === 'tag' && state.facets.tag_folders.length) {
            const label = t('移动到标签夹…');
            entry.setAttribute('aria-haspopup', 'menu');
            entry.setAttribute('aria-expanded', 'false');
            bindTreeMenu(entry, entry, event => openTreeMenu(entry, [[label, () => moveTag(record)]], event, t('标签操作')));
        }
        return entry;
    }

    function renderTagSections(focusSection) {
        const box = $('tagSections');
        if (!box || !state.facets) return;
        const facets = state.facets;
        const unfiled = [];
        const folderTags = new Map(facets.tag_folders.map(folder => [folder.id, []]));
        for (const tag of facets.tags) (folderTags.get(tag.folder_id) || unfiled).push(tag);
        const collator = new Intl.Collator(i18n.language, { numeric: true });
        const sorted = (records, key) => {
            const displayName = key === 'filename_category' ? record => filenameCategoryLabel(record.name) : record => record.name;
            return [...records].sort((a, b) => (tagLayout.countSort.has(key) ? b.count - a.count : 0) ||
                collator.compare(displayName(a), displayName(b)));
        };
        const groups = { tag: unfiled, ucs: facets.ucs, filename_category: facets.filename_categories,
            folders: facets.tag_folders.map(folder => ({ ...folder, count: folderTags.get(folder.id).length })) };
        let focus;
        box.replaceChildren(...tagLayout.order.map((key, index) => {
            const label = key === 'ucs' ? 'UCS' : t(tagSectionNames.get(key));
            const records = sorted(groups[key], key);
            const section = el('section', null, 'tag-section');
            section.dataset.tagSection = key;
            const header = el('div', null, 'tag-section-head tree-row');
            const content = el('div', null, 'tree-children');
            content.id = `tag-section-content-${key}`;
            const open = tagBranch(label, records.length, content, !tagLayout.closed.has(key), expanded => {
                if (expanded) tagLayout.closed.delete(key); else tagLayout.closed.add(key);
            });
            const update = change => { change(); saveTagLayout(); renderTagSections(key); };
            const move = by => update(() => {
                const order = tagLayout.order;
                [order[index], order[index + by]] = [order[index + by], order[index]];
            });
            const showMenu = event => {
                const actions = [];
                if (index > 0) actions.push([t('上移分类'), () => move(-1)]);
                if (index + 1 < tagLayout.order.length) actions.push([t('下移分类'), () => move(1)]);
                if (tagLayout.countSort.has(key)) actions.push([t('按名称排序'), () => update(() => tagLayout.countSort.delete(key))]);
                else actions.push([t(key === 'folders' ? '按标签数量排序' : '按素材数量排序'),
                    () => update(() => tagLayout.countSort.add(key))]);
                openTreeMenu(menu, actions, event, t('标签分类排序'));
            };
            const menu = button('↕', () => showMenu(), 'icon-btn tag-tree-action');
            menu.title = t('标签分类排序');
            menu.setAttribute('aria-label', menu.title);
            menu.setAttribute('aria-haspopup', 'menu');
            menu.setAttribute('aria-expanded', 'false');
            bindTreeMenu(header, menu, showMenu);
            header.append(open);
            if (key === 'folders') {
                const add = button('＋', createTagFolder, 'icon-btn tag-tree-action');
                add.title = t('新建标签夹');
                add.setAttribute('aria-label', add.title);
                header.append(add);
                content.replaceChildren(...records.map(folder => tagFolderNode(folder, sorted(folderTags.get(folder.id), key))));
            } else content.replaceChildren(...records.map(record => tagEntry(key, record)));
            header.append(menu);
            section.append(header, content);
            if (focusSection === key) focus = menu;
            return section;
        }));
        updateTagSelection();
        focus?.focus({ preventScroll: true });
    }

    function tagFolderNode(folder, tags) {
        const node = el('div', null, 'tag-folder');
        node.dataset.tagFolder = folder.id;
        const head = el('div', null, 'tree-row');
        const content = el('div', null, 'tree-children');
        content.id = `tag-folder-content-${folder.id}`;
        const open = tagBranch(folder.name, tags.length, content, tagLayout.expandedFolders.has(folder.id), expanded => {
            if (expanded) tagLayout.expandedFolders.add(folder.id); else tagLayout.expandedFolders.delete(folder.id);
        });
        const label = t('删除标签夹…');
        bindTreeMenu(head, open, event => openTreeMenu(open, [[label, () => removeTagFolder(folder)]], event, t('标签夹操作')));
        head.append(open);
        content.append(...tags.map(record => tagEntry('tag', record)));
        node.append(head, content);
        return node;
    }

    async function createTagFolder() {
        const library = state.library.id;
        const name = await promptText(t('标签夹名称'));
        if (!name) return;
        if ([...name].length > 100) throw new Error(t('请输入 1 至 100 个字符的标签夹名称'));
        const folder = await api.post(route('/catalog/tag-folders', library), { name });
        tagLayout.closed.delete('folders');
        tagLayout.expandedFolders.add(folder.id);
        saveTagLayout();
        if (state.library?.id === library) await renderTree();
    }

    async function removeTagFolder(folder) {
        const library = state.library.id;
        if (!await actionDialog({
            message: t('删除标签夹「{name}」？', { name: folder.name }),
            detail: t('夹内标签会回到自定义标签，素材和原有标签会保留。'),
            confirm: t('删除')
        })) return;
        await api.delete(route(`/catalog/tag-folders/${encodeURIComponent(folder.id)}`, library));
        tagLayout.expandedFolders.delete(folder.id);
        saveTagLayout();
        if (state.library?.id === library) await renderTree();
    }

    async function moveTag(record) {
        const library = state.library.id;
        const dialog = $('tagFolderDialog');
        const select = $('tagFolderDestination');
        $('tagFolderPrompt').textContent = t('为「{name}」选择标签夹', { name: record.name });
        const choices = [{ id: '', name: t('未归入标签夹') }, ...state.facets.tag_folders];
        select.replaceChildren(...choices.map(folder => {
            const option = el('option', folder.name);
            option.value = folder.id;
            return option;
        }));
        select.value = record.folder_id || '';
        dialog.returnValue = 'cancel';
        dialog.showModal();
        await new Promise(resolve => dialog.addEventListener('close', resolve, { once: true }));
        const folderId = select.value || null;
        if (dialog.returnValue !== 'ok' || folderId === record.folder_id) return;
        await api.put(route('/catalog/tag-folder-assignment', library), { tag: record.name, folder_id: folderId });
        if (folderId) {
            tagLayout.closed.delete('folders');
            tagLayout.expandedFolders.add(folderId);
            saveTagLayout();
        }
        if (state.library?.id === library) await renderTree();
    }

    function renderViewTabs() {
        const caption = $('activeViewLabel');
        const label = t(libraryViews[state.view]);
        caption.textContent = label;
        caption.title = label;
        for (const button of $('viewTabs').querySelectorAll('button')) {
            const selected = button.dataset.view === state.view;
            button.classList.toggle('on', selected);
            button.setAttribute('aria-pressed', String(selected));
        }
    }

    async function renderTree() {
        treeView = state.view;
        renderViewTabs();
        closeFolderMenu();
        const generation = ++directoryRevision;
        const library = state.library?.id;
        const box = $('tree');
        if (!library) {
            $('treeLabel').hidden = true;
            box.replaceChildren();
            return;
        }
        box.replaceChildren(button(t(state.view === 'fav' ? '全部收藏' : '全部素材'), () => setFilter({}), 'view-item tree-command'));
        const section = $('treeLabel');
        const label = { folder: '来源目录' }[state.view];
        section.hidden = !label;
        section.textContent = label ? t(label) : '';
        if (state.view === 'folder') {
            const folders = await api.get(route('/catalog/folders', library));
            if (generation !== directoryRevision) return;
            for (const entry of folders) box.append(folderNode(entry));
        } else if (state.view !== 'fav') {
            const facets = await api.get(route('/catalog/facets', library));
            if (generation !== directoryRevision) return;
            state.facets = facets;
            if (state.view === 'tag') {
                const tagSections = el('div', null, 'tag-sections');
                tagSections.id = 'tagSections';
                box.append(tagSections);
                renderTagSections();
            } else {
                box.append(button(t('新建分组…'), createGroup, 'view-item tree-command'));
                for (const group of facets.groups) {
                    const entry = button(group.name, () => setFilter({ group: group.id }), 'view-item');
                    entry.title = group.name;
                    box.append(entry);
                }
            }
        }
    }

    function folderNode(item) {
        const pathKey = folderKey(item.path);
        const library = state.library.id;
        const wrapper = el('div');
        const line = el('div', null, 'tree-row');
        line.dataset.folder = item.path;
        const selected = state.filter.folder ? folderKey(state.filter.folder) : null;
        line.classList.toggle('sel', selected === pathKey);
        const children = el('div', null, 'tree-children');
        children.hidden = true;
        let loaded = false;
        const showChildren = async (open, focusLeaf = false) => {
            if (!loaded) {
                expand.disabled = true;
                try {
                    const rows = await api.get(route(`/catalog/folders?${new URLSearchParams({ folder: item.path })}`, library));
                    if (state.library?.id !== library || !wrapper.isConnected) return;
                    children.append(...rows.map(folderNode));
                    loaded = true;
                    if (!rows.length) {
                        expand.classList.add('leaf');
                        if (focusLeaf) title.focus({ preventScroll: true });
                        return;
                    }
                } finally {
                    expand.disabled = false;
                }
            }
            children.hidden = !open;
            if (open) expandedFolders.add(pathKey); else expandedFolders.delete(pathKey);
            expand.setAttribute('aria-expanded', String(open));
            expand.setAttribute('aria-label', open ? t('折叠目录') : t('展开目录'));
        };
        const expand = button('', () => showChildren(children.hidden, true), 'tree-expand');
        expand.setAttribute('aria-expanded', 'false');
        expand.setAttribute('aria-label', t('展开目录'));
        const name = item.path.split(/[/\\]/).filter(Boolean).at(-1) || item.path;
        const title = button(null, () => setFilter({ folder: item.path }), 'tree-label');
        title.append(el('span', name, 'tree-name'), el('span', item.count == null ? '' : number(item.count), 'tree-count'));
        title.title = item.path;
        title.setAttribute('aria-haspopup', 'menu');
        title.setAttribute('aria-expanded', 'false');
        bindTreeMenu(line, title, event => openFolderMenu(item, title, event));
        line.append(expand, title);
        wrapper.append(line, children);
        if (expandedFolders.has(pathKey) || selected?.startsWith(`${pathKey}/`)) {
            queueMicrotask(() => { if (wrapper.isConnected) attempt(() => showChildren(true)); });
        }
        return wrapper;
    }

    let folderMenu;
    function openFolderMenu(item, anchor, event) {
        const actions = [];
        if (item.id) {
            const library = state.library.id;
            actions.push([t('检查变更'), async () => {
                await api.post(route(`/sources/${encodeURIComponent(item.id)}/scan`, library), {});
                modal('ovJobs');
                await refreshJobs(libraryRevision, true);
            }], [t('重定位…'), () => relocateItem({ source_id: item.id }, true)],
            [t('从音效库移除文件夹…'), () => removeFolderSource(item, library)]);
        }
        actions.push([t('在文件管理器中显示'), () => desktop.revealFile(item.path)]);
        openTreeMenu(anchor, actions, event, t('文件夹操作'));
    }

    function bindTreeMenu(node, anchor, show) {
        node.oncontextmenu = event => {
            event.preventDefault();
            show(event);
        };
        anchor.onkeydown = event => {
            if (event.key !== 'ContextMenu' && !(event.shiftKey && event.key === 'F10')) return;
            event.preventDefault();
            show();
        };
    }

    function openTreeMenu(anchor, actions, event, label) {
        closeFolderMenu();
        const menu = $('folderMenu');
        const controller = new AbortController();
        folderMenu = { anchor, controller };
        if (anchor.getAttribute('aria-haspopup') === 'menu') anchor.setAttribute('aria-expanded', 'true');
        menu.setAttribute('aria-label', label);
        const options = actions.map(([label, action]) => {
            const option = button(label, () => {
                closeFolderMenu(true);
                return action();
            }, 'folder-menu-item');
            option.setAttribute('role', 'menuitem');
            option.tabIndex = -1;
            return option;
        });
        menu.replaceChildren(...options);
        menu.hidden = false;
        const box = anchor.getBoundingClientRect();
        menu.style.left = `${Math.max(8, Math.min(event?.clientX ?? box.left, innerWidth - menu.offsetWidth - 8))}px`;
        menu.style.top = `${Math.max(8, Math.min(event?.clientY ?? box.bottom, innerHeight - menu.offsetHeight - 8))}px`;
        options[0].focus({ preventScroll: true });
        menu.onfocusout = focus => { if (!menu.contains(focus.relatedTarget)) closeFolderMenu(); };
        menu.onkeydown = key => {
            const index = options.indexOf(document.activeElement);
            if (key.key === 'Escape' || key.key === 'Tab') {
                if (key.key === 'Escape') key.preventDefault();
                key.stopPropagation();
                closeFolderMenu(true);
                return;
            }
            const next = { ArrowDown: (index + 1) % options.length,
                ArrowUp: (index + options.length - 1) % options.length, Home: 0, End: options.length - 1 }[key.key];
            if (next == null) return;
            key.preventDefault();
            options[next].focus({ preventScroll: true });
        };
        const listener = { signal: controller.signal, capture: true };
        document.addEventListener('pointerdown', pointer => {
            if (!menu.contains(pointer.target)) closeFolderMenu();
        }, listener);
        document.addEventListener('scroll', () => closeFolderMenu(), listener);
        window.addEventListener('resize', () => closeFolderMenu(), listener);
        window.addEventListener('blur', () => closeFolderMenu(), listener);
    }

    function closeFolderMenu(restoreFocus = false) {
        if (!folderMenu) return;
        const { anchor, controller } = folderMenu;
        folderMenu = null;
        controller.abort();
        const menu = $('folderMenu');
        menu.onkeydown = menu.onfocusout = null;
        menu.hidden = true;
        if (anchor.getAttribute('aria-haspopup') === 'menu') anchor.setAttribute('aria-expanded', 'false');
        if (restoreFocus) anchor.focus({ preventScroll: true });
    }

    let jobsTimer;
    let jobRequest = 0;
    let lastImportRefresh = 0;
    let removingLibrary = null;
    const showJobs = () => $('ovJobs').classList.contains('show');

    function scheduleJobPoll() {
        clearTimeout(jobsTimer);
        if (!activeJobs.length && !showJobs()) return;
        jobsTimer = setTimeout(() => attempt(refreshJobs), 1600);
    }

    async function refreshJobs(revision = libraryRevision, submitted = false) {
        clearTimeout(jobsTimer);
        const request = ++jobRequest;
        const library = state.library?.id;
        if (library && library === removingLibrary) return;
        try {
            const [running, history] = await Promise.all([
                api.get('/api/v1/jobs/active'),
                library && showJobs() ? api.get(route('/jobs', library)) : null
            ]);
            if (request !== jobRequest || revision !== libraryRevision || library === removingLibrary) return;
            const currentImports = running.filter(job => job.kind === 'catalog_import' && job.project_id === library);
            const earlierImports = activeJobs.filter(job => job.kind === 'catalog_import' && job.project_id === library);
            const finished = submitted || earlierImports.some(job => !currentImports.some(item => item.id === job.id));
            const now = performance.now();
            const progressing = now - lastImportRefresh > 3000 && currentImports.some(job =>
                job.imported > (earlierImports.find(previous => previous.id === job.id)?.imported || 0));
            activeJobs = running;
            if (history) state.jobs = history;
            renderJobTray();
            if (showJobs()) renderJobs();
            if (library && (finished || progressing)) {
                lastImportRefresh = now;
                if ($('rows').scrollTop < ROW && !listRequest || !isLibrary()) await loadItems(true);
                if (finished && revision === libraryRevision) {
                    await Promise.all([renderTree(), loadLibraries(library, revision), timeline()?.loadDock(true)]);
                } else if (!isLibrary()) {
                    await timeline()?.refreshDockResults();
                }
            }
        } catch (error) {
            if (request === jobRequest && revision === libraryRevision && library !== removingLibrary) throw error;
        } finally {
            if (request === jobRequest) scheduleJobPoll();
        }
    }

    function renderJobTray() {
        $('jobTray').hidden = !activeJobs.length;
        if (!activeJobs.length) return;
        const job = activeJobs.find(item => item.state === 'running') || activeJobs[0];
        const label = t(job.kind === 'catalog_import' ? '导入' : '关键词索引');
        $('jobName').textContent = `${label} · ${job.library_name}${activeJobs.length > 1 ? ` (+${number(activeJobs.length - 1)})` : ''}`;
        const progress = $('jobProgress');
        if (job.expected > 0) {
            progress.max = job.expected;
            progress.value = Math.min(job.processed, job.expected);
        } else progress.removeAttribute('value');
        $('jobCount').textContent = job.expected > 0
            ? `${number(job.processed)} / ${number(job.expected)}`
            : job.processed ? number(job.processed) : t(job.state === 'pending' ? '排队中' : '处理中');
    }

    function renderJobs() {
        const status = { pending: '排队中', running: '处理中', completed: '已完成', cancelled: '已暂停', failed: '已中断' };
        const rows = [...activeJobs.filter(job => job.project_id !== state.library?.id), ...state.jobs].map(job => {
            const node = el('div', null, 'jobs-item');
            const title = job.kind === 'keyword_rebuild' ? t('关键词索引') : t('导入');
            node.append(el('div', `${title} · ${t(job.stage === 'partial' ? '已完成，有失败项' : status[job.state] || '处理中，尚无状态')}${job.library_name ? ` · ${job.library_name}` : ''}`));
            if (job.discovered != null) node.append(el('div',
                t('已发现 {discovered} · 入库 {imported} · 未变更 {unchanged} · 失败 {failed}', { discovered: job.discovered, imported: job.imported, unchanged: job.unchanged, failed: job.failures })));
            if (job.error_message) {
                const reason = el('p', describeError(job.error_message));
                reason.title = job.error_message;
                node.append(reason);
            }
            if (job.failed_items?.length) {
                const details = el('details');
                details.append(el('summary', t('失败详情')), ...job.failed_items.map(item => {
                    const failure = el('div', `${item.path} · ${describeError(item.message)}`);
                    failure.title = item.message;
                    return failure;
                }));
                node.append(details);
            }
            if (job.kind === 'catalog_import' && ['pending', 'running'].includes(job.state)) {
                node.append(button(t('暂停'), async () => { await api.delete(`/api/v1/jobs/${job.id}`); await refreshJobs(); }));
            }
            if (job.kind === 'catalog_import' && ['cancelled', 'failed'].includes(job.state)) {
                node.append(button(t('继续'), async () => { await api.post(`/api/v1/jobs/${job.id}/resume`, {}); await refreshJobs(libraryRevision, true); }));
            }
            if (job.kind === 'catalog_import' && job.state === 'completed' && job.failed_items?.length) {
                node.append(button(t('重试失败项'), async () => { await api.post(`/api/v1/jobs/${job.id}/retry`, {}); await refreshJobs(libraryRevision, true); }));
            }
            return node;
        });
        $('jobsList').replaceChildren(...(rows.length ? rows : [el('p', t('当前音效库暂无任务'))]));
    }

    let creatingLibrary;
    async function createLibrary() {
        if (creatingLibrary) return creatingLibrary;
        creatingLibrary = (async () => {
            const name = await promptText(t('音效库名称'));
            if (!name) return;
            const data = await api.post('/api/v1/projects', { name });
            await loadLibraries(data.project_id);
            return data.project_id;
        })();
        try { return await creatingLibrary; }
        finally { creatingLibrary = null; }
    }

    async function addFiles(files, libraryId = state.library?.id) {
        if (!files.length) return;
        libraryId ||= await createLibrary();
        if (!libraryId) return;
        const directories = files.filter(item => item.isDirectory);
        for (const dir of directories) await api.post(route('/imports', libraryId), { folder_path: dir.path });
        const paths = files.filter(item => !item.isDirectory).map(item => item.path);
        for (let i = 0; i < paths.length; i += 1000) {
            const result = await api.post(route('/imports', libraryId), { file_paths: paths.slice(i, i+1000) });
            if (result.rejected?.length) say(t('跳过 {count} 个不支持的文件', { count: result.rejected.length }));
        }
        await refreshJobs(libraryRevision, true);
    }

    async function importFiles(folder) {
        const libraryId = state.library?.id;
        const picker = folder ? desktop.fileImport.selectFolder : desktop.fileImport.selectAudioFiles;
        const result = await picker({});
        if (result.canceled) return;
        if (!result.success) throw new Error(result.error || '无法打开文件选择器');
        return addFiles(folder ? [{ path: result.folder, isDirectory: true }] : result.files, libraryId);
    }

    async function removeFolderSource(item, library) {
        const name = item.path.split(/[/\\]/).filter(Boolean).at(-1) || item.path;
        const accepted = await confirm(t('移除来源文件夹「{name}」？原音频文件会保留。', { name }));
        if (!accepted || state.library?.id !== library) return;
        await api.delete(route(`/sources/${encodeURIComponent(item.id)}`, library));
        if (state.library?.id !== library) return;
        const selected = state.selected;
        if (selected) {
            try { await api.get(route(`/catalog/items/${encodeURIComponent(selected.id)}`, library)); }
            catch (error) {
                if (error.code !== 'file_not_found') throw error;
                if (state.selected === selected) await select(null);
                clipSessions.delete(selected.id);
            }
        }
        if (state.library?.id !== library) return;
        state.selectedIds.clear();
        await Promise.all([state.filter.folder ? setFilter({}) : loadItems(true),
            renderTree(), loadLibraries(library), refreshJobs(), timeline()?.loadDock(true)]);
        say(t('已移除来源文件夹'));
    }

    async function relocateItem(payload, directory) {
        const libraryId = state.library.id;
        const pick = directory ? desktop.fileImport.selectFolder : desktop.fileImport.selectAudioFiles;
        const result = await pick({ title: directory ? t('选择来源目录的新位置') : t('选择音频源的新位置') });
        if (result.canceled) return;
        if (!result.success) throw new Error(result.error || '请选择新位置');
        await api.post(route('/catalog/relocate', libraryId), { ...payload, destination: result.filePaths[0] });
        if (state.library.id !== libraryId) return;
        await Promise.all([loadItems(true), renderTree()]);
        const replacement = state.items.find(item => item.id === payload.file_id);
        if (replacement) await select(replacement);
        say(t('已更新源文件位置'));
    }

    function promptText(label, initial = '') {
        const dialog = $('textDialog');
        $('textPrompt').textContent = label;
        $('textValue').value = initial;
        dialog.returnValue = '';
        dialog.showModal();
        $('textValue').select();
        return new Promise(resolve => {
            dialog.addEventListener('close', () => resolve(dialog.returnValue === 'ok' ? $('textValue').value.trim() : null), { once: true });
        });
    }

    async function createGroup() {
        const name = await promptText(t('分组名称'));
        if (!name) return;
        await api.post(route('/folders'), { name });
        await Promise.all([renderTree(), fillBulkGroups(), renderAssetGroups()]);
    }

    async function renderAssetGroups() {
        const revision = ++groupRevision;
        if (!state.library || !isLibrary() || !$('tabGroups').classList.contains('on')) return;
        const selected = state.selected;
        const box = $('assetGroups');
        box.textContent = selected ? t('正在读取分组…') : t('选择素材');
        if (!selected) return;
        const groups = await api.get(route(`/catalog/items/${encodeURIComponent(selected.id)}/groups`));
        if (revision !== groupRevision || state.selected !== selected || !isLibrary()) return;
        box.replaceChildren(...groups.map(group => {
            const entry = el('div', null, 'group-entry');
            const action = button(group.member ? t('移出') : t('加入'), async () => {
                action.disabled = true;
                try {
                    await api.post(route('/catalog/update'), {
                        ids: [selected.id], action: group.member ? 'ungroup' : 'group', value: group.id
                    });
                    await Promise.all([renderAssetGroups(), state.filter.group === group.id && loadItems(true)]);
                } finally { action.disabled = false; }
            }, 'pill-btn');
            entry.append(el('span', group.name), action);
            return entry;
        }));
        if (!groups.length) box.textContent = t('还没有分组');
    }

    async function editAssetInfo() {
        const item = state.selected;
        if (!item) return;
        const dialog = $('assetInfoDialog');
        $('assetTitle').value = item.title || '';
        $('assetNotes').value = item.description || '';
        $('assetTags').value = item.tags.join(', ');
        dialog.returnValue = '';
        dialog.showModal();
        await new Promise(resolve => dialog.addEventListener('close', resolve, { once: true }));
        if (dialog.returnValue !== 'ok') return;
        const value = { title: $('assetTitle').value.trim(), description: $('assetNotes').value.trim(),
            tags: $('assetTags').value.split(/[,，]/).map(value => value.trim()).filter(Boolean) };
        await mutate('metadata', value, [item.id]);
        if (state.selected?.id === item.id) {
            Object.assign(state.selected, value);
            $('editorFileName').textContent = value.title || item.filename;
            updateAssetDetails(state.selected);
        }
    }

    async function fillBulkGroups() {
        const { groups } = await api.get(route('/catalog/facets'));
        $('bulkGroups').replaceChildren(...groups.map(group => {
            const option = el('option', group.name); option.value = group.id; return option;
        }));
    }

    let resolveAction;
    function actionDialog({ message, detail, cancel = t('取消'), confirm: label = t('继续操作') }, priority = false) {
        const dialog = $('actionDialog');
        if (resolveAction) {
            if (!priority) return Promise.resolve(false);
            resolveAction(false);
        }
        $('actionMessage').textContent = message;
        $('actionDetail').textContent = detail || '';
        $('actionDetail').hidden = !detail;
        $('actionCancel').textContent = cancel;
        $('actionConfirm').textContent = label;
        dialog.returnValue = 'cancel';
        if (!dialog.open) dialog.showModal();
        $('actionCancel').focus();
        return new Promise(resolve => { resolveAction = resolve; });
    }
    const confirm = message => actionDialog({ message });

    async function mutate(action, value, ids = [...state.selectedIds]) {
        if (!ids.length) return;
        if (action === 'remove' && !await confirm(t('从当前音效库移除 {count} 条记录？原音频文件会保留。', { count: ids.length }))) return;
        const result = await api.post(route('/catalog/update'), { ids, action, value });
        if (action === 'remove' && ids.includes(state.selected?.id)) await select(null);
        if (action === 'remove') ids.forEach(id => clipSessions.delete(id));
        if (action === 'favorite' && ids.includes(state.selected?.id)) {
            state.selected.favorite = value;
            $('btnFavorite').textContent = value ? t('取消收藏') : t('收藏');
        }
        state.selectedIds.clear();
        renderBulkActions();
        $('bulkDialog').close();
        await Promise.all([loadItems(true), renderTree()]);
        say(t('已更新 {count} 条记录', { count: result.updated }));
    }

    function wholeDrag(item) {
        if (!item?.duration || item.missing) return null;
        return {
            item: { ...item, peaks: miniFor(item) },
            clip: { file_id: item.id, offset: 0, duration: item.duration, gain: 0, fade_in: 0, fade_out: 0 }
        };
    }

    function rangeDrag() {
        const edit = state.range && editorClip();
        return edit ? { ...edit, name: $('clipName').value.trim() || null } : null;
    }

    function deliverySignature({ item, clip, name }) {
        return JSON.stringify([deliveryRevision, state.dawDirectory, previewKey(item), clip,
            $('dawRate').value, $('dawChannels').value, $('dawBits').value, name]);
    }

    async function chooseDaw() {
        const directory = await desktop.chooseDawDirectory();
        if (!directory) return null;
        state.dawDirectory = directory;
        $('btnDawDirectory').textContent = directory.split(/[/\\]/).filter(Boolean).at(-1) || directory;
        $('btnDawDirectory').title = directory;
        invalidateDelivery();
        return directory;
    }

    function finishClipDrag() {
        audioPress = null;
        const drag = clipDrag;
        if (!drag) return;
        clipDrag = null;
        clearTimeout(drag.tabHover);
        drag.ghost.remove();
        drag.lane?.classList.remove('drop-target');
        if (document.body.hasPointerCapture(drag.pointer)) document.body.releasePointerCapture(drag.pointer);
        $('mainWorkspace').classList.remove('library-dragging');
        if (refreshRowsAfterDrag) renderRows();
        timeline()?.refreshDockRows();
    }

    const outsideWindow = event => event.clientX < 0 || event.clientY < 0 ||
        event.clientX >= innerWidth || event.clientY >= innerHeight;

    function leaveWithAudio(drag) {
        drag.leftWindow = true;
        const delivery = preparedDrags.get(deliverySignature(drag.edit));
        if (!delivery) {
            drag.ghost.hidden = true;
            drag.lane?.classList.remove('drop-target');
            drag.lane = null;
            return false;
        }
        finishClipDrag();
        desktop.startDrag(delivery);
        return true;
    }

    function bindAudioDrag(handle, editForDrag, openEditor) {
        handle.dataset.audioDrag = '';
        handle.title = t('拖动音频到 DAW 或工作台');
        handle.setAttribute('aria-label', handle.title);
        handle.ondragstart = event => event.preventDefault();
        handle.onpointerdown = event => {
            if (event.button !== 0 || !event.isPrimary || clipDrag) return;
            const edit = editForDrag();
            if (edit && state.library) audioPress = {
                edit, editForDrag, library: state.library.id, pointer: event.pointerId,
                x: event.clientX, y: event.clientY
            };
        };
        handle.onclick = event => {
            if (openEditor) return attempt(openEditor);
            if (event.detail !== 0) return;
            event.stopPropagation();
            attempt(() => prepareAudioDrag(editForDrag));
        };
    }

    function bindAudioGesture() {
        const tab = document.querySelector('#workspaceSwitch [data-ws="wwise"]');
        const overTab = node => node?.closest('#workspaceSwitch [data-ws="wwise"]') === tab;
        const hit = event => document.elementFromPoint(event.clientX, event.clientY);
        document.addEventListener('pointermove', event => {
            if (audioPress?.pointer !== event.pointerId) return;
            if (!(event.buttons & 1) || audioPress.library !== state.library?.id) { finishClipDrag(); return; }
            if (!clipDrag) {
                if (Math.hypot(event.clientX - audioPress.x, event.clientY - audioPress.y) < 5) return;
                const ghost = el('div', null, 'audio-drag-ghost');
                ghost.setAttribute('aria-hidden', 'true');
                document.body.append(ghost);
                clipDrag = { ...audioPress, ghost, leftWindow: false, lane: null };
                $('mainWorkspace').classList.add('library-dragging');
            }
            if (!document.body.hasPointerCapture(event.pointerId)) document.body.setPointerCapture(event.pointerId);
            const drag = clipDrag;
            if (outsideWindow(event) && leaveWithAudio(drag)) return;
            const node = drag.leftWindow ? null : hit(event);
            if (node) drag.ghost.style.transform = `translate(${event.clientX - 10}px,${event.clientY - 10}px)`;
            if (isLibrary() && overTab(node)) {
                drag.tabHover ??= setTimeout(() => {
                    if (clipDrag === drag) attempt(() => switchWorkspace('wwise'));
                }, 160);
            } else {
                clearTimeout(drag.tabHover);
                drag.tabHover = null;
            }
            const lane = timeline()?.audioDropLane(node);
            if (drag.lane !== lane) {
                drag.lane?.classList.remove('drop-target');
                lane?.classList.add('drop-target');
                drag.lane = lane;
            }
        });
        document.addEventListener('pointerup', event => {
            if (audioPress?.pointer !== event.pointerId) return;
            const drag = clipDrag;
            const node = drag && !drag.leftWindow && !outsideWindow(event) ? hit(event) : null;
            finishClipDrag();
            if (!drag || drag.library !== state.library?.id) return;
            if (drag.leftWindow) {
                if (outsideWindow(event)) attempt(() => prepareAudioDrag(drag.editForDrag));
                return;
            }
            if (overTab(node)) {
                attempt(async () => { await switchWorkspace('wwise'); timeline()?.stageClip(drag.edit.clip, drag.edit.item); });
            } else {
                attempt(() => timeline()?.dropAudio(drag.edit, node, event.clientX));
            }
        });
        const cancel = event => { if (audioPress?.pointer === event.pointerId) finishClipDrag(); };
        document.addEventListener('pointercancel', cancel);
        document.body.addEventListener('lostpointercapture', event => {
            if (!event.buttons) cancel(event);
            else if (clipDrag?.pointer === event.pointerId && outsideWindow(event)) leaveWithAudio(clipDrag);
        });
        document.addEventListener('keydown', event => {
            if (event.key !== 'Escape' || !audioPress) return;
            if (clipDrag) { event.preventDefault(); event.stopPropagation(); }
            finishClipDrag();
        }, { capture: true });
        window.addEventListener('blur', event => { if (event.target === window) finishClipDrag(); });
    }

    async function prepareAudioDrag(editForDrag) {
        if (preparingDrag) return;
        const edit = editForDrag();
        const library = state.library?.id;
        if (!edit || !library || preparedDrags.has(deliverySignature(edit))) return;
        const pending = preparingDrag = { signature: deliverySignature(edit) };
        updateDragHandle();
        say(t('正在准备 DAW 音频…'));
        try {
            const selection = JSON.stringify(edit.clip);
            const directory = state.dawDirectory || await chooseDaw();
            const stillValid = () => library === state.library?.id && directory === state.dawDirectory &&
                selection === JSON.stringify(editForDrag()?.clip);
            if (!directory || !stillValid()) return;
            await fx().finishEditing([libraryEffects || []]);
            if (!stillValid()) return;
            const signature = pending.signature = deliverySignature(editForDrag());
            updateDragHandle();
            const result = await desktop.desktopMedia('prepare', {
                project_id: library,
                source_fingerprint: edit.item.source_fingerprint || null,
                clip: { ...edit.clip, effects: libraryEffects ? fx().enabled(libraryEffects) : [] },
                rate: Number($('dawRate').value) || null,
                channels: Number($('dawChannels').value) || null,
                bits: Number($('dawBits').value),
                name: editForDrag()?.name || null
            });
            if (!stillValid() || signature !== deliverySignature(editForDrag())) return;
            rememberPreview(preparedDrags, signature, result.id, 24);
            say(t('音频已就绪，请再次拖到 DAW'));
        } finally {
            preparingDrag = null;
            updateDragHandle();
        }
    }

    function showInspector(tabId) {
        if (!isLibrary() && !['tabDetail', 'tabVst'].includes(tabId)) tabId = 'tabDetail';
        if (isLibrary()) libraryPanel = tabId;
        else wwisePanel = tabId;
        for (const tab of document.querySelectorAll('.insp-tab')) {
            const active = tab.id === tabId;
            tab.classList.toggle('on', active);
            tab.setAttribute('aria-selected', String(active));
            $(tab.getAttribute('aria-controls')).classList.toggle('hidden', !active);
        }
        if (tabId === 'tabGroups') attempt(renderAssetGroups);
        if (tabId === 'tabVst') {
            requestAnimationFrame(() => fx()?.refreshLayout());
            if (fx()) attempt(() => fx().discover());
        }
        if (tabId === 'tabDetail') loadAssetDetails();
    }

    function labelInspector() {
        $('tabDetail').textContent = isLibrary() ? t('详情') : t('中间件详情');
        $('tabVst').textContent = isLibrary() ? 'VST' : t('VST 效果');
        for (const tab of document.querySelectorAll('.insp-tab')) tab.title = tab.textContent;
    }

    async function switchWorkspace(which) {
        if (state.workspace === which) return;
        if (timeline()?.isBusy()) return say(t('中间件工作台正在处理当前操作'));
        previewRevision++;
        if (isLibrary()) {
            keepClip();
            libraryEditor = state.selected;
        } else {
            closeDockEditor();
            timelinePosition = playback.active ? playback.currentTime() : state.position;
        }
        peakRequest?.abort();
        previewBatch?.abort();
        listRequest?.abort();
        playback.stop();
        state.selected = state.range = state.waveform = renderedWave = null;
        state.workspace = which;
        $('mainWorkspace').classList.toggle('wwise', !isLibrary());
        $('bottomStage').classList.toggle('wwise', !isLibrary());
        syncBottomStage();
        $('importWrap').classList.toggle('hidden', !isLibrary());
        $('wwiseObjectInfo').classList.toggle('hidden', isLibrary());
        $('wwiseActions').classList.toggle('hidden', isLibrary());
        $('assetActions').classList.toggle('hidden', !isLibrary());
        $('fileLoudness').classList.toggle('hidden', !isLibrary());
        fx()?.resetLoudness();
        for (const id of ['tabAi', 'tabGroups']) $(id).classList.toggle('hidden', !isLibrary());
        labelInspector();
        showInspector(isLibrary() ? libraryPanel : wwisePanel);
        document.querySelectorAll('#workspaceSwitch button').forEach(button => {
            button.classList.toggle('on', button.dataset.ws === which);
            button.setAttribute('aria-selected', String(button.dataset.ws === which));
        });
        $('searchInput').value = isLibrary() ? state.search : $('wwiseFilter').value;
        $('searchInput').placeholder = t(isLibrary() ? '搜索音效、标签、UCS…' : '筛选工程素材…');
        if (isLibrary()) {
            timeline()?.hideDock();
            const item = libraryEditor;
            libraryEditor = null;
            attempt(() => select(item));
            if (listQueryKey !== catalogKey()) await Promise.all([loadItems(true), renderTree()]);
            else if (treeView !== state.view) await renderTree();
        } else {
            state.position = timelinePosition;
            await timeline()?.show();
        }
        syncTransport();
    }

    function showLibraryEffects() {
        if (!isLibrary()) return;
        libraryEffects ||= fx()?.create();
        fx()?.useRack(libraryEffects, t('音效库试听主输出'), ({ live } = {}) => {
            invalidateDelivery();
            if (!live && isLibrary()) {
                renderedWave = null;
                refreshAudition();
                syncTransport();
            }
        }, state.selected?.sample_rate || 48000);
    }

    async function locateLibraryItem(id) {
        const item = await api.get(route(`/catalog/items/${encodeURIComponent(id)}`));
        await switchWorkspace('library');
        state.view = 'folder';
        state.filter = {};
        state.search = item.filename;
        $('searchInput').value = item.filename;
        $('queryEcho').textContent = '';
        for (const name of ['filterFormat', 'filterChannels', 'filterDuration']) $(name).value = '';
        await Promise.all([loadItems(true), renderTree(), select(item)]);
        const index = state.items.findIndex(row => row.id === id);
        if (index >= 0) {
            $('rows').scrollTop = index * ROW;
            renderRows();
        }
    }

    let cleanupData;
    function formatDate(value) {
        const timestamp = Date.parse(value);
        return Number.isFinite(timestamp) ? date(timestamp) : value;
    }

    async function openCleanup(execute = false) {
        modal('ovGc');
        const selected = [...$('gcList').querySelectorAll('input[data-cleanup]:checked')].map(input => input.value);
        const data = execute ? await desktop.desktopMedia('cleanup', { paths: selected }) : await api.get('/api/v1/cleanup');
        renderCleanup(data);
    }

    function renderCleanup(data, preserveSelection = false) {
        const selected = new Set([...$('gcList').querySelectorAll('input[data-cleanup]:checked')].map(input => input.value));
        const files = data.files.map(file => {
            const row = el('label', null, 'gc-row');
            const checkbox = el('input');
            checkbox.type = 'checkbox';
            checkbox.dataset.cleanup = '';
            checkbox.value = file.path;
            checkbox.disabled = !file.eligible || file.removed;
            checkbox.checked = !checkbox.disabled && (!preserveSelection || selected.has(file.path));
            checkbox.onchange = () => {
                $('btnGcRun').disabled = !$('gcList').querySelector('input[data-cleanup]:checked');
            };
            row.append(checkbox, el('span', `${file.path} · ${formatBytes(file.bytes)} · ${t(file.removed ? '已清理' : file.reason)}`));
            return row;
        });
        const backups = data.backups.map(file => {
            const row = el('div', null, 'gc-row');
            row.append(el('span', `${file.target} · ${formatDate(file.created_at)}`), button(t('恢复'), async () => {
                if (!await confirm(t('用备份恢复上次写回前的 Wwise 源 WAV？'))) return;
                await desktop.desktopMedia('restore', { id: file.id });
                say(t('已恢复 Wwise 源文件'));
                await timeline()?.resetSelection();
            }));
            return row;
        });
        const recovery = data.recovery.map(file => el('p', t('待恢复 · {target} · 备份 {backup}', { target: file.target, backup: file.backup }), 'gc-row'));
        $('gcList').replaceChildren(...files, ...backups, ...recovery);
        $('gcHint').textContent = files.length || backups.length || recovery.length ? '' : t('暂无可清理的暂存或恢复备份');
        $('btnGcRun').disabled = !$('gcList').querySelector('input[data-cleanup]:checked');
        cleanupData = data;
    }

    let aiSettings;
    function showProvider() {
        const provider = $('llmProvider').value;
        const config = aiSettings?.llm?.config?.[provider] || {};
        $('llmModel').value = config.model || '';
        $('llmUrl').value = config.base_url || '';
        $('llmKey').value = '';
        $('llmKey').placeholder = config.has_api_key ? t('已保存密钥') : '';
        $('llmClearKey').checked = false;
    }

    let settingsAssistantLoad;
    async function showSettingsPage(category) {
        for (const item of document.querySelectorAll('[data-settings]')) {
            const active = item.dataset.settings === category;
            item.classList.toggle('on', active);
            item.setAttribute('aria-pressed', String(active));
            $(`settings-${item.dataset.settings}`).classList.toggle('hidden', !active);
        }
        if (category === 'vst') await fx()?.settings();
        if (category === 'ai') {
            settingsAssistantLoad ??= desktop.aiConfig.get().then(settings => {
                aiSettings = settings;
                $('llmProvider').value = settings.llm.provider;
                showProvider();
            }).catch(error => {
                settingsAssistantLoad = null;
                throw error;
            });
            await settingsAssistantLoad;
        }
    }

    function openSettings() {
        settingsAssistantLoad = null;
        modal('ovSettings');
        return showSettingsPage('general');
    }

    async function updateAssistant(action) {
        const provider = $('llmProvider').value;
        const key = $('llmKey').value.trim();
        const embed = aiSettings?.embedding?.provider || 'default';
        const result = await desktop.aiConfig[action]({
            llm_provider: provider, llm_config: { model: $('llmModel').value, base_url: $('llmUrl').value },
            embedding_provider: embed, embedding_config: aiSettings?.embedding?.config?.[embed] || {},
            llm_secret: key ? { action: 'set', value: key } : { action: $('llmClearKey').checked ? 'clear' : 'keep' }
        });
        if (action === 'save') {
            $('llmKey').value = '';
            say(t('助手设置已保存'));
        } else {
            say(result.success ? t('助手服务连接成功') : t('助手服务连接失败'));
        }
    }

    async function askAssistant() {
        if (!state.library) return;
        const input = $('aiInput');
        const message = input.value.trim();
        if (!message) return;
        input.value = '';
        try {
            await desktop.aiConfig.prepareChat();
        } catch (error) {
            if (!input.value) input.value = message;
            throw error;
        }
        $('aiMsgs').append(el('div', message, 'ai-msg user'));
        const bubble = el('div', null, 'ai-msg bot');
        setText(bubble, () => t('正在检索…'));
        $('aiMsgs').append(bubble);
        const library = state.library.id;
        const result = await api.fetch('/api/v1/ai/chat', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message, project_id: library, locale: i18n.language })
        });
        if (!result.ok) throw new Error('助手请求失败');
        const reader = result.body.pipeThrough(new TextDecoderStream()).getReader();
        let pending = '';
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            pending += value;
            let lineEnd;
            while ((lineEnd = pending.indexOf('\n')) >= 0) {
                const line = pending.slice(0, lineEnd);
                pending = pending.slice(lineEnd + 1);
                if (!line.startsWith('data:')) continue;
                const data = line.slice(5).trim();
                if (data === '[DONE]') continue;
                const event = JSON.parse(data);
                if (event.type === 'searching') setText(bubble, () => t('正在搜索：{query}', { query: event.query }));
                if (event.type === 'chat') setText(bubble, event.content);
                if (event.type === 'error') setText(bubble, () => `${t('助手请求失败')} · ${t('使用关键词搜索仍可查找素材。')}`);
                if (event.type === 'results') {
                    setText(bubble, '');
                    const summary = el('div');
                    setText(summary, () => event.count ? t('相关素材：{count}', { count: event.count }) : t('找不到相关素材'));
                    bubble.append(summary);
                    for (const item of event.results) {
                        bubble.append(button(item.filename, () => {
                            if (state.library?.id === library) return select(item, true);
                            say(t('请切回结果所属的音效库'));
                        }, 'view-item'));
                    }
                }
                $('aiMsgs').scrollTop = $('aiMsgs').scrollHeight;
            }
        }
    }

    function bindRail() {
        for (const handle of document.querySelectorAll('.rail-resize')) {
            const key = `soundbot-rail-width-${handle.dataset.rail}`;
            const rail = handle.previousElementSibling;
            const initial = handle.dataset.rail === 'library' ? 232 : 275;
            let width = Number(localStorage.getItem(key)) || initial;
            let start;
            const apply = desired => {
                const limit = Math.max(135, Math.min(580, window.innerWidth - 545));
                const value = Math.max(135, Math.min(limit, Math.round(desired)));
                rail.style.setProperty('--rail-width', `${value}px`);
                handle.setAttribute('aria-valuemin', '135');
                handle.setAttribute('aria-valuemax', String(limit));
                handle.setAttribute('aria-valuenow', String(value));
                return value;
            };
            handle.onpointerdown = event => {
                if (event.button !== 0) return;
                event.preventDefault();
                start = { x: event.clientX, width: rail.clientWidth };
                handle.setPointerCapture(event.pointerId);
                handle.classList.add('resizing');
            };
            handle.onpointermove = event => {
                if (start) width = apply(start.width + event.clientX - start.x);
            };
            handle.onlostpointercapture = () => {
                start = null;
                handle.classList.remove('resizing');
                localStorage.setItem(key, String(width));
                scheduleMiniPeaks();
            };
            handle.ondblclick = () => {
                width = apply(initial);
                localStorage.removeItem(key);
                scheduleMiniPeaks();
            };
            handle.onkeydown = event => {
                if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return;
                event.preventDefault();
                width = apply(event.key === 'Home' ? initial : rail.clientWidth + (event.key === 'ArrowLeft' ? -16 : 16));
                localStorage.setItem(key, String(width));
                scheduleMiniPeaks();
            };
            window.addEventListener('resize', () => apply(width));
            apply(width);
        }
        let listHeight = 0;
        new ResizeObserver(([entry]) => {
            if (entry.contentRect.height !== listHeight && isLibrary()) renderRows();
            else scheduleMiniPeaks();
            listHeight = entry.contentRect.height;
        }).observe($('rows'));
        new ResizeObserver(scheduleMiniPeaks).observe($('miniRows'));
        $('miniRows').addEventListener('scroll', scheduleMiniPeaks, { passive: true });
    }

    function bind() {
        const on = (id, fn) => { $(id).onclick = () => attempt(fn); };
        bindWave();
        bindRail();
        labelInspector();
        syncBottomStage();
        let searchTimer;
        $('searchInput').oninput = event => {
            clearTimeout(searchTimer);
            if (event.isComposing) return;
            if (!isLibrary()) {
                $('wwiseFilter').value = event.target.value;
                timeline()?.renderTree();
                return;
            }
            state.search = event.target.value;
            searchTimer = setTimeout(() => attempt(() => loadItems(true)), 170);
        };
        for (const id of ['filterFormat', 'filterChannels', 'filterDuration']) $(id).onchange = () => attempt(() => loadItems(true));
        on('groupToggle', async () => {
            state.groupByFolder = !state.groupByFolder;
            $('groupToggle').setAttribute('aria-pressed', String(state.groupByFolder));
            $('groupToggle').classList.toggle('on', state.groupByFolder);
            await loadItems(true);
        });
        document.querySelectorAll('#viewTabs button').forEach(node => {
            node.onclick = () => attempt(async () => {
                state.view = node.dataset.view;
                await Promise.all([setFilter({}), renderTree()]);
            });
        });
        document.querySelectorAll('[data-sort]').forEach(node => {
            node.onclick = () => attempt(async () => {
                const sort = node.dataset.sort;
                state.sort = state.sort === sort ? `${sort}_desc` : sort;
                document.querySelectorAll('[data-sort]').forEach(item => {
                    const selected = state.sort.split('_')[0] === item.dataset.sort;
                    item.setAttribute('aria-pressed', String(selected));
                    item.querySelector('.arrow').textContent = selected ? (state.sort.endsWith('_desc') ? '▼' : '▲') : '';
                });
                await loadItems(true);
            });
        });
        document.querySelectorAll('#workspaceSwitch button').forEach(node => node.onclick = () => attempt(() => switchWorkspace(node.dataset.ws)));
        bindAudioGesture();
        document.querySelectorAll('[data-close]').forEach(node => node.onclick = () => modal(node.dataset.close, false));
        document.querySelectorAll('.overlay').forEach(node => node.addEventListener('click', event => {
            if (event.target === node) modal(node.id, false);
        }));
        for (const [buttonId, menuId] of [['btnFile', 'fileMenu'], ['btnImport', 'importMenu']]) {
            $(buttonId).onclick = () => $(menuId).classList.toggle('show');
        }
        document.addEventListener('click', event => {
            if (!event.target.closest('.menu-wrap')) document.querySelectorAll('.dropdown.show').forEach(menu => menu.classList.remove('show'));
            if (event.target.closest('.dropdown button')) event.target.closest('.dropdown').classList.remove('show');
        });
        $('contextPill').onchange = () => attempt(() => switchLibrary(state.libraries.find(item => item.id === $('contextPill').value)));
        on('btnNewLib', createLibrary);
        on('btnRenameLib', async () => {
            const name = await promptText(t('音效库名称'), state.library.name);
            if (!name) return;
            await api.put(route(), { name });
            await loadLibraries(state.library.id);
        });
        on('btnRemoveLib', async () => {
            const library = state.library;
            if (removingLibrary || !library || !await confirm(t('移除音效库「{name}」？原音频文件会保留。', { name: libraryName(library) }))) return;
            if (timeline()?.hasLibraryClips() && !await timeline().discard()) return;
            removingLibrary = library.id;
            try {
                await api.delete(route('', library.id));
                await loadLibraries();
            } finally { removingLibrary = null; }
        });
        on('impFile', () => importFiles(false));
        on('impFolder', () => importFiles(true));
        $('libraryView').addEventListener('dragover', event => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); });
        desktop.fileImport.onLibraryDrop(result => {
            const library = state.library?.id;
            attempt(async () => {
                if (!result.success) throw new Error(result.error);
                await addFiles(result.files, library);
            });
        });
        on('btnBulk', async () => { await fillBulkGroups(); $('bulkDialog').showModal(); });
        on('closeBulk', () => $('bulkDialog').close());
        on('bulkFavorite', () => mutate('favorite', true));
        on('bulkUnfavorite', () => mutate('favorite', false));
        on('bulkRemove', () => mutate('remove', null));
        on('bulkTags', async () => {
            const text = await promptText(t('标签，用逗号分隔'));
            if (text) await mutate('tags', text.split(/[,，]/).map(value => value.trim()).filter(Boolean));
        });
        on('bulkAddGroup', () => $('bulkGroups').value && mutate('group', $('bulkGroups').value));
        on('btnNewGroup', createGroup);
        on('btnFavorite', () => state.selected && mutate('favorite', !state.selected.favorite, [state.selected.id]));
        on('btnReveal', () => state.selected && desktop.revealFile(state.selected.path));
        on('btnRelocate', () => state.selected && relocateItem({ file_id: state.selected.id }, false));
        on('btnAssetInfo', editAssetInfo);
        on('btnDawDirectory', chooseDaw);
        on('btnBackDock', closeDockEditor);
        on('btnCreateGroup', createGroup);
        desktop.onDeliveryError(message => { invalidateDelivery(); say(describeError(message)); });
        $('actionDialog').onclose = event => {
            const resolve = resolveAction;
            resolveAction = null;
            resolve?.(event.currentTarget.returnValue === 'ok');
        };
        desktop.onAppConfirmation(async options => {
            const accepted = await actionDialog(options, true);
            desktop.respondToConfirmation(options.id, accepted);
        });
        on('btnUseWwise', async () => {
            const editing = editorClip();
            if (!editing || timeline()?.isBusy()) return;
            await switchWorkspace('wwise');
            timeline()?.stageClip(editing.clip, editing.item);
        });
        on('btnJobs', async () => { modal('ovJobs'); await refreshJobs(); });
        on('btnJobHistory', async () => { modal('ovJobs'); await refreshJobs(); });
        on('btnRebuild', async () => { await api.post(route('/index/rebuild'), {}); await refreshJobs(); });
        on('btnGc', () => openCleanup());
        on('btnGcRun', () => openCleanup(true));
        on('btnSettings', openSettings);
        for (const item of document.querySelectorAll('[data-settings]')) {
            item.onclick = () => attempt(() => showSettingsPage(item.dataset.settings));
        }
        $('languageSelect').replaceChildren(...i18n.languages.map(({ id, name }) => {
            const option = el('option', name);
            option.value = option.lang = id;
            return option;
        }));
        $('languageSelect').value = i18n.language;
        desktop.setLanguage(i18n.language);
        $('languageSelect').onchange = event => i18n.setLanguage(event.target.value);
        window.addEventListener('soundbot-language', () => {
            renderJobTray();
            desktop.setLanguage(i18n.language);
            $('searchInput').placeholder = t(isLibrary() ? '搜索音效、标签、UCS…' : '筛选工程素材…');
            $('btnFavorite').textContent = state.selected?.favorite ? t('取消收藏') : t('添加到收藏');
            $('languageSelect').value = i18n.language;
            renderLibraryPicker();
            $('editorSourceBadge').textContent = libraryName(state.library);
            renderSortControls();
            renderFilterSummary();
            $('sampleRateInfo').textContent = state.selected?.sample_rate ? `${number(state.selected.sample_rate)} Hz` : '';
            updateDragHandle();
            showRange();
            labelInspector();
            $('llmKey').placeholder = aiSettings?.llm?.config?.[$('llmProvider').value]?.has_api_key ? t('已保存密钥') : '';
            if (isLibrary()) updateAssetDetails(state.selected);
            attempt(renderTree);
            renderRows();
            if (isLibrary()) {
                showLibraryEffects();
                attempt(renderAssetGroups);
            } else timeline()?.refreshLabels();
            if ($('ovJobs').classList.contains('show')) renderJobs();
            if ($('ovGc').classList.contains('show') && cleanupData) renderCleanup(cleanupData, true);
            if ($('ovSettings').classList.contains('show') && !$('settings-vst').classList.contains('hidden')) attempt(() => fx()?.settings());
        });
        on('btnOpenLogs', () => desktop.backendStatus.openLogs());
        for (const button of document.querySelectorAll('[data-credit-link]')) {
            button.onclick = () => attempt(() => desktop.openCreditLink(button.dataset.creditLink));
        }
        on('btnSaveAI', () => updateAssistant('save'));
        on('btnTestAI', () => updateAssistant('test'));
        $('llmProvider').onchange = showProvider;
        on('btnPlay', togglePlay);
        on('btnStop', () => { previewRevision++; stop(true); });
        on('btnLoop', () => { state.loop = !state.loop; $('btnLoop').classList.toggle('loop-on', state.loop); });
        const step = direction => {
            if (state.dockEditing) return timeline()?.stepDock(direction);
            if (!isLibrary()) return timeline()?.step(direction);
            const index = state.items.findIndex(item => item.id === state.selected?.id);
            const item = state.items[index + direction];
            if (isLibrary() && item) return select(item, playback.active);
        };
        on('btnPrev', () => step(-1));
        on('btnNext', () => step(1));
        $('volRange').oninput = () => {
            $('volVal').textContent = `${$('volRange').value}%`;
            playback.setVolume(Number($('volRange').value) / 100, state.muted);
        };
        on('btnMute', () => {
            state.muted = !state.muted;
            playback.setVolume(Number($('volRange').value) / 100, state.muted);
            $('volWrap').classList.toggle('muted', state.muted);
        });
        for (const tab of document.querySelectorAll('.insp-tab')) on(tab.id, () => showInspector(tab.id));
        on('aiSend', askAssistant);
        $('aiInput').onkeydown = event => { if (event.key === 'Enter' && !event.isComposing) attempt(askAssistant); };
        document.addEventListener('keydown', event => {
            if (event.code !== 'Space' || event.isComposing) return;
            const target = event.target;
            const textInput = target instanceof HTMLInputElement &&
                ['text', 'search', 'email', 'tel', 'url', 'password', 'number'].includes(target.type);
            if (textInput || target instanceof HTMLTextAreaElement || target.isContentEditable) return;
            // Focused buttons, tabs and switches otherwise consume Space on keyup.
            event.preventDefault();
            event.stopPropagation();
            if (!event.repeat && !event.ctrlKey && !event.altKey && !event.metaKey && !event.shiftKey &&
                !document.querySelector('dialog[open], .overlay.show')) togglePlay();
        }, { capture: true });
        const themes = window.matchMedia('(prefers-color-scheme: light)');
        const applyTheme = theme => {
            localStorage.setItem('soundbot-theme', theme);
            const resolved = theme === 'system' ? (themes.matches ? 'light' : 'dark') : theme;
            document.documentElement.dataset.theme = resolved;
            $('iconSun').style.display = resolved === 'dark' ? '' : 'none';
            $('iconMoon').style.display = resolved === 'light' ? '' : 'none';
            document.querySelectorAll('#themeSeg button').forEach(item => item.classList.toggle('on', item.dataset.t === theme));
            renderRows();
            syncTransport();
        };
        document.querySelectorAll('#themeSeg button').forEach(item => item.onclick = () => applyTheme(item.dataset.t));
        $('btnTheme').onclick = () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
        themes.onchange = () => { if (localStorage.getItem('soundbot-theme') === 'system') applyTheme('system'); };
        applyTheme(localStorage.getItem('soundbot-theme') || 'system');
        desktop.onMenuEvent(action => {
            const buttons = { 'menu-new-project': 'btnNewLib', 'menu-import-file': 'impFile', 'menu-import-folder': 'impFolder' };
            if (buttons[action]) $(buttons[action]).click();
        });
    }

    window.SoundBotWorkspace = { $, state, api, desktop, playback, route, say, fmt, attempt, el, button, modal,
        confirm, syncTransport, stop, togglePlay, refreshAudition, switchWorkspace, select, locateLibraryItem, openDockEditor, closeDockEditor,
        editorClip, miniCanvas, assetCover, scheduleMiniPeaks, cachedPeaks,
        catalogKey, browseCatalog, setFilter, libraryName, folderKey, filenameCategoryLabel,
        bindAudioDrag, wholeDrag,
        get draggingAudio() { return Boolean(clipDrag); } };
    document.addEventListener('DOMContentLoaded', () => attempt(async () => {
        if (!desktop) throw new Error('请在 SoundBot 桌面应用中打开');
        bind();
        const runtime = await desktop.runtime.getConfig();
        api.configure(runtime);
        const caps = await desktop.fileImport.getCapabilities();
        for (const [ext, format] of Object.entries(caps.formats || {})) {
            const option = el('option', format.name); option.value = ext.slice(1); $('filterFormat').append(option);
        }
        await loadLibraries(localStorage.getItem('soundbot-library') || 'default');
        setInterval(() => {
            if (playback.active) syncTransport();
            fx()?.meters(playback.levels());
        }, 45);
    }));
})();
