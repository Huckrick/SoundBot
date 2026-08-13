(function (global) {
    'use strict';

    const namespace = global.SoundBotRenderer = global.SoundBotRenderer || {};
    const unsupportedProviders = new Set(['azure', 'gemini', 'anthropic', 'kimi_coding']);
    const llmSecretInputs = Object.freeze({
        openai: 'openaiApiKey',
        azure: 'azureApiKey',
        gemini: 'geminiApiKey',
        kimi: 'kimiApiKey',
        kimi_coding: 'kimiCodingApiKey',
        anthropic: 'anthropicApiKey',
        deepseek: 'deepseekApiKey',
        siliconflow: 'siliconflowApiKey',
        custom: 'customApiKey',
        external: 'externalApiKey'
    });
    const embeddingSecretInputs = Object.freeze({ external: 'externalEmbeddingKey' });

    function setSecretInputState(inputId, config = {}, options = {}) {
        const doc = options.document || global.document;
        const translate = options.translate || (key => key);
        const input = doc?.getElementById(inputId);
        if (!input) return;
        if (!input.dataset.defaultPlaceholder) input.dataset.defaultPlaceholder = input.placeholder || '';

        const hasStoredSecret = Boolean(config.has_api_key);
        input.value = '';
        input.dataset.hasStoredSecret = hasStoredSecret ? 'true' : 'false';
        input.dataset.secretAction = 'keep';
        input.placeholder = hasStoredSecret ? translate('settings.ai.key.saved') : input.dataset.defaultPlaceholder;

        let clearButton = input.parentElement?.querySelector(`[data-secret-clear-for="${inputId}"]`);
        if (!clearButton && input.parentElement) {
            clearButton = doc.createElement('button');
            clearButton.type = 'button';
            clearButton.dataset.secretClearFor = inputId;
            clearButton.className = 'mt-1 text-xs text-red-400 hover:text-red-300';
            clearButton.addEventListener('click', () => {
                const clearing = input.dataset.secretAction !== 'clear';
                input.dataset.secretAction = clearing ? 'clear' : 'keep';
                input.value = '';
                input.placeholder = clearing
                    ? '保存后将清除密钥'
                    : (input.dataset.hasStoredSecret === 'true'
                        ? translate('settings.ai.key.saved')
                        : input.dataset.defaultPlaceholder);
                clearButton.textContent = clearing ? '撤销清除' : '清除已保存密钥';
            });
            input.parentElement.appendChild(clearButton);
        }
        if (clearButton) {
            clearButton.textContent = '清除已保存密钥';
            clearButton.classList.toggle('hidden', !hasStoredSecret);
        }
        if (!input.dataset.secretInputBound) {
            input.dataset.secretInputBound = 'true';
            input.addEventListener('input', () => {
                if (input.value.trim()) input.dataset.secretAction = 'set';
            });
        }
    }

    function secretIntent(input) {
        if (!input) return { action: 'keep' };
        if (input.dataset.secretAction === 'clear') return { action: 'clear' };
        const value = input.value.trim();
        return value ? { action: 'set', value } : { action: 'keep' };
    }

    function collectSecretUpdates(doc, llmProvider, embeddingProvider) {
        return {
            llm_secret: secretIntent(doc.getElementById(llmSecretInputs[llmProvider])),
            embedding_secret: secretIntent(doc.getElementById(embeddingSecretInputs[embeddingProvider]))
        };
    }

    namespace.settings = Object.freeze({
        unsupportedProviders,
        llmSecretInputs,
        embeddingSecretInputs,
        setSecretInputState,
        secretIntent,
        collectSecretUpdates,
        getApiKeyForRequest: () => undefined,
        isUnsupportedProvider: provider => unsupportedProviders.has(provider)
    });
})(typeof window !== 'undefined' ? window : globalThis);
