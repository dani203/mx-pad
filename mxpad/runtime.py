from __future__ import annotations

import threading
import time
from typing import Any, Callable

from gi.repository import GLib

from mx_keypad import MXKeypad
from mxpad.actions import ActionRunner
from mxpad.config import load_config, save_config
from mxpad.focus import focused_app_name, profile_matches
from mxpad.render import render_key
from mxpad.status import StatusCollector, apply_status


StatusFn = Callable[[str], None]
LogFn = Callable[[str], None]


class PadRuntime:
    def __init__(self, on_status: StatusFn | None = None, on_event: LogFn | None = None) -> None:
        self.on_status = on_status or (lambda _s: None)
        self.on_event = on_event or (lambda _s: None)
        self.cfg = load_config()
        self.runner = ActionRunner()
        self.runner.set_page_handler(self._handle_page)
        self.pad: MXKeypad | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._page_index = 0
        self._painted_sig: str | None = None
        self._active_id: str | None = None
        self._paint_source: int | None = None
        self.collector = StatusCollector()
        self._live_sigs: list[str] = [""] * 9

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mxpad-hid", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        with self._lock:
            if self.pad:
                self.pad.close()
                self.pad = None

    def reload(self) -> None:
        self.cfg = load_config()
        self._painted_sig = None
        GLib.idle_add(lambda: self.on_status("Reloaded configuration") or False)

    def save(self) -> None:
        save_config(self.cfg)
        self._painted_sig = None
        self._schedule_paint()

    def _schedule_paint(self) -> None:
        if self._paint_source:
            GLib.source_remove(self._paint_source)
        self._paint_source = GLib.timeout_add(180, self._paint_timeout)

    def _paint_timeout(self) -> bool:
        self._paint_source = None
        self._paint_current()
        return False

    def current_profile(self) -> dict[str, Any]:
        pid = self.cfg.get("active_profile")
        for profile in self.cfg.get("profiles") or []:
            if profile["id"] == pid:
                return profile
        return self.cfg["profiles"][0]

    def current_page(self) -> dict[str, Any]:
        profile = self.current_profile()
        pages = profile.get("pages") or []
        if not pages:
            raise RuntimeError("Profile has no pages")
        self._page_index %= len(pages)
        return pages[self._page_index]

    def set_profile(self, profile_id: str) -> None:
        self.cfg["active_profile"] = profile_id
        self._page_index = 0
        self._painted_sig = None
        self._live_sigs = [""] * 9
        self._paint_current(force=True)
        save_config(self.cfg)

    def set_page(self, index: int) -> None:
        pages = self.current_profile().get("pages") or []
        if not pages:
            return
        self._page_index = index % len(pages)
        self._painted_sig = None
        self._live_sigs = [""] * 9
        self._paint_current(force=True)

    def _handle_page(self, which: str, index: int | None) -> None:
        pages = self.current_profile().get("pages") or []
        n = len(pages)
        if n == 0:
            return
        if which == "goto" and index is not None:
            self.set_page(index)
        elif which == "prev":
            self.set_page((self._page_index - 1) % n)
        else:
            self.set_page((self._page_index + 1) % n)

    def _connect(self) -> None:
        with self._lock:
            if self.pad:
                return
            pad = MXKeypad()
            pad.initialize()
            self.pad = pad
        GLib.idle_add(lambda: self.on_status(f"Connected ({pad.path})") or False)
        self._painted_sig = None
        self._live_sigs = [""] * 9
        self._paint_current(force=True)

    def _disconnect(self, message: str) -> None:
        with self._lock:
            if self.pad:
                try:
                    self.pad.close()
                except OSError:
                    pass
                self.pad = None
        GLib.idle_add(lambda: self.on_status(message) or False)

    def _paint_current(self, force: bool = False) -> None:
        try:
            page = self.current_page()
        except Exception:
            return
        sig = f"{self.current_profile()['id']}:{page.get('id')}:{self._page_index}"
        if force or sig != self._painted_sig:
            self._live_sigs = [""] * 9
        snapshot = self.collector.snapshot()
        keys = page.get("keys") or []
        with self._lock:
            pad = self.pad
            if not pad:
                return
            for i, key in enumerate(keys[:9]):
                painted = apply_status(key, snapshot)
                digest = f"{painted.get('label')}|{painted.get('icon')}|{painted.get('color')}|{painted.get('image')}|{painted.get('show_icon', True)}"
                if not force and self._live_sigs[i] == digest:
                    continue
                try:
                    pad.set_key_image(i, render_key(painted))
                except OSError as exc:
                    GLib.idle_add(lambda e=exc: self.on_status(f"LCD write failed: {e}") or False)
                    return
                self._live_sigs[i] = digest
        self._painted_sig = sig

    def _adapt(self) -> None:
        if not self.cfg.get("adapt_to_app"):
            return
        name = focused_app_name()
        if not name:
            return
        for profile in self.cfg.get("profiles") or []:
            if profile.get("match") and profile_matches(profile, name):
                if self.cfg.get("active_profile") != profile["id"]:
                    self.cfg["active_profile"] = profile["id"]
                    self._page_index = 0
                    self._painted_sig = None
                    save_config(self.cfg)
                    GLib.idle_add(
                        lambda n=name, p=profile["name"]: self.on_status(f"Profile “{p}” ({n})") or False
                    )
                    self._paint_current()
                return

    def _loop(self) -> None:
        last_keep = 0.0
        last_adapt = 0.0
        last_live = 0.0
        while not self._stop.is_set():
            if self.pad is None:
                try:
                    self._connect()
                except Exception as exc:
                    GLib.idle_add(lambda e=str(exc): self.on_status(f"Waiting for keypad: {e}") or False)
                    self._stop.wait(1.5)
                    continue
            now = time.monotonic()
            if now - last_keep > 2.4:
                with self._lock:
                    try:
                        if self.pad:
                            self.pad.keepalive()
                    except OSError:
                        self._disconnect("Keypad disconnected")
                        last_keep = now
                        continue
                last_keep = now
            if now - last_adapt > 1.0:
                try:
                    self._adapt()
                except Exception:
                    pass
                last_adapt = now
            if now - last_live > 1.0:
                try:
                    self._paint_current()
                except Exception:
                    pass
                last_live = now
            try:
                with self._lock:
                    events = self.pad.read_events(0.15) if self.pad else []
            except OSError:
                self._disconnect("Keypad disconnected")
                continue
            for event in events:
                if not event.pressed:
                    continue
                GLib.idle_add(lambda e=event: self.on_event(f"{e.key} pressed") or False)
                try:
                    self._dispatch(event)
                except Exception as exc:
                    GLib.idle_add(lambda e=str(exc): self.on_status(f"Action failed: {e}") or False)

    def _dispatch(self, event: Any) -> None:
        profile = self.current_profile()
        if event.key in ("P1", "P2"):
            action = (profile.get("nav") or {}).get(event.key) or {"type": "page", "which": "next"}
            self.runner.run(action)
            return
        if event.index is None:
            return
        page = self.current_page()
        keys = page.get("keys") or []
        if event.index >= len(keys):
            return
        action = keys[event.index].get("action") or {"type": "none"}
        self.runner.run(action)
