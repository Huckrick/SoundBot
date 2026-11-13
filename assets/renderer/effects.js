/* Inspector controls for the same effects used by preview and delivery. */
(() => {
    const ui = window.SoundBotWorkspace;
    const { $, state, api, desktop, el, button, say, attempt } = ui;
    const { t, setText } = window.SoundBotI18n;
    const equalizer = window.SoundBotEqualizer;
    const playback = window.SoundBotPlayback;
    const builtins = {
        normalize: { name: '响度标准化', type: '响度', controls: [
            ['target', '目标 LUFS', -24, -12, -16, 0.5], ['peak', '真峰值 dBTP', -6, 0, -1, 0.1]
        ] },
        eq: { name: '参数均衡器', type: '均衡' },
        compressor: { name: '压缩器', type: '动态', controls: [
            ['threshold', '阈值 dB', -48, 0, -24, 0.5], ['ratio', '压缩比', 1, 12, 4, 0.1]
        ] },
        reverb: { name: '混响', type: '混响', controls: [
            ['mix', '干湿比 %', 0, 100, 30, 1], ['size', '空间大小 %', 0, 100, 55, 1],
            ['damping', '高频阻尼 %', 0, 100, 45, 1], ['width', '立体声宽度 %', 0, 100, 100, 1]
        ] }
    };
    const defaults = kind => ({
        kind, on: false,
        values: Object.fromEntries((builtins[kind].controls || []).map(([key, , , , value]) => [key, value])),
        ...(kind === 'eq' && { bands: equalizer.defaults() })
    });
    const create = () => Object.keys(builtins).map(defaults);
    function resetEffect(effect) {
        if (builtins[effect.kind]) Object.assign(effect, defaults(effect.kind));
        else if (!editingPlugins.has(effect)) {
            delete effect.plugin_state;
            effect.on = true;
        }
    }
    const activeBand = band => band.enabled && (band.gain !== 0 || band.shape.endsWith('_cut'));
    const enabled = chain => (chain || []).filter(item => item.on && (!item.bands || item.bands.some(activeBand)))
        .map(({ kind, values, plugin_id, plugin_state, bands }) => ({
            kind, values: { ...values }, ...(plugin_id && { plugin_id }),
            ...(plugin_state && { plugin_state }),
            ...(bands && { bands: bands.filter(activeBand).map(band => ({ ...band })) })
        }));
    const editingPlugins = new WeakSet();
    let draggedPlugin;
    let current;
    let analysis;
    let peak = [0, 0];
    let paintSpectrum;
    let spectrumWasActive;
    const loudnessCache = new Map();
    const redraw = [];

    function draw(canvas, effect) {
        const width = canvas.clientWidth;
        const height = canvas.clientHeight;
        if (!width || !height) return;
        const scale = devicePixelRatio || 1;
        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(height * scale);
        const context = canvas.getContext('2d');
        const css = getComputedStyle(document.documentElement);
        context.setTransform(scale, 0, 0, scale, 0, 0);
        context.strokeStyle = css.getPropertyValue('--line2');
        context.beginPath();
        context.moveTo(0, height / 2);
        context.lineTo(width, height / 2);
        context.stroke();
        context.strokeStyle = css.getPropertyValue('--tx1');
        context.lineWidth = 1.5;
        context.beginPath();
        const v = effect.values;
        for (let x = 0; x <= width; x++) {
            const f = x / width;
            const y = effect.kind === 'compressor'
                    ? 1 - f + Math.max(0, f - (v.threshold + 48) / 48) * (1 - 1 / v.ratio)
                    : 0.5 - Math.sin(Math.PI * f) * (v.target + 24) / 40;
            if (x) context.lineTo(x, height * y);
            else context.moveTo(x, height * y);
        }
        context.stroke();
    }

    function knob({ label, min, max, step, read, write, scale = 'linear', digits = 1, editable = false }) {
        const wrapper = el('div', null, 'knob-wrap');
        const slider = el('div', null, 'knob');
        slider.setAttribute('role', 'slider');
        slider.setAttribute('aria-label', label);
        slider.setAttribute('aria-valuemin', min);
        slider.setAttribute('aria-valuemax', max);
        slider.innerHTML = '<svg width="46" height="46" viewBox="0 0 46 46" aria-hidden="true">' +
            '<circle cx="23" cy="23" r="19" transform="rotate(135 23 23)" fill="none" stroke="var(--line2)" stroke-width="3" stroke-dasharray="89.5 119.4" stroke-linecap="round"/>' +
            '<circle class="arc" cx="23" cy="23" r="19" transform="rotate(135 23 23)" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>' +
            '<circle cx="23" cy="23" r="12" fill="var(--bg3)"/></svg>';
        const arc = slider.querySelector('.arc');
        const value = el(editable ? 'input' : 'span', '', 'k-val');
        const toPosition = scale === 'log'
            ? now => Math.log(now / min) / Math.log(max / min)
            : now => (now - min) / (max - min);
        const fromPosition = scale === 'log'
            ? position => min * (max / min) ** position
            : position => min + (max - min) * position;
        let disabled;
        const update = (inactive = false) => {
            disabled = inactive;
            const now = disabled ? null : read();
            const display = now == null ? '—' : now.toFixed(digits);
            slider.tabIndex = disabled ? -1 : 0;
            slider.setAttribute('aria-disabled', String(disabled));
            if (now == null) slider.removeAttribute('aria-valuenow');
            else slider.setAttribute('aria-valuenow', now);
            arc.style.strokeDasharray = `${now == null ? 0 : toPosition(now) * 89.5} 119.4`;
            wrapper.classList.toggle('disabled', disabled);
            if (editable) {
                value.disabled = disabled;
                value.value = display;
            } else value.textContent = display;
        };
        const set = raw => {
            const rounded = Math.round(raw / step) * step;
            const next = Number(Math.min(max, Math.max(min, rounded)).toFixed(Math.max(digits, 2)));
            if (!disabled && Number.isFinite(next) && next !== read()) write(next);
        };
        slider.onpointerdown = event => {
            if (event.button !== 0 || disabled) return;
            event.preventDefault();
            slider.focus({ preventScroll: true });
            const { pointerId, clientY, shiftKey } = event;
            const initial = toPosition(read());
            const travel = (editable ? 220 : 120) * (shiftKey ? 5 : 1);
            const drag = new AbortController();
            const options = { signal: drag.signal };
            const finish = pointer => {
                if (pointer.pointerId === pointerId) drag.abort();
            };
            slider.setPointerCapture(pointerId);
            window.addEventListener('pointermove', pointer => {
                if (pointer.pointerId !== pointerId) return;
                if (!slider.isConnected || !(pointer.buttons & 1)) return drag.abort();
                const position = Math.min(1, Math.max(0, initial + (clientY - pointer.clientY) / travel));
                set(fromPosition(position));
            }, options);
            window.addEventListener('pointerup', finish, options);
            window.addEventListener('pointercancel', finish, options);
            window.addEventListener('blur', () => drag.abort(), options);
        };
        slider.onkeydown = event => {
            const direction = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1 }[event.key];
            if (!direction || disabled) return;
            event.preventDefault();
            const now = read();
            set(scale === 'log' ? now * (max / min) ** (direction * 0.01) : now + step * direction);
        };
        if (editable) {
            value.type = 'text';
            value.inputMode = 'decimal';
            value.setAttribute('aria-label', label);
            value.onfocus = () => value.select();
            value.onchange = () => {
                if (value.value.trim()) set(Number(value.value.replace(',', '.')));
                update(disabled);
            };
            value.onkeydown = event => {
                if (event.key === 'Escape') {
                    event.stopPropagation();
                    update(disabled);
                    value.blur();
                }
                if (event.key === 'Enter') value.blur();
            };
        }
        wrapper.append(slider, value, el('span', label, 'k-name'));
        return { root: wrapper, update };
    }

    async function editPlugin(effect, context) {
        if (editingPlugins.has(effect)) return;
        editingPlugins.add(effect);
        if (current?.chain === context.chain) render();
        try {
            const result = await desktop.desktopMedia('edit-plugin', {
                id: effect.plugin_id, state: effect.plugin_state, values: effect.values
            });
            if (!result?.changed || !context.chain.includes(effect)) return;
            effect.plugin_state = result.state;
            effect.values = {};
            context.changed();
        } finally {
            editingPlugins.delete(effect);
            if (current?.chain === context.chain) render();
        }
    }

    function movePlugin(context, effect, index) {
        const chain = context.chain;
        const previous = chain.indexOf(effect);
        if (previous < 0 || previous === index || index < 0 || index >= chain.length ||
            chain[index].kind === 'normalize') return;
        chain.splice(previous, 1);
        chain.splice(index, 0, effect);
        context.changed();
        render();
        $('rack').children[index]?.querySelector('.vst-editor')?.focus({ preventScroll: true });
    }

    function pluginRow(effect, context) {
        const { chain, changed } = context;
        const index = chain.indexOf(effect);
        const row = el('div', null, 'vst-row');
        const iconButton = (text, label, callback, className = '') => {
            const control = button(text, callback, `vst-action ${className}`);
            control.title = label;
            control.setAttribute('aria-label', label);
            return control;
        };
        const handle = el('span', '⠿', 'vst-grab');
        handle.draggable = true;
        handle.title = t('拖动调整效果顺序');
        handle.ondragstart = event => {
            draggedPlugin = { chain, effect };
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('application/x-soundbot-vst', 'reorder');
            row.classList.add('dragging');
        };
        handle.ondragend = () => {
            draggedPlugin = null;
            row.classList.remove('dragging');
            $('rack').querySelectorAll('.vst-drop-top,.vst-drop-bottom').forEach(node =>
                node.classList.remove('vst-drop-top', 'vst-drop-bottom'));
        };
        const name = button(effect.name, () => attempt(() => editPlugin(effect, context)), 'vst-editor');
        name.disabled = editingPlugins.has(effect);
        name.title = t(name.disabled ? 'VST3 编辑窗口已打开' : '打开插件界面');
        name.setAttribute('aria-label', `${effect.name} · ${name.title}`);
        const toggle = iconButton('⏻', t('旁通插件'), () => {
            effect.on = !effect.on;
            toggle.setAttribute('aria-pressed', String(!effect.on));
            row.classList.toggle('bypassed', !effect.on);
            changed();
        }, 'vst-power');
        toggle.setAttribute('aria-pressed', String(!effect.on));
        row.classList.toggle('bypassed', !effect.on);
        const up = iconButton('↑', t('上移'), () => movePlugin(context, effect, index - 1));
        up.disabled = index < 1 || chain[index - 1]?.kind === 'normalize';
        const down = iconButton('↓', t('下移'), () => movePlugin(context, effect, index + 1));
        down.disabled = index >= chain.length - 1;
        const remove = iconButton('×', t('移除插件'), () => {
            chain.splice(chain.indexOf(effect), 1);
            changed();
            render();
        }, 'vst-remove');
        row.append(handle, name, toggle, up, down, remove);
        return row;
    }

    function acceptPluginDrop(row, effect, context) {
        const accepts = event => draggedPlugin?.chain === context.chain && draggedPlugin.effect !== effect &&
            effect.kind !== 'normalize' && event.dataTransfer.types.includes('application/x-soundbot-vst');
        const below = event => event.clientY >= row.getBoundingClientRect().top + row.offsetHeight / 2;
        const clear = () => row.classList.remove('vst-drop-top', 'vst-drop-bottom');
        row.ondragover = event => {
            if (!accepts(event)) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
            const after = below(event);
            row.classList.toggle('vst-drop-top', !after);
            row.classList.toggle('vst-drop-bottom', after);
        };
        row.ondragleave = event => { if (!row.contains(event.relatedTarget)) clear(); };
        row.ondrop = event => {
            clear();
            if (!accepts(event)) return;
            event.preventDefault();
            const chain = context.chain;
            const source = draggedPlugin.effect;
            const targetIndex = chain.indexOf(effect) + Number(below(event));
            movePlugin(context, source, targetIndex - Number(chain.indexOf(source) < targetIndex));
        };
    }

    function render() {
        const rack = $('rack');
        redraw.length = 0;
        paintSpectrum = null;
        const { chain, label, changed, rate } = current || {};
        $('fxChainLabel').textContent = label || t('效果器');
        $('btnAddEffect').disabled = !chain;
        $('btnResetEffects').disabled = !chain?.length;
        rack.replaceChildren();
        if (!chain) return;
        for (const effect of chain) {
            if (effect.kind === 'vst3') {
                const row = pluginRow(effect, current);
                acceptPluginDrop(row, effect, current);
                rack.append(row);
                continue;
            }
            const definition = builtins[effect.kind];
            const card = el('div', null, `fx-card ${effect.on ? 'on' : 'off'}`);
            const head = el('div', null, 'fx-head');
            let editor;
            const syncToggle = () => {
                toggle.setAttribute('aria-checked', String(effect.on));
                toggle.classList.toggle('on', effect.on);
                card.classList.toggle('on', effect.on);
                card.classList.toggle('off', !effect.on);
            };
            const toggle = button('', () => {
                effect.on = !effect.on;
                syncToggle();
                changed();
            }, `switch ${effect.on ? 'on' : ''}`);
            const reset = () => {
                resetEffect(effect);
                if (editor) {
                    syncToggle();
                    editor.reset();
                } else {
                    const index = chain.indexOf(effect);
                    render();
                    rack.children[index]?.querySelector('.fx-reset')?.focus({ preventScroll: true });
                }
                changed();
            };
            const resetButton = button(t('复位'), reset, 'ghost-btn fx-reset');
            resetButton.title = t('恢复此效果器默认值');
            toggle.setAttribute('role', 'switch');
            toggle.setAttribute('aria-label', effect.name || t(definition?.name || 'VST3'));
            toggle.setAttribute('aria-checked', String(effect.on));
            head.append(el('span', effect.name || t(definition?.name || 'VST3'), 'fx-name'),
                el('span', t(definition?.type || 'VST3'), 'fx-type'), resetButton, toggle);
            card.append(head);
            if (effect.kind === 'eq') {
                editor = equalizer.editor(effect, changed, rate, knob, reset);
                paintSpectrum = editor.paint;
                redraw.push(editor.paint);
                card.append(editor.root);
            } else {
                const reverb = effect.kind === 'reverb';
                const body = el('div', null, reverb ? 'fx-body reverb-controls' : 'fx-body');
                const curve = reverb ? null : el('canvas', null, 'fx-curve');
                const repaint = () => { if (curve) draw(curve, effect); };
                if (curve) redraw.push(repaint);
                for (const [key, name, min, max, , step] of definition.controls) {
                    const control = knob({ label: t(name), min, max, step, read: () => effect.values[key], write: next => {
                        effect.values[key] = next;
                        control.update();
                        repaint();
                        changed();
                    } });
                    control.update();
                    body.append(control.root);
                }
                if (curve) body.append(curve);
                card.append(body);
            }
            acceptPluginDrop(card, effect, current);
            rack.append(card);
        }
        requestAnimationFrame(() => redraw.forEach(paint => paint()));
    }

    function useRack(chain, label, changed, rate = 48000) {
        const locale = window.SoundBotI18n.language;
        if (current?.chain === chain && current?.label === label && current?.rate === rate && current?.locale === locale) return;
        current = { chain, label, changed, rate, locale };
        render();
    }

    let pluginCatalog;

    function discover(refresh = false) {
        if (refresh || !pluginCatalog) {
            const request = api.get('/api/v1/effects/plugins');
            pluginCatalog = request;
            request.catch(() => {
                if (pluginCatalog === request) pluginCatalog = undefined;
            });
        }
        return pluginCatalog;
    }

    function emptyCatalog(available) {
        return el('p', t(available ? '标准安装目录中未找到 VST3 插件。' : 'VST3 宿主组件不可用。'));
    }

    async function settings(refresh = false) {
        const results = $('settingsPlugins');
        const scan = $('btnScanPlugins');
        scan.disabled = true;
        results.replaceChildren(el('p', t('正在扫描 VST3…')));
        try {
            const { available, plugins } = await discover(refresh);
            results.replaceChildren(...(plugins.length ? plugins.map(plugin => el('div', plugin.name)) : [emptyCatalog(available)]));
        } catch (error) {
            results.replaceChildren();
            throw error;
        } finally {
            scan.disabled = false;
        }
    }

    async function pick() {
        const context = current;
        if (!context?.chain || context.chain.length >= 12) return say(t('单条效果链最多 12 个效果器'));
        const dialog = $('pluginDialog');
        const choices = $('pluginChoices');
        const query = $('pluginFilter');
        if (dialog.open) return;
        query.value = '';
        query.disabled = true;
        query.oninput = null;
        choices.replaceChildren(el('p', t('正在扫描 VST3…')));
        dialog.showModal();
        let catalog;
        try {
            catalog = await discover();
        } catch (error) {
            if (dialog.open) dialog.close();
            throw error;
        }
        if (!dialog.open || current?.chain !== context.chain) return;
        const { available, plugins } = catalog;
        const show = () => {
            const needle = query.value.trim().toLowerCase();
            const matches = plugins.filter(plugin => plugin.name.toLowerCase().includes(needle));
            choices.replaceChildren(...matches.map(plugin => button(plugin.name, () => attempt(async () => {
                choices.inert = true;
                query.disabled = true;
                try {
                    const loaded = await desktop.desktopMedia('load-plugin', plugin);
                    if (!loaded || !dialog.open || current?.chain !== context.chain) return;
                    const effect = { kind: 'vst3', plugin_id: loaded.id, name: loaded.name, values: {}, on: true };
                    context.chain.push(effect);
                    dialog.close();
                    current.changed();
                    render();
                    await editPlugin(effect, current);
                } finally {
                    choices.inert = false;
                    query.disabled = false;
                }
            }), 'ghost-btn')));
            if (!matches.length) choices.append(plugins.length
                ? el('p', t('没有匹配的插件。')) : emptyCatalog(available));
        };
        query.disabled = !plugins.length;
        query.oninput = show;
        show();
    }

    function resetLoudness() {
        analysis?.abort();
        analysis = null;
        for (const id of ['lufsI', 'lufsTp', 'lufsLra']) $(id).textContent = '—';
        setText($('fileLoudnessStatus'), '');
        void measure();
    }

    async function measure() {
        const file = state.selected;
        if (!$('fileLoudness').open || $('vstPanel').classList.contains('hidden') ||
            state.workspace !== 'library' || !file || file.missing || analysis) return;
        const key = JSON.stringify([state.library.id, file.id, file.source_fingerprint]);
        const display = result => {
            for (const [metric, id] of [['integrated', 'lufsI'], ['true_peak', 'lufsTp'], ['range', 'lufsLra']]) {
                const value = metric === 'integrated' && result.true_peak == null ? null : result[metric];
                $(id).textContent = value?.toFixed(1) ?? '−∞';
            }
        };
        if (loudnessCache.has(key)) return display(loudnessCache.get(key));
        const request = analysis = new AbortController();
        setText($('fileLoudnessStatus'), () => t('分析中…'));
        const path = `/api/v1/files/${encodeURIComponent(file.id)}/loudness?project_id=${encodeURIComponent(state.library.id)}`;
        try {
            const result = await api.get(path, { signal: request.signal });
            if (request.signal.aborted) return;
            if (loudnessCache.size >= 64) loudnessCache.delete(loudnessCache.keys().next().value);
            loudnessCache.set(key, result);
            display(result);
            setText($('fileLoudnessStatus'), '');
        } catch {
            if (!request.signal.aborted) setText($('fileLoudnessStatus'), () => t('无法分析原文件'));
        } finally {
            if (analysis === request) analysis = null;
        }
    }

    function meters(levels = [0, 0]) {
        const visible = !$('vstPanel').classList.contains('hidden');
        for (const [i, name] of ['L', 'R'].entries()) {
            const amplitude = levels[i] || 0;
            const percent = Math.min(100, Math.max(0, (20 * Math.log10(amplitude) + 60) / 60 * 100));
            peak[i] = playback.active ? Math.max(percent, peak[i] - 1.1) : 0;
            if (visible) {
                $(`m${name}`).style.height = `${percent}%`;
                $(`p${name}`).style.bottom = `${peak[i]}%`;
            }
            $(`ch${name}`).style.height = `${percent}%`;
            $(`chP${name}`).style.bottom = `${peak[i]}%`;
        }
        if (!visible) return;
        const amplitude = Math.max(...levels);
        $('lufsM').textContent = playback.momentary?.toFixed(1) ?? '−∞';
        $('samplePeak').textContent = amplitude > 0 ? (20 * Math.log10(amplitude)).toFixed(1) : '−∞';
        if (paintSpectrum) {
            const spectrum = playback.spectrum();
            if (spectrum || spectrumWasActive) paintSpectrum(spectrum);
            spectrumWasActive = Boolean(spectrum);
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        $('btnResetEffects').onclick = () => {
            if (!current?.chain) return;
            current.chain.forEach(resetEffect);
            current.changed();
            render();
        };
        $('btnAddEffect').onclick = () => attempt(pick);
        $('btnClosePlugins').onclick = () => $('pluginDialog').close();
        $('fileLoudness').ontoggle = () => { if ($('fileLoudness').open) void measure(); };
        $('btnScanPlugins').onclick = () => attempt(() => settings(true));
        new MutationObserver(() => redraw.forEach(paint => paint()))
            .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    });

    window.SoundBotEffects = { create, enabled, useRack, resetLoudness, settings, discover, meters,
        refreshLayout: () => {
            redraw.forEach(paint => paint());
            void measure();
        } };
})();
