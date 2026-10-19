from __future__ import annotations

import json
import shutil
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from mxpad.defaults import default_config, status_page

APP_ID = "dev.mxpad.MxPad"
CONFIG_DIR = Path.home() / ".config" / "mxpad"
CONFIG_PATH = CONFIG_DIR / "config.json"
IMAGE_DIR = CONFIG_DIR / "images"


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    ensure_dirs()
    if not CONFIG_PATH.exists():
        cfg = default_config()
        save_config(cfg)
        return cfg
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return _migrate(data)


def save_config(cfg: dict[str, Any]) -> None:
    ensure_dirs()
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    tmp.replace(CONFIG_PATH)


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    base = default_config()
    if "profiles" not in data:
        return base
    data.setdefault("adapt_to_app", True)
    data.setdefault("active_profile", data["profiles"][0]["id"])
    _fill_missing_icons(data, base)
    _ensure_status_page(data)
    _migrate_show_icon(data)
    return data


def _migrate_show_icon(data: dict[str, Any]) -> None:
    for profile in data.get("profiles") or []:
        for page in profile.get("pages") or []:
            for key in page.get("keys") or []:
                if "show_icon" in key:
                    continue
                status = key.get("status")
                key["show_icon"] = not bool(status and status != "none")


def _ensure_status_page(data: dict[str, Any]) -> None:
    page = status_page()
    profiles = data.get("profiles") or []
    target = next((item for item in profiles if item.get("id") == "general"), None)
    if target is None and profiles:
        target = profiles[0]
    if target is None:
        return
    pages = target.setdefault("pages", [])
    if any(item.get("id") == page["id"] for item in pages):
        return
    pages.append(page)


def _fill_missing_icons(data: dict[str, Any], base: dict[str, Any]) -> None:
    defaults: dict[tuple, str] = {}
    for profile in base.get("profiles") or []:
        for page in profile.get("pages") or []:
            for key in page.get("keys") or []:
                icon = key.get("icon")
                if not icon:
                    continue
                action = key.get("action") or {}
                defaults[(page.get("id"), action.get("type"), json.dumps(action, sort_keys=True))] = icon
                defaults[("label", key.get("label"))] = icon
    for profile in data.get("profiles") or []:
        for page in profile.get("pages") or []:
            for key in page.get("keys") or []:
                if key.get("icon"):
                    continue
                action = key.get("action") or {}
                icon = defaults.get((page.get("id"), action.get("type"), json.dumps(action, sort_keys=True)))
                if not icon:
                    icon = defaults.get(("label", key.get("label")))
                if icon:
                    key["icon"] = icon


def new_profile(name: str, match: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4())[:8],
        "name": name,
        "match": match,
        "nav": {
            "P1": {"type": "page", "which": "prev"},
            "P2": {"type": "page", "which": "next"},
        },
        "pages": [empty_page("Page 1")],
    }


def empty_page(name: str) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4())[:8],
        "name": name,
        "keys": [empty_key(i) for i in range(9)],
    }


def empty_key(index: int) -> dict[str, Any]:
    return {
        "label": f"Key {index + 1}",
        "color": "#2b303b",
        "text_color": "#ffffff",
        "icon": None,
        "image": None,
        "status": None,
        "show_icon": True,
        "action": {"type": "none"},
    }


def duplicate_profile(src: dict[str, Any], name: str) -> dict[str, Any]:
    dst = deepcopy(src)
    dst["id"] = str(uuid.uuid4())[:8]
    dst["name"] = name
    return dst


def import_image(path: Path) -> str:
    ensure_dirs()
    suffix = path.suffix.lower() or ".png"
    dest = IMAGE_DIR / f"{uuid.uuid4().hex}{suffix}"
    shutil.copy2(path, dest)
    return dest.name
