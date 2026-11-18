# SoundBot

[日本語](README.ja.md) · [简体中文](README.md#简体中文) · [English](README.md#english)

Huckrick’s free, open-source sound library manager for game audio, with offline keyword search, waveform editing, Wwise writeback, and WAV export.

The current source version is **v1.0.16 (stable)**. The latest stable release lists the published Windows and macOS installers and their SHA-256 checksums. [Download the latest stable release](https://github.com/Huckrick/SoundBot/releases/latest) · [Historical releases](docs/release-history.md)

SoundBot is a local sound library, waveform editor, and middleware workspace for macOS Apple Silicon and Windows x64. The main README covers installation, workflow, permissions, and troubleshooting in English. [English user guide](README.md#english) · [Changelog](CHANGELOG.md)

The interface supports Simplified Chinese, Traditional Chinese, Japanese, English, Brazilian Portuguese, and Spanish. Change the language in **Settings → General → Language**. [Interface language details](README.md#interface-languages)

## Library management

Import files or source folders in place. Look up audio by filename, description, tags, or UCS category and narrow the results by folder, format, length, or channel count. Use groups and favorites for project selections, and use the inspector to check audio properties, file tags, and artwork. Right-click **Remove folder** to hide a shared folder entry or unimport an exclusive folder. The original media stays on disk. In **Settings → Library management**, restore hidden entries, check or relocate sources, refresh metadata, or rebuild the current keyword index. [Full library guide](README.md#manage-a-sound-library)

In the waveform editor, drag **Drag to DAW** to deliver a whole sound or selection. Keep holding the mouse button if the WAV takes a moment to render. SoundBot remembers the media folder and asks whether to change it at startup. [Release history](CHANGELOG.md)

## Wwise workspace

Connect to an open Wwise project over local WAAPI, select an AudioFileSource under Sound SFX, and check its target format in the inspector. Drag a sound or selection from the library dock to another track to layer it with the source. Use trim, snapping, gain, fades, crossfades, stereo pan, and per-track effects, then compare the result with mute or solo. Supported targets are writable mono or stereo 16/24-bit PCM WAVs in the project's `Originals` folder. SoundBot backs up the original, replaces it at the same path, and verifies the Wwise binding. Confirm playback and conversion in Wwise. Use the Cleanup Center to restore a backup. [Connection, writeback checks, and recovery](docs/middleware.md#english)

**FMOD Studio and CRI Atom Craft are experimental and untested in the actual hosts.** The implemented path browses an asset folder and exports a new WAV. Import or registration in the host awaits real-project feedback. Please include your OS, host version, and the step you tested. [Experimental workflows](docs/middleware.md#english) · [Discussions](https://github.com/Huckrick/SoundBot/discussions)

## Effects and VST3

The VST panel measures the processed audition before monitor volume. It shows Momentary, Integrated, LRA and True Peak for 60 seconds after playback stops. Click the red peak warning to clear it, or let it clear when the signal recovers. The library also shows the selected source file's Integrated, LRA and True Peak for comparison.

Use the built-in parametric EQ, compressor, reverb, and loudness tools, or select a plug-in discovered in your system's VST3 folders. Group the picker by developer or effect type. SoundBot skips declared instruments and generators and lists plug-ins without type metadata under Unclassified plug-ins. Open the plug-in's editor by clicking its name. Reorder, bypass, or remove it in the rack. The library shares a master chain across sounds and renders it into DAW delivery. In the workspace, settings belong to individual tracks and reset after successful delivery. SoundBot shows the rendered waveform and preview tails. An effect error during audition can bypass the affected plug-in. A render error stops delivery and keeps your edits. [Effects guide](README.md#built-in-effects-and-vst3)

## Support and feedback

Issues and discussions are welcome, handled as time allows. Pull requests and code submissions are not accepted, to keep the release pipeline safe. Do not attach keys, audio, databases, raw logs, or private paths in public. [Open an issue](https://github.com/Huckrick/SoundBot/issues/new/choose) · [Discussions](https://github.com/Huckrick/SoundBot/discussions) · [Repository policy](.github/CONTRIBUTING.md)

Maintained by **Huckrick** under GPL-3.0-or-later. [Credits and sources](docs/credits.md) · [License](LICENSE) · [Build and release](docs/build-and-retrieval.md) · [Security policy](.github/SECURITY.md)
