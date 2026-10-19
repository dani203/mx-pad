from __future__ import annotations

import sys
from pathlib import Path
from typing import Any  # Path used by file picker callback

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from mxpad.actions import list_applications
from mxpad.config import duplicate_profile, empty_key, empty_page, import_image, new_profile
from mxpad.mdi import render_icon, search as search_mdi
from mxpad.runtime import PadRuntime
from mxpad.status import METRICS, apply_status

ACTION_TYPES = [
    ("none", "None"),
    ("hotkey", "Keyboard shortcut"),
    ("text", "Type text"),
    ("launch", "Launch application"),
    ("open", "Open file or URL"),
    ("command", "Run command"),
    ("media", "Media playback"),
    ("system", "System / settings"),
    ("page", "Go to page"),
]

MEDIA_COMMANDS = [
    ("play-pause", "Play / Pause"),
    ("previous", "Previous"),
    ("next", "Next"),
    ("stop", "Stop"),
    ("mute", "Mute"),
    ("volume-down", "Volume down"),
    ("volume-up", "Volume up"),
]

SYSTEM_COMMANDS = [
    ("screenshot", "Screenshot"),
    ("lock", "Lock screen"),
    ("show-desktop", "Show desktop"),
    ("night-light", "Toggle night light"),
    ("dark-mode", "Toggle dark / light mode"),
    ("brightness-down", "Brightness down"),
    ("brightness-up", "Brightness up"),
    ("logout", "Log out"),
]

PAGE_WHICH = [("prev", "Previous page"), ("next", "Next page"), ("goto", "Specific page")]

CSS = """
.key-tile {
  min-width: 96px;
  min-height: 96px;
  border-radius: 14px;
  padding: 8px;
  border: 2px solid transparent;
}
.key-tile.selected {
  border-color: @accent_color;
}
.mdi-browser {
  min-width: 72px;
  min-height: 72px;
}
.pad-grid {
  margin: 12px;
}
.status-ok { color: @success_color; }
.status-wait { color: @warning_color; }
"""


def _css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS.encode())
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def _pixbuf(name: str, size: int = 48, color=(255, 255, 255)) -> GdkPixbuf.Pixbuf | None:
    from io import BytesIO

    img = render_icon(name, size, color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    if len(data) < 40:
        return None
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(data)
    loader.close()
    return loader.get_pixbuf()


class IconChooser(Adw.Window):
    def __init__(self, parent: Gtk.Window, callback) -> None:
        super().__init__(transient_for=parent, modal=True, title="Material Design Icons")
        self.set_default_size(560, 620)
        self._callback = callback
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        search = Gtk.SearchEntry()
        search.set_placeholder_text("Search 7,000+ MDI icons…")
        search.connect("search-changed", self._filter)
        self._flow = Gtk.FlowBox()
        self._flow.set_max_children_per_line(8)
        self._flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flow.set_homogeneous(True)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self._flow)
        box.append(search)
        box.append(scroller)
        self.set_content(box)
        self._rebuild("")
        search.grab_focus()

    def _rebuild(self, query: str) -> None:
        while (child := self._flow.get_first_child()) is not None:
            self._flow.remove(child)
        for item in search_mdi(query, limit=120):
            name = item["n"]
            btn = Gtk.Button()
            btn.add_css_class("mdi-browser")
            btn.set_tooltip_text(name)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            inner.set_halign(Gtk.Align.CENTER)
            pix = _pixbuf(name, 36)
            if pix:
                inner.append(Gtk.Image.new_from_pixbuf(pix))
            lbl = Gtk.Label(label=name)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.add_css_class("caption")
            inner.append(lbl)
            btn.set_child(inner)
            btn.connect("clicked", lambda _b, n=name: self._pick(n))
            self._flow.append(btn)

    def _filter(self, entry: Gtk.SearchEntry) -> None:
        self._rebuild(entry.get_text())

    def _pick(self, name: str) -> None:
        self._callback(name)
        self.close()


class AppChooser(Adw.Window):
    def __init__(self, parent: Gtk.Window, callback) -> None:
        super().__init__(transient_for=parent, modal=True, title="Choose application")
        self.set_default_size(420, 520)
        self._callback = callback
        self._apps = list_applications()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        search = Gtk.SearchEntry()
        search.set_placeholder_text("Search apps")
        search.connect("search-changed", self._filter)
        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.add_css_class("boxed-list")
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self._list)
        choose = Gtk.Button(label="Choose")
        choose.add_css_class("suggested-action")
        choose.connect("clicked", self._choose)
        box.append(search)
        box.append(scroller)
        box.append(choose)
        self.set_content(box)
        self._rebuild("")
        search.grab_focus()

    def _rebuild(self, query: str) -> None:
        while (child := self._list.get_first_child()) is not None:
            self._list.remove(child)
        q = query.lower()
        for ident, name in self._apps:
            if q and q not in name.lower() and q not in ident.lower():
                continue
            row = Adw.ActionRow(title=name, subtitle=ident)
            row.ident = ident
            self._list.append(row)

    def _filter(self, entry: Gtk.SearchEntry) -> None:
        self._rebuild(entry.get_text())

    def _choose(self, _btn: Gtk.Button) -> None:
        row = self._list.get_selected_row()
        if row is None:
            return
        self._callback(row.ident, row.get_title())
        self.close()


class MxPadWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="MX Pad")
        self.set_default_size(1080, 720)
        self._selected = 0
        self._suspend = False
        self.runtime = PadRuntime(on_status=self._on_status, on_event=self._on_event)
        self._build()
        self.runtime.start()
        self.connect("close-request", self._on_close)

    def _build(self) -> None:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="MX Pad", subtitle="Logitech MX Keypad"))
        self._adapt = Gtk.Switch()
        self._adapt.set_valign(Gtk.Align.CENTER)
        self._adapt.set_active(bool(self.runtime.cfg.get("adapt_to_app")))
        self._adapt.connect("notify::active", self._toggle_adapt)
        adapt_row = Gtk.Box(spacing=8)
        adapt_row.append(Gtk.Label(label="Adapt to app"))
        adapt_row.append(self._adapt)
        header.pack_end(adapt_row)
        add = Gtk.Button.new_from_icon_name("list-add-symbolic")
        add.set_tooltip_text("New profile")
        add.connect("clicked", self._add_profile)
        header.pack_start(add)
        toolbar.add_top_bar(header)

        split = Adw.NavigationSplitView(vexpand=True)
        split.set_sidebar(self._sidebar())
        split.set_content(self._content())
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(split)
        self._status = Gtk.Label(xalign=0)
        self._status.add_css_class("dim-label")
        self._status.set_margin_start(16)
        self._status.set_margin_end(16)
        self._status.set_margin_bottom(8)
        self._status.set_text("Starting…")
        outer.append(self._status)
        toolbar.set_content(outer)
        self.set_content(toolbar)
        self._refresh_sidebar()
        self._refresh_pages()
        self._refresh_grid()
        self._load_inspector()
        GLib.timeout_add(1000, self._tick_live)

    def _sidebar(self) -> Adw.NavigationPage:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._profile_list = Gtk.ListBox()
        self._profile_list.add_css_class("navigation-sidebar")
        self._profile_list.connect("row-selected", self._profile_selected)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self._profile_list)
        box.append(scroller)
        return Adw.NavigationPage(title="Profiles", child=box)

    def _content(self) -> Adw.NavigationPage:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        self._page_bar = Gtk.Box(spacing=6)
        box.append(self._page_bar)
        self._grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        self._grid.add_css_class("pad-grid")
        self._grid.set_halign(Gtk.Align.CENTER)
        self._tiles: list[Gtk.Button] = []
        for i in range(9):
            btn = Gtk.Button()
            btn.add_css_class("key-tile")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            inner.set_halign(Gtk.Align.CENTER)
            inner.set_valign(Gtk.Align.CENTER)
            icon = Gtk.Image()
            icon.set_pixel_size(36)
            lbl = Gtk.Label()
            inner.append(icon)
            inner.append(lbl)
            btn.set_child(inner)
            btn._icon = icon
            btn._label = lbl
            btn.connect("clicked", lambda _b, idx=i: self._select_key(idx))
            self._tiles.append(btn)
            self._grid.attach(btn, i % 3, i // 3, 1, 1)
        box.append(self._grid)
        nav = Gtk.Box(spacing=12)
        nav.set_halign(Gtk.Align.CENTER)
        self._p1 = Gtk.Button(label="P1")
        self._p2 = Gtk.Button(label="P2")
        self._p1.connect("clicked", lambda *_: self._edit_nav("P1"))
        self._p2.connect("clicked", lambda *_: self._edit_nav("P2"))
        nav.append(self._p1)
        nav.append(self._p2)
        box.append(nav)
        box.append(Gtk.Separator())
        box.append(self._inspector())
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(box)
        return Adw.NavigationPage(title="Keypad", child=scroller)

    def _inspector(self) -> Gtk.Widget:
        grp = Adw.PreferencesGroup(title="Selected key")
        self._label_row = Adw.EntryRow(title="Label")
        self._label_row.connect("changed", self._label_changed)
        grp.add(self._label_row)

        color_row = Adw.ActionRow(title="Background color")
        self._color_btn = Gtk.ColorDialogButton()
        self._color_btn.set_dialog(Gtk.ColorDialog())
        self._color_btn.connect("notify::rgba", self._color_changed)
        color_row.add_suffix(self._color_btn)
        grp.add(color_row)

        self._icon_row = Adw.ActionRow(title="MDI icon")
        self._icon_preview = Gtk.Image()
        self._icon_preview.set_pixel_size(28)
        self._icon_name = Gtk.Label()
        self._icon_name.add_css_class("dim-label")
        browse = Gtk.Button(label="Browse")
        browse.connect("clicked", self._pick_mdi)
        clear_icon = Gtk.Button(label="Clear")
        clear_icon.connect("clicked", self._clear_mdi)
        self._icon_row.add_prefix(self._icon_preview)
        self._icon_row.add_suffix(self._icon_name)
        self._icon_row.add_suffix(browse)
        self._icon_row.add_suffix(clear_icon)
        grp.add(self._icon_row)

        self._show_icon_row = Adw.SwitchRow(
            title="Show icon",
            subtitle="Turn off to fill the LCD with larger text",
        )
        self._show_icon_row.connect("notify::active", self._show_icon_changed)
        grp.add(self._show_icon_row)

        img_row = Adw.ActionRow(title="Custom image")
        pick = Gtk.Button(label="Choose image")
        pick.connect("clicked", self._pick_image)
        clear = Gtk.Button(label="Clear")
        clear.connect("clicked", self._clear_image)
        img_row.add_suffix(pick)
        img_row.add_suffix(clear)
        grp.add(img_row)

        self._status_row = Adw.ComboRow(title="Live status")
        self._status_row.set_model(Gtk.StringList.new([label for _id, label in METRICS]))
        self._status_row.connect("notify::selected", self._status_metric_changed)
        grp.add(self._status_row)

        self._type_row = Adw.ComboRow(title="Action")
        store = Gtk.StringList.new([label for _id, label in ACTION_TYPES])
        self._type_row.set_model(store)
        self._type_row.connect("notify::selected", self._type_changed)
        grp.add(self._type_row)

        self._hotkey_row = Adw.EntryRow(title="Shortcut (e.g. ctrl+shift+s)")
        self._hotkey_row.connect("changed", self._hotkey_changed)
        grp.add(self._hotkey_row)

        rec = Adw.ActionRow(title="Capture shortcut")
        rec_btn = Gtk.Button(label="Record next keypress")
        rec_btn.connect("clicked", self._record_hotkey)
        rec.add_suffix(rec_btn)
        grp.add(rec)
        self._record_row = rec

        self._text_row = Adw.EntryRow(title="Text to type")
        self._text_row.connect("changed", self._text_changed)
        grp.add(self._text_row)

        self._launch_row = Adw.ActionRow(title="Application")
        launch_btn = Gtk.Button(label="Choose…")
        launch_btn.connect("clicked", self._pick_app)
        self._launch_label = Gtk.Label()
        self._launch_label.add_css_class("dim-label")
        self._launch_row.add_suffix(self._launch_label)
        self._launch_row.add_suffix(launch_btn)
        grp.add(self._launch_row)

        self._open_row = Adw.EntryRow(title="File path or URL")
        self._open_row.connect("changed", self._open_changed)
        grp.add(self._open_row)

        self._cmd_row = Adw.EntryRow(title="Shell command")
        self._cmd_row.connect("changed", self._cmd_changed)
        grp.add(self._cmd_row)

        self._media_row = Adw.ComboRow(title="Media command")
        self._media_row.set_model(Gtk.StringList.new([l for _i, l in MEDIA_COMMANDS]))
        self._media_row.connect("notify::selected", self._media_changed)
        grp.add(self._media_row)

        self._system_row = Adw.ComboRow(title="System command")
        self._system_row.set_model(Gtk.StringList.new([l for _i, l in SYSTEM_COMMANDS]))
        self._system_row.connect("notify::selected", self._system_changed)
        grp.add(self._system_row)

        self._page_row = Adw.ComboRow(title="Page action")
        self._page_row.set_model(Gtk.StringList.new([l for _i, l in PAGE_WHICH]))
        self._page_row.connect("notify::selected", self._page_action_changed)
        grp.add(self._page_row)

        self._extra_rows = [
            self._hotkey_row,
            self._record_row,
            self._text_row,
            self._launch_row,
            self._open_row,
            self._cmd_row,
            self._media_row,
            self._system_row,
            self._page_row,
        ]
        return grp

    def _key(self) -> dict[str, Any]:
        return self.runtime.current_page()["keys"][self._selected]

    def _commit(self) -> None:
        if self._suspend:
            return
        self.runtime.save()
        self._refresh_grid()

    def _refresh_sidebar(self) -> None:
        self._suspend = True
        while (child := self._profile_list.get_first_child()) is not None:
            self._profile_list.remove(child)
        active = self.runtime.cfg.get("active_profile")
        for profile in self.runtime.cfg["profiles"]:
            row = Adw.ActionRow(title=profile["name"])
            row.profile_id = profile["id"]
            match = profile.get("match") or {}
            if match.get("name"):
                row.set_subtitle(f"App: {match['name']}")
            else:
                row.set_subtitle("Always / fallback")
            menu = Gtk.Box(spacing=4)
            if profile["id"] != "general":
                bind = Gtk.Button.new_from_icon_name("focus-windows-symbolic")
                bind.set_tooltip_text("Bind to focused app")
                bind.add_css_class("flat")
                bind.connect("clicked", lambda _b, p=profile: self._bind_app(p))
                menu.append(bind)
            self._profile_list.append(row)
            if profile["id"] == active:
                self._profile_list.select_row(row)
        self._suspend = False

    def _refresh_pages(self) -> None:
        while (child := self._page_bar.get_first_child()) is not None:
            self._page_bar.remove(child)
        pages = self.runtime.current_profile().get("pages") or []
        for i, page in enumerate(pages):
            btn = Gtk.ToggleButton(label=page.get("name") or f"Page {i+1}")
            btn.set_active(i == self.runtime._page_index)
            btn.connect("clicked", lambda _b, idx=i: self._switch_page(idx))
            self._page_bar.append(btn)
        add = Gtk.Button.new_from_icon_name("list-add-symbolic")
        add.set_tooltip_text("Add page")
        add.connect("clicked", self._add_page)
        self._page_bar.append(add)

    def _refresh_grid(self) -> None:
        page = self.runtime.current_page()
        keys = page.get("keys") or []
        snapshot = self.runtime.collector.snapshot()
        for i, tile in enumerate(self._tiles):
            key = keys[i] if i < len(keys) else empty_key(i)
            painted = apply_status(key, snapshot)
            tile._label.set_text(painted.get("label") or f"{i+1}")
            icon_name = painted.get("icon")
            show_icon = painted.get("show_icon", True) is not False
            pix = _pixbuf(icon_name, 36) if icon_name and show_icon else None
            if pix:
                tile._icon.set_from_pixbuf(pix)
                tile._icon.set_visible(True)
            else:
                tile._icon.clear()
                tile._icon.set_visible(False)
            tile.remove_css_class("selected")
            if i == self._selected:
                tile.add_css_class("selected")
            color = key.get("color") or "#2b303b"
            css = Gtk.CssProvider()
            css.load_from_data(f".key-tile-{i} {{ background: {color}; color: white; }}".encode())
            tile.add_css_class(f"key-tile-{i}")
            tile.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _load_inspector(self) -> None:
        self._suspend = True
        key = self._key()
        action = key.get("action") or {"type": "none"}
        kind = action.get("type") or "none"
        self._label_row.set_text(key.get("label") or "")
        icon_name = key.get("icon") or ""
        self._icon_name.set_text(icon_name or "None")
        pix = _pixbuf(icon_name, 28) if icon_name else None
        if pix:
            self._icon_preview.set_from_pixbuf(pix)
        else:
            self._icon_preview.clear()
        rgba = Gdk.RGBA()
        rgba.parse(key.get("color") or "#2b303b")
        self._color_btn.set_rgba(rgba)
        status = key.get("status") or "none"
        sidx = next((i for i, (k, _) in enumerate(METRICS) if k == status), 0)
        self._status_row.set_selected(sidx)
        self._show_icon_row.set_active(key.get("show_icon", True) is not False)
        idx = next((i for i, (k, _) in enumerate(ACTION_TYPES) if k == kind), 0)
        self._type_row.set_selected(idx)
        self._hotkey_row.set_text("+".join(action.get("keys") or []))
        self._text_row.set_text(action.get("text") or "")
        self._launch_label.set_text(action.get("id") or "")
        self._open_row.set_text(action.get("url") or action.get("path") or "")
        self._cmd_row.set_text(action.get("command") or "")
        if kind == "media":
            midx = next((i for i, (k, _) in enumerate(MEDIA_COMMANDS) if k == action.get("command")), 0)
            self._media_row.set_selected(midx)
        if kind == "system":
            sidx = next((i for i, (k, _) in enumerate(SYSTEM_COMMANDS) if k == action.get("command")), 0)
            self._system_row.set_selected(sidx)
        if kind == "page":
            pidx = next((i for i, (k, _) in enumerate(PAGE_WHICH) if k == action.get("which")), 1)
            self._page_row.set_selected(pidx)
        self._show_action_rows(kind)
        self._suspend = False

    def _show_action_rows(self, kind: str) -> None:
        mapping = {
            "hotkey": {self._hotkey_row, self._record_row},
            "text": {self._text_row},
            "launch": {self._launch_row},
            "open": {self._open_row},
            "command": {self._cmd_row},
            "media": {self._media_row},
            "system": {self._system_row},
            "page": {self._page_row},
        }
        visible = mapping.get(kind, set())
        for row in self._extra_rows:
            row.set_visible(row in visible)

    def _select_key(self, index: int) -> None:
        self._selected = index
        self._refresh_grid()
        self._load_inspector()

    def _label_changed(self, row: Adw.EntryRow) -> None:
        if self._suspend:
            return
        self._key()["label"] = row.get_text()
        self._commit()

    def _color_changed(self, *_args) -> None:
        if self._suspend:
            return
        rgba = self._color_btn.get_rgba()
        self._key()["color"] = "#{:02x}{:02x}{:02x}".format(
            int(rgba.red * 255), int(rgba.green * 255), int(rgba.blue * 255)
        )
        self._commit()

    def _tick_live(self) -> bool:
        self._refresh_grid()
        return True

    def _show_icon_changed(self, *_args) -> None:
        if self._suspend:
            return
        self._key()["show_icon"] = self._show_icon_row.get_active()
        self._commit()

    def _status_metric_changed(self, *_args) -> None:
        if self._suspend:
            return
        metric = METRICS[self._status_row.get_selected()][0]
        self._key()["status"] = None if metric == "none" else metric
        self._commit()

    def _type_changed(self, *_args) -> None:
        if self._suspend:
            return
        kind = ACTION_TYPES[self._type_row.get_selected()][0]
        action: dict[str, Any] = {"type": kind}
        if kind == "hotkey":
            action["keys"] = ["ctrl", "c"]
        elif kind == "text":
            action["text"] = ""
        elif kind == "launch":
            action["id"] = "firefox.desktop"
        elif kind == "open":
            action["url"] = "https://"
        elif kind == "command":
            action["command"] = ""
        elif kind == "media":
            action["command"] = "play-pause"
        elif kind == "system":
            action["command"] = "screenshot"
        elif kind == "page":
            action["which"] = "next"
        self._key()["action"] = action
        self._show_action_rows(kind)
        self._load_inspector()
        self._commit()

    def _hotkey_changed(self, row: Adw.EntryRow) -> None:
        if self._suspend:
            return
        keys = [p.strip().lower() for p in row.get_text().replace(" ", "+").split("+") if p.strip()]
        self._key()["action"] = {"type": "hotkey", "keys": keys}
        self._commit()

    def _text_changed(self, row: Adw.EntryRow) -> None:
        if self._suspend:
            return
        self._key()["action"] = {"type": "text", "text": row.get_text()}
        self._commit()

    def _open_changed(self, row: Adw.EntryRow) -> None:
        if self._suspend:
            return
        text = row.get_text().strip()
        field = "url" if "://" in text else "path"
        self._key()["action"] = {"type": "open", field: text}
        self._commit()

    def _cmd_changed(self, row: Adw.EntryRow) -> None:
        if self._suspend:
            return
        self._key()["action"] = {"type": "command", "command": row.get_text()}
        self._commit()

    def _media_changed(self, *_args) -> None:
        if self._suspend:
            return
        cmd = MEDIA_COMMANDS[self._media_row.get_selected()][0]
        self._key()["action"] = {"type": "media", "command": cmd}
        self._commit()

    def _system_changed(self, *_args) -> None:
        if self._suspend:
            return
        cmd = SYSTEM_COMMANDS[self._system_row.get_selected()][0]
        self._key()["action"] = {"type": "system", "command": cmd}
        self._commit()

    def _page_action_changed(self, *_args) -> None:
        if self._suspend:
            return
        which = PAGE_WHICH[self._page_row.get_selected()][0]
        self._key()["action"] = {"type": "page", "which": which}
        self._commit()

    def _pick_app(self, *_args) -> None:
        def chosen(ident: str, name: str) -> None:
            self._key()["action"] = {"type": "launch", "id": ident}
            self._key()["label"] = name.split()[0][:10]
            self._commit()
            self._load_inspector()

        AppChooser(self, chosen).present()

    def _pick_mdi(self, *_args) -> None:
        def chosen(name: str) -> None:
            self._key()["icon"] = name
            self._commit()
            self._load_inspector()

        IconChooser(self, chosen).present()

    def _clear_mdi(self, *_args) -> None:
        self._key()["icon"] = None
        self._commit()
        self._load_inspector()

    def _pick_image(self, *_args) -> None:
        dialog = Gtk.FileDialog(title="Choose key image")
        filt = Gtk.FileFilter()
        filt.add_pixbuf_formats()
        dialog.set_default_filter(filt)

        def done(dlg, result) -> None:
            try:
                file = dlg.open_finish(result)
            except GLib.Error:
                return
            path = Path(file.get_path())
            self._key()["image"] = import_image(path)
            self._commit()
            self._load_inspector()

        dialog.open(self, None, done)

    def _clear_image(self, *_args) -> None:
        self._key()["image"] = None
        self._commit()

    def _record_hotkey(self, _btn: Gtk.Button) -> None:
        self._on_status("Press a shortcut (Esc cancels)…")
        ctl = Gtk.EventControllerKey()
        self.add_controller(ctl)

        def on_key(_c, keyval, _code, state):
            if keyval == Gdk.KEY_Escape:
                self.remove_controller(ctl)
                self._on_status("Capture cancelled")
                return True
            names = []
            if state & Gdk.ModifierType.CONTROL_MASK:
                names.append("ctrl")
            if state & Gdk.ModifierType.SHIFT_MASK:
                names.append("shift")
            if state & Gdk.ModifierType.ALT_MASK:
                names.append("alt")
            if state & Gdk.ModifierType.SUPER_MASK:
                names.append("super")
            name = Gdk.keyval_name(Gdk.keyval_to_lower(keyval)) or ""
            skip = {"Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R", "Super_L", "Super_R", "Meta_L", "Meta_R"}
            if name not in skip:
                mapped = name.lower().replace("return", "enter").replace("iso_left_tab", "tab")
                names.append(mapped)
            if len(names) == 0:
                return True
            self._key()["action"] = {"type": "hotkey", "keys": names}
            self._commit()
            self._load_inspector()
            self.remove_controller(ctl)
            self._on_status("Shortcut saved")
            return True

        ctl.connect("key-pressed", on_key)

    def _profile_selected(self, _list, row) -> None:
        if self._suspend or row is None:
            return
        self.runtime.set_profile(row.profile_id)
        self._page_index_sync()
        self._refresh_pages()
        self._refresh_grid()
        self._load_inspector()

    def _page_index_sync(self) -> None:
        pass

    def _switch_page(self, index: int) -> None:
        self.runtime.set_page(index)
        self._refresh_pages()
        self._refresh_grid()
        self._load_inspector()

    def _add_page(self, *_args) -> None:
        profile = self.runtime.current_profile()
        n = len(profile["pages"]) + 1
        profile["pages"].append(empty_page(f"Page {n}"))
        self.runtime.save()
        self.runtime.set_page(n - 1)
        self._refresh_pages()
        self._refresh_grid()
        self._load_inspector()

    def _add_profile(self, *_args) -> None:
        dialog = Adw.AlertDialog(heading="New profile", body="Name this profile")
        entry = Gtk.Entry()
        entry.set_placeholder_text("Name")
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "Create")
        dialog.set_default_response("ok")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def done(dlg, task) -> None:
            try:
                response = dlg.choose_finish(task)
            except GLib.Error:
                return
            if response != "ok":
                return
            name = entry.get_text().strip() or "Profile"
            profile = new_profile(name)
            self.runtime.cfg["profiles"].append(profile)
            self.runtime.set_profile(profile["id"])
            self._refresh_sidebar()
            self._refresh_pages()
            self._refresh_grid()
            self._load_inspector()

        dialog.choose(self, None, done)

    def _bind_app(self, profile: dict) -> None:
        from mxpad.focus import focused_app_name

        name = focused_app_name()
        if not name:
            self._on_status("Could not detect the focused app")
            return
        profile["match"] = {"name": name}
        self.runtime.save()
        self._refresh_sidebar()
        self._on_status(f"“{profile['name']}” follows {name}")

    def _edit_nav(self, which: str) -> None:
        nav = self.runtime.current_profile().setdefault("nav", {})
        current = nav.get(which) or {"type": "page", "which": "next"}
        next_which = "prev" if current.get("which") != "prev" else "next"
        nav[which] = {"type": "page", "which": next_which}
        self.runtime.save()
        self._on_status(f"{which} → {next_which} page")

    def _toggle_adapt(self, switch: Gtk.Switch, *_args) -> None:
        self.runtime.cfg["adapt_to_app"] = switch.get_active()
        self.runtime.save()

    def _on_status(self, text: str) -> None:
        self._status.set_text(text)

    def _on_event(self, text: str) -> None:
        self._status.set_text(text)

    def _on_close(self, *_args) -> bool:
        self.runtime.stop()
        return False


class MxPadApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="dev.mxpad.MxPad", flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window: MxPadWindow | None = None

    def do_activate(self) -> None:
        _css()
        if self.window is None:
            self.window = MxPadWindow(self)
        self.window.present()


def main(argv: list[str] | None = None) -> int:
    app = MxPadApplication()
    return app.run(argv or sys.argv)
