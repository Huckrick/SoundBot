(function (global) {
    'use strict';

    const namespace = global.SoundBotRenderer = global.SoundBotRenderer || {};

    function createStore(initialState = {}) {
        const state = Object.assign({}, initialState);
        const listeners = new Set();

        function get(key) {
            return key === undefined ? Object.assign({}, state) : state[key];
        }

        function set(key, value) {
            const previous = state[key];
            state[key] = value;
            if (previous !== value) {
                listeners.forEach(listener => listener(key, value, previous));
            }
            return value;
        }

        function update(values = {}) {
            Object.entries(values).forEach(([key, value]) => set(key, value));
            return get();
        }

        function subscribe(listener) {
            listeners.add(listener);
            return () => listeners.delete(listener);
        }

        return Object.freeze({ get, set, update, subscribe });
    }

    function normalizePath(filePath) {
        return String(filePath || '').replace(/\\/g, '/').replace(/\/+$/, '');
    }

    function isSameOrChildPath(candidatePath, parentPath) {
        const candidate = normalizePath(candidatePath);
        const parent = normalizePath(parentPath);
        if (!parent) return Boolean(candidate);
        return candidate === parent || candidate.startsWith(`${parent}/`);
    }

    function parentPath(filePath) {
        const normalized = normalizePath(filePath);
        const index = normalized.lastIndexOf('/');
        return index >= 0 ? normalized.substring(0, index) : '';
    }

    namespace.state = Object.freeze({
        createStore,
        normalizePath,
        isSameOrChildPath,
        parentPath
    });
})(typeof window !== 'undefined' ? window : globalThis);
