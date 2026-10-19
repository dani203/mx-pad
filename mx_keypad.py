"""MX Keypad (046d:c354) HID client for Linux hidraw.

Talks the same vendor HID collections Options+ uses:
  report 0x11  HID++ short (20 bytes)
  report 0x13  HID++ long  (32 bytes)
  report 0x14  LCD JPEG    (4095 bytes, Feature)

Observed on this machine's report descriptor (hidraw, usage page 0xFF43).
"""

from __future__ import annotations

import fcntl
import glob
import os
import select
import struct
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

VID = 0x046D
PID = 0xC354

REPORT_HIDPP_SHORT = 0x11
REPORT_HIDPP_LONG = 0x13
REPORT_LCD = 0x14

HIDPP_SHORT_LEN = 20
HIDPP_LONG_LEN = 32
LCD_PACKET_LEN = 4095

KEY_SIZE = 118
KEY_GAP = 40
ORIGIN_X = 23
ORIGIN_Y = 6
GRID = 3

# HID++ 2.0: device 0xff, feature index 0x0b (ReprogramControlsV4),
# function 3 / sw_id 0xb — subscribe to P1/P2 hidIds 0x01a1 / 0x01a2.
INIT_REPORTS = (
    bytes([0x11, 0xFF, 0x0B, 0x3B, 0x01, 0xA1, 0x03] + [0x00] * 13),
    bytes([0x11, 0xFF, 0x0B, 0x3B, 0x01, 0xA2, 0x03] + [0x00] * 13),
)

# Feature 0x04 (keepalive) fn=1, 3000 ms — Options+ ~1 Hz heartbeat.
KEEPALIVE = bytes([0x11, 0xFF, 0x04, 0x1D, 0x0B, 0xB8] + [0x00] * 14)

HIDIOCGRDESCSIZE = 0x80044801
HIDIOCGRAWINFO = 0x80084803


def _hid_ioc_sfeature(length: int) -> int:
    # _IOC(_IOC_WRITE|_IOC_READ, 'H', 0x06, length)
    return 0xC0004806 | (length << 16)


@dataclass(frozen=True)
class ButtonEvent:
    key: str
    index: int | None
    pressed: bool
    hid_id: int


def key_origin(index: int) -> tuple[int, int]:
    row, col = divmod(index, GRID)
    return ORIGIN_X + col * (KEY_SIZE + KEY_GAP), ORIGIN_Y + row * (KEY_SIZE + KEY_GAP)


def find_hidraw() -> str:
    for path in sorted(glob.glob("/dev/hidraw*")):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            buf = array_u32()
            fcntl.ioctl(fd, HIDIOCGRAWINFO, buf)
            _bustype, vendor, product = struct.unpack("IHH", buf)
            if vendor == VID and product == PID:
                return path
        except OSError:
            continue
        finally:
            os.close(fd)
    raise FileNotFoundError(
        "MX Keypad hidraw not found (046d:c354). Plug it in and grant hidraw access."
    )


def array_u32() -> bytearray:
    return bytearray(8)


def _fragment_flags(index: int, first: bool, last: bool) -> int:
    value = index | 0b0010_0000
    if first:
        value |= 0b1000_0000
    if last:
        value |= 0b0100_0000
    return value


def jpeg_packets(jpeg: bytes, x: int, y: int, w: int, h: int) -> list[bytes]:
    packets: list[bytes] = []
    header = 20
    first_payload = min(len(jpeg), LCD_PACKET_LEN - header)
    pkt = bytearray(LCD_PACKET_LEN)
    pkt[header : header + first_payload] = jpeg[:first_payload]
    pkt[0] = REPORT_LCD
    pkt[1] = 0xFF
    pkt[2] = 0x02
    pkt[3] = 0x2B
    pkt[4] = _fragment_flags(1, True, first_payload >= len(jpeg))
    pkt[5:7] = (0x01, 0x00)
    pkt[7:9] = (0x01, 0x00)
    pkt[9:11] = ((x >> 8) & 0xFF, x & 0xFF)
    pkt[11:13] = ((y >> 8) & 0xFF, y & 0xFF)
    pkt[13:15] = ((w >> 8) & 0xFF, w & 0xFF)
    pkt[15:17] = ((h >> 8) & 0xFF, h & 0xFF)
    pkt[18:20] = ((len(jpeg) >> 8) & 0xFF, len(jpeg) & 0xFF)
    packets.append(bytes(pkt))

    remaining = len(jpeg) - first_payload
    offset = first_payload
    part = 2
    while remaining > 0:
        chunk = min(remaining, LCD_PACKET_LEN - 5)
        pkt = bytearray(LCD_PACKET_LEN)
        pkt[5 : 5 + chunk] = jpeg[offset : offset + chunk]
        remaining -= chunk
        pkt[0] = REPORT_LCD
        pkt[1] = 0xFF
        pkt[2] = 0x02
        pkt[3] = 0x2B
        pkt[4] = _fragment_flags(part, False, remaining == 0)
        packets.append(bytes(pkt))
        offset += chunk
        part += 1
    return packets


def encode_jpeg(image: Image.Image, quality: int = 80) -> bytes:
    rgb = image.convert("RGB")
    buf = BytesIO()
    rgb.save(buf, format="JPEG", quality=quality, subsampling=0)
    return buf.getvalue()


def solid_key(color: tuple[int, int, int], label: str | None = None) -> bytes:
    img = Image.new("RGB", (KEY_SIZE, KEY_SIZE), color)
    if label:
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", 28
            )
        except OSError:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            ((KEY_SIZE - tw) / 2, (KEY_SIZE - th) / 2),
            label,
            fill=(255, 255, 255),
            font=font,
        )
    return encode_jpeg(img)


class MXKeypad:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or find_hidraw()
        self.fd = os.open(self.path, os.O_RDWR)
        self._pressed_grid: set[int] = set()
        self._last_grid_state = 0
        self._pressed_nav: set[int] = set()

    def close(self) -> None:
        if getattr(self, "fd", -1) >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> "MXKeypad":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _write(self, report: bytes) -> None:
        n = os.write(self.fd, report)
        if n != len(report):
            raise OSError(f"short hidraw write {n}/{len(report)}")

    def _set_feature(self, report: bytes) -> None:
        buf = bytearray(report)
        try:
            fcntl.ioctl(self.fd, _hid_ioc_sfeature(len(buf)), buf)
        except OSError:
            self._write(report)

    def initialize(self) -> None:
        for report in INIT_REPORTS:
            self._write(report)
            time.sleep(0.01)
        self.keepalive()

    def keepalive(self) -> None:
        self._write(KEEPALIVE)

    def set_key_image(self, index: int, jpeg: bytes) -> None:
        if not 0 <= index <= 8:
            raise ValueError("key index must be 0..8")
        x, y = key_origin(index)
        for packet in jpeg_packets(jpeg, x, y, KEY_SIZE, KEY_SIZE):
            self._set_feature(packet)
            time.sleep(0.002)

    def set_key_color(
        self, index: int, color: tuple[int, int, int], label: str | None = None
    ) -> None:
        self.set_key_image(index, solid_key(color, label))

    def paint_grid(
        self, colors: Iterable[tuple[int, int, int]], labels: Iterable[str] | None = None
    ) -> None:
        labels_list = list(labels) if labels is not None else [str(i) for i in range(9)]
        for i, color in enumerate(colors):
            label = labels_list[i] if i < len(labels_list) else None
            self.set_key_color(i, color, label)

    def read_events(self, timeout: float = 0.1) -> list[ButtonEvent]:
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return []
        data = os.read(self.fd, 256)
        if not data:
            return []
        return self._parse(data)

    def _parse(self, data: bytes) -> list[ButtonEvent]:
        events: list[ButtonEvent] = []
        report_id = data[0]
        if report_id == REPORT_HIDPP_LONG and len(data) >= 8:
            events.extend(self._parse_grid_long(data))
        if report_id == REPORT_HIDPP_SHORT and len(data) >= 7:
            events.extend(self._parse_nav(data))
            events.extend(self._parse_grid_short_quirk(data))
        return events

    def _parse_grid_long(self, data: bytes) -> list[ButtonEvent]:
        # 13 ff 02 00 ?? 01 hidId...  (julusian / Options+ long input)
        if data[1] != 0xFF or data[2] != 0x02 or data[3] != 0x00:
            return []
        if len(data) > 5 and data[5] != 0x01:
            return []
        current: set[int] = set()
        for b in data[6:]:
            if b == 0:
                break
            if 1 <= b <= 9:
                current.add(b - 1)
        return self._diff_grid(current)

    def _parse_grid_short_quirk(self, data: bytes) -> list[ButtonEvent]:
        # Some firmware paths put the 1..9 key id at byte 6 of a short report.
        if data[1] != 0xFF:
            return []
        if data[2] == 0x0B:
            return []
        state = data[6] if len(data) > 6 else 0
        if 1 <= state <= 9:
            if state == 1 and self._last_grid_state not in (0, 1):
                self._last_grid_state = state
                return []
            current = {state - 1}
            self._last_grid_state = state
            return self._diff_grid(current)
        if state == 0 and self._pressed_grid:
            self._last_grid_state = 0
            return self._diff_grid(set())
        self._last_grid_state = state
        return []

    def _diff_grid(self, current: set[int]) -> list[ButtonEvent]:
        events: list[ButtonEvent] = []
        for idx in sorted(current - self._pressed_grid):
            events.append(ButtonEvent(f"key{idx}", idx, True, idx + 1))
        for idx in sorted(self._pressed_grid - current):
            events.append(ButtonEvent(f"key{idx}", idx, False, idx + 1))
        self._pressed_grid = current
        return events

    def _parse_nav(self, data: bytes) -> list[ButtonEvent]:
        # 11 ff 0b 00 01 a1 / 01 a2  (P1 / P2)
        if data[1] != 0xFF or data[2] != 0x0B or data[3] != 0x00:
            return []
        current: set[int] = set()
        for i in range(4, len(data) - 1, 2):
            hid_id = (data[i] << 8) | data[i + 1]
            if hid_id in (0x01A1, 0x01A2):
                current.add(hid_id)
            elif data[i] == 0x01 and data[i + 1] in (0xA1, 0xA2):
                current.add((data[i] << 8) | data[i + 1])
        events: list[ButtonEvent] = []
        names = {0x01A1: "P1", 0x01A2: "P2"}
        for hid_id in sorted(current - self._pressed_nav):
            events.append(ButtonEvent(names[hid_id], None, True, hid_id))
        for hid_id in sorted(self._pressed_nav - current):
            events.append(ButtonEvent(names[hid_id], None, False, hid_id))
        self._pressed_nav = current
        return events


def default_palette() -> list[tuple[int, int, int]]:
    return [
        (220, 50, 47),
        (38, 139, 210),
        (133, 153, 0),
        (181, 137, 0),
        (211, 54, 130),
        (42, 161, 152),
        (108, 113, 196),
        (203, 75, 22),
        (88, 110, 117),
    ]
