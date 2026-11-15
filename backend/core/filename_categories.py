"""Conservative, offline categories derived from the source filename."""

import re
import unicodedata
from pathlib import PurePath


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_WORDS = re.compile(r"[^\W\d_]+")

# Category IDs are persisted in the catalog index. Rebuild the index in a
# schema migration when changing these rules.
_RULES = {
    "footsteps": ("footstep footsteps footfall footfalls", ("脚步", "腳步", "足音")),
    "doors": ("door doors gate gates", ("开门", "关门", "開門", "關門", "ドア")),
    "ambience": ("ambience ambiance ambiences atmos roomtone", ("环境音", "環境音", "環境音声")),
    "weather": ("rain thunder hail blizzard", ("雨声", "雨聲", "雷声", "雷聲", "雷鳴")),
    "water": ("water splash splashes splashing underwater", ("水声", "水聲", "水しぶき")),
    "vehicles": ("vehicle vehicles car cars truck trucks motorcycle motorcycles", ("汽车", "汽車", "車両")),
    "weapons": ("gun guns gunshot gunshots rifle pistol shotgun sword", ("枪声", "槍聲", "銃声")),
    "impacts": ("impact impacts hit hits punch punches", ("撞击", "撞擊", "打撃")),
    "whooshes": ("whoosh whooshes swoosh swooshes swish swishes", ("呼啸", "呼嘯")),
    "interface": ("ui notification notifications", ("界面音", "介面音", "通知音")),
}

_WORD_CATEGORY = {word: category for category, (words, _) in _RULES.items() for word in words.split()}
_PHRASE_CATEGORY = {"room tone": "ambience", "foot step": "footsteps", "gun shot": "weapons"}
_CJK_CATEGORY = {term: category for category, (_, terms) in _RULES.items() for term in terms}
_CJK_TERMS = re.compile("|".join(map(re.escape, sorted(_CJK_CATEGORY, key=len, reverse=True))))


def filename_category(filename: str) -> str:
    stem = unicodedata.normalize("NFKC", PurePath(filename).stem)
    words = _WORDS.findall(_CAMEL_BOUNDARY.sub(" ", stem).casefold())
    matches = {_WORD_CATEGORY[word] for word in words if word in _WORD_CATEGORY}
    matches.update(
        _PHRASE_CATEGORY[phrase]
        for a, b in zip(words, words[1:])
        if (phrase := f"{a} {b}") in _PHRASE_CATEGORY
    )
    matches.update(_CJK_CATEGORY[term] for term in _CJK_TERMS.findall(stem))
    return matches.pop() if len(matches) == 1 else ""
