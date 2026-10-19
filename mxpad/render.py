from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mx_keypad import KEY_SIZE, encode_jpeg
from mxpad.config import IMAGE_DIR
from mxpad.mdi import render_icon

FONT_CANDIDATES = (
    "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf",
    "/usr/share/fonts/adwaita-sans-fonts/AdwaitaSans-Regular.ttf",
    "/usr/share/fonts/google-noto-vf/NotoSans[wght].ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
)


@lru_cache(maxsize=1)
def _font_path() -> str | None:
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            return path
    root = Path("/usr/share/fonts")
    if root.is_dir():
        for name in (
            "LiberationSans-Bold.ttf",
            "DejaVuSans-Bold.ttf",
            "AdwaitaSans-Regular.ttf",
        ):
            matches = list(root.rglob(name))
            if matches:
                return str(matches[0])
    return None


@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _font_path()
    if path:
        return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _hex_rgb(value: str) -> tuple[int, int, int]:
    h = value.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _show_icon(key: dict) -> bool:
    return key.get("show_icon", True) is not False


def _size_for(draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int, hi: int) -> ImageFont.FreeTypeFont:
    lo = 18
    best = _font(lo)
    while lo <= hi:
        mid = (lo + hi) // 2
        font = _font(mid)
        bbox = draw.textbbox((0, 0), text, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w <= max_w and h <= max_h:
            best = font
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def render_key(key: dict) -> bytes:
    color = _hex_rgb(key.get("color") or "#2b303b")
    text_color = _hex_rgb(key.get("text_color") or "#ffffff")
    img = Image.new("RGB", (KEY_SIZE, KEY_SIZE), color)
    draw = ImageDraw.Draw(img)

    image_name = key.get("image")
    icon_name = key.get("icon")
    show_icon = _show_icon(key)
    has_graphic = False

    if show_icon and image_name:
        path = Path(image_name)
        if not path.is_file():
            path = IMAGE_DIR / image_name
        if path.is_file():
            overlay = Image.open(path).convert("RGBA")
            overlay.thumbnail((KEY_SIZE - 20, 44), Image.Resampling.LANCZOS)
            x = (KEY_SIZE - overlay.width) // 2
            y = 4
            base = img.convert("RGBA")
            base.alpha_composite(overlay, (x, y))
            img = base.convert("RGB")
            draw = ImageDraw.Draw(img)
            has_graphic = True
    elif show_icon and icon_name:
        glyph_img = render_icon(icon_name, 40, text_color)
        base = img.convert("RGBA")
        x = (KEY_SIZE - glyph_img.width) // 2
        y = 0
        base.alpha_composite(glyph_img, (x, y))
        img = base.convert("RGB")
        draw = ImageDraw.Draw(img)
        has_graphic = True

    label = " ".join((key.get("label") or "").split())
    if label:
        if has_graphic:
            font = _size_for(draw, label, KEY_SIZE - 8, 50, 36)
            draw.text(
                (KEY_SIZE / 2, 90),
                label,
                fill=text_color,
                font=font,
                anchor="mm",
                stroke_width=1,
                stroke_fill=(0, 0, 0),
            )
        else:
            font = _size_for(draw, label, KEY_SIZE - 10, KEY_SIZE - 16, 72)
            draw.text(
                (KEY_SIZE / 2, KEY_SIZE / 2),
                label,
                fill=text_color,
                font=font,
                anchor="mm",
                stroke_width=2,
                stroke_fill=(0, 0, 0),
            )
    return encode_jpeg(img, quality=90)
