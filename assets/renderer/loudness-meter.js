/* Momentary stereo loudness with sample-rate-adjusted K-weighting.
 * The distribution notice records the filter calculation attribution.
 */
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
        this.port.onmessage = ({ data }) => this.reset(data);
        this.reset(0);
    }

    reset(epoch) {
        this.epoch = epoch;
        this.filters = Array.from({ length: 2 }, () => [new Float64Array(2), new Float64Array(2)]);
        this.history = new Float64Array(4);
        this.index = this.count = this.energy = this.samples = 0;
    }

    biquad(sample, coeffs, delay) {
        const output = coeffs[0] * sample + delay[0];
        delay[0] = coeffs[1] * sample - coeffs[3] * output + delay[1];
        delay[1] = coeffs[2] * sample - coeffs[4] * output;
        return output;
    }

    process([input]) {
        if (!input.length) return true;
        for (let frame = 0; frame < input[0].length; frame++) {
            for (let ch = 0; ch < Math.min(2, input.length); ch++) {
                const [shelf, cut] = this.filters[ch];
                const weighted = this.biquad(this.biquad(input[ch][frame], this.shelf, shelf), this.cut, cut);
                this.energy += weighted * weighted;
            }
            if (++this.samples !== this.slice) continue;
            this.history[this.index] = this.energy;
            this.index = (this.index + 1) % this.history.length;
            this.count = Math.min(this.count + 1, this.history.length);
            const mean = this.history.reduce((sum, value) => sum + value, 0) / (this.count * this.slice);
            this.port.postMessage({ epoch: this.epoch, lufs: mean > 1e-12 ? -0.691 + 10 * Math.log10(mean) : null });
            this.samples = this.energy = 0;
        }
        return true;
    }
}

registerProcessor('soundbot-loudness', SoundBotLoudnessMeter);
