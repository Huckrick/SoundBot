(function (global) {
    'use strict';

    const namespace = global.SoundBotRenderer = global.SoundBotRenderer || {};
    let apiBase = 'http://127.0.0.1:8000';
    let wsBase = 'ws://127.0.0.1:8000';

    function normalizeBase(value, fallback) {
        const resolved = String(value || fallback || '').trim();
        return resolved.replace(/\/+$/, '');
    }

    function configure(config = {}) {
        apiBase = normalizeBase(config.apiBase, apiBase);
        wsBase = normalizeBase(config.wsBase, wsBase);
        return getConfig();
    }

    function getConfig() {
        return { apiBase, wsBase };
    }

    function url(path = '') {
        const value = String(path || '');
        if (/^https?:\/\//i.test(value)) return value;
        return `${apiBase}${value.startsWith('/') ? value : `/${value}`}`;
    }

    function wsUrl(path = '') {
        const value = String(path || '');
        if (/^wss?:\/\//i.test(value)) return value;
        return `${wsBase}${value.startsWith('/') ? value : `/${value}`}`;
    }

    async function fetchResponse(path, options = {}) {
        return global.fetch(url(path), options);
    }

    async function parseResponse(response) {
        if (response.status === 204) return null;
        const contentType = response.headers?.get?.('content-type') || '';
        if (contentType.includes('application/json')) return response.json();
        const text = await response.text();
        return text ? { data: text } : null;
    }

    function errorFromPayload(response, payload) {
        const detail = payload?.detail && typeof payload.detail === 'object'
            ? payload.detail
            : null;
        const structured = payload?.error && typeof payload.error === 'object'
            ? payload.error
            : (detail || payload || {});
        const message = typeof payload?.detail === 'string'
            ? payload.detail
            : (typeof payload?.error === 'string'
                ? payload.error
                : (structured.message || `HTTP ${response.status}`));
        const error = new Error(message);
        error.code = structured.code || payload?.code || `HTTP_${response.status}`;
        error.status = response.status;
        error.retryable = Boolean(
            structured.retryable
            ?? [408, 425, 429, 502, 503, 504].includes(response.status)
        );
        error.details = structured.details && typeof structured.details === 'object'
            ? structured.details
            : {};
        error.detail = error.details;
        return error;
    }

    async function request(path, options = {}) {
        const response = await fetchResponse(path, options);
        let payload = null;
        try {
            payload = await parseResponse(response);
        } catch (parseError) {
            if (response.ok) throw parseError;
        }
        if (!response.ok) throw errorFromPayload(response, payload);
        return payload;
    }

    function jsonOptions(method, body, options = {}) {
        const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
        return Object.assign({}, options, {
            method,
            headers,
            body: body === undefined ? undefined : JSON.stringify(body)
        });
    }

    namespace.api = Object.freeze({
        configure,
        getConfig,
        url,
        wsUrl,
        fetch: fetchResponse,
        request,
        get: (path, options) => request(path, Object.assign({}, options, { method: 'GET' })),
        post: (path, body, options) => request(path, jsonOptions('POST', body, options)),
        put: (path, body, options) => request(path, jsonOptions('PUT', body, options)),
        delete: (path, options) => request(path, Object.assign({}, options, { method: 'DELETE' }))
    });
})(typeof window !== 'undefined' ? window : globalThis);
