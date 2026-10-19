from __future__ import annotations

import ctypes
import fcntl
import os
import time
from pathlib import Path

EV_SYN = 0x00
EV_KEY = 0x01
SYN_REPORT = 0
BUS_USB = 0x03

UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565

KEY_CODES: dict[str, int] = {
    "esc": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "minus": 12, "equal": 13, "backspace": 14, "tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23, "o": 24, "p": 25,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37, "l": 38,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50,
    "enter": 28, "space": 57, "leftbrace": 26, "rightbrace": 27, "backslash": 43,
    "semicolon": 39, "apostrophe": 40, "grave": 41, "comma": 51, "dot": 52, "slash": 53,
    "capslock": 58,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64, "f7": 65, "f8": 66,
    "f9": 67, "f10": 68, "f11": 87, "f12": 88,
    "ctrl": 29, "leftctrl": 29, "rightctrl": 97,
    "shift": 42, "leftshift": 42, "rightshift": 54,
    "alt": 56, "leftalt": 56, "rightalt": 100,
    "super": 125, "meta": 125, "leftmeta": 125, "rightmeta": 126, "win": 125,
    "up": 103, "down": 108, "left": 105, "right": 106,
    "insert": 110, "delete": 111, "home": 102, "end": 107, "pageup": 104, "pagedown": 109,
    "playpause": 164, "play": 207, "pause": 119, "stop": 166,
    "nextsong": 163, "previoussong": 165, "mute": 113, "volumeup": 115, "volumedown": 114,
    "brightnessdown": 224, "brightnessup": 225,
    "print": 99, "compose": 127, "menu": 139,
}

SHIFT_CHARS = {
    "A": "a", "B": "b", "C": "c", "D": "d", "E": "e", "F": "f", "G": "g", "H": "h",
    "I": "i", "J": "j", "K": "k", "L": "l", "M": "m", "N": "n", "O": "o", "P": "p",
    "Q": "q", "R": "r", "S": "s", "T": "t", "U": "u", "V": "v", "W": "w", "X": "x",
    "Y": "y", "Z": "z",
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
    "(": "9", ")": "0", "_": "minus", "+": "equal", "{": "leftbrace", "}": "rightbrace",
    "|": "backslash", ":": "semicolon", '"': "apostrophe", "<": "comma", ">": "dot",
    "?": "slash", "~": "grave",
}

CHAR_KEYS = {
    " ": "space", "\n": "enter", "\t": "tab",
    "-": "minus", "=": "equal", "[": "leftbrace", "]": "rightbrace", "\\": "backslash",
    ";": "semicolon", "'": "apostrophe", "`": "grave", ",": "comma", ".": "dot", "/": "slash",
}


class InputId(ctypes.Structure):
    _fields_ = [
        ("bustype", ctypes.c_uint16),
        ("vendor", ctypes.c_uint16),
        ("product", ctypes.c_uint16),
        ("version", ctypes.c_uint16),
    ]


class UInputUserDev(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char * 80),
        ("id", InputId),
        ("ff_effects_max", ctypes.c_int32),
        ("absmax", ctypes.c_int32 * 64),
        ("absmin", ctypes.c_int32 * 64),
        ("absfuzz", ctypes.c_int32 * 64),
        ("absflat", ctypes.c_int32 * 64),
    ]


class InputEvent(ctypes.Structure):
    _fields_ = [
        ("tv_sec", ctypes.c_uint64),
        ("tv_usec", ctypes.c_uint64),
        ("type", ctypes.c_uint16),
        ("code", ctypes.c_uint16),
        ("value", ctypes.c_int32),
    ]


class VirtualKeyboard:
    def __init__(self, path: str = "/dev/uinput") -> None:
        self.path = path
        self.fd = -1
        if Path(path).exists():
            self._create()

    def _create(self) -> None:
        self.fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_SYN)
        for code in set(KEY_CODES.values()):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        setup = UInputUserDev()
        setup.name = b"MX Pad Virtual Keyboard"
        setup.id.bustype = BUS_USB
        setup.id.vendor = 0x046D
        setup.id.product = 0xC354
        setup.id.version = 1
        os.write(self.fd, bytes(setup))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        time.sleep(0.15)

    def close(self) -> None:
        if self.fd >= 0:
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            except OSError:
                pass
            os.close(self.fd)
            self.fd = -1

    def available(self) -> bool:
        return self.fd >= 0

    def _emit(self, etype: int, code: int, value: int) -> None:
        ev = InputEvent(0, 0, etype, code, value)
        os.write(self.fd, bytes(ev))

    def tap(self, names: list[str]) -> None:
        if self.fd < 0:
            raise RuntimeError("No /dev/uinput access. Grant ACL or install the udev rule.")
        codes = []
        for name in names:
            key = name.lower().strip()
            if key not in KEY_CODES:
                raise ValueError(f"Unknown key: {name}")
            codes.append(KEY_CODES[key])
        for code in codes:
            self._emit(EV_KEY, code, 1)
            self._emit(EV_SYN, SYN_REPORT, 0)
        time.sleep(0.02)
        for code in reversed(codes):
            self._emit(EV_KEY, code, 0)
            self._emit(EV_SYN, SYN_REPORT, 0)

    def type_text(self, text: str) -> None:
        for ch in text:
            if ch in CHAR_KEYS:
                self.tap([CHAR_KEYS[ch]])
            elif ch.lower() in KEY_CODES and ch.lower() in "abcdefghijklmnopqrstuvwxyz0123456789":
                if ch.isupper() or ch in SHIFT_CHARS:
                    self.tap(["shift", ch.lower() if ch.isalpha() else SHIFT_CHARS.get(ch, ch)])
                else:
                    self.tap([ch.lower()])
            elif ch in SHIFT_CHARS:
                self.tap(["shift", SHIFT_CHARS[ch]])
            else:
                continue
            time.sleep(0.008)
