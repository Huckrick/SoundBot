const { constants } = require('node:fs');
const { copyFile } = require('node:fs/promises');
const { createRequire } = require('node:module');
const path = require('node:path');

const builderRequire = createRequire(require.resolve('app-builder-lib/package.json'));
const { signAsync } = builderRequire('@electron/osx-sign');

module.exports = async function sign(options) {
    const backend = path.join(options.app, 'Contents', 'Resources', 'backend', 'soundbot-backend');
    const worker = path.join(backend, 'soundbot-vst-worker');
    const entitlements = path.resolve(__dirname, '../build/entitlements.vst-worker.mac.plist');

    // Both entry points use the same frozen Python runtime and libraries.
    // Per-file signing grants the compatibility exception only to the VST host.
    await copyFile(path.join(backend, 'soundbot-backend'), worker, constants.COPYFILE_EXCL);
    await signAsync({
        ...options,
        optionsForFile(file) {
            const standard = options.optionsForFile(file);
            return file === worker ? { ...standard, entitlements } : standard;
        }
    });
};
