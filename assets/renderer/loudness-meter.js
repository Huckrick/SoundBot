/* Live mono/stereo metering using BS.1770 gating and EBU Tech 3342 LRA.
 * The distribution notice records the K-weighting filter attribution.
 */
const absoluteGate = -70;
const binSize = 0.1;
const binCount = 1001;
const peakTaps = 12;
const peakHistoryMask = 15;
// Processed previews encode unity at the largest signed 16-bit sample.
const fullScalePCM16 = 32767 / 32768;
const absoluteGatePower = 10 ** ((absoluteGate + 0.691) / 10);
const toLufs = energy => -0.691 + 10 * Math.log10(energy);
const histogramBin = lufs => Math.min(binCount - 1, Math.max(0, Math.floor((lufs - absoluteGate) / binSize)));

class LoudnessHistogram {
    constructor(relativeGate) {
        this.relativeGate = relativeGate;
        this.counts = new Float64Array(binCount);
        this.powers = new Float64Array(binCount);
        this.reset();
    }

    reset() {
        this.counts.fill(0);
        this.powers.fill(0);
        this.count = this.power = 0;
    }

    add(power) {
        if (!(power >= absoluteGatePower)) return;
        const bin = histogramBin(toLufs(power));
        this.counts[bin]++;
        this.powers[bin] += power;
        this.count++;
        this.power += power;
    }

    gated(profile) {
        if (!this.count) return null;
        const first = histogramBin(toLufs(this.power / this.count) + this.relativeGate);
        let count = 0;
        let power = 0;
        for (let bin = first; bin < binCount; bin++) {
            count += this.counts[bin];
            power += this.powers[bin];
        }
        if (!profile) return toLufs(power / count);
        const lower = Math.round((count - 1) * 0.10);
        const upper = Math.round((count - 1) * 0.95);
        let cumulative = 0;
        let low;
        for (let bin = first; bin < binCount; bin++) {
            cumulative += this.counts[bin];
            if (low === undefined && cumulative > lower) low = bin;
            if (cumulative > upper) return (bin - low) * binSize;
        }
        return null;
    }
}

function interpolationPhases(factor) {
    // Windowed sinc interpolates between original samples. Their unfiltered
    // amplitudes are also included in the maximum.
    return Array.from({ length: factor - 1 }, (_, phase) => {
        const fraction = (phase + 1) / factor;
        const coefficients = Float64Array.from({ length: peakTaps }, (_, tap) => {
            const x = tap - (peakTaps / 2 - 1) - fraction;
            const window = 0.5 + 0.5 * Math.cos(Math.PI * x / (peakTaps / 2));
            return window * Math.sin(Math.PI * x) / (Math.PI * x);
        });
        const sum = coefficients.reduce((total, value) => total + value);
        return coefficients.map(value => value / sum);
    });
}

class SoundBotLoudnessMeter extends AudioWorkletProcessor {
    constructor() {
        super();
        const shelf = Math.tan(Math.PI * 1681.974450955533 / sampleRate);
        const shelfQ = 0.7071752369554196;
        const highGain = 10 ** (3.999843853973347 / 20);
        const midGain = highGain ** 0.4996667741545416;
        const shelfA = 1 + shelf / shelfQ + shelf ** 2;
        this.shelf = [
            (highGain + midGain * shelf / shelfQ + shelf ** 2) / shelfA,
            2 * (shelf ** 2 - highGain) / shelfA,
            (highGain - midGain * shelf / shelfQ + shelf ** 2) / shelfA,
            2 * (shelf ** 2 - 1) / shelfA,
            (1 - shelf / shelfQ + shelf ** 2) / shelfA
        ];
        const cut = Math.tan(Math.PI * 38.13547087602444 / sampleRate);
        const cutQ = 0.5003270373238773;
        const cutA = 1 + cut / cutQ + cut ** 2;
        this.cut = [1, -2, 1, 2 * (cut ** 2 - 1) / cutA, (1 - cut / cutQ + cut ** 2) / cutA];
        this.slice = Math.round(sampleRate / 10);
        this.phases = interpolationPhases(sampleRate < 96000 ? 4 : 2);
        this.filters = Array.from({ length: 2 }, () => [new Float64Array(2), new Float64Array(2)]);
        this.peakHistory = Array.from({ length: 2 }, () => new Float64Array(peakHistoryMask + 1));
        this.history = new Float64Array(30);
        this.integrated = new LoudnessHistogram(-10);
        this.range = new LoudnessHistogram(-20);
        this.active = false;
        this.port.onmessage = ({ data }) => {
            if (data.command === 'start') this.reset(data.epoch);
            if (data.command === 'stop' && this.active && this.epoch === data.epoch) this.finish();
        };
    }

    reset(epoch) {
        this.epoch = epoch;
        this.filters.forEach(stages => stages.forEach(delay => delay.fill(0)));
        this.peakHistory.forEach(history => history.fill(0));
        this.history.fill(0);
        this.integrated.reset();
        this.range.reset();
        this.index = this.count = this.energy = this.samples = this.shortPower = 0;
        this.peakIndex = this.maximum = this.overload = this.channels = 0;
        this.momentary = this.lastMomentary = null;
        this.active = true;
    }

    biquad(sample, coeffs, delay) {
        const output = coeffs[0] * sample + delay[0];
        delay[0] = coeffs[1] * sample - coeffs[3] * output + delay[1];
        delay[1] = coeffs[2] * sample - coeffs[4] * output;
        return output;
    }

    measurePeak(channel, value) {
        const history = this.peakHistory[channel];
        history[this.peakIndex] = value;
        const samplePeak = Math.abs(value);
        let peak = samplePeak;
        for (const coefficients of this.phases) {
            let interpolated = 0;
            for (let tap = 0; tap < peakTaps; tap++) {
                interpolated += history[(this.peakIndex - tap) & peakHistoryMask] * coefficients[tap];
            }
            peak = Math.max(peak, Math.abs(interpolated));
        }
        this.maximum = Math.max(this.maximum, peak);
        if (peak >= 1 || samplePeak >= fullScalePCM16) this.overload |= 1 << channel;
    }

    publish(final = false) {
        this.port.postMessage({
            epoch: this.epoch,
            momentary: final ? this.lastMomentary : this.momentary,
            integrated: this.integrated.gated(false),
            range: this.range.gated(true),
            truePeak: this.maximum > 0 ? 20 * Math.log10(this.maximum) : null,
            overload: this.channels === 1 && this.overload ? 3 : this.overload
        });
        this.overload = 0;
    }

    finish() {
        // Read the last interpolated peaks without adding silence to loudness.
        for (let frame = 0; frame < peakTaps; frame++) {
            for (let ch = 0; ch < this.channels; ch++) this.measurePeak(ch, 0);
            this.peakIndex = (this.peakIndex + 1) & peakHistoryMask;
        }
        this.publish(true);
        this.active = false;
    }

    process([input]) {
        if (!this.active || !input.length) return true;
        this.channels = Math.min(2, input.length);
        for (let frame = 0; frame < input[0].length; frame++) {
            for (let ch = 0; ch < this.channels; ch++) {
                const [shelf, cut] = this.filters[ch];
                const value = input[ch][frame];
                const weighted = this.biquad(this.biquad(value, this.shelf, shelf), this.cut, cut);
                this.energy += weighted * weighted;
                this.measurePeak(ch, value);
            }
            this.peakIndex = (this.peakIndex + 1) & peakHistoryMask;
            if (++this.samples !== this.slice) continue;
            const power = this.energy / this.slice;
            this.shortPower += power - this.history[this.index];
            this.history[this.index] = power;
            this.index = (this.index + 1) % this.history.length;
            this.count = Math.min(this.count + 1, this.history.length);
            if (this.count >= 4) {
                let momentPower = 0;
                for (let i = 1; i <= 4; i++) {
                    momentPower += this.history[(this.index + this.history.length - i) % this.history.length];
                }
                momentPower /= 4;
                this.momentary = momentPower >= 1e-12 ? toLufs(momentPower) : null;
                if (this.momentary !== null) this.lastMomentary = this.momentary;
                this.integrated.add(momentPower);
            }
            if (this.count === this.history.length) this.range.add(this.shortPower / this.history.length);
            this.publish();
            this.samples = this.energy = 0;
        }
        return true;
    }
}

registerProcessor('soundbot-loudness', SoundBotLoudnessMeter);
