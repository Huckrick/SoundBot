# Release audio fixtures

These files are deterministic, 0.35-second, 440 Hz stereo fixtures at 48 kHz.
They were generated from one PCM WAV with FFmpeg 8.0.1, then encoded as:

- WAV/AIF/AIFF: PCM 16-bit
- MP3: `libmp3lame`, 96 kbit/s
- FLAC: native FLAC
- OGG: native Vorbis (`-strict -2`)
- M4A/AAC: native AAC, 96 kbit/s
- WMA: `wmav2`, 96 kbit/s

`manifest.json` pins every binary SHA-256. Release CI verifies those hashes,
copies the files under a Unicode path containing spaces and `%_#+()`, and asks
the frozen backend—not a system FFmpeg—to decode all nine supported suffixes.
