from __future__ import annotations

from typing import Any


def _key(
    label: str,
    color: str,
    action: dict[str, Any],
    icon: str | None = None,
    image: str | None = None,
    status: str | None = None,
    show_icon: bool = True,
) -> dict[str, Any]:
    return {
        "label": label,
        "color": color,
        "text_color": "#ffffff",
        "icon": icon,
        "image": image,
        "status": status,
        "show_icon": show_icon,
        "action": action,
    }


def default_config() -> dict[str, Any]:
    media = {
        "id": "page-media",
        "name": "Media",
        "keys": [
            _key("Prev", "#1a5c8a", {"type": "media", "command": "previous"}, "skip-previous"),
            _key("Play", "#0d6e4f", {"type": "media", "command": "play-pause"}, "play"),
            _key("Next", "#1a5c8a", {"type": "media", "command": "next"}, "skip-next"),
            _key("Mute", "#5c3d1a", {"type": "media", "command": "mute"}, "volume-off"),
            _key("Vol −", "#4a3728", {"type": "media", "command": "volume-down"}, "volume-minus"),
            _key("Vol +", "#4a3728", {"type": "media", "command": "volume-up"}, "volume-plus"),
            _key("Shot", "#3d3d6b", {"type": "system", "command": "screenshot"}, "monitor-screenshot"),
            _key("Lock", "#3b2a4a", {"type": "system", "command": "lock"}, "lock"),
            _key("Files", "#2a4a3b", {"type": "launch", "id": "org.gnome.Nautilus.desktop"}, "folder"),
        ],
    }
    launch = {
        "id": "page-apps",
        "name": "Apps",
        "keys": [
            _key("Browser", "#1f4e79", {"type": "launch", "id": "org.mozilla.firefox.desktop"}, "firefox"),
            _key("Term", "#1a3d2f", {"type": "launch", "id": "org.gnome.Ptyxis.desktop"}, "console"),
            _key("Editor", "#4a3a1a", {"type": "launch", "id": "org.gnome.TextEditor.desktop"}, "file-document-edit"),
            _key("Theme", "#3a2a4a", {"type": "system", "command": "dark-mode"}, "theme-light-dark"),
            _key("Settings", "#2a3040", {"type": "launch", "id": "org.gnome.Settings.desktop"}, "cog"),
            _key("Calc", "#2a4040", {"type": "launch", "id": "org.gnome.Calculator.desktop"}, "calculator"),
            _key("Night", "#3d3558", {"type": "system", "command": "night-light"}, "weather-night"),
            _key("Bright−", "#3a3a20", {"type": "system", "command": "brightness-down"}, "brightness-5"),
            _key("Bright+", "#3a3a20", {"type": "system", "command": "brightness-up"}, "brightness-7"),
        ],
    }
    keys = {
        "id": "page-keys",
        "name": "Keys",
        "keys": [
            _key("Copy", "#2d4a3e", {"type": "hotkey", "keys": ["ctrl", "c"]}, "content-copy"),
            _key("Paste", "#2d4a3e", {"type": "hotkey", "keys": ["ctrl", "v"]}, "content-paste"),
            _key("Cut", "#4a3a2d", {"type": "hotkey", "keys": ["ctrl", "x"]}, "content-cut"),
            _key("Undo", "#3a3a4a", {"type": "hotkey", "keys": ["ctrl", "z"]}, "undo"),
            _key("Redo", "#3a3a4a", {"type": "hotkey", "keys": ["ctrl", "shift", "z"]}, "redo"),
            _key("Save", "#1f4e79", {"type": "hotkey", "keys": ["ctrl", "s"]}, "content-save"),
            _key("Find", "#3d3558", {"type": "hotkey", "keys": ["ctrl", "f"]}, "magnify"),
            _key("Close", "#5a2a2a", {"type": "hotkey", "keys": ["ctrl", "w"]}, "close"),
            _key("Desktop", "#2a3040", {"type": "system", "command": "show-desktop"}, "monitor"),
        ],
    }
    general = {
        "id": "general",
        "name": "General",
        "match": None,
        "nav": {
            "P1": {"type": "page", "which": "prev"},
            "P2": {"type": "page", "which": "next"},
        },
        "pages": [media, launch, keys, status_page()],
    }
    return {
        "adapt_to_app": True,
        "page_animation": True,
        "active_profile": "general",
        "profiles": [general],
    }


def status_page() -> dict[str, Any]:
    monitor = {"type": "launch", "id": "org.gnome.SystemMonitor.desktop"}
    settings = {"type": "launch", "id": "org.gnome.Settings.desktop"}
    return {
        "id": "page-status",
        "name": "Status",
        "keys": [
            _key("Time", "#1f3d5c", {"type": "none"}, "clock-outline", status="time", show_icon=False),
            _key("Date", "#1f3d5c", {"type": "none"}, "calendar", status="date", show_icon=False),
            _key("Battery", "#2d4a3e", settings, "battery", status="battery", show_icon=False),
            _key("CPU °", "#5c3d1a", monitor, "thermometer", status="cpu_temp", show_icon=False),
            _key("CPU", "#4a3728", monitor, "chip", status="cpu", show_icon=False),
            _key("RAM", "#3d3d6b", monitor, "memory", status="ram", show_icon=False),
            _key("Disk", "#2a4a3b", {"type": "launch", "id": "org.gnome.Nautilus.desktop"}, "harddisk", status="disk", show_icon=False),
            _key("Vol", "#4a3728", {"type": "media", "command": "mute"}, "volume-high", status="volume", show_icon=False),
            _key("Net", "#1a5c8a", settings, "lan", status="network", show_icon=False),
        ],
    }
