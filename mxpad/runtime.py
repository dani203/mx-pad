from __future__ import annotations

import threading
import time
from typing import Any, Callable

from gi.repository import GLib

from mx_keypad import KEY_GAP, KEY_SIZE, GRID, ORIGIN_X, ORIGIN_Y, MXKeypad, encode_jpeg
from mxpad.actions import ActionRunner
from mxpad.config import default_nav, load_config, save_config
from mxpad.doom import KONAMI, DoomError, DoomSession
from mxpad.focus import focused_app_name, profile_matches
from mxpad.render import page_canvas, render_key, slide_canvas
from mxpad.status import StatusCollector, apply_status

PAGE_SLIDE_STEPS = 5


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
        self._konami: list[int] = []
        self._doom: DoomSession | None = None
        self._live_paint_source: int | None = None
        from mxpad.ha_stream import hub

        hub.add_listener(self._on_ha)

    def start(self) -> None:
        self._stop.clear()
        from mxpad.ha_stream import hub
        from mxpad.homeassistant import is_configured

        if is_configured():
            hub.ensure()
        self._thread = threading.Thread(target=self._loop, name="mxpad-hid", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._stop_doom()
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

    def _on_ha(self, _entity_id: str | None) -> None:
        GLib.idle_add(self._schedule_live_paint)

    def _schedule_live_paint(self) -> bool:
        if self._live_paint_source:
            return False
        self._live_paint_source = GLib.timeout_add(50, self._live_paint_timeout)
        return False

    def _live_paint_timeout(self) -> bool:
        self._live_paint_source = None
        if not self._doom:
            try:
                self._paint_current()
            except Exception:
                pass
        return False

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

    def set_page(self, index: int, direction: int | None = None) -> None:
        pages = self.current_profile().get("pages") or []
        if not pages:
            return
        target = index % len(pages)
        if target == self._page_index:
            return
        old_index = self._page_index
        if direction is None:
            forward = (target - old_index) % len(pages)
            back = (old_index - target) % len(pages)
            direction = 1 if forward <= back else -1
        old_keys = list((pages[old_index].get("keys") or [])[:9])
        self._page_index = target
        self._painted_sig = None
        self._live_sigs = [""] * 9
        if self.cfg.get("page_animation", True):
            self._slide_pages(old_keys, direction)
        self._paint_current(force=True)

    def delete_page(self, index: int | None = None) -> bool:
        profile = self.current_profile()
        pages = profile.get("pages") or []
        if len(pages) <= 1:
            return False
        idx = self._page_index if index is None else index
        idx = max(0, min(idx, len(pages) - 1))
        del pages[idx]
        if self._page_index >= len(pages):
            self._page_index = len(pages) - 1
        elif self._page_index > idx:
            self._page_index -= 1
        self._painted_sig = None
        self._live_sigs = [""] * 9
        save_config(self.cfg)
        self._paint_current(force=True)
        return True

    def _handle_page(self, which: str, index: int | None) -> None:
        pages = self.current_profile().get("pages") or []
        n = len(pages)
        if n == 0:
            return
        if which == "goto" and index is not None:
            self.set_page(index)
        elif which == "prev":
            self.set_page((self._page_index - 1) % n, direction=-1)
        else:
            self.set_page((self._page_index + 1) % n, direction=1)

    def _nav_action(self, key: str) -> dict[str, Any]:
        nav = self.current_profile().get("nav") or {}
        action = nav.get(key)
        if isinstance(action, dict) and action.get("type"):
            return action
        return default_nav()[key]

    def _slide_pages(self, old_keys: list[dict[str, Any]], direction: int) -> None:
        try:
            snapshot = self.collector.snapshot()
            outgoing = [apply_status(key, snapshot) for key in old_keys]
            incoming = [apply_status(key, snapshot) for key in (self.current_page().get("keys") or [])[:9]]
            while len(outgoing) < 9:
                outgoing.append({"label": "", "color": "#111111"})
            while len(incoming) < 9:
                incoming.append({"label": "", "color": "#111111"})
            old_img = page_canvas(outgoing)
            new_img = page_canvas(incoming)
            width = GRID * KEY_SIZE + (GRID - 1) * KEY_GAP
            with self._lock:
                pad = self.pad
                if not pad:
                    return
                old_gap = pad.lcd_gap
                pad.lcd_gap = 0.0
                try:
                    for step in range(1, PAGE_SLIDE_STEPS + 1):
                        frame = slide_canvas(old_img, new_img, step / PAGE_SLIDE_STEPS, direction)
                        pad.set_lcd_region(
                            encode_jpeg(frame, quality=55),
                            ORIGIN_X,
                            ORIGIN_Y,
                            width,
                            width,
                        )
                finally:
                    pad.lcd_gap = old_gap
        except Exception:
            return

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
                digest = (
                    f"{painted.get('label')}|{painted.get('icon')}|{painted.get('color')}|"
                    f"{painted.get('text_color')}|{painted.get('image')}|{painted.get('show_icon', True)}"
                )
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
            if self._doom:
                try:
                    with self._lock:
                        pad = self.pad
                        alive = self._doom.pump(pad) if pad else False
                    if not alive:
                        self._stop_doom()
                        self._paint_current(force=True)
                except Exception as exc:
                    GLib.idle_add(lambda e=str(exc): self.on_status(f"Doom: {e}") or False)
                    self._stop_doom()
            elif now - last_live > 1.0:
                try:
                    self._paint_current()
                except Exception:
                    pass
                last_live = now
            try:
                with self._lock:
                    timeout = 0.02 if self._doom else 0.15
                    events = self.pad.read_events(timeout) if self.pad else []
            except OSError:
                self._disconnect("Keypad disconnected")
                continue
            for event in events:
                if event.pressed:
                    GLib.idle_add(lambda e=event: self.on_event(f"{e.key} pressed") or False)
                try:
                    self._dispatch(event)
                except Exception as exc:
                    GLib.idle_add(lambda e=str(exc): self.on_status(f"Action failed: {e}") or False)

    def _dispatch(self, event: Any) -> None:
        if self._doom:
            if event.key == "P1" and event.pressed:
                self._stop_doom()
                self._paint_current(force=True)
                return
            self._doom.handle_event(event)
            return
        if event.key in ("P1", "P2"):
            if not event.pressed:
                return
            profile = self.current_profile()
            action = self._nav_action(event.key)
            self.runner.run(action)
            return
        if not event.pressed or event.index is None:
            return
        self._konami.append(event.index)
        self._konami = self._konami[-len(KONAMI) :]
        if tuple(self._konami) == KONAMI:
            self._konami.clear()
            self._start_doom()
            return
        page = self.current_page()
        keys = page.get("keys") or []
        if event.index >= len(keys):
            return
        action = keys[event.index].get("action") or {"type": "none"}
        self.runner.run(action)

    def _start_doom(self) -> None:
        if self._doom:
            return
        GLib.idle_add(lambda: self.on_status("Summoning Doom…") or False)
        session = DoomSession(
            on_status=lambda s: GLib.idle_add(lambda m=s: self.on_status(m) or False)
        )
        try:
            session.start()
        except (DoomError, OSError) as exc:
            GLib.idle_add(lambda e=str(exc): self.on_status(f"Doom unavailable: {e}") or False)
            session.stop()
            return
        self._doom = session

    def _stop_doom(self) -> None:
        session = self._doom
        self._doom = None
        if session:
            session.stop()
            GLib.idle_add(lambda: self.on_status("Back from Hell") or False)
