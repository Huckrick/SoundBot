#!/usr/bin/env python3
"""Generate the application and native drag icons from the tracked SVG on macOS."""

import io
import os
import subprocess
import tempfile
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "soundbot.svg"
SVG_NS = "http://www.w3.org/2000/svg"
SWIFT = """
import AppKit
import Foundation

guard let image = NSImage(contentsOfFile: CommandLine.arguments[1]),
      let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let png = bitmap.representation(using: .png, properties: [:]) else {
    exit(1)
}
FileHandle.standardOutput.write(png)
"""


def rasterize(source, scratch):
    env = {**os.environ, "SWIFT_MODULECACHE_PATH": scratch, "CLANG_MODULE_CACHE_PATH": scratch}
    png = subprocess.run(["swift", "-e", SWIFT, str(source)], check=True, stdout=subprocess.PIPE, env=env).stdout
    with Image.open(io.BytesIO(png)) as image:
        return image.convert("RGBA")


def generate_drag_icon(scratch):
    logo = ElementTree.parse(SOURCE)
    mark = logo.find(f"{{{SVG_NS}}}g[@id='capsule-mark']")
    if mark is None:
        raise ValueError("The logo SVG must contain the capsule-mark group")
    mark.attrib.pop("transform", None)
    ElementTree.register_namespace("", SVG_NS)
    drag_svg = ElementTree.Element(f"{{{SVG_NS}}}svg", width="64", height="64", viewBox="-2 -2 26 26")
    drag_svg.append(mark)
    source = Path(scratch) / "drag-capsules.svg"
    ElementTree.ElementTree(drag_svg).write(source, encoding="utf-8")
    rasterize(source, scratch).save(ROOT / "assets" / "audio-icon.png")


def main():
    with tempfile.TemporaryDirectory(prefix="soundbot-icons-") as scratch:
        icon = rasterize(SOURCE, scratch)
        icon.save(ROOT / "build" / "icon.png")
        icon.save(ROOT / "build" / "icon.icns")
        icon.save(ROOT / "build" / "icon.ico", sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])
        generate_drag_icon(scratch)


if __name__ == "__main__":
    main()
