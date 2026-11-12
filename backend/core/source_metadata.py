"""Read source tags and embedded artwork without decoding the audio stream."""

from __future__ import annotations

from base64 import b64decode
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
import re
from struct import error as StructError, unpack_from
from xml.etree import ElementTree

import mutagen
from mutagen.flac import Picture
from mutagen.id3 import ID3
from PIL import Image, ImageOps

_MAX_FIELDS = 128
_MAX_TEXT = 8192
_MAX_CHUNK = 512 * 1024
_MAX_COVER = 32 * 1024 * 1024
_BROWSER_IMAGES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp",
                   "GIF": "image/gif", "BMP": "image/bmp"}
_TAG_NAMES = {
    "TIT2": "title", "TPE1": "artist", "TPE2": "album_artist", "TALB": "album",
    "TIT1": "grouping", "TIT3": "subtitle", "TCOM": "composer", "TEXT": "lyricist",
    "TCON": "genre", "TDRC": "date", "TDOR": "original_date", "TRCK": "track",
    "TPOS": "disc", "COMM": "comment", "USLT": "lyrics", "TCOP": "copyright",
    "TPUB": "publisher", "TSRC": "isrc", "TBPM": "bpm", "TENC": "encoded_by",
    "TSSE": "encoder", "TLAN": "language", "TMOO": "mood",
    "©nam": "title", "©ART": "artist", "aART": "album_artist", "©alb": "album",
    "©wrt": "composer", "©gen": "genre", "©day": "date", "trkn": "track",
    "disk": "disc", "©cmt": "comment", "©lyr": "lyrics", "©grp": "grouping",
    "cprt": "copyright", "tmpo": "bpm", "©too": "encoder", "WM/AlbumTitle": "album",
    "WM/AlbumArtist": "album_artist", "Author": "artist", "WM/Genre": "genre",
    "WM/Year": "date", "WM/TrackNumber": "track", "WM/Composer": "composer",
}


@dataclass(frozen=True)
class Artwork:
    data: bytes
    mime: str
    format: str
    width: int
    height: int
    size: int
    role: int | None
    description: str


def _native_file(source):
    try:
        return mutagen.File(source)
    except (mutagen.MutagenError, OSError, ValueError, OverflowError):
        return None


def _text(value):
    if isinstance(value, (bytes, bytearray)):
        return ""
    if isinstance(value, tuple) and len(value) == 2 and all(isinstance(n, int) for n in value):
        return f"{value[0]}/{value[1]}" if value[1] else str(value[0])
    if isinstance(value, (tuple, list)):
        return " · ".join(filter(None, map(_text, value[:64])))[:_MAX_TEXT]
    return "" if value is None else str(value).strip()[:_MAX_TEXT]


def _tags(values):
    tags = {}
    for key, value in values:
        if len(tags) >= _MAX_FIELDS:
            break
        name = str(key)[:192]
        content = _text(value)
        if name and content:
            tags[name] = f"{tags[name]} · {content}"[:_MAX_TEXT] if name in tags and tags[name] != content else content
    return tags


def _native_tags(media):
    if media is None or not media.tags:
        return {}
    if isinstance(media.tags, ID3):
        return _tags((
            frame.desc if frame.FrameID in {"TXXX", "WXXX"} and frame.desc else _TAG_NAMES.get(frame.FrameID, frame.FrameID),
            getattr(frame, "text", getattr(frame, "url", None)),
        ) for frame in media.tags.values())
    return _tags((_TAG_NAMES.get(key, key), [getattr(item, "value", item) for item in value]
                  if isinstance(value, list) else value)
                 for key, value in media.tags.items()
                 if key.lower() not in {"covr", "metadata_block_picture", "coverart", "coverartmime", "wm/picture"})


def _asf_picture(data):
    # WM/Picture stores a type byte, size, two UTF-16LE strings and the image.
    if not isinstance(data, bytes) or len(data) < 5:
        return None
    length = unpack_from("<I", data, 1)[0]
    start = len(data) - length
    if 0 < length <= _MAX_COVER and start >= 9 and data[start-2:start] == b"\0\0":
        fields = data[5:start].decode("utf-16-le", "replace").split("\0", 2)
        return data[0], data[start:], fields[1] if len(fields) > 1 else ""
    return None


def _native_pictures(media):
    if media is None:
        return
    for image in getattr(media, "pictures", []):
        yield int(image.type), image.data, image.desc
    if not media.tags:
        return
    if isinstance(media.tags, ID3):
        for frame in media.tags.getall("APIC"):
            if frame.mime != "-->":
                yield int(frame.type), frame.data, frame.desc
        return
    for data in media.tags.get("covr", []):
        yield 3, bytes(data), ""
    for value in media.tags.get("metadata_block_picture", [])[:32]:
        if len(value) > _MAX_COVER * 2:
            continue
        try:
            picture = Picture(b64decode(value))
        except (mutagen.MutagenError, ValueError, TypeError, OverflowError, StructError):
            continue
        if picture.mime != "-->":
            yield int(picture.type), picture.data, picture.desc
    for value in media.tags.get("coverart", [])[:32]:
        if len(value) <= _MAX_COVER * 2:
            try:
                yield 3, b64decode(value), ""
            except (ValueError, TypeError):
                continue
    for value in media.tags.get("WM/Picture", [])[:32]:
        picture = _asf_picture(value.value)
        if picture:
            yield picture


def _image(data, role, description):
    if not 0 < len(data) <= _MAX_COVER:
        return None
    try:
        with Image.open(BytesIO(data), formats=[*_BROWSER_IMAGES, "TIFF"]) as image:
            width, height = image.size
            if width * height > 40_000_000:
                return None
            format_name = image.format
            mime = _BROWSER_IMAGES.get(format_name)
            if mime and max(width, height) <= 640 and len(data) <= 1024 * 1024:
                # Decode the small image here so a corrupt front cover can fall back to another embedded picture.
                image.load()
                return Artwork(data, mime, format_name, width, height, len(data), role, description[:200])
            preview = ImageOps.exif_transpose(image)
            preview.thumbnail((480, 480))
            output = BytesIO()
            if preview.mode in ("RGBA", "LA") or "transparency" in preview.info:
                preview.convert("RGBA").save(output, format="PNG")
                mime = "image/png"
            else:
                preview.convert("RGB").save(output, format="JPEG", quality=85)
                mime = "image/jpeg"
            return Artwork(output.getvalue(), mime, format_name, width, height, len(data), role, description[:200])
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None


def read_artwork(source, av):
    # Choose a front cover ahead of back covers and other attached pictures.
    candidates = sorted(_native_pictures(_native_file(source)), key=lambda item: item[0] != 3)
    for role, data, description in candidates:
        cover = _image(data, role, description)
        if cover:
            return cover
    # FFmpeg also recognizes attachments in containers Mutagen cannot read.
    with av.open(str(source), mode="r", timeout=(2.0, 2.0)) as container:
        pictures = [stream for stream in container.streams.video
                    if stream.disposition & av.stream.Disposition.attached_pic
                    and not stream.disposition & av.stream.Disposition.timed_thumbnails]
        pictures.sort(key=lambda stream: stream.metadata.get("comment", "").casefold() != "cover (front)")
        packets = container.demux(*pictures) if pictures else ()
        for packet in packets:
            if 0 < packet.size <= _MAX_COVER:
                role = 3 if packet.stream.metadata.get("comment", "").casefold() == "cover (front)" else None
                cover = _image(bytes(packet), role, packet.stream.metadata.get("title", ""))
                if cover:
                    return cover
        if candidates or pictures:
            raise ValueError("Embedded artwork is damaged or unsupported")
    return None


def _ixml(raw):
    if b"<!DOCTYPE" in raw.replace(b"\0", b"").upper():
        return {}
    try:
        # Writers commonly pad an iXML chunk to an even size.
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        try:
            root = ElementTree.fromstring(raw.rstrip(b"\0"))
        except ElementTree.ParseError:
            return {}

    def entries(node, path="", depth=0):
        if depth > 12:
            return
        if node.text and node.text.strip():
            yield path or node.tag, node.text
        for key, value in node.attrib.items():
            yield f"{path}/@{key}".lstrip("/"), value
        counts = Counter(child.tag for child in node)
        seen = Counter()
        for child in node:
            seen[child.tag] += 1
            tag = child.tag.rsplit("}", 1)[-1]
            name = f"{tag}[{seen[child.tag]}]" if counts[child.tag] > 1 else tag
            yield from entries(child, f"{path}/{name}".lstrip("/"), depth+1)

    return _tags(entries(root))


def _bext(data):
    if len(data) < 348:
        return {}
    strings = [
        ("description", 0, 256), ("originator", 256, 288), ("originator_reference", 288, 320),
        ("origination_date", 320, 330), ("origination_time", 330, 338), ("coding_history", 602, len(data)),
    ]
    version = unpack_from("<H", data, 346)[0]
    fields = [(name, data[start:end].split(b"\0", 1)[0].decode("utf-8", "replace"))
              for name, start, end in strings]
    fields.extend((("time_reference", unpack_from("<Q", data, 338)[0]), ("version", version)))
    if version >= 1 and len(data) >= 412 and any(data[348:412]):
        fields.append(("umid", data[348:412].hex().upper()))
    if version >= 2 and len(data) >= 422:
        names = ("loudness_value", "loudness_range", "max_true_peak", "max_momentary", "max_short_term")
        fields.extend((name, f"{value / 100:.2f}") for name, value in zip(names, unpack_from("<5h", data, 412))
                      if value != 0x7fff)
    return _tags(fields)


def _wave_chunks(source, length):
    """Walk RIFF headers by seeking over audio, including the RF64 data chunk."""
    with source.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12 or header[:4] not in (b"RIFF", b"RF64", b"BW64") or header[8:] != b"WAVE":
            return
        rf64_data_size = None
        for _ in range(4096):
            chunk = stream.read(8)
            if len(chunk) != 8:
                break
            kind, size = unpack_from("<4sI", chunk)
            kind = kind.lower()
            if kind == b"data" and size == 0xffffffff and rf64_data_size is not None:
                size = rf64_data_size
            start = stream.tell()
            if start + size > length:
                break
            if kind == b"ds64" and size >= 16:
                rf64_data_size = unpack_from("<Q", stream.read(16), 8)[0]
            if kind in (b"bext", b"ixml", b"cue ", b"smpl") and size <= _MAX_CHUNK:
                yield kind, stream.read(size)
            stream.seek(start + size + (size & 1))


def _wave_details(source, length):
    result = {}
    for kind, data in _wave_chunks(source, length):
        if kind == b"ixml":
            result["ixml"] = _ixml(data)
        elif kind == b"bext":
            result["bwf"] = _bext(data)
        elif kind == b"cue " and len(data) >= 4:
            count = min(unpack_from("<I", data)[0], (len(data)-4)//24, 128)
            result["markers"] = [{"id": identifier, "sample": sample}
                                 for identifier, *_, sample in
                                 (unpack_from("<6I", data, 4 + i*24) for i in range(count))]
        elif kind == b"smpl" and len(data) >= 36:
            count = min(unpack_from("<I", data, 28)[0], (len(data)-36)//24, 128)
            result["loops"] = [{"id": identifier, "start": start, "end": end, "count": plays}
                               for identifier, _, start, end, _, plays in
                               (unpack_from("<6I", data, 36 + i*24) for i in range(count))]
    return result


def read_details(source, av):
    stat = source.stat()
    media = _native_file(source)
    native = _native_tags(media)
    info = getattr(media, "info", None)
    with av.open(str(source), mode="r", timeout=(4.0, 4.0)) as container:
        stream = next(iter(container.streams.audio), None)
        if stream is None:
            raise ValueError("container has no audio stream")
        context = stream.codec_context
        rate = context.sample_rate
        duration = (float(stream.duration * stream.time_base) if stream.duration is not None and stream.time_base
                    else (container.duration or 0) / av.time_base)
        pcm = re.match(r"pcm_[suf](\d+)", context.name)
        depth = int(pcm[1]) if pcm else None
        if context.name in {"flac", "alac", "wmalossless"}:
            depth = getattr(info, "bits_per_sample", None)
        return {
            "file": {"size": stat.st_size, "modified_at": stat.st_mtime},
            "audio": {
                "container": container.format.name, "codec": context.name, "duration": max(0, duration),
                "sample_rate": rate, "channels": context.channels,
                "channel_layout": context.layout.name if context.layout else None,
                "bit_depth": depth, "bit_rate": context.bit_rate or getattr(info, "bitrate", None) or None,
                "audio_streams": len(container.streams.audio),
            },
            "tags": {"container": _tags(container.metadata.items()), "stream": _tags(stream.metadata.items()),
                     "embedded": native},
            **(_wave_details(source, stat.st_size) if source.suffix.lower() == ".wav" else {}),
        }
