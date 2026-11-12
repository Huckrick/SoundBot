/* Localize initial document labels and re-render stateful UI on language changes. */
(() => {
    const locale = window.SoundBotLocale;
    const storageKey = 'soundbot_language';
    const routeLocale = new URLSearchParams(location.search).get('locale');
    const initialLocale = locale.normalize(routeLocale) || locale.normalize(localStorage.getItem(storageKey));
    let formatter = locale.create(initialLocale);
    const labels = [];
    const stateLabels = new WeakMap();
    const t = (key, args) => formatter.t(key, args);

    function remember(node, attribute, raw) {
        const key = raw.trim();
        if (locale.has(key)) labels.push({ node, attribute, raw, key });
    }

    function apply() {
        document.documentElement.lang = formatter.locale;
        for (const { node, attribute, raw, key } of labels) {
            if (!node.isConnected) continue;
            const translated = raw.replace(key, () => t(key));
            if (attribute) node.setAttribute(attribute, translated);
            else node.nodeValue = translated;
        }
        for (const node of document.querySelectorAll('[data-locale-message]')) {
            const render = stateLabels.get(node);
            if (render) node.textContent = render();
        }
    }

    function setText(node, render) {
        node.toggleAttribute('data-locale-message', typeof render === 'function');
        if (typeof render === 'function') {
            stateLabels.set(node, render);
            node.textContent = render();
        } else {
            stateLabels.delete(node);
            node.textContent = render ?? '';
        }
    }

    function setLanguage(value) {
        const next = locale.normalize(value);
        if (!next || next === formatter.locale) return;
        formatter = locale.create(next);
        localStorage.setItem(storageKey, next);
        apply();
        window.dispatchEvent(new Event('soundbot-language'));
    }

    const errorCodes = Object.freeze({
        DIALOG_BUSY: '已有文件选择窗口正在打开',
        WINDOW_UNAVAILABLE: '窗口不可用',
        BACKEND_NOT_READY: '后端未就绪',
        REQUEST_FAILED: '请求失败',
        project_not_found: '音效库不存在',
        file_not_found: '素材不存在',
        invalid_relocation: '请选择素材或来源目录的新位置',
        artwork_unavailable: '无法读取封面',
        metadata_unavailable: '无法读取源文件信息'
    });

    function describeError(error) {
        const message = error?.message ?? error;
        if (locale.has(message)) return t(message);
        if (typeof message === 'string' && /^WAAPI 查询失败 \([^)]*\)$/.test(message)) return t('WAAPI 查询失败');
        const key = Object.hasOwn(errorCodes, error?.code) ? errorCodes[error.code] : null;
        return t(key || '操作未完成。请重试或打开日志。');
    }

    document.addEventListener('DOMContentLoaded', () => {
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) {
            const text = walker.currentNode;
            if (!text.parentElement.closest('script,style')) remember(text, null, text.nodeValue);
        }
        for (const attribute of ['title', 'placeholder', 'aria-label', 'alt']) {
            for (const node of document.querySelectorAll(`[${attribute}]`)) remember(node, attribute, node.getAttribute(attribute));
        }
        apply();
    });

    window.SoundBotI18n = Object.freeze({
        t, describeError, setText, setLanguage, languages: locale.languages,
        number: (n, options) => formatter.number(n, options),
        date: value => formatter.date(value),
        get language() { return formatter.locale; }
    });
})();
