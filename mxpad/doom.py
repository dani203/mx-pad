"""Run Doom across the MX Keypad LCDs (easter egg).

The nine key screens are a 3×3 bezel matrix. Those same keys are the pad:

    select/enter   up      start/esc
    left           run     right
    use            down    fire

P1 exits. Engine is ozkl/doomgeneric (GPLv2) plus a Freedoom or Doom IWAD
from the user cache — commercial WADs are never bundled.
"""

from __future__ import annotations

import mmap
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image

from mx_keypad import GRID, KEY_GAP, KEY_SIZE, ORIGIN_X, ORIGIN_Y

CACHE = Path.home() / ".cache" / "mxpad"
CONFIG_WADS = Path.home() / ".config" / "mxpad"
FREEDOOM_URL = (
    "https://github.com/freedoom/freedoom/releases/download/v0.13.0/"
    "freedoom-0.13.0.zip"
)
DOOMGENERIC_GIT = "https://github.com/ozkl/doomgeneric.git"

# doomkeys.h
KEY_RIGHTARROW = 0xAE
KEY_LEFTARROW = 0xAC
KEY_UPARROW = 0xAD
KEY_DOWNARROW = 0xAF
KEY_USE = 0xA2
KEY_FIRE = 0xA3
KEY_ESCAPE = 27
KEY_ENTER = 13
KEY_RSHIFT = 0x80 + 0x36

KEY_TAB = 9

# 3×3: side keys D-pad, corners select/start/use/fire, center run
KEY_MAP = {
    0: KEY_ENTER,
    1: KEY_UPARROW,
    2: KEY_ESCAPE,
    3: KEY_LEFTARROW,
    4: KEY_RSHIFT,
    5: KEY_RIGHTARROW,
    6: KEY_USE,
    7: KEY_DOWNARROW,
    8: KEY_FIRE,
}

KONAMI = (1, 1, 7, 7, 3, 5, 3, 5, 8, 6)

HEADER = 16
RESX = 320
RESY = 200
CANVAS = KEY_SIZE * GRID
# Bounding box of all nine keys, including the physical 40 px gaps.
MATRIX_W = GRID * KEY_SIZE + (GRID - 1) * KEY_GAP
MATRIX_H = MATRIX_W
JPEG_QUALITY = 28
MAX_JPEG = 4075 * 6  # one grid present in a handful of HID fragments


class DoomError(RuntimeError):
    pass


def find_iwad() -> Path | None:
    names = (
        "freedoom1.wad",
        "freedoom.wad",
        "doom1.wad",
        "doom.wad",
        "DOOM1.WAD",
        "DOOM.WAD",
    )
    dirs = [
        CONFIG_WADS,
        CACHE,
        Path("/usr/share/games/doom"),
        Path("/usr/share/doom"),
        Path("/usr/share/freedoom"),
        Path("/usr/share/games/freedoom"),
    ]
    for folder in dirs:
        if not folder.is_dir():
            continue
        for name in names:
            path = folder / name
            if path.is_file():
                return path
        for path in folder.glob("*.wad"):
            if path.name.lower() in {n.lower() for n in names}:
                return path
    return None


def ensure_iwad(on_status=lambda _s: None) -> Path:
    existing = find_iwad()
    if existing:
        return existing
    CACHE.mkdir(parents=True, exist_ok=True)
    on_status("Downloading Freedoom IWAD…")
    zip_path = CACHE / "freedoom-0.13.0.zip"
    urllib.request.urlretrieve(FREEDOOM_URL, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        member = next(
            (n for n in zf.namelist() if n.lower().endswith("freedoom1.wad")),
            None,
        )
        if not member:
            raise DoomError("Freedoom zip did not contain freedoom1.wad")
        target = CACHE / "freedoom1.wad"
        with zf.open(member) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    return target


def ensure_binary(on_status=lambda _s: None) -> Path:
    cached = CACHE / "mxpad-doom"
    if cached.is_file() and os.access(cached, os.X_OK):
        return cached
    gcc = shutil.which("gcc") or shutil.which("cc")
    git = shutil.which("git")
    if not gcc or not git:
        raise DoomError("Need gcc and git to build the Doom helper")
    CACHE.mkdir(parents=True, exist_ok=True)
    src = CACHE / "doomgeneric"
    on_status("Fetching doomgeneric…")
    if not (src / "doomgeneric" / "doomgeneric.c").is_file():
        cloned = subprocess.run(
            ["git", "clone", "--depth", "1", DOOMGENERIC_GIT, str(src)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if cloned.returncode != 0:
            raise DoomError(cloned.stderr.strip() or "git clone failed")
    pad_c = Path(__file__).resolve().parent / "doom_pad.c"
    makefile = Path(__file__).resolve().parent / "Makefile.doom"
    dg = src / "doomgeneric"
    shutil.copy2(pad_c, dg / "doomgeneric_mxpad.c")
    shutil.copy2(makefile, dg / "Makefile.mxpad")
    on_status("Building Doom…")
    built = subprocess.run(
        [
            "make",
            "-C",
            str(dg),
            "-f",
            "Makefile.mxpad",
            f"DOOMGENERIC_DIR={dg}",
            f"OUTPUT={cached}",
            f"CC={gcc}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if built.returncode != 0:
        raise DoomError((built.stderr or built.stdout)[-800:].strip() or "make failed")
    cached.chmod(0o755)
    return cached


def _frame_image(mm: mmap.mmap, last_seq: int) -> tuple[Image.Image | None, int]:
    seq = int.from_bytes(mm[4:8], "little")
    if seq == last_seq or seq == 0:
        return None, last_seq
    w = int.from_bytes(mm[8:10], "little") or RESX
    h = int.from_bytes(mm[10:12], "little") or RESY
    raw = bytes(mm[HEADER : HEADER + w * h * 4])
    img = Image.frombytes("RGB", (w, h), raw, "raw", "BGRX")
    return img, seq


def _jpeg(image: Image.Image, quality: int) -> bytes:
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=quality, subsampling=2, optimize=False)
    return buf.getvalue()


def matrix_from_frame(frame: Image.Image) -> bytes:
    """One JPEG for the whole 3×3, gaps included so all keys present together."""
    canvas = frame.resize((MATRIX_W, MATRIX_H), Image.Resampling.BILINEAR)
    quality = JPEG_QUALITY
    jpeg = _jpeg(canvas, quality)
    while len(jpeg) > MAX_JPEG and quality > 10:
        quality -= 6
        jpeg = _jpeg(canvas, quality)
    return jpeg


def tiles_from_frame(frame: Image.Image) -> list[bytes]:
    canvas = frame.resize((CANVAS, CANVAS), Image.Resampling.BILINEAR)
    out: list[bytes] = []
    for index in range(9):
        row, col = divmod(index, GRID)
        tile = canvas.crop(
            (col * KEY_SIZE, row * KEY_SIZE, (col + 1) * KEY_SIZE, (row + 1) * KEY_SIZE)
        )
        out.append(_jpeg(tile, JPEG_QUALITY))
    return out


class DoomSession:
    def __init__(self, on_status=lambda _s: None) -> None:
        self.on_status = on_status
        self.proc: subprocess.Popen | None = None
        self._mm: mmap.mmap | None = None
        self._fb_file = None
        self._fb_path: Path | None = None
        self._seq = 0
        self._rr = 0
        self._matrix = True
        self._tmpdir = None

    def start(self) -> None:
        binary = ensure_binary(self.on_status)
        iwad = ensure_iwad(self.on_status)
        self._tmpdir = tempfile.TemporaryDirectory(prefix="mxpad-doom-")
        self._fb_path = Path(self._tmpdir.name) / "fb"
        self._fb_path.write_bytes(b"\x00" * (HEADER + RESX * RESY * 4))
        self._fb_file = self._fb_path.open("r+b")
        self._mm = mmap.mmap(self._fb_file.fileno(), 0)
        env = os.environ.copy()
        env["MXPAD_DOOM_FB"] = str(self._fb_path)
        log = (CACHE / "mxpad-doom.log").open("wb")
        self.proc = subprocess.Popen(
            [str(binary), "-iwad", str(iwad), "-nosound"],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(CACHE),
        )
        self.on_status("Rip and tear — P1 to quit")

    def handle_event(self, event) -> None:
        if self.proc is None or self.proc.stdin is None:
            return
        if event.key == "P2":
            key = KEY_TAB
        elif event.index is None:
            return
        else:
            key = KEY_MAP.get(event.index)
        if key is None:
            return
        try:
            self.proc.stdin.write(bytes((1 if event.pressed else 0, key & 0xFF)))
            self.proc.stdin.flush()
        except BrokenPipeError:
            pass

    def pump(self, pad) -> bool:
        if self.proc is None:
            return False
        if self.proc.poll() is not None:
            return False
        if self._mm is None:
            return True
        frame, self._seq = _frame_image(self._mm, self._seq)
        if frame is None:
            return True
        old_gap = getattr(pad, "lcd_gap", 0.002)
        pad.lcd_gap = 0.0
        try:
            if self._matrix:
                try:
                    pad.set_lcd_region(
                        matrix_from_frame(frame),
                        ORIGIN_X,
                        ORIGIN_Y,
                        MATRIX_W,
                        MATRIX_H,
                    )
                except OSError:
                    self._matrix = False
                    self._paint_tiles_balanced(pad, frame)
            else:
                self._paint_tiles_balanced(pad, frame)
        finally:
            pad.lcd_gap = old_gap
        return True

    def _paint_tiles_balanced(self, pad, frame: Image.Image) -> None:
        jpegs = tiles_from_frame(frame)
        start = self._rr
        self._rr = (self._rr + 1) % 9
        for n in range(9):
            i = (start + n) % 9
            pad.set_key_image(i, jpegs[i])

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.write(bytes((1, KEY_ESCAPE)))
                    self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        if self._mm:
            try:
                self._mm.close()
            except BufferError:
                pass
            self._mm = None
        if self._fb_file:
            self._fb_file.close()
            self._fb_file = None
        if self._tmpdir:
            self._tmpdir.cleanup()
            self._tmpdir = None
