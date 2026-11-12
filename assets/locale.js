/* Locale negotiation and message formatting shared by Electron and the renderer. */
((factory) => {
    if (typeof module === 'object' && module.exports) module.exports = factory(require('./locale-messages'));
    else globalThis.SoundBotLocale = factory(globalThis.SoundBotLocaleMessages);
})((rows) => {
    const languages = Object.freeze([
        Object.freeze({ id: 'zh-CN', name: '简体中文' }),
        Object.freeze({ id: 'zh-TW', name: '繁體中文' }),
        Object.freeze({ id: 'ja', name: '日本語' }),
        Object.freeze({ id: 'en', name: 'English' }),
        Object.freeze({ id: 'pt-BR', name: 'Português (Brasil)' }),
        Object.freeze({ id: 'es', name: 'Español' })
    ]);
    const defaultLocale = 'zh-CN';
    const columns = ['zh-CN', 'en', 'zh-TW', 'ja', 'pt-BR', 'es'];
    const messages = new Map(rows.map(row => [row[0], row]));
    const has = key => messages.has(key);

    function normalize(value) {
        if (typeof value !== 'string' || !value || value.length > 64) return null;
        try {
            const locale = new Intl.Locale(value.replaceAll('_', '-'));
            switch (locale.language) {
            case 'zh':
                if (locale.script) return locale.script === 'Hant' ? 'zh-TW' : 'zh-CN';
                return ['TW', 'HK', 'MO'].includes(locale.region) ? 'zh-TW' : 'zh-CN';
            case 'en': case 'ja': case 'es': return locale.language;
            case 'pt': return 'pt-BR';
            default: return null;
            }
        } catch {
            return null;
        }
    }

    function create(value) {
        const locale = normalize(value) || defaultLocale;
        const column = columns.indexOf(locale);
        const number = new Intl.NumberFormat(locale);
        const date = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'medium' });
        const t = (key, args) => {
            const message = messages.get(key)?.[column] ?? key;
            if (typeof message !== 'string' || !args) return message;
            return message.replace(/\{([A-Za-z][\w]*)\}/g, (placeholder, name) => {
                if (!Object.hasOwn(args, name) || args[name] == null) return placeholder;
                return typeof args[name] === 'number' ? number.format(args[name]) : String(args[name]);
            });
        };
        return Object.freeze({
            locale, t,
            number: (n, options) => options ? new Intl.NumberFormat(locale, options).format(n) : number.format(n),
            date: value => date.format(value)
        });
    }

    return Object.freeze({ languages, defaultLocale, normalize, create, has });
});
