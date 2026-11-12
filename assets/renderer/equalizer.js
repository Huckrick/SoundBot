/* The graph shows the configured EQ response over the live output spectrum. */
(() => {
    const { el, button, togglePlay, playback } = window.SoundBotWorkspace;
    const { t } = window.SoundBotI18n;
    const shapes = {
        bell: ['peaking', '钟形'],
        low_shelf: ['lowshelf', '低架'],
        high_shelf: ['highshelf', '高架'],
        low_cut: ['highpass', '低切'],
        high_cut: ['lowpass', '高切']
    };
    const bandLimit = 8;
    const frequencies = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000];
    const clamp = (n, min, max) => Math.min(max, Math.max(min, n));
    const newBand = (frequency = 1000, gain = 0, shape = 'bell') => ({ shape, frequency, gain, q: 0.707, enabled: true });
    const defaults = () => [newBand(120, 0, 'low_shelf'), newBand(), newBand(6000, 0, 'high_shelf')];
    const hz = fraction => 20 * 1000 ** fraction;
    const log = frequency => Math.log(frequency / 20) / Math.log(1000);
    const isCut = band => band.shape.endsWith('_cut');
    const hasQ = band => !band.shape.endsWith('_shelf');
    const number = (n, digits) => Number(n.toFixed(digits));

    function editor(effect, changed, sourceRate, knob, reset) {
        const root = el('div', null, 'eq-editor');
        const controls = el('div');
        const plot = el('div', null, 'eq-plot');
        plot.title = t('双击添加频点，拖动调整，右键选择滤波类型');
        const canvas = el('canvas');
        canvas.setAttribute('aria-hidden', 'true');
        const points = el('div', null, 'eq-points');
        plot.append(canvas, points);
        const fields = el('div', null, 'eq-fields fx-body');
        const frequency = makeKnob('frequency', t('频率 Hz'), 20, 20000, 1, 0, 'log');
        const gain = makeKnob('gain', t('增益 dB'), -24, 24, 0.1, 1);
        const q = makeKnob('q', 'Q', 0.1, 18, 0.001, 3, 'log');
        fields.append(frequency.root, gain.root, q.root);
        const actions = el('div', null, 'eq-actions');
        const shape = button('', () => openMenu(shape), 'ghost-btn eq-shape');
        shape.setAttribute('aria-haspopup', 'menu');
        shape.setAttribute('aria-expanded', 'false');
        const add = button(t('＋ 频点'), () => {
            const bounds = [20, ...effect.bands.map(band => band.frequency).sort((a, b) => a - b), 20000];
            let left = 20;
            let right = 20;
            for (let i = 1; i < bounds.length; i++) {
                if (bounds[i] / bounds[i - 1] > right / left) [left, right] = [bounds[i - 1], bounds[i]];
            }
            addBand(Math.round(Math.sqrt(left * right)), 0);
        }, 'ghost-btn');
        const menu = el('div', null, 'eq-menu');
        menu.setAttribute('role', 'menu');
        menu.setAttribute('aria-label', t('频点选项'));
        menu.hidden = true;
        let menuSession;
        const dialog = el('dialog', null, 'modal eq-dialog');
        dialog.setAttribute('aria-label', t('参数均衡器'));
        const close = button(t('完成'), () => dialog.close(), 'ghost-btn eq-done');
        const audition = button('', () => { togglePlay(); refreshAuditionLabel(); }, 'ghost-btn eq-audition');
        const header = el('div', null, 'eq-dialog-head');
        header.append(el('h3', t('参数均衡器')),
            button(t('复位'), reset, 'ghost-btn eq-reset'), audition, close);
        const expand = button(t('展开'), () => {
            closeMenu();
            dialog.append(controls);
            expand.hidden = true;
            dialog.showModal();
            paint(lastSpectrum);
        }, 'ghost-btn');
        dialog.onclose = () => {
            closeMenu();
            root.insertBefore(controls, dialog);
            expand.hidden = false;
            paint(lastSpectrum);
            expand.focus();
        };
        dialog.append(header);
        const actionsRight = el('div', null, 'eq-actions-right');
        actionsRight.append(add, expand);
        actions.append(shape, actionsRight);
        controls.append(plot, fields, actions, menu);
        root.append(controls, dialog);
        let selected = Math.min(1, effect.bands.length - 1);
        let responseContext, filters;
        let lastSpectrum;
        const responseFrequencies = Float32Array.from({ length: 181 }, (_, i) => hz(i / 180));
        const magnitude = new Float32Array(responseFrequencies.length);
        const phase = new Float32Array(responseFrequencies.length);
        const response = new Float32Array(responseFrequencies.length);
        const dimensions = () => ({
            left: 26, top: 12,
            width: Math.max(1, plot.clientWidth - 40),
            height: Math.max(1, plot.clientHeight - 33)
        });
        const xFor = (frequency, box) => box.left + log(frequency) * box.width;
        const yFor = (db, box) => box.top + (24 - clamp(db, -24, 24)) / 48 * box.height;
        const valuesAt = event => {
            const rect = plot.getBoundingClientRect();
            const box = dimensions();
            const x = clamp((event.clientX - rect.left - plot.clientLeft - box.left) / box.width, 0, 1);
            const y = clamp((event.clientY - rect.top - plot.clientTop - box.top) / box.height, 0, 1);
            return [Math.round(hz(x)), number(24 - y * 48, 1)];
        };

        function makeKnob(field, label, min, max, step, digits, scale) {
            return knob({ label, min, max, step, digits, scale, editable: true,
                read: () => effect.bands[selected]?.[field],
                write: next => update({ [field]: next })
            });
        }

        function deleteBand() {
            if (!effect.bands[selected]) return;
            effect.bands.splice(selected, 1);
            selected = Math.min(selected, effect.bands.length - 1);
            rebuildPoints();
            sync();
            (points.children[selected] || add).focus({ preventScroll: true });
            changed();
        }

        function openMenu(anchor, event) {
            const band = effect.bands[selected];
            if (!band) return;
            closeMenu();
            const session = new AbortController();
            menuSession = { session, anchor };
            anchor.setAttribute('aria-expanded', 'true');

            const options = Object.entries(shapes).map(([key, [, label]]) => {
                const item = button(t(label), () => {
                    closeMenu(true);
                    update({ shape: key });
                }, 'eq-menu-item');
                item.setAttribute('role', 'menuitemradio');
                item.setAttribute('aria-checked', String(key === band.shape));
                return item;
            });
            const bypass = button(band.enabled ? t('旁通频点') : t('启用频点'), () => {
                closeMenu(true);
                update({ enabled: !band.enabled });
            }, 'eq-menu-item');
            const remove = button(t('删除频点'), () => {
                closeMenu();
                deleteBand();
            }, 'eq-menu-item');
            for (const item of [bypass, remove]) item.setAttribute('role', 'menuitem');
            const divider = el('div', null, 'eq-menu-divider');
            divider.setAttribute('role', 'separator');
            const items = [...options, bypass, remove];
            for (const item of items) item.tabIndex = -1;
            menu.replaceChildren(...options, divider, bypass, remove);
            menu.hidden = false;
            const box = anchor.getBoundingClientRect();
            const x = event?.clientX ?? box.left;
            const y = event?.clientY ?? box.bottom + 5;
            menu.style.left = `${clamp(x, 8, Math.max(8, innerWidth - menu.offsetWidth - 8))}px`;
            menu.style.top = `${clamp(y, 8, Math.max(8, innerHeight - menu.offsetHeight - 8))}px`;
            options.find(item => item.getAttribute('aria-checked') === 'true').focus({ preventScroll: true });

            const listener = { signal: session.signal };
            document.addEventListener('pointerdown', pointer => {
                if (!menu.contains(pointer.target)) closeMenu();
            }, { ...listener, capture: true });
            document.addEventListener('scroll', () => closeMenu(), { ...listener, capture: true });
            window.addEventListener('blur', () => closeMenu(), listener);
            menu.onfocusout = focus => { if (!menu.contains(focus.relatedTarget)) closeMenu(); };
            menu.onkeydown = key => {
                const current = items.indexOf(document.activeElement);
                if (key.key === 'Escape' || key.key === 'Tab') {
                    if (key.key === 'Escape') key.preventDefault();
                    key.stopPropagation();
                    closeMenu(true);
                    return;
                }
                const next = {
                    ArrowDown: (current + 1) % items.length, ArrowUp: (current + items.length - 1) % items.length,
                    Home: 0, End: items.length - 1
                }[key.key];
                if (next == null) return;
                key.preventDefault();
                items[next].focus({ preventScroll: true });
            };
        }

        function closeMenu(restoreFocus = false) {
            if (!menuSession) return;
            const { session, anchor } = menuSession;
            menuSession = null;
            session.abort();
            menu.onkeydown = menu.onfocusout = null;
            menu.hidden = true;
            anchor.setAttribute('aria-expanded', 'false');
            if (restoreFocus) anchor.focus({ preventScroll: true });
        }

        function update(fields) {
            const band = effect.bands[selected];
            if (!band || Object.entries(fields).every(([key, value]) => band[key] === value)) return;
            Object.assign(band, fields);
            sync();
            changed();
        }

        function addBand(frequency, gain) {
            if (effect.bands.length >= bandLimit) return;
            effect.bands.push(newBand(frequency, gain));
            selected = effect.bands.length - 1;
            rebuildPoints();
            sync();
            points.children[selected].focus({ preventScroll: true });
            changed();
        }

        function rebuildPoints() {
            points.replaceChildren(...effect.bands.map((band, index) => {
                const point = el('button', null, 'eq-point');
                point.type = 'button';
                point.setAttribute('aria-haspopup', 'menu');
                point.setAttribute('aria-expanded', 'false');
                point.onfocus = point.onclick = () => {
                    if (selected === index) return;
                    selected = index;
                    sync();
                };
                point.oncontextmenu = event => {
                    event.preventDefault();
                    selected = index;
                    sync();
                    openMenu(point, event);
                };
                point.onpointerdown = event => {
                    if (event.button !== 0) return;
                    event.preventDefault();
                    point.focus({ preventScroll: true });
                    selected = index;
                    sync();
                    beginDrag(event, band);
                };
                point.onkeydown = event => {
                    selected = index;
                    if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
                        event.preventDefault();
                        openMenu(point);
                    } else if (event.key === 'Delete' || event.key === 'Backspace') {
                        event.preventDefault();
                        event.stopPropagation();
                        deleteBand();
                    } else if (['ArrowLeft', 'ArrowRight'].includes(event.key)) {
                        event.preventDefault();
                        const direction = event.key === 'ArrowRight' ? 1 : -1;
                        update({ frequency: Math.round(clamp(band.frequency * 2 ** (direction / 24), 20, 20000)) });
                    } else if (['ArrowUp', 'ArrowDown'].includes(event.key) && !isCut(band)) {
                        event.preventDefault();
                        update({ gain: number(clamp(band.gain + (event.key === 'ArrowUp' ? 0.5 : -0.5), -24, 24), 1) });
                    }
                };
                return point;
            }));
        }

        function beginDrag(event, band) {
            const { pointerId, clientX, clientY } = event;
            const gesture = new AbortController();
            const options = { signal: gesture.signal };
            let moving = false;
            const finish = pointer => {
                if (pointer.pointerId === pointerId) gesture.abort();
            };
            plot.setPointerCapture(pointerId);
            window.addEventListener('pointermove', pointer => {
                if (pointer.pointerId !== pointerId) return;
                if (!plot.isConnected || !(pointer.buttons & 1)) return gesture.abort();
                if (!moving && Math.hypot(pointer.clientX - clientX, pointer.clientY - clientY) < 3) return;
                moving = true;
                const [frequency, gain] = valuesAt(pointer);
                update(isCut(band) ? { frequency } : { frequency, gain });
            }, options);
            window.addEventListener('pointerup', finish, options);
            window.addEventListener('pointercancel', finish, options);
            window.addEventListener('blur', () => gesture.abort(), options);
        }

        function sync() {
            const band = effect.bands[selected];
            shape.disabled = !band;
            shape.textContent = band ? `${t(shapes[band.shape][1])} ▾` : t('滤波类型');
            shape.classList.toggle('bypassed', Boolean(band && !band.enabled));
            add.disabled = effect.bands.length >= bandLimit;
            frequency.update(!band);
            gain.update(!band || isCut(band));
            q.update(!band || !hasQ(band));
            updateResponse();
            paint(lastSpectrum);
        }

        function updateResponse() {
            response.fill(0);
            if (!responseContext) {
                responseContext = new OfflineAudioContext(1, 1, sourceRate);
                filters = Array.from({ length: bandLimit }, () => responseContext.createBiquadFilter());
            }
            for (const [index, band] of effect.bands.entries()) {
                if (!band.enabled) continue;
                const filter = filters[index];
                filter.type = shapes[band.shape][0];
                filter.frequency.value = Math.min(band.frequency, sourceRate * 0.49);
                filter.gain.value = band.gain;
                filter.Q.value = isCut(band) ? 20 * Math.log10(band.q) : band.q;
                filter.getFrequencyResponse(responseFrequencies, magnitude, phase);
                for (let i = 0; i < magnitude.length; i++) {
                    if (Number.isFinite(magnitude[i])) response[i] += 20 * Math.log10(Math.max(1e-6, magnitude[i]));
                }
            }
        }

        function paint(spectrum = null) {
            lastSpectrum = spectrum;
            if (dialog.open) refreshAuditionLabel();
            const width = plot.clientWidth, height = plot.clientHeight;
            if (!width || !height) return;
            const box = dimensions();
            const dpr = devicePixelRatio || 1;
            const pixelWidth = Math.round(width * dpr);
            const pixelHeight = Math.round(height * dpr);
            if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
                canvas.width = pixelWidth;
                canvas.height = pixelHeight;
            }
            const ctx = canvas.getContext('2d');
            const css = getComputedStyle(root);
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            ctx.clearRect(0, 0, width, height);
            const bottom = box.top + box.height;
            ctx.lineWidth = 1;
            ctx.font = `9px ${css.getPropertyValue('--font')}`;
            ctx.fillStyle = css.getPropertyValue('--tx2');
            ctx.strokeStyle = css.getPropertyValue('--line');
            ctx.beginPath();
            const tickLabels = width > 380 ? frequencies : [20, 100, 1000, 5000, 20000];
            for (const frequency of frequencies) {
                const x = xFor(frequency, box);
                ctx.moveTo(x, box.top);
                ctx.lineTo(x, bottom);
                if (tickLabels.includes(frequency)) {
                    const label = frequency >= 1000 ? `${frequency / 1000}k` : String(frequency);
                    ctx.textAlign = 'center';
                    ctx.fillText(label, x, bottom + 13);
                }
            }
            ctx.textAlign = 'right';
            for (const db of [24, 12, 0, -12, -24]) {
                const y = yFor(db, box);
                ctx.moveTo(box.left, y);
                ctx.lineTo(box.left + box.width, y);
                ctx.fillText(db > 0 ? `+${db}` : String(db), box.left - 5, y + 3);
            }
            ctx.stroke();
            if (spectrum) {
                ctx.beginPath();
                ctx.moveTo(box.left, bottom);
                const { channels, hzPerBin } = spectrum;
                const last = channels[0].length - 1;
                const level = bin => Math.max(-100, channels[0][bin], channels[1][bin]);
                let lastBin = 0;
                for (let x = 0; x <= box.width; x++) {
                    const at = Math.min(last, hz(x / box.width) / hzPerBin);
                    const bin = Math.floor(at);
                    const low = level(bin);
                    let db = low + (level(Math.min(last, bin + 1)) - low) * (at - bin);
                    for (let skipped = lastBin + 1; skipped < bin; skipped++) db = Math.max(db, level(skipped));
                    lastBin = bin;
                    ctx.lineTo(box.left + x, bottom - clamp((db + 100) / 85, 0, 1) * box.height);
                }
                ctx.lineTo(box.left + box.width, bottom);
                ctx.closePath();
                const fill = ctx.createLinearGradient(0, box.top, 0, bottom);
                fill.addColorStop(0, css.getPropertyValue('--acc-dim2'));
                fill.addColorStop(1, css.getPropertyValue('--acc-dim'));
                ctx.fillStyle = fill;
                ctx.fill();
            }
            ctx.beginPath();
            for (let i = 0; i < response.length && responseFrequencies[i] < sourceRate / 2; i++) {
                const x = xFor(responseFrequencies[i], box), y = yFor(response[i], box);
                if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
            }
            ctx.strokeStyle = css.getPropertyValue('--tx1');
            ctx.lineJoin = ctx.lineCap = 'round';
            ctx.lineWidth = 1.5;
            ctx.shadowColor = css.getPropertyValue('--acc-glow');
            ctx.shadowBlur = 8;
            ctx.stroke();
            ctx.shadowBlur = 0;
            for (const [i, point] of [...points.children].entries()) {
                const band = effect.bands[i];
                const active = selected === i;
                point.style.left = `${xFor(band.frequency, box)}px`;
                point.style.top = `${yFor(isCut(band) ? 0 : band.gain, box)}px`;
                point.classList.toggle('on', active);
                point.classList.toggle('bypassed', !band.enabled);
                const label = `${band.frequency} Hz · ${t(shapes[band.shape][1])}${band.enabled ? '' : ` · ${t('已旁通')}`}`;
                point.title = label;
                point.setAttribute('aria-label', label);
                point.setAttribute('aria-pressed', String(active));
            }
        }

        function refreshAuditionLabel() {
            const label = playback.active ? t('暂停试听') : t('试听');
            if (audition.textContent !== label) audition.textContent = label;
        }

        plot.ondblclick = event => { if (!event.target.closest('.eq-point')) addBand(...valuesAt(event)); };
        rebuildPoints();
        sync();
        return { root, paint, reset: () => {
            closeMenu();
            selected = Math.min(1, effect.bands.length - 1);
            rebuildPoints();
            sync();
        } };
    }

    window.SoundBotEqualizer = { defaults, editor };
})();
