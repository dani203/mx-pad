from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DATA_DIR = Path(__file__).resolve().parent / "data"
FONT_PATH = DATA_DIR / "materialdesignicons-webfont.ttf"
CATALOG_PATH = DATA_DIR / "mdi-catalog.json"

_catalog: list[dict] | None = None
_by_name: dict[str, str] | None = None


def catalog() -> list[dict]:
    global _catalog, _by_name
    if _catalog is None:
        _catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        _by_name = {}
        for item in _catalog:
            _by_name[item["n"]] = item["c"]
            for alias in (item.get("s") or "").split():
                _by_name.setdefault(alias, item["c"])
    return _catalog


def normalize_name(name: str | None) -> str | None:
    if not name:
        return None
    n = name.strip().lower()
    if n.startswith("mdi-"):
        n = n[4:]
    if n.startswith("mdi:"):
        n = n[4:]
    return n or None


def codepoint(name: str | None) -> str | None:
    catalog()
    n = normalize_name(name)
    if not n or _by_name is None:
        return None
    return _by_name.get(n)


def search(query: str, limit: int = 96) -> list[dict]:
    items = catalog()
    q = " ".join(query.lower().split())
    if not q:
        popular = (
            "play", "pause", "skip-next", "skip-previous", "volume-high", "folder",
            "firefox", "console", "cog", "lock", "content-copy", "content-paste",
            "home", "web", "email", "camera", "lightbulb", "power",
        )
        found = [i for i in items if i["n"] in popular]
        return found[:limit]
    words = q.split()
    scored: list[tuple[int, dict]] = []
    for item in items:
        name = item["n"]
        blob = f"{name} {item.get('s') or ''}"
        if not all(word in blob for word in words):
            continue
        if name == q:
            rank = 0
        elif name.startswith(q):
            rank = 1
        elif q in name.split("-"):
            rank = 2
        elif name.startswith(words[0]):
            rank = 3
        else:
            rank = 4
        scored.append((rank, len(name), item))
    scored.sort(key=lambda row: (row[0], row[1], row[2]["n"]))
    return [item for _r, _l, item in scored[:limit]]


@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size)


def glyph(name: str | None) -> str | None:
    cp = codepoint(name)
    if not cp:
        return None
    return chr(int(cp, 16))


def render_icon(name: str, size: int, color: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ch = glyph(name)
    if not ch:
        return img
    font = _font(max(12, int(size * 0.86)))
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), ch, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) / 2 - bbox[0]
    y = (size - th) / 2 - bbox[1]
    draw.text((x, y), ch, font=font, fill=color + (255,))
    return img
