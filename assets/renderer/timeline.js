/* Shared multi-track session for a selected middleware audio source. */
(() => {
    const ui = window.SoundBotWorkspace;
    const { $, state, api, desktop, playback, route, fmt, say, attempt, el, button, confirm, stop, syncTransport } = ui;
    const { waveform } = window.SoundBotRenderer;
    const fx = window.SoundBotEffects;
    const { t, number, describeError, setText } = window.SoundBotI18n;
    const { clipDragType } = ui;
    const hosts = Object.freeze({ wwise: 'Wwise', fmod: 'FMOD Studio', cri: 'CRI Atom Craft' });
    const savedHost = localStorage.getItem('soundbot-middleware-host');
    let host = Object.hasOwn(hosts, savedHost) ? savedHost : 'wwise';
    let connection;
    let deliveredFile;
    let source;
    let target;
    let tracks = [];
    let selectedTrack;
    let selectedClip;
    let dockSelection;
    let dockItems = [];
    let dockDrag;
    let pendingClip;
    let dockRequest;
    let dirty = false;
    let busy = false;
    let grid = 0.25;
    let snap = true;
    let autoCrossfade = true;
    let crossfadeCurve = 'equal_power';
    let zoom = 1;
    let zoomReference = 8;
    let editingClip = false;
    let panClip;
    const expanded = new Map();
    const filteredExpansion = new Map();
    let previewGeneration = 0;
    const minClip = 0.01;
    const limit = (value, lo, hi) => Math.max(lo, Math.min(hi, value));
    const startAt = time => Math.max(0, snap ? Math.round(time / grid) * grid : time);
    const makeTrack = ({ name, label, number: trackNumber, clips = [] }) => ({
        id: crypto.randomUUID(), name, label, trackNumber, clips, mute: false, solo: false,
        gain: 0, channels: null, effects: fx.create(), crossfades: new Map()
    });
    const trackName = track => track.name || t(track.label, { count: track.trackNumber });
    const duration = () => Math.max(0, ...tracks.flatMap(track => track.clips.map(clip => clip.start + clip.duration)));
    const timelineLength = () => Math.max(8, Math.ceil(duration() + 1));
    const active = () => tracks.find(track => track.id === selectedTrack);
    const countClips = () => tracks.reduce((count, track) => count + track.clips.length, 0);
    const hasLibraryClips = () => Boolean(pendingClip || tracks.some(track => track.clips.some(clip => clip.file_id)));

    function dirtyState(value) {
        dirty = value;
        desktop?.editingDirty(value);
        $('wwiseWriteState').textContent = value ? t(host === 'wwise' ? '有未写回编辑' : '有未交付编辑') : t('未修改');
        $('btnExportWwise').disabled = !value || !target || busy || !duration();
        $('dawTracks').inert = busy;
        if (busy) closeClipMenu();
        $('crossfadeCurve').disabled = busy || !autoCrossfade;
        if (state.workspace === 'wwise') $('rack').parentElement.inert = busy;
    }

    function pause(reset = false) {
        previewGeneration++;
        stop(reset);
    }

    function emptySession() {
        pause(true);
        target = null;
        source = null;
        tracks = [];
        selectedTrack = selectedClip = panClip = null;
        zoom = 1;
        zoomReference = 8;
        autoCrossfade = true;
        crossfadeCurve = 'equal_power';
        $('dawTracks').scrollLeft = 0;
        dirtyState(false);
        renderLog();
        render();
    }

    async function discard() {
        if (busy) return false;
        if (dirty && !await confirm(t('丢弃当前中间件会话中的编辑？'))) return false;
        pendingClip = null;
        const previous = source;
        emptySession();
        if (previous && connection) await choose(previous, true);
        return true;
    }

    const objectAliases = { PhysicalFolder: 'Folder', Container: 'ActorMixer' };
    const objectIcons = {
        WorkUnit: 'M4 3h11l5 5v13H4z M15 3v5h5 M8 13h8m-8 4h8',
        Folder: 'M3 6h7l2 3h9v10H3z',
        ActorMixer: 'M5 3v18M12 3v18M19 3v18M3 8h4m3 8h4m3-6h4',
        RandomSequenceContainer: 'M3 6h3c6 0 6 12 12 12h3m-4-4 4 4-4 4M3 18h3m5-6c2-3 4-6 7-6h3m-4-4 4 4-4 4',
        SwitchContainer: 'M3 12h6m0 0 6-6h6m-12 6 6 6h6M18 3l3 3-3 3m0 6 3 3-3 3',
        BlendContainer: 'M3 17c5 0 5-10 9-10s4 10 9 10M3 10c5 0 5 7 9 7s4-7 9-7',
        Sound: 'M3 9h4l5-4v14l-5-4H3z M16 9c2 1 2 5 0 6m3-9c4 3 4 9 0 12',
        AudioFileSource: 'M4 3h10l6 6v12H4z M14 3v6h6 M7 14h2l1-3 3 6 1-3h3'
    };

    function svg(tag, attributes = {}) {
        const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
        for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
        return node;
    }

    function treeLabel(node, label, type, color) {
        type = Object.hasOwn(objectAliases, type) ? objectAliases[type] : type;
        const icon = svg('svg', { viewBox: '0 0 24 24', class: 'wwise-icon', 'aria-hidden': 'true' });
        icon.append(svg('path', { d: Object.hasOwn(objectIcons, type) ? objectIcons[type] : objectIcons.Folder }));
        if (/^#[\da-f]{6}$/i.test(color || '')) icon.style.color = color;
        node.append(icon, el('span', label, 'tree-name'));
    }

    function treeBranch(tree, parts) {
        let level = tree;
        for (const part of parts) {
            if (!level.has(part)) level.set(part, { items: new Map() });
            level = level.get(part).items;
        }
        return level;
    }

    const treeRowHeight = 29;
    let treeRows = [];
    let treeFrame;
    let treeFocus;
    let treeWindow;
    let treeExpansion = expanded;

    function drawTree() {
        const root = $('wwiseTree');
        if (!treeRows.length || !root.clientHeight) return;
        const first = Math.max(0, Math.floor(root.scrollTop / treeRowHeight) - 8);
        const last = Math.min(treeRows.length, first + Math.ceil(root.clientHeight / treeRowHeight) + 16);
        if (treeWindow?.rows === treeRows && treeWindow.first === first && treeWindow.last === last && treeWindow.focus === treeFocus) return;
        treeWindow = { rows: treeRows, first, last, focus: treeFocus };
        const fragment = document.createDocumentFragment();
        const spacer = count => {
            const node = el('div');
            node.style.height = `${count * treeRowHeight}px`;
            node.setAttribute('aria-hidden', 'true');
            fragment.append(node);
        };
        spacer(first);
        for (let index = first; index < last; index++) {
            const row = treeRows[index];
            const { depth, item, path, label, metadata, key, open, hasChildren } = row;
            const node = el('button', null, 'wwise-node');
            node.type = 'button';
            node.dataset.treeIndex = index;
            node.tabIndex = -1;
            node.setAttribute('role', 'treeitem');
            node.setAttribute('aria-level', depth + 1);
            node.style.setProperty('--tree-depth', depth);
            if (depth) {
                const guides = el('span', null, 'wwise-guides');
                guides.setAttribute('aria-hidden', 'true');
                node.append(guides);
            }
            const arrow = el('span', null, 'wwise-disclosure');
            if (hasChildren) {
                arrow.classList.add(open ? 'open' : 'closed');
                node.setAttribute('aria-expanded', String(open));
            }
            node.append(arrow);
            treeLabel(node, item?.name || label, item ? item.type || 'AudioFileSource' : metadata?.type,
                item?.color || metadata?.color);
            if (item) {
                node.setAttribute('aria-selected', String(source?.id === item.id));
                node.classList.toggle('sel', source?.id === item.id);
            }
            node.title = path;
            node.onfocus = () => { treeFocus = index; };
            node.onclick = () => {
                treeFocus = index;
                if (item) attempt(() => choose(item));
                else if (hasChildren) {
                    treeExpansion.set(key, !open);
                    renderTree();
                }
                root.querySelector(`[data-tree-index="${index}"]`)?.focus({ preventScroll: true });
            };
            fragment.append(node);
        }
        spacer(treeRows.length - last);
        if (connection?.truncated && last === treeRows.length) fragment.append(
            el('p', t('素材目录超出扫描上限，请选择更具体的素材目录。'), 'wwise-tree-note'));
        const focused = root.contains(document.activeElement) && document.activeElement !== root;
        const offset = root.scrollTop;
        root.replaceChildren(fragment);
        root.scrollTop = offset;
        if (focused) {
            const next = root.querySelector(`[data-tree-index="${treeFocus}"]`);
            (next || root).focus({ preventScroll: true });
        }
    }

    function renderTree() {
        const root = $('wwiseTree');
        const query = $('wwiseFilter').value.trim().toLowerCase();
        const objects = connection?.objects || {};
        const tree = new Map();
        const separator = host === 'wwise' ? '\\' : '/';
        const prefix = host === 'wwise' ? separator : '';
        const focusKey = treeRows[treeFocus]?.key;
        treeExpansion = query ? filteredExpansion : expanded;
        for (const [path, node] of Object.entries(objects)) {
            if (node.type !== 'Sound' && path.toLowerCase().includes(query)) {
                treeBranch(tree, path.split(separator).filter(Boolean));
            }
        }
        for (const item of connection?.sources || []) {
            if (!(item.path || item.name).toLowerCase().includes(query)) continue;
            const parts = (item.path || '').split(separator).filter(Boolean);
            treeBranch(tree, parts.slice(0, -1)).set(item.id, { item });
        }
        treeRows = [];
        function flatten(entries, parents = []) {
            for (const [label, node] of entries) {
                const depth = parents.length;
                if (node.item) {
                    treeRows.push({ depth, item: node.item, key: node.item.id, path: node.item.path });
                    continue;
                }
                const parts = [...parents, label];
                const path = prefix + parts.join(separator);
                const metadata = objects[path];
                const key = metadata?.id || path;
                const hasChildren = node.items.size > 0;
                const open = hasChildren && (treeExpansion.get(key) ?? (Boolean(query) || depth < 2));
                treeRows.push({ depth, path, label, metadata, key, hasChildren, open });
                if (open) flatten(node.items, parts);
            }
        }
        flatten(tree);
        treeFocus = Math.max(0, treeRows.findIndex(row => row.key === focusKey));
        if (treeRows.length) {
            root.scrollTop = Math.min(root.scrollTop, Math.max(0, treeRows.length * treeRowHeight - root.clientHeight));
            drawTree();
        } else {
            root.replaceChildren(el('div', connection
                ? t(host === 'wwise' ? '没有匹配的 Sound SFX 源' : '没有匹配的工程音频源')
                : t(host === 'wwise' ? '连接后显示当前工程的 Sound SFX 源' : '打开工程后显示素材目录中的音频'), 'wwise-tree-note'));
        }
    }

    function bindTree() {
        const root = $('wwiseTree');
        root.onscroll = () => {
            if (treeFrame != null) return;
            treeFrame = requestAnimationFrame(() => {
                treeFrame = null;
                drawTree();
            });
        };
        new ResizeObserver(drawTree).observe(root);
        root.onfocus = () => {
            const index = Math.min(treeRows.length - 1, Math.max(0, Math.floor(root.scrollTop / treeRowHeight)));
            if (index >= 0) {
                const row = root.querySelector(`[data-tree-index="${index}"]`);
                if (row) { treeFocus = index; row.focus({ preventScroll: true }); }
            }
        };
        root.onkeydown = event => {
            const row = treeRows[treeFocus];
            if (!row) return;
            let next = treeFocus;
            if (event.key === 'ArrowDown') next++;
            else if (event.key === 'ArrowUp') next--;
            else if (event.key === 'Home') next = 0;
            else if (event.key === 'End') next = treeRows.length - 1;
            else if (event.key === 'ArrowRight' && row.hasChildren) {
                if (row.open) next++;
                else { treeExpansion.set(row.key, true); renderTree(); }
            } else if (event.key === 'ArrowLeft') {
                if (row.open) { treeExpansion.set(row.key, false); renderTree(); }
                else while (next > 0 && treeRows[next].depth >= row.depth) next--;
            } else return;
            event.preventDefault();
            treeFocus = limit(next, 0, treeRows.length - 1);
            const y = treeFocus * treeRowHeight;
            if (y < root.scrollTop) root.scrollTop = y;
            else if (y + treeRowHeight > root.scrollTop + root.clientHeight) {
                root.scrollTop = y + treeRowHeight - root.clientHeight;
            }
            drawTree();
            root.querySelector(`[data-tree-index="${treeFocus}"]`)?.focus({ preventScroll: true });
        };
    }

    function renderHostLabels() {
        const local = host !== 'wwise';
        const name = hosts[host];
        for (const id of ['middlewareHost', 'middlewareSettingsHost']) {
            $(id).value = host;
            $(id).disabled = busy;
        }
        $('waapiSettings').classList.toggle('hidden', local);
        $('btnChooseHostProject').classList.toggle('hidden', !local);
        $('btnChooseHostProject').disabled = busy;
        $('btnConnectInline').disabled = $('btnWwiseConnect').disabled = busy;
        $('middlewareTreeTitle').textContent = t('{host} · 工程浏览器', { host: name });
        const open = local ? t('打开工程…') : t('连接 Wwise');
        $('btnConnectInline').textContent = connection ? t('刷新工程') : open;
        $('btnWwiseConnect').textContent = connection ? t(local ? '关闭工程' : '断开连接') : open;
        $('wwiseConnText').textContent = connection ? `${t(local ? '工程已打开' : '已连接')} · ${connection.project.name}`
            : t(local ? '未打开工程' : 'WAAPI 未连接');
        $('wwiseConnDot').classList.toggle('off', !connection);
        $('inspWaapiState').textContent = connection ? name : t('未连接');
        $('middlewareSetupHint').textContent = t(local
            ? '选择工程文件及宿主设置中实际使用的素材目录。'
            : '在 Wwise 中开启本机 WAAPI，并打开要编辑的工程。');
        $('btnExportWwise').textContent = t(local ? '渲染新 WAV' : '渲染并导回 Wwise');
        $('middlewareDeliveryMode').textContent = t(local ? '渲染为新 WAV，交由宿主导入' : '多轨混音 → 替换源 WAV · 保留 Sound SFX 与引用');
        $('middlewareRecoveryPolicy').textContent = t(local ? '原始素材和工程文件保持不变' : '先备份原文件 · 不直接改 .wem');
    }

    function renderLog(render) {
        setText($('middlewareLogText'), render || (() => t(host === 'wwise'
            ? '多轨修改只保存在会话中；点击右侧“渲染并导回 Wwise”后才替换目标源 WAV。'
            : '多轨修改只保存在会话中。渲染后请在宿主中导入新素材。')));
        $('btnRevealMiddlewareFile').classList.toggle('hidden', !deliveredFile);
    }

    async function connect(pick = false) {
        if (busy || (dirty && !await confirm(t('重新打开工程会丢弃当前中间件会话。继续？')))) return;
        busy = true;
        dirtyState(dirty);
        renderHostLabels();
        $('wwiseConnText').textContent = t(host === 'wwise' ? '正在连接 WAAPI…' : '正在打开工程…');
        try {
            const result = await desktop.desktopMedia('connect', { host, pick, url: $('waapiUrl').value.trim() });
            if (!result) return;
            if (connection?.project?.path !== result.project?.path || connection?.project?.id !== result.project?.id) expanded.clear();
            connection = result;
            if (host === 'wwise') localStorage.setItem('soundbot-waapi', $('waapiUrl').value.trim());
            deliveredFile = null;
            emptySession();
            renderTree();
        } finally {
            busy = false;
            renderHostLabels();
            dirtyState(dirty);
        }
        if (pendingClip && connection.sources.length === 1) await choose(connection.sources[0], true);
    }

    async function disconnect(nextHost = host) {
        if (busy || (dirty && !await confirm(t('关闭工程会丢弃当前中间件会话。继续？')))) {
            renderHostLabels();
            return;
        }
        busy = true;
        dirtyState(dirty);
        renderHostLabels();
        try {
            if (connection) await desktop.desktopMedia('disconnect');
            connection = null;
            expanded.clear();
            if (nextHost === host) pendingClip = null;
            deliveredFile = null;
            host = nextHost;
            $('wwiseFilter').value = '';
            if (state.workspace === 'wwise') $('searchInput').value = '';
            localStorage.setItem('soundbot-middleware-host', host);
            emptySession();
            renderTree();
        } finally {
            busy = false;
            renderHostLabels();
            dirtyState(dirty);
        }
    }

    async function choose(item, force = false) {
        if (busy || (!force && source?.id === item.id)) return;
        if (!force && dirty && !await confirm(t('切换音频源会丢弃当前编辑。继续？'))) return;
        ui.closeDockEditor();
        pause(true);
        busy = true;
        renderHostLabels();
        try {
            const selected = await desktop.desktopMedia('select', { id: item.id });
            target = selected;
            source = item;
            const mainClip = { id: crypto.randomUUID(), name: selected.name, middleware_token: selected.token,
                duration: selected.duration, maxDuration: selected.duration, offset: 0, start: 0,
                gain: 0, fade_in: 0, fade_out: 0 };
            tracks = [makeTrack({ label: '主源', clips: [mainClip] })];
            zoom = 1;
            zoomReference = timelineLength();
            autoCrossfade = true;
            crossfadeCurve = 'equal_power';
            $('dawTracks').scrollLeft = 0;
            selectedTrack = tracks[0].id;
            selectedClip = mainClip.id;
            dirtyState(false);
            renderTree();
            render();
            if (pendingClip) {
                addClip(pendingClip.clip, pendingClip.item, true, 0);
                pendingClip = null;
            }
            const token = target.token;
            api.get(`/api/v1/middleware/selection/${encodeURIComponent(token)}/waveform`).then(data => {
                if (target?.token !== token) return;
                mainClip.peaks = data.peaks;
                render();
            }).catch(() => {});
        } finally {
            busy = false;
            renderHostLabels();
            dirtyState(dirty);
        }
    }

    const curves = {
        linear: ['线性', x => x],
        equal_power: ['等功率', x => Math.sin(x * Math.PI / 2)],
        ease_in: ['缓起', x => x * x],
        ease_out: ['缓收', x => x * (2 - x)],
        smooth: ['S 曲线', x => x * x * (3 - 2 * x)]
    };

    function curveLine(fn) {
        return Array.from({ length: 25 }, (_, i) => {
            const x = i / 24;
            return `${i ? 'L' : 'M'}${x * 100} ${(1 - fn(x)) * 40}`;
        }).join(' ');
    }

    function curveGraphic(className, paths) {
        const graph = svg('svg', { viewBox: '0 0 100 40', preserveAspectRatio: 'none',
            class: className, 'aria-hidden': 'true' });
        graph.append(...paths.map(d => svg('path', { d, 'vector-effect': 'non-scaling-stroke' })));
        return graph;
    }

    function panLine(points) {
        if (!points.length) return 'M0 20 L100 20';
        const y = pan => (1 + pan) * 20;
        return `M0 ${y(points[0][1])} ${points.map(([x, value]) => `L${x * 100} ${y(value)}`).join(' ')} L100 ${y(points.at(-1)[1])}`;
    }

    function closeClipMenu() {
        const menu = $('clipMenu');
        if (!menu.hidden) menu.hidden = true;
    }

    function showClipMenu(event, anchor, label, options) {
        event.preventDefault();
        event.stopPropagation();
        const menu = $('clipMenu');
        menu.setAttribute('aria-label', t(label));
        const controls = [];
        for (const section of options) {
            if (section.label) controls.push(el('div', t(section.label), 'clip-menu-label'));
            for (const item of section.items) {
                const action = button(t(item.label), () => {
                    closeClipMenu();
                    item.run();
                }, 'eq-menu-item');
                action.setAttribute('role', item.checked == null ? 'menuitem' : 'menuitemradio');
                if (item.checked != null) action.setAttribute('aria-checked', String(item.checked));
                if (item.danger) action.classList.add('clip-menu-danger');
                action.disabled = Boolean(item.disabled);
                controls.push(action);
            }
        }
        menu.replaceChildren(...controls);
        menu.hidden = false;
        const bounds = anchor.getBoundingClientRect();
        menu.style.left = `${Math.max(8, Math.min(event.clientX || bounds.left, innerWidth - menu.offsetWidth - 8))}px`;
        menu.style.top = `${Math.max(8, Math.min(event.clientY || bounds.bottom, innerHeight - menu.offsetHeight - 8))}px`;
        const items = controls.filter(node => node.tagName === 'BUTTON' && !node.disabled);
        items[0]?.focus({ preventScroll: true });
        menu.onkeydown = key => {
            if (key.key === 'Escape' || key.key === 'Tab') {
                closeClipMenu();
                anchor.focus({ preventScroll: true });
                if (key.key === 'Escape') key.preventDefault();
            } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(key.key) && items.length) {
                key.preventDefault();
                const index = items.indexOf(document.activeElement);
                const next = key.key === 'Home' ? 0 : key.key === 'End' ? items.length - 1 :
                    (index + (key.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length;
                items[next].focus({ preventScroll: true });
            }
        };
    }

    function editClip(action) {
        pause();
        action();
        dirtyState(true);
        render();
    }

    function curveOptions(selected, apply) {
        return Object.entries(curves).map(([value, [label]]) => ({
            label, checked: value === selected, run: () => editClip(() => apply(value))
        }));
    }

    function fadeMenu(event, anchor, clip, field) {
        const key = `${field}_curve`;
        showClipMenu(event, anchor, field === 'fade_in' ? '淡入曲线' : '淡出曲线', [
            { items: curveOptions(clip[key] || 'linear', value => {
                clip[key] = value;
                clip[field] ||= Math.min(0.15, clip.duration / 3);
            }) },
            { items: [{ label: '清除淡化', disabled: !clip[field], run: () => editClip(() => { clip[field] = 0; }) }] }
        ]);
    }

    function clipMenu(event, node, clip, track) {
        if (busy) return;
        selectedTrack = track.id;
        selectedClip = clip.id;
        renderControls();
        showClipMenu(event, node, '剪辑操作', [
            { items: [
                { label: '声像曲线', checked: panClip === clip.id, disabled: target?.channels !== 2, run: () => {
                    panClip = panClip === clip.id ? null : clip.id;
                    render();
                } },
                { label: '清除声像曲线', disabled: !clip.pan?.length, run: () => editClip(() => { clip.pan = []; }) }
            ] },
            { label: '淡入曲线', items: curveOptions(clip.fade_in_curve || 'linear', value => {
                clip.fade_in_curve = value;
                clip.fade_in ||= Math.min(0.15, clip.duration / 3);
            }) },
            { label: '淡出曲线', items: curveOptions(clip.fade_out_curve || 'linear', value => {
                clip.fade_out_curve = value;
                clip.fade_out ||= Math.min(0.15, clip.duration / 3);
            }) },
            { items: [
                { label: '清除淡化', disabled: !(clip.fade_in || clip.fade_out),
                    run: () => editClip(() => { clip.fade_in = clip.fade_out = 0; }) },
                { label: '移除剪辑', danger: true, run: removeClip }
            ] }
        ]);
    }

    function clipPanEditor(clip, node) {
        const editable = panClip === clip.id && target?.channels === 2;
        const graph = el('div', null, `clip-pan-layer${editable ? ' editing' : ''}`);
        const curves = curveGraphic('clip-pan', ['M0 20H100', panLine(clip.pan || [])]);
        curves.firstChild.classList.add('clip-pan-center');
        graph.append(curves);
        if (!editable) return graph;
        const markers = el('div', null, 'clip-pan-markers');
        graph.append(markers, el('span', 'L', 'clip-pan-left'), el('span', 'R', 'clip-pan-right'));
        graph.title = t('在波形上添加或拖动声像点');
        graph.setAttribute('role', 'group');
        graph.setAttribute('aria-label', t('声像曲线'));
        let gesture;
        const paint = () => {
            const points = clip.pan || [];
            curves.lastChild.setAttribute('d', panLine(points));
            while (markers.children.length > points.length) markers.lastChild.remove();
            for (let index = markers.children.length; index < points.length; index++) {
                const marker = button('', () => {}, 'clip-pan-node');
                marker.dataset.panIndex = index;
                marker.setAttribute('aria-label', t('声像点 {count}', { count: index + 1 }));
                markers.append(marker);
            }
            points.forEach(([x, y], index) => {
                const marker = markers.children[index];
                marker.style.left = `${x * 100}%`;
                marker.style.top = `${(y + 1) * 50}%`;
                marker.title = y === 0 ? 'C' : `${y < 0 ? 'L' : 'R'} ${Math.round(Math.abs(y) * 100)}%`;
            });
        };
        const pointer = event => {
            const box = markers.getBoundingClientRect();
            return [limit((event.clientX - box.left) / Math.max(box.width, 1), 0, 1),
                limit((event.clientY - box.top) / Math.max(box.height, 1) * 2 - 1, -1, 1)];
        };
        const move = (index, x, y) => {
            const points = clip.pan;
            const low = index ? points[index - 1][0] + 0.001 : 0;
            const high = index + 1 < points.length ? points[index + 1][0] - 0.001 : 1;
            points[index] = [limit(x, low, high), Math.abs(y) < 0.035 ? 0 : limit(y, -1, 1)];
            paint();
        };
        graph.onpointerdown = event => {
            event.stopPropagation();
            if (event.button !== 0 || busy || gesture) return;
            event.preventDefault();
            const before = (clip.pan || []).map(point => [...point]);
            const [x, y] = pointer(event);
            const marker = event.target.closest('.clip-pan-node');
            marker?.focus({ preventScroll: true });
            let index = marker ? Number(marker.dataset.panIndex) : before.findIndex(point => Math.abs(point[0] - x) < 0.002);
            if (index < 0) {
                if (before.length >= 32) return;
                clip.pan = [...before, [x, y]].sort((a, b) => a[0] - b[0]);
                index = clip.pan.findIndex(point => point[0] === x);
            }
            gesture = { index, before, id: event.pointerId, changed: !marker && clip.pan.length !== before.length };
            pause();
            editingClip = true;
            node.classList.add('editing-envelope');
            graph.setPointerCapture(event.pointerId);
            paint();
        };
        graph.onpointermove = event => {
            if (event.pointerId !== gesture?.id) return;
            gesture.changed = true;
            move(gesture.index, ...pointer(event));
        };
        const finish = cancelled => {
            if (!gesture) return;
            if (cancelled) clip.pan = gesture.before;
            else if (gesture.changed) dirtyState(true);
            gesture = null;
            editingClip = false;
            node.classList.remove('editing-envelope');
            paint();
        };
        graph.onpointerup = () => finish(false);
        graph.onpointercancel = graph.onlostpointercapture = () => finish(true);
        graph.oncontextmenu = event => {
            const marker = event.target.closest('.clip-pan-node');
            if (!marker) return;
            showClipMenu(event, marker, '声像曲线', [
                { items: [{ label: '移除点', run: () => editClip(() => clip.pan.splice(Number(marker.dataset.panIndex), 1)) }] }
            ]);
        };
        graph.onkeydown = event => {
            const marker = event.target.closest('.clip-pan-node');
            if (!marker) return;
            const index = Number(marker.dataset.panIndex);
            const [x, y] = clip.pan[index];
            const deltas = { ArrowLeft: [-0.01, 0], ArrowRight: [0.01, 0], ArrowUp: [0, -0.05], ArrowDown: [0, 0.05] };
            const delta = deltas[event.key];
            if (!delta && !['Delete', 'Backspace'].includes(event.key)) return;
            event.stopPropagation();
            event.preventDefault();
            pause();
            if (delta) move(index, x + delta[0], y + delta[1]);
            else {
                clip.pan.splice(index, 1);
                paint();
                (markers.children[Math.min(index, markers.children.length - 1)] || node).focus();
            }
            dirtyState(true);
        };
        paint();
        return graph;
    }

    function clipEditor(clip, track, lane, length) {
        const node = el('div', null, `daw-clip ${selectedClip === clip.id ? 'sel' : ''}`);
        node.tabIndex = 0;
        node.dataset.clipId = clip.id;
        node.setAttribute('aria-label', clip.name);
        node.setAttribute('aria-haspopup', 'menu');
        node.onfocus = () => {
            if (busy || selectedClip === clip.id) return;
            selectedTrack = track.id;
            selectedClip = clip.id;
            renderControls();
        };
        node.setAttribute('aria-selected', String(selectedClip === clip.id));
        node.style.left = `${clip.start / length * 100}%`;
        node.style.width = `${Math.max(0.25, clip.duration / length * 100)}%`;
        const caption = el('div', null, 'clip-caption');
        const title = el('span', clip.name, 'clip-name');
        const gain = el('input', null, 'clip-gain');
        Object.assign(gain, { type: 'number', min: -96, max: 24, step: 1, value: clip.gain });
        gain.title = t('剪辑增益 (dB)');
        gain.setAttribute('aria-label', gain.title);
        gain.onchange = () => {
            const value = limit(Number(gain.value) || 0, -96, 24);
            gain.value = value;
            pause();
            clip.gain = value;
            dirtyState(true);
        };
        caption.append(title, gain);
        node.append(caption);
        node.oncontextmenu = event => {
            if (!event.target.closest('input')) clipMenu(event, node, clip, track);
        };
        node.onkeydown = event => {
            if (event.target !== node || !(event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10'))) return;
            clipMenu(event, node, clip, track);
        };
        let paint = () => {};
        if (clip.peaks) {
            const canvas = el('canvas');
            canvas.style.pointerEvents = 'none';
            node.append(canvas);
            paint = () => {
                if (!canvas.isConnected) return;
                const from = Math.floor(clip.offset / clip.maxDuration * clip.peaks.length);
                const to = Math.ceil((clip.offset + clip.duration) / clip.maxDuration * clip.peaks.length);
                waveform.drawMini(canvas, { waveformData: clip.peaks.slice(from, to), inactiveColor: getComputedStyle(node).getPropertyValue('--tx2').trim() });
            };
            requestAnimationFrame(paint);
        }
        const startHandle = el('span', null, 'clip-trim left');
        const endHandle = el('span', null, 'clip-trim right');
        const incoming = curveLine(curves[clip.fade_in_curve || 'linear'][1]);
        const outgoing = curveLine(x => curves[clip.fade_out_curve || 'linear'][1](1 - x));
        const fadeIn = curveGraphic('clip-fade in', [incoming + ' L100 0 L0 0 Z', incoming]);
        const fadeOut = curveGraphic('clip-fade out', [outgoing + ' L100 0 L0 0 Z', outgoing]);
        const fadeInHandle = button('', () => {}, 'clip-fade-handle in');
        const fadeOutHandle = button('', () => {}, 'clip-fade-handle out');
        for (const [handle, field, label] of [
            [fadeInHandle, 'fade_in', '淡入曲线'], [fadeOutHandle, 'fade_out', '淡出曲线']
        ]) {
            handle.setAttribute('aria-label', t(label));
            handle.oncontextmenu = event => fadeMenu(event, handle, clip, field);
            handle.onkeydown = event => {
                if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) fadeMenu(event, handle, clip, field);
                if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
                event.preventDefault();
                const step = (event.key === 'ArrowRight' ? 1 : -1) * (field === 'fade_in' ? 1 : -1);
                pause();
                clip[field] = limit(clip[field] + step * (event.shiftKey ? 0.1 : 0.01), 0, clip.duration);
                setFades();
                dirtyState(true);
            };
        }
        startHandle.title = t('裁剪起点');
        endHandle.title = t('裁剪终点');
        fadeInHandle.title = t('淡入 (秒)');
        fadeOutHandle.title = t('淡出 (秒)');
        if (clip.pan?.length || panClip === clip.id) node.append(clipPanEditor(clip, node));
        node.append(startHandle, endHandle, fadeIn, fadeOut, fadeInHandle, fadeOutHandle);
        const setFades = () => {
            const incoming = clip.fade_in / clip.duration * 100;
            const outgoing = clip.fade_out / clip.duration * 100;
            fadeIn.style.width = `${incoming}%`;
            fadeOut.style.width = `${outgoing}%`;
            fadeInHandle.style.setProperty('--fade-edge', `${incoming}%`);
            node.classList.toggle('overlap-handles', incoming + outgoing >= 93);
            fadeOutHandle.style.setProperty('--fade-edge', `${outgoing}%`);
            fadeInHandle.title = `${t('淡入 (秒)')} · ${number(clip.fade_in, { maximumFractionDigits: 3 })}`;
            fadeOutHandle.title = `${t('淡出 (秒)')} · ${number(clip.fade_out, { maximumFractionDigits: 3 })}`;
        };
        setFades();
        let gesture;
        let hoveredLane;
        node.onpointerdown = event => {
            if (event.button !== 0 || busy || event.target.closest('input')) return;
            event.stopPropagation();
            selectedTrack = track.id;
            selectedClip = clip.id;
            renderControls();
            gesture = { x: event.clientX, kind: event.target, start: clip.start,
                offset: clip.offset, duration: clip.duration, fade_in: clip.fade_in, fade_out: clip.fade_out,
                left: lane.getBoundingClientRect().left };
            editingClip = true;
            node.setPointerCapture(event.pointerId);
        };
        node.onpointermove = event => {
            if (!gesture) return;
            const pixels = event.clientX - gesture.x;
            if (Math.abs(pixels) < 2 && !gesture.moved) return;
            if (!gesture.moved) { gesture.moved = true; pause(); }
            const delta = (pixels + gesture.left - lane.getBoundingClientRect().left) / lane.clientWidth * length;
            if (gesture.kind === startHandle) {
                const adjusted = limit(startAt(gesture.start + delta) - gesture.start,
                    -Math.min(gesture.start, gesture.offset), gesture.duration - minClip);
                clip.start = gesture.start + adjusted;
                clip.offset = gesture.offset + adjusted;
                clip.duration = gesture.duration - adjusted;
            } else if (gesture.kind === endHandle) {
                clip.duration = limit(startAt(gesture.start + gesture.duration + delta) - gesture.start,
                    minClip, clip.maxDuration - clip.offset);
            } else if (gesture.kind === fadeInHandle) {
                clip.fade_in = limit(gesture.fade_in + delta, 0, clip.duration);
            } else if (gesture.kind === fadeOutHandle) {
                clip.fade_out = limit(gesture.fade_out - delta, 0, clip.duration);
            } else {
                clip.start = Math.min(86400 - clip.duration, startAt(gesture.start + delta));
                const next = document.elementFromPoint(event.clientX, event.clientY)?.closest('.track-lane');
                if (hoveredLane !== next) {
                    hoveredLane?.classList.remove('drop-target');
                    hoveredLane = next;
                    if (hoveredLane !== lane) hoveredLane?.classList.add('drop-target');
                }
            }
            clip.fade_in = Math.min(clip.duration, clip.fade_in);
            clip.fade_out = Math.min(clip.duration, clip.fade_out);
            node.style.left = `${clip.start / length * 100}%`;
            node.style.width = `${clip.duration / length * 100}%`;
            setFades();
            if (gesture.kind === startHandle || gesture.kind === endHandle) paint();
            drawCrossfades(lane, track, length);
        };
        const finish = cancelled => {
            if (!gesture) return;
            if (cancelled) {
                for (const key of ['start', 'offset', 'duration', 'fade_in', 'fade_out']) clip[key] = gesture[key];
            } else if (gesture.moved) {
                const destination = tracks.find(item => item.id === hoveredLane?.dataset.trackId);
                if (destination && destination !== track) {
                    track.clips = track.clips.filter(item => item !== clip);
                    destination.clips.push(clip);
                    selectedTrack = destination.id;
                }
                dirtyState(true);
            }
            hoveredLane?.classList.remove('drop-target');
            gesture = null;
            editingClip = false;
            render();
        };
        node.onpointerup = () => finish(false);
        node.onpointercancel = node.onlostpointercapture = () => finish(true);
        return node;
    }

    const overlapKey = (left, right) => `${left.id}/${right.id}`;

    function trackOverlaps(track) {
        const rate = target?.sample_rate || 48000;
        const ordered = [...track.clips].sort((a, b) => Math.round(a.start * rate) - Math.round(b.start * rate));
        const overlaps = [];
        for (let index = 1; index < ordered.length; index++) {
            const left = ordered[index - 1], right = ordered[index];
            const end = left.start + left.duration;
            const a = Math.round(left.start * rate), b = Math.round(right.start * rate);
            const aEnd = a + Math.round(left.duration * rate), bEnd = b + Math.round(right.duration * rate);
            if (a < b && b < aEnd && aEnd < bEnd) overlaps.push({ left, right, end });
        }
        return overlaps;
    }

    function drawCrossfades(lane, track, length) {
        lane.querySelectorAll('.clip-crossfade').forEach(marker => marker.remove());
        if (!autoCrossfade) return;
        for (const { left, right, end } of trackOverlaps(track)) {
            const key = overlapKey(left, right);
            const override = track.crossfades.get(key);
            const curve = override || crossfadeCurve;
            const region = el('div', null, `clip-crossfade${curve === 'off' ? ' off' : ''}`);
            region.style.left = `${right.start / length * 100}%`;
            region.style.width = `${(end - right.start) / length * 100}%`;
            if (curve !== 'off') {
                const shape = curves[curve][1];
                const outgoing = curve === 'equal_power' ? x => shape(1 - x) : x => 1 - shape(x);
                region.append(curveGraphic('crossfade-paths', [curveLine(shape), curveLine(outgoing)]));
            }
            const open = event => {
                if (busy) return;
                showClipMenu(event, handle, '交叉淡化曲线', [
                    { items: [
                        { label: '跟随默认曲线', checked: !override, run: () => editClip(() => track.crossfades.delete(key)) },
                        ...curveOptions(override, value => track.crossfades.set(key, value)),
                        { label: '关闭此处交叉淡化', checked: curve === 'off',
                            run: () => editClip(() => track.crossfades.set(key, 'off')) }
                    ] }
                ]);
            };
            const handle = button('×', () => {}, 'crossfade-menu-handle');
            handle.title = t('交叉淡化曲线');
            handle.setAttribute('aria-label', handle.title);
            handle.setAttribute('aria-haspopup', 'menu');
            handle.onclick = open;
            region.oncontextmenu = open;
            region.append(handle);
            lane.append(region);
        }
    }

    function syncRulerScroll() {
        $('dawRuler').style.transform = `translateX(${-$('dawTracks').scrollLeft}px)`;
    }

    function layoutTimeline() {
        const tracksBox = $('dawTracks');
        const viewport = $('dawRulerViewport');
        const visibleWidth = viewport.clientWidth - (tracksBox.offsetWidth - tracksBox.clientWidth);
        if (visibleWidth < 1) return;
        const width = `${Math.ceil(visibleWidth * Math.max(1, zoom * timelineLength() / zoomReference))}px`;
        tracksBox.style.setProperty('--lane-width', width);
        $('dawRuler').style.setProperty('--lane-width', width);
        syncRulerScroll();
    }

    function changeZoom(factor) {
        const ruler = $('dawRuler');
        const box = $('dawTracks');
        const visible = $('dawRulerViewport').clientWidth;
        const position = playback.active && !state.dockEditing ? playback.currentTime() : state.position;
        const playheadPixel = position / timelineLength() * ruler.clientWidth - box.scrollLeft;
        const anchor = playheadPixel >= 0 && playheadPixel <= visible ? playheadPixel : visible / 2;
        const fraction = (box.scrollLeft + anchor) / (ruler.clientWidth || 1);
        if (factor === 1) zoomReference = timelineLength();
        zoom = factor === 1 ? 1 : limit(zoom * factor, 1, 16);
        layoutTimeline();
        box.scrollLeft = Math.max(0, fraction * ruler.clientWidth - anchor);
        syncRulerScroll();
        $('timelineZoomReset').textContent = `${Math.round(zoom * 100)}%`;
    }

    function render() {
        if (editingClip) return;
        closeClipMenu();
        $('wwiseObjName').textContent = source?.name || t('选择工程音频源');
        $('wwiseObjPath').textContent = source?.path || '';
        $('dawTrackCount').textContent = t('轨道：{count}', { count: tracks.length });
        $('dawDuration').textContent = fmt(duration());
        $('btnAddTrack').disabled = !target || busy;
        $('timelineCrossfade').setAttribute('aria-pressed', String(autoCrossfade));
        $('crossfadeCurve').value = crossfadeCurve;
        $('crossfadeCurve').disabled = !autoCrossfade || busy;
        $('timelineZoomReset').textContent = `${Math.round(zoom * 100)}%`;
        const ruler = $('dawRuler');
        ruler.replaceChildren();
        const length = timelineLength();
        const markEvery = Math.max(0.125, 2 ** Math.ceil(Math.log2(zoomReference / zoom / 12)), length / 240);
        for (let time = 0; time < length; time += markEvery) {
            const marker = el('span', fmt(time).slice(0, markEvery < 1 ? 8 : 5), 'grid-line major');
            marker.style.left = `${time / length * 100}%`;
            ruler.append(marker);
        }
        ruler.append(el('span', null, 'ruler-playhead'));
        $('dawTracks').replaceChildren(...tracks.map(track => {
            const row = el('div', null, 'daw-track');
            row.classList.toggle('sel', track.id === selectedTrack);
            const head = el('div', null, 'track-head');
            const title = el('input', null, 'track-name');
            title.value = trackName(track);
            title.setAttribute('aria-label', t('轨道名称'));
            title.onchange = () => {
                const next = title.value.trim();
                if (next && next !== trackName(track)) {
                    track.name = next;
                    dirtyState(true);
                    renderControls();
                }
                title.value = trackName(track);
            };
            const controls = el('div', null, 'track-controls');
            for (const [key, label] of [['mute', 'M'], ['solo', 'S']]) {
                const action = button(label, () => { pause(); track[key] = !track[key]; dirtyState(true); render(); }, 'track-btn');
                action.title = t(key === 'mute' ? '静音' : '独奏');
                action.setAttribute('aria-label', action.title);
                action.setAttribute('aria-pressed', String(track[key]));
                action.classList.toggle('on', track[key]);
                controls.append(action);
            }
            const gain = el('input', null, 'track-vol');
            gain.type = 'range'; gain.min = '-48'; gain.max = '12'; gain.value = track.gain;
            gain.title = `${track.gain} dB`;
            gain.setAttribute('aria-label', t('轨道增益'));
            gain.oninput = () => { pause(); track.gain = Number(gain.value); gain.title = `${track.gain} dB`; dirtyState(true); };
            const channels = el('select', null, 'track-channel');
            for (const [value, label] of [['', t('跟随目标声道')], ['1', t('单声道')], ['2', t('立体声')]]) {
                const option = el('option', label); option.value = value; channels.append(option);
            }
            channels.value = track.channels || '';
            channels.setAttribute('aria-label', t('轨道声道'));
            channels.onchange = () => { pause(); track.channels = Number(channels.value) || null; dirtyState(true); };
            const editEffects = button('FX', () => {
                selectedTrack = track.id;
                selectedClip = null;
                renderControls();
                $('tabVst').click();
            }, 'track-btn');
            editEffects.title = t('编辑轨道效果器');
            editEffects.setAttribute('aria-label', editEffects.title);
            editEffects.classList.toggle('on', fx.enabled(track.effects).length > 0);
            const removeTrack = button('×', () => {
                pause();
                tracks = tracks.filter(item => item !== track);
                selectedTrack = tracks[0]?.id;
                selectedClip = null;
                dirtyState(true);
                render();
            }, 'track-btn track-remove');
            removeTrack.title = t('移除轨道');
            removeTrack.setAttribute('aria-label', removeTrack.title);
            controls.append(gain, editEffects, removeTrack);
            head.append(title, controls, channels);
            head.onclick = event => {
                if (event.target.closest('button,input,select')) return;
                selectedTrack = track.id;
                selectedClip = null;
                renderControls();
            };
            const lane = el('div', null, 'track-lane');
            lane.dataset.trackId = track.id;
            if (snap) lane.style.backgroundSize = `${grid / length * 100}% 100%`;
            const getTime = event => limit((event.clientX - lane.getBoundingClientRect().left) / lane.clientWidth * length, 0, length);
            lane.onclick = event => {
                if (event.target !== lane) return;
                ui.closeDockEditor();
                pause();
                state.position = startAt(getTime(event));
                selectedTrack = track.id;
                selectedClip = null;
                renderControls();
                syncTransport();
            };
            lane.ondragover = event => {
                if (target && !busy && event.dataTransfer.types.includes(clipDragType)) {
                    event.preventDefault();
                    lane.classList.add('drop-target');
                }
            };
            lane.ondragleave = () => lane.classList.remove('drop-target');
            lane.ondrop = event => {
                lane.classList.remove('drop-target');
                const id = event.dataTransfer.getData(clipDragType);
                if (!target || busy || !id) return;
                const edit = ui.libraryDrag?.id === id ? wholeAsset(ui.libraryDrag)
                    : state.dockEditing && state.selected?.id === id ? ui.editorClip()
                    : dockDrag?.id === id ? wholeAsset(dockDrag) : null;
                if (!edit) return;
                event.preventDefault();
                const position = getTime(event);
                ui.closeDockEditor();
                selectedTrack = track.id;
                attempt(() => addClip(edit.clip, edit.item, false, position));
            };
            lane.append(...track.clips.map(clip => clipEditor(clip, track, lane, length)));
            drawCrossfades(lane, track, length);
            const handle = el('span', null, 'lane-playhead');
            handle.setAttribute('aria-hidden', 'true');
            handle.title = t('拖动播放头');
            lane.append(handle);
            row.append(head, lane);
            return row;
        }));
        layoutTimeline();
        renderControls();
        syncTransport();
    }

    function renderControls() {
        const track = active();
        $('miniTarget').textContent = track ? `${t('当前轨道')} · ${trackName(track)}` : t('选择音频源后添加轨道');
        $('inspTrackName').textContent = track ? trackName(track) : '—';
        $('btnMiniInsert').disabled = !target || !dockSelection?.duration || dockSelection.missing || !track || busy;
        $('btnMiniSend').disabled = !target || !dockSelection?.duration || dockSelection.missing || busy;
        document.querySelectorAll('.daw-track').forEach((row, index) => row.classList.toggle('sel', tracks[index]?.id === selectedTrack));
        document.querySelectorAll('.daw-clip').forEach(node => {
            const active = node.dataset.clipId === selectedClip;
            node.classList.toggle('sel', active);
            node.setAttribute('aria-selected', String(active));
        });
        if (state.workspace === 'wwise') {
            $('inspName').textContent = source?.name || t('选择工程音频源');
            $('inspMeta').textContent = target ? `${number(target.sample_rate)} Hz · ${t('声道数：{count}', { count: target.channels })} · ${target.bits} bit` : '';
            $('inspWwisePath').textContent = source?.path || '—';
            $('wwiseCurrentSource').textContent = source?.name || '—';
            const label = t('轨道效果器');
            fx.useRack(track?.effects, track ? `${label} · ${trackName(track)}` : label, () => {
                if (!state.dockEditing) ui.refreshAudition();
                dirtyState(true);
            }, target?.sample_rate || 48000);
        }
        const clip = track?.clips.find(item => item.id === selectedClip);
        $('btnShowLibrary').disabled = !clip?.file_id;
        const panButton = $('timelinePan');
        panButton.disabled = !clip || target?.channels !== 2 || busy;
        panButton.setAttribute('aria-pressed', String(Boolean(clip && panClip === clip.id)));
        panButton.title = t(target?.channels === 2 ? '在波形上添加或拖动声像点' : '声像曲线需要立体声目标');
    }

    function removeClip() {
        const track = active();
        if (!track || !selectedClip || busy) return;
        pause();
        track.clips = track.clips.filter(clip => clip.id !== selectedClip);
        if (panClip === selectedClip) panClip = null;
        selectedClip = null;
        dirtyState(true);
        render();
    }

    function addClip(clip, item, newTrack, position = 0) {
        if (!target) throw new Error('请先选择工程音频源');
        if (countClips() >= 128) throw new Error('会话最多支持 128 个剪辑');
        let track = active();
        if (newTrack || !track) {
            if (tracks.length >= 32) throw new Error('会话最多支持 32 条轨道');
            track = makeTrack({ name: item.filename });
            tracks.push(track);
            selectedTrack = track.id;
        }
        pause();
        const next = { ...clip, id: crypto.randomUUID(), name: item.filename,
            maxDuration: item.duration, start: startAt(position), peaks: item.peaks };
        track.clips.push(next);
        selectedClip = next.id;
        dirtyState(true);
        render();
        if (next.file_id && !next.peaks) {
            const params = new URLSearchParams({ project_id: state.library.id });
            api.get(`/api/v1/files/${encodeURIComponent(next.file_id)}/waveform?${params}`).then(data => {
                if (!tracks.some(current => current.clips.includes(next))) return;
                next.peaks = data.peaks;
                render();
            }).catch(() => {});
        }
    }

    function stageClip(clip, item) {
        if (target) return addClip(clip, item, true);
        pendingClip = { clip, item };
        say(t('请选择工程音频源以添加选区'));
    }

    function wholeAsset(item) {
        if (!item?.duration || item.missing) return null;
        return {
            item: { ...item, peaks: ui.cachedPeaks(item) },
            clip: { file_id: item.id, offset: 0, duration: item.duration, gain: 0, fade_in: 0, fade_out: 0 }
        };
    }

    function stageWholeAsset(item) {
        const edit = wholeAsset(item);
        if (edit) stageClip(edit.clip, edit.item);
    }

    function insertDock(newTrack, position = state.position) {
        const edit = wholeAsset(dockSelection);
        if (edit) addClip(edit.clip, edit.item, newTrack, position);
    }

    function renderDock() {
        const nodes = dockItems.map(item => {
            const node = el('div', null, 'mini-row');
            node.classList.toggle('sel', dockSelection?.id === item.id);
            node.title = item.path;
            const info = button(null, () => editDockItem(item), 'mini-name');
            const title = el('div', null, 'mini-card-title');
            title.append(ui.assetCover(item), el('span', item.title || item.filename));
            info.append(title, ui.miniCanvas(item), el('span', item.missing ? t('离线') : fmt(item.duration), 'mini-dur'));
            node.append(info);
            node.draggable = Boolean(item.duration && !item.missing);
            node.ondragstart = event => {
                if (!wholeAsset(item)) { event.preventDefault(); return; }
                dockDrag = item;
                event.dataTransfer.setData(clipDragType, item.id);
                event.dataTransfer.effectAllowed = 'copy';
            };
            node.ondragend = () => { dockDrag = null; };
            return node;
        });
        $('miniRows').replaceChildren(...(nodes.length ? nodes : [el('div', t('没有可用素材'), 'mini-empty')]));
        ui.scheduleMiniPeaks();
        renderControls();
    }

    async function editDockItem(item) {
        dockSelection = item;
        $('miniRows').querySelectorAll('.mini-row').forEach((row, index) => {
            row.classList.toggle('sel', dockItems[index].id === item.id);
        });
        renderControls();
        await ui.openDockEditor(item);
    }

    async function loadDock() {
        dockRequest?.abort();
        if (!state.library) {
            dockItems = [];
            dockSelection = null;
            renderDock();
            return;
        }
        if (state.workspace !== 'wwise') return;
        const request = dockRequest = new AbortController();
        const q = $('miniSearch').value;
        const library = state.library.id;
        const result = await api.get(route(`/catalog?${new URLSearchParams({ q, limit: 24 })}`), { signal: request.signal });
        if (request.signal.aborted || library !== state.library?.id || state.workspace !== 'wwise') return;
        dockItems = result.files;
        dockSelection = dockItems.find(item => item.id === dockSelection?.id);
        renderDock();
    }

    function mixRequest() {
        return {
            project_id: state.library?.id || 'default',
            rate: target.sample_rate,
            channels: target.channels,
            tracks: tracks.map(track => ({
                gain: track.gain, mute: track.mute, solo: track.solo, channels: track.channels,
                auto_crossfade: autoCrossfade,
                crossfade_curve: crossfadeCurve,
                crossfade_overrides: trackOverlaps(track).flatMap(({ left, right }) => {
                    const curve = track.crossfades.get(overlapKey(left, right));
                    return curve ? [[track.clips.indexOf(left), track.clips.indexOf(right), curve]] : [];
                }),
                effects: fx.enabled(track.effects),
                clips: track.clips.map(({ id, name, maxDuration, peaks, ...clip }) => clip)
            }))
        };
    }

    async function play() {
        if (!target || !duration() || busy) return say(t('请先选择音频源并添加剪辑'));
        const start = state.position < duration() ? state.position : 0;
        const token = ++previewGeneration;
        return playback.playPCM(api.url('/api/v1/preview'), {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...mixRequest(), start })
        }, start, () => {
            state.position = 0;
            if (state.loop && token === previewGeneration && state.workspace === 'wwise') attempt(play);
            syncTransport();
        });
    }

    async function writeback() {
        if (busy || !target || !dirty) return;
        const project = target.project.name;
        const expectedTarget = target;
        const replace = target.delivery === 'replace';
        const parameters = { project, name: source.name, rate: target.sample_rate, channels: target.channels, bits: target.bits };
        if (!await confirm(t(replace
            ? '将当前混音写回 {project} 中的「{name}」？规格 {rate} Hz、{channels} 声道、{bits} bit。原 WAV 会保留恢复备份。'
            : '将混音交付到 {project} 的素材目录？新 WAV 为 {rate} Hz、{channels} 声道、{bits} bit，原始素材将保留。', parameters))) return;
        if (busy || expectedTarget !== target) return;
        pause(true);
        const previous = source;
        const session = { ...mixRequest(), token: target.token };
        busy = true;
        dirtyState(true);
        renderHostLabels();
        renderLog(() => t('正在渲染并验证…'));
        try {
            const result = await desktop.desktopMedia('writeback', session);
            dirtyState(false);
            if (replace) {
                emptySession();
                renderLog(() => t('源 WAV 已更新 · Wwise 转换状态待确认'));
                say(t('已更新 Wwise 源 WAV'));
            } else {
                deliveredFile = result.path;
                renderLog(() => `${t('已交付 {name}', { name: result.name })} · ${t(host === 'fmod'
                    ? '在 FMOD Assets 中右键选择 Import。'
                    : '在 Atom Craft 的素材目录中注册未注册文件。')}`);
                say(t('已交付 {name}', { name: result.name }));
            }
        } catch (error) {
            renderLog(() => describeError(error));
            throw error;
        } finally {
            busy = false;
            renderHostLabels();
            dirtyState(dirty);
        }
        if (replace) await choose(previous, true);
    }

    function playhead(time) {
        const progress = limit(time / timelineLength(), 0, 1);
        document.querySelectorAll('.track-lane').forEach(lane => lane.style.setProperty('--play', String(progress)));
        const marker = $('dawRuler').querySelector('.ruler-playhead');
        if (marker) marker.style.left = `${progress * 100}%`;
    }

    function bind() {
        bindTree();
        const on = (id, fn) => { $(id).onclick = () => attempt(fn); };
        const viewport = $('dawRulerViewport');
        const box = $('dawTracks');
        box.addEventListener('scroll', syncRulerScroll, { passive: true });
        new ResizeObserver(layoutTimeline).observe(box);
        const resizeTimeline = factor => { changeZoom(factor); render(); };
        on('timelineZoomIn', () => resizeTimeline(1.5));
        on('timelineZoomOut', () => resizeTimeline(1 / 1.5));
        on('timelineZoomReset', () => resizeTimeline(1));
        for (const [key, [label]] of Object.entries(curves)) {
            const option = el('option');
            setText(option, () => t(label));
            option.value = key;
            $('crossfadeCurve').append(option);
        }
        $('crossfadeCurve').onchange = event => {
            if (busy) return;
            pause();
            crossfadeCurve = event.target.value;
            if (target) dirtyState(true);
            render();
        };
        on('timelinePan', () => {
            if (!selectedClip || target?.channels !== 2 || busy) return;
            panClip = panClip === selectedClip ? null : selectedClip;
            render();
        });
        document.addEventListener('pointerdown', event => {
            if (!event.target.closest('#clipMenu')) closeClipMenu();
        });
        box.addEventListener('scroll', closeClipMenu, { passive: true });
        window.addEventListener('resize', closeClipMenu);
        on('timelineCrossfade', () => {
            if (busy) return;
            pause();
            autoCrossfade = !autoCrossfade;
            if (target) dirtyState(true);
            render();
        });
        for (const area of [viewport, box]) area.addEventListener('wheel', event => {
            if (!event.ctrlKey && !event.metaKey) return;
            event.preventDefault();
            resizeTimeline(event.deltaY < 0 ? 1.25 : 0.8);
        }, { passive: false });
        let scrub;
        const seek = event => {
            const ruler = $('dawRuler').getBoundingClientRect();
            if (!ruler.width) return;
            state.position = limit((event.clientX - ruler.left) / ruler.width * timelineLength(), 0, duration());
            syncTransport();
        };
        for (const area of [viewport, box]) {
            area.onpointerdown = event => {
                if (event.button !== 0 || !target || busy || scrub ||
                    (area === box && !event.target.closest('.lane-playhead'))) return;
                event.preventDefault();
                ui.closeDockEditor();
                scrub = { id: event.pointerId, resume: playback.active };
                pause();
                area.setPointerCapture(event.pointerId);
                seek(event);
            };
            area.onpointermove = event => { if (scrub?.id === event.pointerId) seek(event); };
            area.onpointerup = event => {
                if (scrub?.id !== event.pointerId) return;
                seek(event);
                const resume = scrub.resume;
                scrub = null;
                if (resume && state.position < duration()) attempt(play);
            };
            area.onlostpointercapture = () => { scrub = null; };
        }
        viewport.onkeydown = event => {
            if (!target || busy || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            ui.closeDockEditor();
            const position = playback.active ? playback.currentTime() : state.position;
            pause();
            state.position = event.key === 'Home' ? 0 : event.key === 'End' ? duration()
                : limit(position + (event.key === 'ArrowLeft' ? -grid : grid), 0, duration());
            syncTransport();
        };
        $('waapiUrl').value = localStorage.getItem('soundbot-waapi') || $('waapiUrl').value;
        for (const id of ['middlewareHost', 'middlewareSettingsHost']) {
            $(id).onchange = event => {
                const next = event.target.value;
                if (next !== host && Object.hasOwn(hosts, next)) attempt(() => disconnect(next));
            };
        }
        renderHostLabels();
        renderLog();
        window.addEventListener('soundbot-language', renderHostLabels);
        on('btnConnectInline', () => connect());
        on('btnWwiseConnect', () => connection ? disconnect() : connect());
        on('btnChooseHostProject', () => connect(true));
        on('btnRevealMiddlewareFile', () => deliveredFile && desktop.revealFile(deliveredFile));
        on('btnExportWwise', writeback);
        on('btnShowLibrary', () => {
            const clip = active()?.clips.find(item => item.id === selectedClip);
            if (clip?.file_id) return ui.locateLibraryItem(clip.file_id);
        });
        on('btnAddTrack', () => {
            if (!target || busy) return;
            if (tracks.length >= 32) throw new Error('会话最多支持 32 条轨道');
            const track = makeTrack({ label: '轨道 {count}', number: tracks.length + 1 });
            tracks.push(track);
            selectedTrack = track.id;
            selectedClip = null;
            dirtyState(true);
            render();
        });
        on('btnMiniInsert', () => insertDock(false));
        on('btnMiniSend', () => insertDock(true));
        on('btnMiniExpand', async () => {
            await ui.switchWorkspace('library');
            if (dockSelection) await ui.select(dockSelection);
        });
        const toggleDock = () => {
            $('bottomStage').classList.toggle('dock-collapsed');
            ui.scheduleMiniPeaks();
        };
        on('btnMiniClose', toggleDock);
        on('btnMiniLibrary', toggleDock);
        on('btnMiniLibraryInline', () => {
            ui.closeDockEditor();
            $('bottomStage').classList.remove('dock-collapsed');
            ui.scheduleMiniPeaks();
        });
        $('wwiseFilter').oninput = event => {
            if (event.isComposing) return;
            if (state.workspace === 'wwise') $('searchInput').value = event.target.value;
            $('wwiseTree').scrollTop = 0;
            filteredExpansion.clear();
            renderTree();
        };
        let searchTimer;
        $('miniSearch').oninput = event => {
            clearTimeout(searchTimer);
            if (event.isComposing) return;
            searchTimer = setTimeout(() => attempt(loadDock), 160);
        };
        $('gridPrecision').onchange = () => { grid = Number($('gridPrecision').value); render(); };
        document.querySelectorAll('#snapMode button').forEach(node => {
            node.onclick = () => {
                snap = node.dataset.snap === 'snap';
                document.querySelectorAll('#snapMode button').forEach(item => {
                    item.classList.toggle('on', item === node);
                    item.setAttribute('aria-pressed', String(item === node));
                });
                render();
            };
        });
        document.addEventListener('keydown', event => {
            if (state.workspace === 'wwise' && !state.dockEditing && ['Delete', 'Backspace'].includes(event.key) &&
                event.target.closest('.daw-clip') && !event.target.closest('input,.clip-pan-node')) {
                event.preventDefault();
                removeClip();
            }
        });
    }

    window.SoundBotTimeline = {
        show: async () => { renderTree(); render(); await loadDock(); }, duration, renderTree, loadDock,
        hasLibraryClips, discard, stageClip, stageWholeAsset, play, playhead,
        isBusy: () => busy,
        refreshLabels: () => {
            renderHostLabels();
            renderTree();
            render();
            renderDock();
            dirtyState(dirty);
        },
        stepDock: direction => {
            const at = dockItems.findIndex(item => item.id === dockSelection?.id);
            const next = dockItems[at + direction];
            if (next) return editDockItem(next);
        },
        step: direction => {
            const items = connection?.sources || [];
            const at = items.findIndex(item => item.id === source?.id);
            const next = items[Math.max(0, Math.min(items.length - 1, at + direction))];
            if (next) return choose(next);
        },
        resetSelection: async () => {
            if (host !== 'wwise') return;
            const previous = source;
            emptySession();
            if (previous && connection) await choose(previous, true);
        }
    };
    document.addEventListener('DOMContentLoaded', () => { if (desktop) bind(); });
})();
