/* Native source playback and a bounded PCM preview queue shared by both editors. */
(() => {
    const audio = new Audio();
    let context;
    let master;
    let splitter;
    let nativeMeter;
    let analysisBus;
    let analysisGain;
    let loudnessNode;
    let meterEpoch = 0;
    let momentary = null;
    let analyzers = [];
    let samples;
    let spectra;
    let session;
    let volume = 0.8;
    let muted = false;
    let nodes = new Set();

    function stop() {
        session?.abort.abort();
        session = null;
        nativeMeter?.disconnect();
        nativeMeter = null;
        momentary = null;
        meterEpoch++;
        loudnessNode?.port.postMessage(meterEpoch);
        audio.pause();
        audio.removeAttribute('src');
        for (const node of nodes) node.stop();
        nodes.clear();
    }

    function setVolume(value, silent) {
        volume = value;
        muted = silent;
        audio.volume = muted ? 0 : volume;
        if (master) master.gain.value = audio.volume;
        if (analysisGain) analysisGain.gain.value = audio.volume;
    }

    function currentTime() {
        return session?.clock?.() ?? 0;
    }

    function ensureContext() {
        if (context) return;
        context = new AudioContext();
        master = context.createGain();
        master.connect(context.destination);
        analysisBus = context.createGain();
        analysisBus.channelCount = 2;
        analysisBus.channelCountMode = 'explicit';
        analysisBus.channelInterpretation = 'discrete';
        analysisGain = context.createGain();
        analysisGain.connect(analysisBus);
        master.connect(analysisBus);
        splitter = context.createChannelSplitter(2);
        analysisBus.connect(splitter);
        analyzers = [0, 1].map(channel => {
            const analyser = context.createAnalyser();
            analyser.fftSize = 4096;
            analyser.smoothingTimeConstant = 0.65;
            splitter.connect(analyser, channel);
            return analyser;
        });
        samples = new Float32Array(analyzers[0].fftSize);
        spectra = analyzers.map(node => new Float32Array(node.frequencyBinCount));
        context.audioWorklet.addModule(new URL('loudness-meter.js', scriptURL)).then(() => {
            loudnessNode = new AudioWorkletNode(context, 'soundbot-loudness', { numberOfOutputs: 0 });
            loudnessNode.port.onmessage = ({ data }) => {
                if (data.epoch === meterEpoch) momentary = data.lufs;
            };
            loudnessNode.port.postMessage(meterEpoch);
            analysisBus.connect(loudnessNode);
        }).catch(error => console.warn('SoundBot loudness meter:', error.message));
        setVolume(volume, muted);
    }

    function levels() {
        if (!session || !samples || (session.native && !nativeMeter) || muted) return [0, 0];
        return analyzers.map(analyser => {
            analyser.getFloatTimeDomainData(samples);
            let peak = 0;
            for (const value of samples) peak = Math.max(peak, Math.abs(value));
            return peak;
        });
    }

    function spectrum() {
        if (!session || !spectra || (session.native && !nativeMeter) || muted) return null;
        for (const [index, analyser] of analyzers.entries()) analyser.getFloatFrequencyData(spectra[index]);
        return { channels: spectra, hzPerBin: context.sampleRate / analyzers[0].fftSize };
    }

    async function monitorNative(active) {
        try {
            const stream = audio.captureStream();
            const attach = async () => {
                if (session !== active || !stream.getAudioTracks().length) return;
                ensureContext();
                await context.resume();
                if (session !== active || nativeMeter) return;
                nativeMeter = context.createMediaStreamSource(stream);
                nativeMeter.connect(analysisGain);
            };
            stream.addEventListener('addtrack', () => attach().catch(() => {}), { signal: active.abort.signal });
            await attach();
        } catch {
            // Native decoding and audio output continue if capture is unavailable.
        }
    }

    async function playSource(url, offset, onEnd) {
        stop();
        const active = { abort: new AbortController(), clock: () => audio.currentTime, native: true };
        session = active;
        audio.src = url;
        audio.volume = muted ? 0 : volume;
        if (offset > 0) audio.currentTime = offset;
        audio.addEventListener('ended', () => {
            if (session === active) { stop(); onEnd(); }
        }, { once: true, signal: active.abort.signal });
        try {
            await audio.play();
            if (session === active) void monitorNative(active);
        } catch (error) {
            if (session === active) stop();
            throw error;
        }
    }

    async function playPCM(url, options, offset, onEnd) {
        stop();
        const active = { abort: new AbortController(), clock: () => offset };
        session = active;
        try {
            ensureContext();
            await context.resume();
            const response = await fetch(url, { ...options, signal: active.abort.signal });
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.detail || error.error?.message || '无法读取音频预览');
            }
            const reader = response.body.getReader();
            let remaining = new Uint8Array();
            let channels, rate;
            let scheduled = context.currentTime;
            let origin;
            let expectedBytes;
            let receivedBytes = 0;
            try {
                while (!active.abort.signal.aborted) {
                    while (scheduled - context.currentTime > 0.4 && !active.abort.signal.aborted) {
                        await new Promise(resolve => setTimeout(resolve, 30));
                    }
                    const { value, done } = await reader.read();
                    if (done || active.abort.signal.aborted) break;
                    const combined = new Uint8Array(remaining.length + value.length);
                    combined.set(remaining);
                    combined.set(value, remaining.length);
                    remaining = combined;
                    if (!channels) {
                        if (remaining.length < 44) continue;
                        const header = new DataView(remaining.buffer);
                        channels = header.getUint16(22, true);
                        rate = header.getUint32(24, true);
                        expectedBytes = header.getUint32(40, true);
                        if (header.getUint32(0, false) !== 0x52494646 || !channels || channels > 32 ||
                            !rate || header.getUint16(34, true) !== 16) throw new Error('音频预览格式无效');
                        remaining = remaining.slice(44);
                    }
                    const frames = Math.floor(remaining.length / (channels * 2));
                    if (!frames) continue;
                    const consumed = frames * channels * 2;
                    const samples = new DataView(remaining.buffer, remaining.byteOffset, consumed);
                    const buffer = context.createBuffer(channels, frames, rate);
                    for (let channel = 0; channel < channels; channel++) {
                        const data = buffer.getChannelData(channel);
                        for (let frame = 0; frame < frames; frame++) {
                            data[frame] = samples.getInt16((frame * channels + channel) * 2, true) / 32768;
                        }
                    }
                    remaining = remaining.slice(consumed);
                    receivedBytes += consumed;
                    const node = context.createBufferSource();
                    node.buffer = buffer;
                    node.connect(master);
                    nodes.add(node);
                    node.onended = () => { nodes.delete(node); node.disconnect(); };
                    scheduled = Math.max(scheduled, context.currentTime + 0.015);
                    if (origin === undefined) {
                        origin = scheduled;
                        active.clock = () => offset + Math.max(0, Math.min(scheduled, context.currentTime) - origin);
                    }
                    node.start(scheduled);
                    scheduled += frames / rate;
                }
                if (!active.abort.signal.aborted && (!expectedBytes || expectedBytes !== receivedBytes)) {
                    throw new Error('音频预览中断');
                }
            } finally {
                await reader.cancel().catch(() => {});
            }
            while (session === active && context.currentTime < scheduled) {
                await new Promise(resolve => setTimeout(resolve, 25));
            }
            if (session === active) { stop(); onEnd(); }
        } catch (error) {
            if (active.abort.signal.aborted) return;
            stop();
            throw error;
        }
    }

    const scriptURL = document.currentScript.src;
    window.SoundBotPlayback = { playSource, playPCM, setVolume, currentTime, levels, spectrum, stop,
        get generation() { return meterEpoch; },
        get momentary() { return session && !muted ? momentary : null; },
        get active() { return !!session; } };
})();
