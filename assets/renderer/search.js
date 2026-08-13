(function (global) {
    'use strict';

    const namespace = global.SoundBotRenderer = global.SoundBotRenderer || {};
    const MAX_TOP_K = 1000;
    const DEFAULT_TOP_K = 400;

    function finiteNumber(value, fallback) {
        const number = Number(value);
        return Number.isFinite(number) ? number : fallback;
    }

    function requireProjectId(value) {
        const projectId = typeof value === 'string' ? value.trim() : '';
        if (!projectId) throw new Error('projectId is required');
        return projectId;
    }

    function buildPayload(options = {}) {
        const pageSize = Math.max(1, Math.min(200, Math.floor(finiteNumber(options.pageSize, 50))));
        const requestedTopK = Math.floor(finiteNumber(options.topK, Math.max(pageSize * 4, 200)));
        return {
            query: String(options.query || '').trim(),
            top_k: Math.max(1, Math.min(MAX_TOP_K, requestedTopK || DEFAULT_TOP_K)),
            threshold: Math.max(0, Math.min(1, finiteNumber(options.threshold, 0.15))),
            page: Math.max(1, Math.floor(finiteNumber(options.page, 1))),
            page_size: pageSize,
            project_id: requireProjectId(options.projectId)
        };
    }

    function buildChatPayload(options = {}) {
        const search = buildPayload({
            query: options.message,
            topK: options.topK || 20,
            threshold: options.threshold ?? 0.1,
            pageSize: options.topK || 20,
            projectId: options.projectId
        });
        return {
            message: String(options.message || ''),
            project_id: search.project_id,
            history: Array.isArray(options.history) ? options.history.slice(-10) : [],
            top_k: search.top_k,
            threshold: search.threshold
        };
    }

    namespace.search = Object.freeze({ MAX_TOP_K, DEFAULT_TOP_K, buildPayload, buildChatPayload });
})(typeof window !== 'undefined' ? window : globalThis);
