(function (global) {
    'use strict';

    const namespace = global.SoundBotRenderer = global.SoundBotRenderer || {};
    const TRANSCODE_EXTENSIONS = new Set(['aif', 'aiff', 'm4a', 'aac', 'wma']);
    const playbackSourceCache = new Map();

    function extensionOf(sound) {
        const candidate = sound?.filePath || sound?.name || sound?.type || '';
        const clean = String(candidate).split(/[?#]/, 1)[0];
        const match = clean.match(/\.([^.\\/]+)$/);
        if (match) return match[1].toLowerCase();
        return String(sound?.type || '').replace(/^\./, '').toLowerCase();
    }

    function requiresPlaybackSource(sound) {
        return TRANSCODE_EXTENSIONS.has(extensionOf(sound));
    }

    function playbackAction(currentSound, requestedSound, isPlaying, options = {}) {
        const currentId = currentSound?.id ?? currentSound?.fileId ?? null;
        const requestedId = requestedSound?.id ?? requestedSound?.fileId ?? null;
        const sameSound = currentId !== null
            && requestedId !== null
            && String(currentId) === String(requestedId);
        return sameSound && isPlaying && !options.forcePlay ? 'pause' : 'play';
    }

    function localSource(filePath) {
        if (!filePath) return '';
        const value = String(filePath);
        if (/^(blob:|https?:|soundmind-audio:)/i.test(value)) return value;
        return `soundmind-audio://local/${encodeURIComponent(value)}`;
    }

    async function resolvePlaybackSource(sound, options = {}) {
        const filePath = sound?.filePath;
        if (!filePath) throw new Error('Audio path is missing');
        if (!requiresPlaybackSource(sound)) return localSource(filePath);

        const fileId = sound?.fileId;
        if (typeof fileId !== 'string' || !fileId.trim()) {
            const error = new Error('A stable file ID is required for this audio format');
            error.code = 'PLAYBACK_FILE_ID_REQUIRED';
            throw error;
        }

        const projectId = options.projectId || 'default';
        const cacheKey = `${projectId}:${fileId}:${filePath}`;
        if (playbackSourceCache.has(cacheKey)) return await playbackSourceCache.get(cacheKey);

        const api = options.api || namespace.api;
        if (!api?.get) throw new Error('Renderer API is unavailable');
        const pendingSource = api.get(
            `/api/v1/files/${encodeURIComponent(fileId)}/playback-source?project_id=${encodeURIComponent(projectId)}`
        ).then(payload => {
            if (!payload?.path) {
                const error = new Error('Playback source response has no path');
                error.code = 'PLAYBACK_SOURCE_INVALID';
                throw error;
            }
            return localSource(payload.path);
        });
        playbackSourceCache.set(cacheKey, pendingSource);
        try {
            return await pendingSource;
        } finally {
            // Cache only concurrent requests. The backend owns an LRU cache and
            // may evict a generated WAV at any time, so a persisted renderer
            // path would eventually point at a deleted file.
            if (playbackSourceCache.get(cacheKey) === pendingSource) {
                playbackSourceCache.delete(cacheKey);
            }
        }
    }

    function waitForMetadata(player) {
        return new Promise((resolve, reject) => {
            const cleanup = () => {
                player.removeEventListener('loadedmetadata', onLoaded);
                player.removeEventListener('error', onError);
            };
            const onLoaded = () => {
                cleanup();
                resolve();
            };
            const onError = () => {
                cleanup();
                reject(new Error('音频加载失败'));
            };
            if (player.readyState >= 1) {
                resolve();
                return;
            }
            player.addEventListener('loadedmetadata', onLoaded);
            player.addEventListener('error', onError);
        });
    }

    namespace.audio = Object.freeze({
        extensionOf,
        requiresPlaybackSource,
        playbackAction,
        localSource,
        resolvePlaybackSource,
        waitForMetadata,
        clearPlaybackSourceCache: () => playbackSourceCache.clear()
    });
})(typeof window !== 'undefined' ? window : globalThis);
