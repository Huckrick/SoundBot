"""Prepare unmodified Xiaomi variable fonts for embedding in SoundBot."""

import argparse
import hashlib
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "assets" / "fonts" / "misans"
BASE_URL = "https://hyperos.mi.com/font-download/"
LICENSE = ROOT / "build" / "licenses" / "MiSans-License.pdf"
LICENSE_SHA256 = "4a93a27cd2bd81b3b5ecfd0a853144a876fa26938a93a68443c67d74172fcb86"
FONTS = (
    ("MiSans.zip", "MiSansVF.ttf", "MiSansVF.ttf",
     "0ddef90648998900175cfdca9a6f087a2544c182f130b0ad4f7e94a03a115e79"),
    ("MiSans_TC.zip", "MisansTC VF.ttf", "MiSansTCVF.ttf",
     "4236ee573cdc4fd7ef631e9b884fc112c8c2125824e3a4f3b47dc6c496e7467f"),
    ("MiSans_Latin.zip", "MiSansLatinVF.ttf", "MiSansLatinVF.ttf",
     "0e9cd4d867fa61cecd7b6a315bc67a54bee91bd1c693d865cbbcc9e030b051db"),
)


def matches(path: Path, expected: str) -> bool:
    if not path.is_file():
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == expected


def prepare(archive_dir: Path | None, download: bool) -> None:
    if not matches(LICENSE, LICENSE_SHA256):
        raise ValueError(f"Missing or modified Xiaomi license: {LICENSE}")
    missing = [font for font in FONTS if not matches(FONT_DIR / font[2], font[3])]
    if not missing:
        print("Verified three original MiSans variable fonts and the Xiaomi license.")
        return
    if not download and archive_dir is None:
        raise ValueError("MiSans assets missing or changed. See docs/build-and-retrieval.md.")

    FONT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=FONT_DIR) as staging:
        staging_dir = Path(staging)
        for archive, member, filename, expected in missing:
            if download:
                archive_path = staging_dir / archive
                print(f"Downloading {archive} from Xiaomi", flush=True)
                with urlopen(BASE_URL + archive, timeout=120) as response, archive_path.open("wb") as out:
                    shutil.copyfileobj(response, out)
            else:
                archive_path = archive_dir / archive
            with ZipFile(archive_path) as source:
                candidates = [entry for entry in source.infolist()
                              if PurePosixPath(entry.filename).name == member]
                if len(candidates) != 1 or candidates[0].file_size > 25_000_000:
                    raise ValueError(f"Unexpected contents in {archive}")
                target = staging_dir / filename
                with source.open(candidates[0]) as font, target.open("wb") as out:
                    shutil.copyfileobj(font, out)
            if not matches(target, expected):
                raise ValueError(f"{archive} does not contain the pinned {member}")
            target.replace(FONT_DIR / filename)
            if download:
                archive_path.unlink()
    print("Verified three original MiSans variable fonts and the Xiaomi license.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--download", action="store_true", help="Fetch the originals after reviewing Xiaomi's license.")
    source.add_argument("--archive-dir", type=Path, help="Read the three official ZIPs from this directory.")
    args = parser.parse_args()
    try:
        prepare(args.archive_dir, args.download)
    except (OSError, ValueError) as error:
        parser.exit(1, f"MiSans preparation failed: {error}\n")
