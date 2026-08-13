(function (global) {
    'use strict';

    const namespace = global.SoundBotRenderer = global.SoundBotRenderer || {};
    const layerCache = new WeakMap();

    function isValidData(value) {
        return Array.isArray(value) && value.length > 0 && value.every(Number.isFinite);
    }

    function syncFromPeaks(sound) {
        if (sound && !isValidData(sound.waveform) && isValidData(sound.peaks)) sound.waveform = sound.peaks;
        return sound;
    }

    function getForSound(sound) {
        syncFromPeaks(sound);
        if (isValidData(sound?.waveform)) return sound.waveform;
        if (isValidData(sound?.peaks)) return sound.peaks;
        return null;
    }

    function fileIdForSound(sound) {
        const fileId = sound?.fileId;
        return typeof fileId === 'string' && fileId.trim() ? fileId : null;
    }

    function applyPayload(sound, payload, invalidMessage = 'Invalid waveform data') {
        if (!payload || !isValidData(payload.peaks)) throw new Error(invalidMessage);
        sound.waveform = payload.peaks;
        sound.peaks = payload.peaks;
        sound.waveformDuration = payload.duration;
        sound.waveformSampleRate = payload.sample_rate;
        sound.waveformChannels = payload.channels;
        sound.waveformError = null;
        if (payload.file_id && !sound.fileId) sound.fileId = payload.file_id;
        invalidate();
        return payload;
    }

    async function requestForSound(sound, options = {}) {
        const backendAPI = options.backendAPI;
        if (!backendAPI) throw new Error(options.unavailableMessage || 'Waveform API is unavailable');
        const fileId = fileIdForSound(sound);
        if (fileId && backendAPI.getWaveformById) {
            return backendAPI.getWaveformById(
                fileId,
                options.projectId || 'default',
                options.requestId || null
            );
        }
        if (sound?.filePath && backendAPI.getWaveform) {
            return backendAPI.getWaveform(sound.filePath, options.requestId || null);
        }
        throw new Error(options.unavailableMessage || 'Waveform API is unavailable');
    }

    function surface(canvas, fallbackWidth, fallbackHeight) {
        const rect = canvas.getBoundingClientRect();
        const width = Math.max(1, Math.round(rect.width || fallbackWidth));
        const height = Math.max(1, Math.round(rect.height || fallbackHeight));
        const dpr = Math.max(1, Math.min(3, global.devicePixelRatio || 1));
        const pixelWidth = Math.max(1, Math.round(width * dpr));
        const pixelHeight = Math.max(1, Math.round(height * dpr));
        const resized = canvas.width !== pixelWidth || canvas.height !== pixelHeight;
        if (resized) {
            canvas.width = pixelWidth;
            canvas.height = pixelHeight;
            layerCache.delete(canvas);
        }
        const ctx = canvas.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        return { ctx, width, height, dpr, pixelWidth, pixelHeight, resized };
    }

    function setStatus(state, message = '', options = {}) {
        const doc = options.document || global.document;
        const translate = options.translate || (key => key);
        const canvas = doc?.getElementById('mainWaveform');
        const emptyState = doc?.getElementById('waveformEmptyState');
        const status = doc?.getElementById('waveformStatus');
        const statusText = doc?.getElementById('waveformStatusText');
        const statusIcon = doc?.getElementById('waveformStatusIcon');
        const retryButton = doc?.getElementById('waveformRetryBtn');
        if (!status || !statusText || !statusIcon || !retryButton) return;

        const resolvedMessage = message || (state === 'loading'
            ? translate('waveform.loading')
            : (state === 'error' ? translate('waveform.error') : ''));
        if (status.dataset.state === state && status.dataset.message === resolvedMessage) return;
        status.dataset.state = state;
        status.dataset.message = resolvedMessage;
        status.classList.add('hidden');
        status.classList.remove('flex');
        retryButton.classList.add('hidden');
        statusIcon.classList.remove('hidden', 'animate-spin');
        statusIcon.setAttribute('data-lucide', 'loader-circle');

        if (state === 'empty') {
            canvas?.classList.add('hidden');
            emptyState?.classList.remove('hidden');
        } else if (state === 'ready') {
            canvas?.classList.remove('hidden');
            emptyState?.classList.add('hidden');
        } else {
            canvas?.classList.add('hidden');
            emptyState?.classList.add('hidden');
            status.classList.remove('hidden');
            status.classList.add('flex');
            statusText.textContent = resolvedMessage;
            if (state === 'error') {
                statusIcon.setAttribute('data-lucide', 'circle-alert');
                statusIcon.classList.remove('animate-spin');
                retryButton.classList.remove('hidden');
            }
        }
        if (global.lucide) global.lucide.createIcons();
    }

    function observeResize(target, callback) {
        if (!target || typeof global.ResizeObserver === 'undefined') return null;
        let frame = null;
        const observer = new global.ResizeObserver(() => {
            if (frame !== null) global.cancelAnimationFrame(frame);
            frame = global.requestAnimationFrame(() => {
                frame = null;
                invalidate();
                callback();
            });
        });
        observer.observe(target);
        return observer;
    }

    function createLayer(doc, pixelWidth, pixelHeight, dpr) {
        const canvas = doc.createElement('canvas');
        canvas.width = pixelWidth;
        canvas.height = pixelHeight;
        const ctx = canvas.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        return { canvas, ctx };
    }

    function drawWaveShape(ctx, width, height, waveformData, color, alpha, zoomLevel, scrollOffset) {
        const centerY = height / 2;
        const maxAmplitude = height * 0.85;
        const actualWidth = width * zoomLevel;
        const samples = waveformData.length;
        const step = actualWidth / samples;
        ctx.save();
        ctx.beginPath();
        ctx.rect(0, 0, width, height);
        ctx.clip();
        ctx.translate(-scrollOffset, 0);
        ctx.globalAlpha = alpha;
        ctx.fillStyle = color;
        for (const direction of [-1, 1]) {
            ctx.beginPath();
            ctx.moveTo(0, centerY);
            for (let index = 0; index <= samples; index += 1) {
                const amplitude = Math.abs(waveformData[Math.min(index, samples - 1)]);
                ctx.lineTo(index * step, centerY + direction * amplitude * maxAmplitude * 0.5);
            }
            ctx.lineTo(actualWidth, centerY);
            ctx.closePath();
            ctx.fill();
        }
        ctx.restore();
    }

    function cacheMatches(entry, options, size) {
        return entry
            && entry.waveformData === options.waveformData
            && entry.width === size.width
            && entry.height === size.height
            && entry.dpr === size.dpr
            && entry.zoomLevel === options.zoomLevel
            && entry.scrollOffset === options.scrollOffset
            && entry.backgroundColor === options.backgroundColor
            && entry.activeColor === options.activeColor
            && entry.inactiveColor === options.inactiveColor;
    }

    function ensureLayers(canvas, size, options) {
        let entry = layerCache.get(canvas);
        if (cacheMatches(entry, options, size)) return entry;

        const doc = canvas.ownerDocument || global.document;
        const inactive = createLayer(doc, size.pixelWidth, size.pixelHeight, size.dpr);
        const active = createLayer(doc, size.pixelWidth, size.pixelHeight, size.dpr);
        inactive.ctx.fillStyle = options.backgroundColor;
        inactive.ctx.fillRect(0, 0, size.width, size.height);
        drawWaveShape(
            inactive.ctx, size.width, size.height, options.waveformData,
            options.inactiveColor, 0.25, options.zoomLevel, options.scrollOffset
        );
        drawWaveShape(
            active.ctx, size.width, size.height, options.waveformData,
            options.activeColor, 0.9, options.zoomLevel, options.scrollOffset
        );
        entry = Object.assign({}, options, size, {
            inactiveLayer: inactive.canvas,
            activeLayer: active.canvas
        });
        layerCache.set(canvas, entry);
        return entry;
    }

    function drawSelection(ctx, width, height, options) {
        const start = options.selectionStart;
        const end = options.selectionEnd;
        const duration = options.duration;
        if (start === null || end === null || !(duration > 0) || start >= end) return;

        const actualWidth = width * options.zoomLevel;
        const globalStart = (start / duration) * actualWidth;
        const globalEnd = (end / duration) * actualWidth;
        const visibleStart = options.scrollOffset;
        const visibleEnd = visibleStart + width;
        const clippedStart = Math.max(globalStart, visibleStart);
        const clippedEnd = Math.min(globalEnd, visibleEnd);
        if (clippedEnd <= clippedStart) return;

        const left = clippedStart - visibleStart;
        const right = clippedEnd - visibleStart;
        ctx.save();
        ctx.globalAlpha = 0.2;
        ctx.fillStyle = '#3b82f6';
        ctx.fillRect(left, 0, right - left, height);
        ctx.globalAlpha = 0.8;
        ctx.strokeStyle = '#3b82f6';
        ctx.lineWidth = 2;
        for (const boundary of [globalStart, globalEnd]) {
            if (boundary < visibleStart || boundary > visibleEnd) continue;
            const x = boundary - visibleStart;
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, height);
            ctx.stroke();
            ctx.globalAlpha = 1;
            ctx.fillStyle = '#3b82f6';
            const direction = boundary === globalStart ? 1 : -1;
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x + direction * 6, 0);
            ctx.lineTo(x, 8);
            ctx.closePath();
            ctx.fill();
            ctx.beginPath();
            ctx.moveTo(x, height);
            ctx.lineTo(x + direction * 6, height);
            ctx.lineTo(x, height - 8);
            ctx.closePath();
            ctx.fill();
        }
        ctx.restore();
    }

    function compositeProgress(ctx, entry, width, height, options) {
        const progress = Math.max(0, Math.min(1, Number.isFinite(options.progress) ? options.progress : 0));
        const actualWidth = width * options.zoomLevel;
        const progressX = progress * actualWidth - options.scrollOffset;
        const clippedProgressX = Math.max(0, Math.min(width, progressX));
        if (clippedProgressX > 0) {
            ctx.save();
            ctx.beginPath();
            ctx.rect(0, 0, clippedProgressX, height);
            ctx.clip();
            ctx.drawImage(
                entry.activeLayer, 0, 0, entry.activeLayer.width, entry.activeLayer.height,
                0, 0, width, height
            );
            ctx.restore();
        }
        if (progressX < 0 || progressX > width || progress <= 0) return;

        const centerY = height / 2;
        ctx.save();
        ctx.globalAlpha = 1;
        ctx.shadowColor = options.activeColor;
        ctx.shadowBlur = 6;
        ctx.strokeStyle = options.activeColor;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(progressX, 0);
        ctx.lineTo(progressX, height);
        ctx.stroke();
        ctx.shadowBlur = 0;
        ctx.fillStyle = options.activeColor;
        ctx.beginPath();
        ctx.arc(progressX, centerY, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(progressX, centerY, 1.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
    }

    function renderMain(canvas, options = {}) {
        if (!canvas || !isValidData(options.waveformData)) return false;
        const size = surface(canvas, options.fallbackWidth || 1200, options.fallbackHeight || 120);
        const normalized = Object.assign({
            backgroundColor: '#141414',
            activeColor: '#e5e5e5',
            inactiveColor: '#a3a3a3',
            progress: 0,
            zoomLevel: 1,
            scrollOffset: 0,
            selectionStart: null,
            selectionEnd: null,
            duration: 0
        }, options);
        const entry = ensureLayers(canvas, size, normalized);
        const ctx = size.ctx;
        ctx.clearRect(0, 0, size.width, size.height);
        ctx.drawImage(
            entry.inactiveLayer, 0, 0, entry.inactiveLayer.width, entry.inactiveLayer.height,
            0, 0, size.width, size.height
        );
        drawSelection(ctx, size.width, size.height, normalized);
        compositeProgress(ctx, entry, size.width, size.height, normalized);
        return true;
    }

    function drawMini(canvas, options = {}) {
        const size = surface(canvas, 112, 32);
        const { ctx, width, height } = size;
        ctx.clearRect(0, 0, width, height);
        const waveformData = options.waveformData;
        if (!isValidData(waveformData)) {
            if (options.loading || options.error) {
                ctx.globalAlpha = 0.5;
                ctx.fillStyle = options.error ? '#ef4444' : (options.inactiveColor || '#a3a3a3');
                ctx.font = '12px sans-serif';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(options.error ? '!' : '…', width / 2, height / 2);
                ctx.globalAlpha = 1;
            }
            return false;
        }
        drawWaveShape(
            ctx, width, height, waveformData,
            options.isCurrent ? (options.activeColor || '#e5e5e5') : (options.inactiveColor || '#a3a3a3'),
            options.isCurrent ? 1 : 0.5,
            1,
            0
        );
        return true;
    }

    function invalidate(canvas) {
        if (canvas) layerCache.delete(canvas);
        // WeakMap cannot be cleared; entries are naturally replaced when their cache key changes.
    }

    namespace.waveform = Object.freeze({
        isValidData,
        syncFromPeaks,
        getForSound,
        fileIdForSound,
        applyPayload,
        requestForSound,
        surface,
        setStatus,
        observeResize,
        renderMain,
        drawMini,
        invalidate
    });
})(typeof window !== 'undefined' ? window : globalThis);
