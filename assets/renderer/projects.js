(function (global) {
    'use strict';

    const namespace = global.SoundBotRenderer = global.SoundBotRenderer || {};

    function api() {
        if (!namespace.api?.request) throw new Error('Renderer API is unavailable');
        return namespace.api;
    }

    function projectPath(projectId, suffix = '') {
        return `/api/v1/projects/${encodeURIComponent(projectId)}${suffix}`;
    }

    function artifactSummary(status, kind) {
        const counts = status?.artifacts?.[kind] || {};
        const ready = Number(counts.ready || 0);
        const failed = Number(counts.failed || 0);
        const pending = Number(counts.pending || 0) + Number(counts.processing || 0) + Number(counts.stale || 0);
        return { ready, pending, failed };
    }

    function wait(milliseconds) {
        return new Promise(resolve => global.setTimeout(resolve, milliseconds));
    }

    async function waitForJob(jobId, options = {}) {
        const intervalMs = Math.max(250, options.intervalMs || 1000);
        const timeoutMs = Math.max(intervalMs, options.timeoutMs || 10 * 60 * 1000);
        const startedAt = Date.now();
        while (Date.now() - startedAt < timeoutMs) {
            const job = await api().get(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
            options.onUpdate?.(job);
            if (['completed', 'failed', 'cancelled'].includes(job?.state)) return job;
            await wait(intervalMs);
        }
        const error = new Error('Index job polling timed out');
        error.code = 'INDEX_JOB_TIMEOUT';
        throw error;
    }

    namespace.projects = Object.freeze({
        list: (recent = false) => api().get(recent ? '/api/v1/projects/recent' : '/api/v1/projects'),
        get: projectId => api().get(projectPath(projectId)),
        create: payload => api().post('/api/v1/projects', payload),
        delete: projectId => api().delete(projectPath(projectId)),
        switch: projectId => api().post(projectPath(projectId, '/switch')),
        listFolders: projectId => api().get(projectPath(projectId, '/folders')),
        createFolder: (projectId, payload) => api().post(projectPath(projectId, '/folders'), payload),
        updateFolder: (projectId, folderId, payload) => api().put(
            projectPath(projectId, `/folders/${encodeURIComponent(folderId)}`),
            payload
        ),
        deleteFolder: (projectId, folderId) => api().delete(
            projectPath(projectId, `/folders/${encodeURIComponent(folderId)}`)
        ),
        listFolderMappings: projectId => api().get(projectPath(projectId, '/folder-mappings')),
        updateFolderMapping: (projectId, folderPath, userFolderId) => {
            const query = userFolderId ? `?user_folder_id=${encodeURIComponent(userFolderId)}` : '';
            return api().post(
                projectPath(projectId, `/folder-mappings/${encodeURIComponent(folderPath)}${query}`)
            );
        },
        listFiles: (projectId, options = {}) => {
            const query = new URLSearchParams({
                project_id: projectId || 'default',
                limit: String(options.limit || 200)
            });
            if (options.cursor) query.set('cursor', String(options.cursor));
            return api().get(`/api/v1/files?${query.toString()}`);
        },
        indexStatus: projectId => api().get(projectPath(projectId, '/index/status')),
        reconcileIndex: (projectId, kinds) => api().post(
            projectPath(projectId, '/index/reconcile'),
            { kinds }
        ),
        rebuildIndex: (projectId, kinds) => api().post(
            projectPath(projectId, '/index/rebuild'),
            { kinds }
        ),
        artifactSummary,
        waitForJob
    });
})(typeof window !== 'undefined' ? window : globalThis);
