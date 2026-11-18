/* Native source playback and a bounded PCM preview queue shared by both editors. */
(() => {
    const audio = new Audio();
    let context;
    let mediaInput;
    let signal;
    let master;
    let splitter;
    let analysisBus;
    let loudnessNode;
    let meterReady;
    let meterEpoch = 0;
    let measuredEpoch = null;
    let meterValues = {};
    let meterHoldUntil = 0;
    let reportedOverload = 0;
    let dismissedOverload = 0;
    const peakWarningUntil = [0, 0];
    const meterHoldMs = 60000;
    const peakWarningMs = 2000;
    let analyzers = [];
    let samples;
    let spectra;
    let session;
    let volume = 0.8;
    let muted = false;
    let nodes = new Set();

    function renderedPeaks(nominalEnd, nominalStart = 0) {
        // Reserve the tail separately. A short clip needs the same detail as
        // its source waveform even if an effect can add 30 seconds of silence.
        const sourceBins = 12000;
        const tailStep = 0.001;
        const step = Math.max(1 / 192000, (nominalEnd - nominalStart) / sourceBins);
        const peaks = new Float32Array(sourceBins + 30002).fill(-1);
        const indexAt = time => time >= nominalEnd
            ? sourceBins + Math.floor((time - nominalEnd) / tailStep + 1e-7)
            : Math.max(0, Math.min(sourceBins - 1, Math.floor((time - nominalStart) / step + 1e-7)));
        const bucketEnd = index => index < sourceBins
            ? Math.min(nominalEnd, nominalStart + (index + 1) * step)
            : nominalEnd + (index - sourceBins + 1) * tailStep;
        let duration = 0;
        let revision = 0;
        return {
            get duration() { return duration; },
            get revision() { return revision; },
            truncate(time) {
                peaks.fill(-1, Math.min(peaks.length, indexAt(time)));
                duration = Math.min(duration, time);
                revision++;
            },
            append(buffer, offset) {
                const channels = Array.from({ length: buffer.numberOfChannels }, (_, index) => buffer.getChannelData(index));
                for (let frame = 0; frame < buffer.length;) {
                    const index = indexAt(offset + frame / buffer.sampleRate);
                    if (index >= peaks.length) break;
                    const boundary = Math.ceil((bucketEnd(index) - offset) * buffer.sampleRate - 1e-7);
                    const end = Math.min(buffer.length, Math.max(frame + 1, boundary));
                    let peak = Math.max(0, peaks[index]);
                    for (const channel of channels) {
                        for (let i = frame; i < end; i++) peak = Math.max(peak, Math.abs(channel[i]));
                    }
                    peaks[index] = peak;
                    frame = end;
                }
                duration = offset + buffer.duration;
                revision++;
            },
            peakBetween(start, end) {
                if (end <= nominalStart || start >= duration) return -1;
                const first = indexAt(start);
                const last = indexAt(Math.max(start, Math.min(duration, end) - 1e-9));
                let peak = -1;
                for (let i = first; i <= Math.min(last, peaks.length - 1); i++) peak = Math.max(peak, peaks[i]);
                return peak;
            }
        };
    }

    function stop() {
        if (session && measuredEpoch === meterEpoch) {
            meterHoldUntil = performance.now() + meterHoldMs;
            loudnessNode.port.postMessage({ command: 'stop', epoch: measuredEpoch });
        }
        session?.abort.abort();
        session = null;
        meterEpoch++;
        audio.pause();
        mediaInput?.disconnect();
        if (audio.hasAttribute('src')) {
            audio.removeAttribute('src');
            audio.load();
        }
        for (const node of nodes) {
            node.onended = null;
            node.stop();
            node.disconnect();
        }
        nodes.clear();
    }

    function resetMeterReading() {
        measuredEpoch = null;
        meterValues = {};
        meterHoldUntil = reportedOverload = dismissedOverload = 0;
        peakWarningUntil.fill(0);
    }

    function startMeter() {
        if (!loudnessNode) return;
        measuredEpoch = meterEpoch;
        loudnessNode.port.postMessage({ command: 'start', epoch: measuredEpoch });
    }

    function acceptMeterReading({ epoch, overload, momentary, integrated, range, truePeak }) {
        if (epoch !== measuredEpoch) return;
        meterValues = { momentary, integrated, range, truePeak };
        reportedOverload = overload;
        dismissedOverload &= overload;
        const limit = performance.now() + peakWarningMs;
        for (let channel = 0; channel < peakWarningUntil.length; channel++) {
            if ((overload & ~dismissedOverload) & (1 << channel)) peakWarningUntil[channel] = limit;
        }
    }

    function meterReading() {
        const now = performance.now();
        const clipping = (now < peakWarningUntil[0] ? 1 : 0) | (now < peakWarningUntil[1] ? 2 : 0);
        const visible = session || now < meterHoldUntil;
        return { values: visible ? meterValues : {}, clipping: visible ? clipping : 0 };
    }

    function clearPeakWarning() {
        dismissedOverload = reportedOverload;
        peakWarningUntil.fill(0);
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
        signal = context.createGain();
        master = context.createGain();
        signal.connect(master);
        master.connect(context.destination);
        audio.crossOrigin = 'anonymous';
        mediaInput = context.createMediaElementSource(audio);
        analysisBus = context.createGain();
        analysisBus.channelCount = 2;
        analysisBus.channelCountMode = 'explicit';
        signal.connect(analysisBus);
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
        meterReady = context.audioWorklet.addModule(new URL('loudness-meter.js', scriptURL)).then(() => {
            loudnessNode = new AudioWorkletNode(context, 'soundbot-loudness', {
                numberOfOutputs: 0, channelCount: 2, channelCountMode: 'clamped-max'
            });
            loudnessNode.port.onmessage = ({ data }) => acceptMeterReading(data);
            signal.connect(loudnessNode);
        }).catch(error => console.warn('SoundBot loudness meter:', error.message));
        setVolume(volume, muted);
    }

    function levels() {
        if (!session || !samples) return [0, 0];
        return analyzers.map(analyser => {
            analyser.getFloatTimeDomainData(samples);
            let peak = 0;
            for (const value of samples) peak = Math.max(peak, Math.abs(value));
            return peak;
        });
    }

    function spectrum() {
        if (!session || !spectra) return null;
        for (const [index, analyser] of analyzers.entries()) analyser.getFloatFrequencyData(spectra[index]);
        return { channels: spectra, hzPerBin: context.sampleRate / analyzers[0].fftSize };
    }

    async function playSource(url, offset, onEnd) {
        stop();
        resetMeterReading();
        const active = { abort: new AbortController(), clock: () => audio.currentTime };
        session = active;
        try {
            ensureContext();
            await Promise.all([context.resume(), meterReady]);
            if (session !== active) return;
            startMeter();
            mediaInput.connect(signal);
            audio.src = url;
            if (offset > 0) audio.currentTime = offset;
            audio.addEventListener('ended', () => {
                if (session === active) { stop(); onEnd(); }
            }, { once: true, signal: active.abort.signal });
            await audio.play();
        } catch (error) {
            if (session === active) stop();
            throw error;
        }
    }

    async function playPCM(url, options, offset, onEnd, onStream, onRendered) {
        stop();
        resetMeterReading();
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
                    if (origin === undefined) {
                        await meterReady;
                        if (session !== active) break;
                        startMeter();
                    }
                    const node = context.createBufferSource();
                    node.buffer = buffer;
                    node.connect(signal);
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
        meterReading, resetMeterReading, clearPeakWarning,
        get generation() { return meterEpoch; },
        get active() { return !!session; } };
})();
