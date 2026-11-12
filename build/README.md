# Native assets and release targets

[SoundBot SVG](../assets/soundbot.svg) is the icon source. On macOS, regenerate the checked-in PNG, ICNS, and ICO with Python 3.12, Pillow, and the Swift toolchain.

```bash
python scripts/generate_icons.py
```

The release workflow builds macOS arm64 and Windows x64 on their respective native hosts. The frozen backend includes FTS5, the PyAV audio runtime, and the VST3 host. See the [project README](../README.md) for the current build and verification commands.
