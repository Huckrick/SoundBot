#!/usr/bin/env node
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const locale = require('../../assets/locale');
const messages = require('../../assets/locale-messages');

const root = path.resolve(__dirname, '..', '..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const locales = ['zh-CN', 'en', 'zh-TW', 'ja', 'pt-BR', 'es'];
const placeholders = text => [...text.matchAll(/\{([A-Za-z][\w]*)\}/g)].map(match => match[1]).sort();
const keys = new Set();
assert.deepEqual(locale.languages.map(item => item.id).sort(), locales.slice().sort());
for (const row of messages) {
    const [key] = row;
    assert.equal(row.length, locales.length, `incomplete translation: ${key}`);
    assert(!keys.has(key), `duplicate key: ${key}`);
    keys.add(key);
    const args = Object.fromEntries(placeholders(key).map(name => [name, '42']));
    for (const [column, id] of locales.entries()) {
        const message = row[column];
        assert.equal(typeof message, 'string', `invalid ${id} translation: ${key}`);
        assert(message.trim(), `empty ${id} translation: ${key}`);
        assert.deepEqual(placeholders(message), placeholders(key), `wrong ${id} placeholders: ${key}`);
        assert.deepEqual(placeholders(locale.create(id).t(key, args)), [], `unresolved ${id} placeholders: ${key}`);
    }
}
const aliases = {
    zh: 'zh-CN', 'zh-Hant': 'zh-TW', 'zh-HK': 'zh-TW', 'zh_Hans_SG': 'zh-CN',
    'en-GB': 'en', 'pt-PT': 'pt-BR', 'es-MX': 'es', 'ja-JP': 'ja'
};
for (const [input, id] of Object.entries(aliases)) assert.equal(locale.normalize(input), id);
for (const input of [null, '', 'unknown', 'fr', '__proto__', '<img>']) {
    assert.equal(locale.normalize(input), null);
    assert.equal(locale.create(input).locale, 'zh-CN');
}
assert.equal(locale.create('en').t('选择 {name}', { name: '$& {other}.wav' }), 'Select $& {other}.wav');
assert.equal(locale.create('es').t('结果：{count}', { count: 12345 }), 'Resultados: 12.345');
assert.equal(locale.create('pt-BR').number(1.5), '1,5');

const han = /\p{Script=Han}/u;
for (const file of ['index.html', 'splash.html']) {
    const html = read(file).replace(/<!--[\s\S]*?-->|<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, '');
    for (const match of html.matchAll(/>([^<>]+)</g)) {
        const label = match[1].trim();
        if (han.test(label)) assert(keys.has(label), `${file}: static label missing: ${label}`);
    }
    for (const match of html.matchAll(/(?:title|placeholder|aria-label|alt)="([^"]+)"/g)) {
        const label = match[1].trim();
        if (han.test(label)) assert(keys.has(label), `${file}: accessibility label missing: ${label}`);
    }
}
for (const file of ['main.js', 'assets/i18n.js', ...fs.readdirSync(path.join(root, 'assets/renderer')).map(f => `assets/renderer/${f}`)]) {
    for (const [, key] of read(file).matchAll(/\bt\('([^'\n]+)'/g)) {
        if (han.test(key)) assert(keys.has(key), `${file}: dynamic label missing: ${key}`);
    }
}

// Exercise the same two scripts as the isolated renderer and splash page.
const browser = vm.createContext({});
for (const file of ['assets/locale-messages.js', 'assets/locale.js']) vm.runInContext(read(file), browser, { filename: file });
assert.equal(browser.SoundBotLocale.create('ja').t('文件夹'), 'フォルダー');
assert.equal(browser.SoundBotLocale.create('es').t('退出应用'), 'Salir de la aplicación');
console.log(`Localization checked: ${messages.length} messages across ${locales.length} languages.`);
