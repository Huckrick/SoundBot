/* Native source playback and a bounded PCM preview queue shared by both editors. */
(() => {
    const audio = new Audio();
    let context;
    let master;
    let splitter;
    let analysisBus;
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

    function renderedPeaks(nominalEnd) {
        // Fixed time buckets keep samples aligned while bounding long previews.
        const step = Math.max(0.01, (nominalEnd + 30) / 12000);
        const peaks = new Float32Array(12002).fill(-1);
        let duration = 0;
        return {
            get duration() { return duration; },
            append(buffer, offset) {
                const bins = Array.from({ length: buffer.numberOfChannels }, (_, index) => buffer.getChannelData(index));
                let bin = Math.floor(offset / step + 1e-7);
                let end = Math.ceil(((bin + 1) * step - offset) * buffer.sampleRate);
                let peak = Math.max(0, peaks[bin]);
                for (let i = 0; i < buffer.length; i++) {
                    if (i >= end) {
                        peaks[bin++] = peak;
                        end = Math.ceil(((bin + 1) * step - offset) * buffer.sampleRate);
                        peak = Math.max(0, peaks[bin]);
                    }
                    for (const channel of bins) peak = Math.max(peak, Math.abs(channel[i]));
                }
                peaks[bin] = peak;
                duration = offset + buffer.duration;
            },
            peakBetween(start, end) {
                const first = Math.floor(start / step + 1e-7);
                const last = Math.max(first, Math.ceil(end / step - 1e-7) - 1);
                let peak = -1;
                for (let i = first; i <= Math.min(last, peaks.length - 1); i++) peak = Math.max(peak, peaks[i]);
                return peak;
            }
        };
    }

    function stop() {
        session?.abort.abort();
        session = null;
        momentary = null;
        meterEpoch++;
        loudnessNode?.port.postMessage(meterEpoch);
        audio.pause();
        if (audio.hasAttribute('src')) {
            audio.removeAttribute('src');
            audio.load();
        }
        for (const node of nodes) node.stop();
        nodes.clear();
    }

    function setVolume(value, silent) {
        volume = value;
        muted = silent;
        if (master) master.gain.value = muted ? 0 : volume;
    }

    function currentTime() {
        return session?.clock?.() ?? 0;
    }

    function ensureContext() {
        if (context) return;
        context = new AudioContext();
        master = context.createGain();
        master.connect(context.destination);
        audio.crossOrigin = 'anonymous';
        context.createMediaElementSource(audio).connect(master);
        analysisBus = context.createGain();
        analysisBus.channelCount = 2;
        analysisBus.channelCountMode = 'explicit';
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
        if (!session || !samples || muted) return [0, 0];
        return analyzers.map(analyser => {
            analyser.getFloatTimeDomainData(samples);
            let peak = 0;
            for (const value of samples) peak = Math.max(peak, Math.abs(value));
            return peak;
        });
    }

    function spectrum() {
        if (!session || !spectra || muted) return null;
        for (const [index, analyser] of analyzers.entries()) analyser.getFloatFrequencyData(spectra[index]);
        return { channels: spectra, hzPerBin: context.sampleRate / analyzers[0].fftSize };
    }

    async function playSource(url, offset, onEnd) {
        stop();
        const active = { abort: new AbortController(), clock: () => audio.currentTime };
        session = active;
        try {
            ensureContext();
            const resumed = context.resume();
            audio.src = url;
            if (offset > 0) audio.currentTime = offset;
            audio.addEventListener('ended', () => {
                if (session === active) { stop(); onEnd(); }
            }, { once: true, signal: active.abort.signal });
            await Promise.all([resumed, audio.play()]);
        } catch (error) {
            if (session === active) stop();
            throw error;
        }
    }

    async function playPCM(url, options, offset, onEnd, onStream, onRendered) {
        stop();
        const active = { abort: new AbortController(), clock: () => offset };
        session = active;
        try {
            ensureContext();
            await context.resume();
            const response = await fetch(url, { ...options, signal: active.abort.signal });
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                const detail = typeof error.detail === 'string' ? error.detail : error.detail?.message;
                throw new Error(detail || error.error?.message || error.message || '无法读取音频预览');
            }
            onStream?.();
            const reader = response.body.getReader();
            let remaining = new Uint8Array();
            let channels, rate;
            const pcm = response.headers.get('X-SoundBot-PCM');
            if (pcm) {
                [rate, channels] = pcm.split('/').map(Number);
                if (!Number.isInteger(rate) || rate < 8000 || rate > 384000 ||
                    !Number.isInteger(channels) || channels < 1 || channels > 32) throw new Error('音频预览格式无效');
            }
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
                    if (pcm) onRendered?.(buffer, offset + receivedBytes / (channels * 2 * rate));
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
                if (!active.abort.signal.aborted && (!receivedBytes || remaining.length ||
                    (!pcm && expectedBytes !== receivedBytes))) {
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
    window.SoundBotPlayback = { playSource, playPCM, setVolume, currentTime, levels, spectrum, stop, renderedPeaks,
        get generation() { return meterEpoch; },
        get momentary() { return session && !muted ? momentary : null; },
        get active() { return !!session; } };
})();
