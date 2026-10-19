from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from mxpad.uinput_kb import VirtualKeyboard


def _run(argv: list[str], **kwargs: Any) -> None:
    subprocess.Popen(argv, start_new_session=True, **kwargs)


def _gdbus(*args: str) -> None:
    _run(["gdbus", "call", "--session", *args])


class ActionRunner:
    def __init__(self, keyboard: VirtualKeyboard | None = None) -> None:
        self.keyboard = keyboard or VirtualKeyboard()
        self._page_handler: Callable[[str, int | None], None] | None = None

    def set_page_handler(self, handler: Callable[[str, int | None], None]) -> None:
        self._page_handler = handler

    def run(self, action: dict[str, Any]) -> None:
        kind = action.get("type") or "none"
        if kind in ("none", "", None):
            return
        handler = getattr(self, f"_do_{kind.replace('-', '_')}", None)
        if handler is None:
            raise ValueError(f"Unknown action type: {kind}")
        handler(action)

    def _do_hotkey(self, action: dict[str, Any]) -> None:
        keys = action.get("keys") or []
        if keys:
            self.keyboard.tap([str(k) for k in keys])

    def _do_text(self, action: dict[str, Any]) -> None:
        self.keyboard.type_text(str(action.get("text") or ""))

    def _do_launch(self, action: dict[str, Any]) -> None:
        desktop_id = action.get("id") or action.get("desktop")
        command = action.get("command")
        if desktop_id:
            from gi.repository import Gio

            info = Gio.DesktopAppInfo.new(desktop_id)
            if info is None:
                # try without org. prefix variants
                for candidate in _desktop_candidates(desktop_id):
                    info = Gio.DesktopAppInfo.new(candidate)
                    if info:
                        break
            if info:
                info.launch([], None)
                return
            raise RuntimeError(f"Application not found: {desktop_id}")
        if command:
            _run(["bash", "-lc", command])

    def _do_open(self, action: dict[str, Any]) -> None:
        target = action.get("url") or action.get("path") or ""
        if not target:
            return
        from gi.repository import Gio

        uri = target if "://" in target else Path(target).expanduser().resolve().as_uri()
        Gio.AppInfo.launch_default_for_uri(uri, None)

    def _do_open_url(self, action: dict[str, Any]) -> None:
        self._do_open(action)

    def _do_open_file(self, action: dict[str, Any]) -> None:
        self._do_open(action)

    def _do_command(self, action: dict[str, Any]) -> None:
        cmd = action.get("command") or ""
        if cmd:
            _run(["bash", "-lc", cmd])

    def _do_media(self, action: dict[str, Any]) -> None:
        cmd = action.get("command")
        mapping = {
            "play-pause": ["playpause"],
            "next": ["nextsong"],
            "previous": ["previoussong"],
            "stop": ["stop"],
            "mute": ["mute"],
            "volume-up": ["volumeup"],
            "volume-down": ["volumedown"],
        }
        if cmd in mapping:
            try:
                self.keyboard.tap(mapping[cmd])
                return
            except Exception:
                pass
        if cmd in ("volume-up", "volume-down", "mute"):
            self._pipewire_volume(cmd)
            return
        if cmd in ("play-pause", "next", "previous", "stop"):
            playerctl = {
                "play-pause": "play-pause",
                "next": "next",
                "previous": "previous",
                "stop": "stop",
            }[cmd]
            _run(["playerctl", playerctl])

    def _pipewire_volume(self, cmd: str) -> None:
        if cmd == "mute":
            _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"])
        elif cmd == "volume-up":
            _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%+"])
        elif cmd == "volume-down":
            _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"])

    def _do_system(self, action: dict[str, Any]) -> None:
        cmd = action.get("command")
        if cmd == "lock":
            _gdbus(
                "--dest", "org.gnome.ScreenSaver",
                "--object-path", "/org/gnome/ScreenSaver",
                "--method", "org.gnome.ScreenSaver.Lock",
            )
        elif cmd == "screenshot":
            self._screenshot()
        elif cmd == "show-desktop":
            try:
                self.keyboard.tap(["super", "d"])
            except Exception:
                _run(["wmctrl", "-k", "on"])
        elif cmd == "night-light":
            self._toggle_gsetting(
                "org.gnome.settings-daemon.plugins.color", "night-light-enabled"
            )
        elif cmd in ("dark-mode", "color-scheme"):
            self._toggle_color_scheme()
        elif cmd == "brightness-up":
            try:
                self.keyboard.tap(["brightnessup"])
            except Exception:
                _run(["gdbus", "call", "--session", "--dest", "org.gnome.SettingsDaemon.Power",
                      "--object-path", "/org/gnome/SettingsDaemon/Power",
                      "--method", "org.gnome.SettingsDaemon.Power.Keyboard.StepUp"])
        elif cmd == "brightness-down":
            try:
                self.keyboard.tap(["brightnessdown"])
            except Exception:
                _run(["gdbus", "call", "--session", "--dest", "org.gnome.SettingsDaemon.Power",
                      "--object-path", "/org/gnome/SettingsDaemon/Power",
                      "--method", "org.gnome.SettingsDaemon.Power.Keyboard.StepDown"])
        elif cmd == "logout":
            _gdbus(
                "--dest", "org.gnome.SessionManager",
                "--object-path", "/org/gnome/SessionManager",
                "--method", "org.gnome.SessionManager.Logout", "uint32:0",
            )
        else:
            raise ValueError(f"Unknown system command: {cmd}")

    def _screenshot(self) -> None:
        # GNOME blocks org.gnome.Shell.Screenshot from sandboxed/third-party
        # callers. Trigger the same shortcut the Shell already allows, then
        # fall back to the desktop portal's interactive UI.
        for keys in self._screenshot_shortcuts():
            try:
                self.keyboard.tap(keys)
                return
            except Exception:
                continue
        if self._screenshot_portal():
            return
        if shutil_which("gnome-screenshot"):
            _run(["gnome-screenshot", "-i"])
            return
        raise RuntimeError("Could not open the GNOME screenshot UI")

    def _screenshot_shortcuts(self) -> list[list[str]]:
        names = ("show-screenshot-ui", "screenshot", "screenshot-window")
        found: list[list[str]] = []
        try:
            from gi.repository import Gio

            settings = Gio.Settings.new("org.gnome.shell.keybindings")
            for name in names:
                try:
                    for accel in settings.get_strv(name):
                        keys = _parse_accelerator(accel)
                        if keys:
                            found.append(keys)
                except Exception:
                    continue
        except Exception:
            pass
        found.append(["print"])
        found.append(["shift", "super", "s"])
        # de-dupe while preserving order
        uniq: list[list[str]] = []
        seen: set[tuple[str, ...]] = set()
        for keys in found:
            token = tuple(keys)
            if token not in seen:
                seen.add(token)
                uniq.append(keys)
        return uniq

    def _screenshot_portal(self) -> bool:
        try:
            from gi.repository import Gio, GLib

            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync(
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.Screenshot",
                "Screenshot",
                GLib.Variant("(sa{sv})", ("", {"interactive": GLib.Variant("b", True)})),
                GLib.VariantType.new("(o)"),
                Gio.DBusCallFlags.NONE,
                4000,
                None,
            )
            return True
        except Exception:
            return False

    def _toggle_color_scheme(self) -> None:
        from gi.repository import Gio

        settings = Gio.Settings.new("org.gnome.desktop.interface")
        current = settings.get_string("color-scheme")
        settings.set_string(
            "color-scheme",
            "prefer-light" if current == "prefer-dark" else "prefer-dark",
        )

    def _toggle_gsetting(self, schema: str, key: str) -> None:
        try:
            from gi.repository import Gio

            settings = Gio.Settings.new(schema)
            settings.set_boolean(key, not settings.get_boolean(key))
        except Exception:
            _run(["gsettings", "set", schema, key, "toggle"])

    def _do_page(self, action: dict[str, Any]) -> None:
        if self._page_handler:
            which = str(action.get("which") or "next")
            index = action.get("index")
            self._page_handler(which, index)

    def _do_sequence(self, action: dict[str, Any]) -> None:
        import time

        for step in action.get("steps") or []:
            delay = float(step.get("delay_ms") or 0) / 1000.0
            if delay:
                time.sleep(delay)
            inner = {k: v for k, v in step.items() if k != "delay_ms"}
            if "type" in inner:
                self.run(inner)


def _parse_accelerator(accel: str) -> list[str]:
    text = (accel or "").strip()
    if not text:
        return []
    mods = [m.lower() for m in re.findall(r"<([^>]+)>", text)]
    key = re.sub(r"<[^>]+>", "", text).strip().lower()
    names: list[str] = []
    aliases = {
        "control": "ctrl",
        "ctrl": "ctrl",
        "primary": "ctrl",
        "shift": "shift",
        "alt": "alt",
        "super": "super",
        "meta": "super",
        "mod4": "super",
    }
    for mod in mods:
        mapped = aliases.get(mod)
        if mapped and mapped not in names:
            names.append(mapped)
    if not key:
        return names
    key_aliases = {
        "sys_req": "print",
        "sysrq": "print",
        "printscreen": "print",
        "print_screen": "print",
        "return": "enter",
    }
    names.append(key_aliases.get(key, key))
    return names


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


def _desktop_candidates(desktop_id: str) -> list[str]:
    names = [desktop_id]
    if not desktop_id.endswith(".desktop"):
        names.append(desktop_id + ".desktop")
    return names


def list_applications() -> list[tuple[str, str]]:
    from gi.repository import Gio

    apps: list[tuple[str, str]] = []
    for info in Gio.AppInfo.get_all():
        if not info.should_show():
            continue
        ident = info.get_id() or ""
        name = info.get_display_name() or ident
        if ident:
            apps.append((ident, name))
    apps.sort(key=lambda item: item[1].lower())
    return apps
