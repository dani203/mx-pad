"""Home Assistant REST client for MX Pad keys.

Credentials live in ~/.config/mxpad/homeassistant.json (mode 0600), not in
the shared keypad layout. Prefer the browser OAuth login; a long-lived access
token from the HA profile still works as a fallback.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import colorsys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from mxpad.config import CONFIG_DIR, ensure_dirs

CREDENTIALS_PATH = CONFIG_DIR / "homeassistant.json"

BRIGHTNESS_STEP_PCT = 15
COLOR_TEMP_STEP_K = 300
CLIMATE_TEMP_STEP = 1.0

# Per-domain keypad actions. Synthetic ids (brightness_up, …) expand to turn_on/set_* with data.
DOMAIN_SERVICES: dict[str, list[tuple[str, str]]] = {
    "light": [
        ("toggle", "Toggle"),
        ("turn_on", "Turn on"),
        ("turn_off", "Turn off"),
        ("brightness_up", "Brightness up"),
        ("brightness_down", "Brightness down"),
        ("color_temp_warmer", "Warmer"),
        ("color_temp_cooler", "Cooler"),
    ],
    "switch": [("toggle", "Toggle"), ("turn_on", "Turn on"), ("turn_off", "Turn off")],
    "fan": [
        ("toggle", "Toggle"),
        ("turn_on", "Turn on"),
        ("turn_off", "Turn off"),
        ("increase_speed", "Speed up"),
        ("decrease_speed", "Speed down"),
    ],
    "cover": [
        ("toggle", "Toggle"),
        ("open_cover", "Open"),
        ("close_cover", "Close"),
        ("stop_cover", "Stop"),
        ("open_cover_tilt", "Tilt open"),
        ("close_cover_tilt", "Tilt close"),
    ],
    "lock": [("lock", "Lock"), ("unlock", "Unlock"), ("open", "Open")],
    "scene": [("turn_on", "Activate")],
    "script": [("turn_on", "Run"), ("turn_off", "Stop"), ("toggle", "Toggle")],
    "automation": [
        ("trigger", "Trigger"),
        ("toggle", "Toggle"),
        ("turn_on", "Enable"),
        ("turn_off", "Disable"),
    ],
    "input_boolean": [("toggle", "Toggle"), ("turn_on", "Turn on"), ("turn_off", "Turn off")],
    "input_button": [("press", "Press")],
    "button": [("press", "Press")],
    "media_player": [
        ("media_play_pause", "Play / pause"),
        ("media_play", "Play"),
        ("media_pause", "Pause"),
        ("media_stop", "Stop"),
        ("media_next_track", "Next"),
        ("media_previous_track", "Previous"),
        ("volume_up", "Volume up"),
        ("volume_down", "Volume down"),
        ("volume_mute", "Mute"),
        ("toggle", "Toggle power"),
        ("turn_on", "Turn on"),
        ("turn_off", "Turn off"),
    ],
    "climate": [
        ("turn_on", "Turn on"),
        ("turn_off", "Turn off"),
        ("temperature_up", "Temperature up"),
        ("temperature_down", "Temperature down"),
    ],
    "vacuum": [
        ("start", "Start"),
        ("pause", "Pause"),
        ("stop", "Stop"),
        ("return_to_base", "Dock"),
        ("locate", "Locate"),
        ("clean_spot", "Spot clean"),
    ],
    "siren": [("toggle", "Toggle"), ("turn_on", "Turn on"), ("turn_off", "Turn off")],
    "humidifier": [("toggle", "Toggle"), ("turn_on", "Turn on"), ("turn_off", "Turn off")],
    "remote": [("toggle", "Toggle"), ("turn_on", "Turn on"), ("turn_off", "Turn off")],
    "alarm_control_panel": [
        ("alarm_disarm", "Disarm"),
        ("alarm_arm_home", "Arm home"),
        ("alarm_arm_away", "Arm away"),
        ("alarm_arm_night", "Arm night"),
    ],
    "valve": [
        ("toggle", "Toggle"),
        ("open_valve", "Open"),
        ("close_valve", "Close"),
        ("stop_valve", "Stop"),
    ],
    "water_heater": [("turn_on", "Turn on"), ("turn_off", "Turn off")],
    "lawn_mower": [("start_mowing", "Start"), ("pause", "Pause"), ("dock", "Dock")],
    "timer": [
        ("start", "Start"),
        ("pause", "Pause"),
        ("cancel", "Cancel"),
        ("finish", "Finish"),
    ],
    "counter": [("increment", "Increment"), ("decrement", "Decrement"), ("reset", "Reset")],
    "camera": [
        ("enable_motion_detection", "Motion on"),
        ("disable_motion_detection", "Motion off"),
    ],
    "input_number": [("increment", "Increment"), ("decrement", "Decrement")],
    "number": [("increment", "Increment"), ("decrement", "Decrement")],
}

# Keypad action ids that are not HA service names (resolved in build_service_call).
SYNTHETIC_SERVICES = frozenset(
    {
        "brightness_up",
        "brightness_down",
        "color_temp_warmer",
        "color_temp_cooler",
        "temperature_up",
        "temperature_down",
        "increment",
        "decrement",
    }
)

SWITCHABLE = tuple(DOMAIN_SERVICES.keys())

STATUS_SKIP = (
    "persistent_notification",
    "conversation",
    "tts",
    "stt",
    "event",
    "todo",
    "calendar",
    "zone",
    "device_tracker",
)

DOMAIN_ICONS = {
    "light": "lightbulb",
    "switch": "light-switch",
    "fan": "fan",
    "cover": "window-shutter",
    "lock": "lock",
    "scene": "palette",
    "script": "script-text",
    "automation": "robot",
    "input_boolean": "toggle-switch",
    "input_button": "gesture-tap-button",
    "button": "gesture-tap-button",
    "media_player": "speaker",
    "climate": "thermostat",
    "vacuum": "robot-vacuum",
    "siren": "bullhorn",
    "humidifier": "air-humidifier",
    "remote": "remote",
    "alarm_control_panel": "shield-home",
    "valve": "pipe-valve",
    "sensor": "chart-line",
    "binary_sensor": "checkbox-marked-circle-outline",
    "weather": "weather-partly-cloudy",
    "person": "account",
    "sun": "white-balance-sunny",
    "number": "numeric",
    "input_number": "numeric",
    "input_select": "form-select",
    "select": "form-select",
    "water_heater": "water-boiler",
    "timer": "timer-outline",
    "counter": "counter",
    "camera": "cctv",
    "lawn_mower": "robot-mower",
    "input_number": "numeric",
    "number": "numeric",
}

HA_STATUS_PREFIX = "ha:"

# Fallback labels when no entity is selected yet.
HA_SERVICES = [
    ("toggle", "Toggle"),
    ("turn_on", "Turn on"),
    ("turn_off", "Turn off"),
]


@dataclass(frozen=True)
class Instance:
    name: str
    url: str


OFF_COLOR = "#1c1f24"
ON_COLOR = "#c9a227"
SWITCH_ON = "#3d9b5f"
OPEN_COLOR = "#3d7ea6"
LOCKED_COLOR = "#4a7d5a"
UNLOCKED_COLOR = "#a65c3d"
COLOR_DOMAINS = {
    "light",
    "switch",
    "fan",
    "input_boolean",
    "cover",
    "lock",
    "binary_sensor",
    "siren",
    "media_player",
}


@dataclass(frozen=True)
class Entity:
    entity_id: str
    name: str
    domain: str
    state: str
    unit: str = ""
    rgb: tuple[int, int, int] | None = None
    hs: tuple[float, float] | None = None
    kelvin: int | None = None
    brightness: int | None = None
    color_modes: tuple[str, ...] = ()
    min_kelvin: int | None = None
    max_kelvin: int | None = None
    temperature: float | None = None
    min_temp: float | None = None
    max_temp: float | None = None
    target_temp_step: float | None = None
    number_value: float | None = None
    number_min: float | None = None
    number_max: float | None = None
    number_step: float | None = None
    supported_features: int = 0

    @property
    def icon(self) -> str:
        return DOMAIN_ICONS.get(self.domain, "home-assistant")

    @property
    def status_key(self) -> str:
        return f"{HA_STATUS_PREFIX}{self.entity_id}"

    @property
    def supports_brightness(self) -> bool:
        modes = {str(m) for m in self.color_modes}
        if modes == {"onoff"}:
            return False
        if self.brightness is not None:
            return True
        bright_modes = {"brightness", "color_temp", "hs", "xy", "rgb", "rgbw", "rgbww", "white"}
        if modes & bright_modes:
            return True
        return not modes

    @property
    def supports_color_temp(self) -> bool:
        if self.kelvin is not None:
            return True
        return "color_temp" in {str(m) for m in self.color_modes}

    @property
    def supports_tilt(self) -> bool:
        # cover.SUPPORT_OPEN_TILT = 16, CLOSE_TILT = 32 (legacy bitflags)
        return bool(self.supported_features & (16 | 32))

    def status_text(self, width: int = 10) -> str:
        state = (self.state or "").strip()
        if state in ("unavailable", "unknown", ""):
            text = "—"
        elif self.unit:
            text = f"{state}{self.unit}".replace(" ", "")
        elif state in ("on", "off", "open", "closed", "locked", "unlocked", "home", "away"):
            text = state[:1].upper() + state[1:]
        else:
            text = state
        text = " ".join(text.split())
        if len(text) <= width:
            return text
        return text[: width - 1] + "…"

    def status_colors(self) -> tuple[str | None, str | None]:
        """LCD background and contrasting label color, if this entity has a visual state."""
        if self.domain not in COLOR_DOMAINS:
            return None, None
        state = (self.state or "").lower()
        if state in ("unavailable", "unknown", ""):
            return "#2a2e35", "#9aa0a6"
        if self.domain == "light":
            bg = _light_color(self, state)
        elif self.domain == "cover":
            bg = OPEN_COLOR if state in ("open", "opening") else OFF_COLOR
        elif self.domain == "lock":
            bg = LOCKED_COLOR if state == "locked" else UNLOCKED_COLOR
        elif self.domain == "media_player":
            bg = ON_COLOR if state in ("on", "playing", "paused") else OFF_COLOR
        else:
            bg = SWITCH_ON if state in ("on", "true", "home") else OFF_COLOR
        return bg, _contrast_text(bg)


class HomeAssistantError(RuntimeError):
    pass


def _normalize_url(url: str) -> str:
    text = (url or "").strip().rstrip("/")
    if text and "://" not in text:
        text = "http://" + text
    return text


def load_credentials() -> dict[str, Any]:
    ensure_dirs()
    if not CREDENTIALS_PATH.is_file():
        return {}
    try:
        with CREDENTIALS_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_credentials(data: dict[str, Any]) -> None:
    ensure_dirs()
    payload = {
        "url": _normalize_url(str(data.get("url") or "")),
        "token": str(data.get("token") or "").strip(),
        "refresh_token": str(data.get("refresh_token") or "").strip(),
        "client_id": str(data.get("client_id") or "").strip(),
        "expires_at": data.get("expires_at") or 0,
        "auth": str(data.get("auth") or ("oauth" if data.get("refresh_token") else "token")),
        "name": str(data.get("name") or "").strip(),
        "insecure": bool(data.get("insecure")),
    }
    tmp = CREDENTIALS_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, CREDENTIALS_PATH)
    os.chmod(CREDENTIALS_PATH, 0o600)
    from mxpad.ha_stream import hub

    hub.reconnect()


def clear_credentials() -> None:
    try:
        CREDENTIALS_PATH.unlink()
    except FileNotFoundError:
        pass
    from mxpad.ha_stream import hub

    hub.stop()


def is_configured() -> bool:
    creds = load_credentials()
    return bool(creds.get("url") and creds.get("token"))


def discover_instances(timeout: float = 3.0) -> list[Instance]:
    found: dict[str, Instance] = {}
    try:
        addr = socket.getaddrinfo("homeassistant.local", 8123, type=socket.SOCK_STREAM)
        if addr:
            found["http://homeassistant.local:8123"] = Instance(
                "homeassistant.local", "http://homeassistant.local:8123"
            )
    except OSError:
        pass
    try:
        proc = subprocess.run(
            ["avahi-browse", "-p", "-t", "-r", "_home-assistant._tcp"],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        proc = None
    if proc and proc.stdout:
        for line in proc.stdout.splitlines():
            if not line.startswith("="):
                continue
            parts = line.split(";")
            if len(parts) < 8:
                continue
            name = parts[3] or "Home Assistant"
            host = parts[6].rstrip(".")
            port = parts[7] or "8123"
            txt = ";".join(parts[8:])
            use_tls = "internal_url=https" in txt or "base_url=https" in txt
            scheme = "https" if use_tls or port == "443" else "http"
            if host.endswith(".local"):
                url = f"{scheme}://{host}:{port}"
            else:
                url = f"{scheme}://{host}:{port}"
            found[url] = Instance(name=name, url=_normalize_url(url))
    return sorted(found.values(), key=lambda item: item.name.lower())


def _form_post(url: str, fields: dict[str, str], insecure: bool, timeout: float = 12.0) -> dict[str, Any]:
    payload = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context(insecure)) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:180]
        raise HomeAssistantError(f"Home Assistant HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise HomeAssistantError(f"Cannot reach Home Assistant: {exc.reason}") from exc
    try:
        data = json.loads(raw.decode())
    except json.JSONDecodeError as exc:
        raise HomeAssistantError("Home Assistant returned a non-JSON token response") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise HomeAssistantError("Home Assistant did not return an access token")
    return data


def _store_tokens(url: str, tokens: dict[str, Any], *, client_id: str, insecure: bool, auth: str) -> dict[str, Any]:
    expires_in = int(tokens.get("expires_in") or 1800)
    payload = {
        "url": url,
        "token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token") or load_credentials().get("refresh_token") or "",
        "client_id": client_id,
        "expires_at": time.time() + max(expires_in - 60, 30),
        "auth": auth,
        "insecure": insecure,
        "name": load_credentials().get("name") or "",
    }
    save_credentials(payload)
    return payload


def refresh_oauth() -> str:
    creds = load_credentials()
    refresh = str(creds.get("refresh_token") or "").strip()
    client_id = str(creds.get("client_id") or "").strip()
    base = _normalize_url(str(creds.get("url") or ""))
    if not (refresh and client_id and base):
        raise HomeAssistantError("Home Assistant login expired; sign in again")
    tokens = _form_post(
        f"{base}/auth/token",
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
        },
        bool(creds.get("insecure")),
    )
    stored = _store_tokens(
        base,
        tokens,
        client_id=client_id,
        insecure=bool(creds.get("insecure")),
        auth="oauth",
    )
    return str(stored["token"])


def authorize_in_browser(
    url: str,
    insecure: bool = False,
    timeout: float = 180.0,
    abort: threading.Event | None = None,
) -> dict[str, Any]:
    """OAuth2 authorization-code login in the user's browser (HA IndieAuth)."""
    base = _normalize_url(url)
    if not base:
        raise HomeAssistantError("Enter a Home Assistant URL first")
    abort = abort or threading.Event()
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if not query.get("code") and not query.get("error"):
                body = (
                    b"<!doctype html><title>MX Pad</title>"
                    b"<p>Waiting for Home Assistant to redirect here...</p>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if query.get("code") and query.get("state", [""])[0] == state:
                result["code"] = query["code"][0]
                body = (
                    b"<!doctype html><title>MX Pad</title>"
                    b"<p>Signed in. You can close this tab and return to MX Pad.</p>"
                )
                self.send_response(200)
            else:
                err = (query.get("error_description") or query.get("error") or ["authorization failed"])[0]
                result["error"] = err if isinstance(err, str) else "authorization failed"
                body = b"<!doctype html><title>MX Pad</title><p>Login did not complete. Return to MX Pad.</p>"
                self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            threading.Thread(target=httpd.shutdown, daemon=True).start()

        def log_message(self, fmt: str, *args: object) -> None:
            return

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    client_id = f"http://127.0.0.1:{port}/"
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": client_id,
            "response_type": "code",
            "state": state,
        }
    )
    authorize = f"{base}/auth/authorize?{query}"
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    open_authorize_url(authorize)
    try:
        deadline = time.time() + timeout
        while time.time() < deadline and not result and not abort.is_set():
            time.sleep(0.2)
    finally:
        httpd.shutdown()
        worker.join(timeout=1)
        httpd.server_close()
    if abort.is_set():
        raise HomeAssistantError("Sign-in cancelled")
    if result.get("error"):
        raise HomeAssistantError(result["error"])
    if not result.get("code"):
        raise HomeAssistantError("Sign-in timed out")
    tokens = _form_post(
        f"{base}/auth/token",
        {
            "grant_type": "authorization_code",
            "code": result["code"],
            "client_id": client_id,
        },
        insecure,
    )
    return _store_tokens(base, tokens, client_id=client_id, insecure=insecure, auth="oauth")


def open_authorize_url(authorize: str) -> None:
    try:
        from gi.repository import Gio

        Gio.AppInfo.launch_default_for_uri(authorize, None)
        return
    except Exception:
        pass
    import webbrowser

    webbrowser.open(authorize)


def _ssl_context(insecure: bool) -> ssl.SSLContext | None:
    if not insecure:
        return ssl.create_default_context()
    ctx = ssl._create_unverified_context()
    ctx.check_hostname = False
    return ctx


def request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    url: str | None = None,
    token: str | None = None,
    insecure: bool | None = None,
    timeout: float = 8.0,
) -> Any:
    creds = load_credentials()
    base = _normalize_url(url if url is not None else str(creds.get("url") or ""))
    if insecure is None:
        insecure = bool(creds.get("insecure"))
    if token is None and creds.get("refresh_token") and creds.get("expires_at"):
        try:
            if time.time() >= float(creds.get("expires_at") or 0):
                token = refresh_oauth()
        except (TypeError, ValueError, HomeAssistantError):
            pass
    auth = (token if token is not None else str(creds.get("token") or "")).strip()
    if not base:
        raise HomeAssistantError("No Home Assistant URL configured")
    if not auth:
        raise HomeAssistantError("No Home Assistant token configured")
    payload = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base}{path}",
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context(insecure)) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 401 and token is None and creds.get("refresh_token"):
            new_token = refresh_oauth()
            return request(
                method,
                path,
                body,
                url=base,
                token=new_token,
                insecure=insecure,
                timeout=timeout,
            )
        detail = exc.read().decode("utf-8", "replace")[:180]
        raise HomeAssistantError(f"Home Assistant HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise HomeAssistantError(f"Cannot reach Home Assistant: {exc.reason}") from exc
    except TimeoutError as exc:
        raise HomeAssistantError("Home Assistant timed out") from exc
    if not raw:
        return None
    try:
        return json.loads(raw.decode())
    except json.JSONDecodeError:
        return raw.decode(errors="replace")


def ping(url: str, token: str, insecure: bool = False) -> str:
    data = request("GET", "/api/", url=url, token=token, insecure=insecure, timeout=6)
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])
    return "Connected"


def list_entities(kind: str = "action", timeout: float = 12.0) -> list[Entity]:
    from mxpad.ha_stream import hub

    cached = hub.cached_entities(kind)
    if cached is not None:
        return cached
    states = request("GET", "/api/states", timeout=timeout)
    if not isinstance(states, list):
        raise HomeAssistantError("Unexpected /api/states response")
    entities: list[Entity] = []
    for item in states:
        ent = entity_from_state(item)
        if ent is None:
            continue
        if kind == "action" and ent.domain not in SWITCHABLE:
            continue
        if kind == "status" and ent.domain in STATUS_SKIP:
            continue
        entities.append(ent)
    entities.sort(key=lambda ent: (ent.domain, ent.name.lower()))
    return entities


def entity_from_state(item: dict[str, Any] | None) -> Entity | None:
    if not isinstance(item, dict):
        return None
    entity_id = str(item.get("entity_id") or "")
    if "." not in entity_id:
        return None
    domain = entity_id.split(".", 1)[0]
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    modes = attrs.get("supported_color_modes") or attrs.get("color_modes") or []
    if not isinstance(modes, (list, tuple)):
        modes = []
    state = str(item.get("state") or "")
    number_value = _float_or_none(state) if domain in ("number", "input_number") else None
    return Entity(
        entity_id,
        str(attrs.get("friendly_name") or entity_id),
        domain,
        state,
        str(attrs.get("unit_of_measurement") or ""),
        rgb=_int_triple(attrs.get("rgb_color")),
        hs=_float_pair(attrs.get("hs_color")),
        kelvin=_kelvin(attrs),
        brightness=_int_or_none(attrs.get("brightness")),
        color_modes=tuple(str(m) for m in modes),
        min_kelvin=_int_or_none(attrs.get("min_color_temp_kelvin")),
        max_kelvin=_int_or_none(attrs.get("max_color_temp_kelvin")),
        temperature=_float_or_none(attrs.get("temperature")),
        min_temp=_float_or_none(attrs.get("min_temp")),
        max_temp=_float_or_none(attrs.get("max_temp")),
        target_temp_step=_float_or_none(attrs.get("target_temp_step")),
        number_value=number_value,
        number_min=_float_or_none(attrs.get("min")),
        number_max=_float_or_none(attrs.get("max")),
        number_step=_float_or_none(attrs.get("step")),
        supported_features=_int_or_none(attrs.get("supported_features")) or 0,
    )


def services_for_domain(domain: str) -> list[tuple[str, str]]:
    return list(DOMAIN_SERVICES.get(domain or "", ()))


def _lookup_entity(entity_id: str) -> Entity | None:
    try:
        from mxpad.ha_stream import hub

        return hub.get_entity(entity_id)
    except Exception:
        return None


def services_for_entity(entity_id: str) -> list[tuple[str, str]]:
    entity_id = (entity_id or "").strip()
    if "." not in entity_id:
        return []
    domain = entity_id.split(".", 1)[0]
    options = services_for_domain(domain)
    ent = _lookup_entity(entity_id)
    if ent is None:
        return options
    skip: set[str] = set()
    if domain == "light":
        if not ent.supports_brightness:
            skip.update({"brightness_up", "brightness_down"})
        if not ent.supports_color_temp:
            skip.update({"color_temp_warmer", "color_temp_cooler"})
    elif domain == "cover" and ent.supported_features and not ent.supports_tilt:
        skip.update({"open_cover_tilt", "close_cover_tilt"})
    elif domain == "climate" and ent.temperature is None and ent.min_temp is None:
        skip.update({"temperature_up", "temperature_down"})
    if not skip:
        return options
    return [(svc, label) for svc, label in options if svc not in skip]


def default_service(entity_id: str) -> str:
    options = services_for_entity(entity_id)
    if not options:
        return "toggle"
    preferred = (
        "toggle",
        "press",
        "media_play_pause",
        "start",
        "start_mowing",
        "trigger",
        "lock",
        "open_cover",
        "turn_on",
        "alarm_disarm",
        "increment",
    )
    ids = {svc for svc, _label in options}
    for name in preferred:
        if name in ids:
            return name
    return options[0][0]


def _service_for(entity_id: str, service: str) -> tuple[str, str]:
    domain = entity_id.split(".", 1)[0]
    wanted = (service or "").strip()
    allowed = {svc for svc, _label in services_for_domain(domain)}

    # Older builds only offered toggle / turn_on / turn_off for every entity.
    legacy = {
        ("lock", "toggle"): ("homeassistant", "toggle"),
        ("lock", "turn_on"): ("lock", "lock"),
        ("lock", "turn_off"): ("lock", "unlock"),
        ("cover", "turn_on"): ("cover", "open_cover"),
        ("cover", "turn_off"): ("cover", "close_cover"),
        ("valve", "turn_on"): ("valve", "open_valve"),
        ("valve", "turn_off"): ("valve", "close_valve"),
        ("vacuum", "turn_on"): ("vacuum", "start"),
        ("vacuum", "turn_off"): ("vacuum", "return_to_base"),
        ("timer", "turn_on"): ("timer", "start"),
        ("timer", "turn_off"): ("timer", "cancel"),
        ("timer", "toggle"): ("timer", "start"),
        ("lawn_mower", "turn_on"): ("lawn_mower", "start_mowing"),
        ("lawn_mower", "turn_off"): ("lawn_mower", "dock"),
        ("button", "toggle"): ("button", "press"),
        ("button", "turn_on"): ("button", "press"),
        ("input_button", "toggle"): ("input_button", "press"),
        ("input_button", "turn_on"): ("input_button", "press"),
        ("scene", "toggle"): ("scene", "turn_on"),
        ("media_player", "toggle"): ("media_player", "media_play_pause"),
    }
    if (domain, wanted) in legacy:
        return legacy[(domain, wanted)]

    if wanted in SYNTHETIC_SERVICES:
        return domain, wanted

    if not wanted or (allowed and wanted not in allowed):
        wanted = default_service(entity_id)
        allowed = {svc for svc, _label in services_for_domain(domain)}

    if wanted == "toggle" and "toggle" not in allowed:
        return "homeassistant", "toggle"
    return domain, wanted


def build_service_call(entity_id: str, service: str) -> tuple[str, str, dict[str, Any]]:
    """Resolve keypad service id → HA domain, service name, and service_data."""
    domain, wanted = _service_for(entity_id, service)
    data: dict[str, Any] = {"entity_id": entity_id}
    ent = _lookup_entity(entity_id)

    if wanted == "brightness_up":
        data["brightness_step_pct"] = BRIGHTNESS_STEP_PCT
        return "light", "turn_on", data
    if wanted == "brightness_down":
        data["brightness_step_pct"] = -BRIGHTNESS_STEP_PCT
        return "light", "turn_on", data
    if wanted == "color_temp_warmer":
        kelvin = (ent.kelvin if ent and ent.kelvin else 3000) - COLOR_TEMP_STEP_K
        lo = (ent.min_kelvin if ent and ent.min_kelvin else 2000)
        data["color_temp_kelvin"] = max(lo, kelvin)
        return "light", "turn_on", data
    if wanted == "color_temp_cooler":
        kelvin = (ent.kelvin if ent and ent.kelvin else 4000) + COLOR_TEMP_STEP_K
        hi = (ent.max_kelvin if ent and ent.max_kelvin else 6500)
        data["color_temp_kelvin"] = min(hi, kelvin)
        return "light", "turn_on", data
    if wanted in ("temperature_up", "temperature_down"):
        step = (ent.target_temp_step if ent and ent.target_temp_step else CLIMATE_TEMP_STEP)
        current = ent.temperature if ent and ent.temperature is not None else 20.0
        delta = step if wanted == "temperature_up" else -step
        target = current + delta
        if ent and ent.min_temp is not None:
            target = max(ent.min_temp, target)
        if ent and ent.max_temp is not None:
            target = min(ent.max_temp, target)
        data["temperature"] = round(target, 1)
        return "climate", "set_temperature", data
    if domain in ("number", "input_number") and wanted in ("increment", "decrement"):
        step = (ent.number_step if ent and ent.number_step else 1.0)
        current = ent.number_value if ent and ent.number_value is not None else 0.0
        target = current + step if wanted == "increment" else current - step
        if ent and ent.number_min is not None:
            target = max(ent.number_min, target)
        if ent and ent.number_max is not None:
            target = min(ent.number_max, target)
        data["value"] = target
        return domain, "set_value", data

    if wanted == "toggle" and "toggle" not in {s for s, _ in services_for_domain(domain)}:
        return "homeassistant", "toggle", data
    return domain, wanted, data


def call_entity(entity_id: str, service: str = "toggle") -> None:
    entity_id = (entity_id or "").strip()
    if not entity_id or "." not in entity_id:
        raise HomeAssistantError("No Home Assistant entity selected")
    domain, svc, data = build_service_call(entity_id, service)
    from mxpad.ha_stream import hub

    hub.ensure()
    hub.apply_optimistic(entity_id, service, data)
    if hub.call_service(domain, svc, data):
        return
    request("POST", f"/api/services/{domain}/{svc}", data)


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_triple(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        rgb = tuple(max(0, min(255, int(v))) for v in value[:3])
    except (TypeError, ValueError):
        return None
    return rgb[0], rgb[1], rgb[2]


def _float_pair(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _kelvin(attrs: dict[str, Any]) -> int | None:
    kelvin = _int_or_none(attrs.get("color_temp_kelvin"))
    if kelvin:
        return kelvin
    mired = _int_or_none(attrs.get("color_temp"))
    if mired and mired > 0:
        return int(1_000_000 / mired)
    return None


def _hex(rgb: tuple[float, float, float]) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _contrast_text(bg: str) -> str:
    h = bg.lstrip("#")
    if len(h) != 6:
        return "#ffffff"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#1a1a1a" if luma > 160 else "#ffffff"


def _kelvin_rgb(kelvin: int) -> tuple[float, float, float]:
    temp = max(1000, min(40000, kelvin)) / 100.0
    if temp <= 66:
        r = 255.0
        g = 99.4708025861 * (temp ** 0.5292067432) - 161.1195681661
    else:
        r = 329.698727446 * ((temp - 60) ** -0.1332047592)
        g = 288.1221695283 * ((temp - 60) ** -0.0755148492)
    if temp >= 66:
        b = 255.0
    elif temp <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * ((temp - 10) ** 0.3883376408) - 305.0447927307
    return (r, g, b)


def _scale_brightness(rgb: tuple[float, float, float], brightness: int | None) -> tuple[float, float, float]:
    if brightness is None:
        return rgb
    level = max(0.18, min(1.0, brightness / 255.0))
    return (rgb[0] * level, rgb[1] * level, rgb[2] * level)


def _light_color(entity: Entity, state: str) -> str:
    if state != "on":
        return OFF_COLOR
    if entity.rgb:
        rgb = _scale_brightness(
            (float(entity.rgb[0]), float(entity.rgb[1]), float(entity.rgb[2])),
            entity.brightness,
        )
        return _hex(rgb)
    if entity.hs:
        hue = (entity.hs[0] % 360.0) / 360.0
        sat = max(0.0, min(1.0, entity.hs[1] / 100.0))
        r, g, b = colorsys.hsv_to_rgb(hue, sat, 1.0)
        rgb = _scale_brightness((r * 255.0, g * 255.0, b * 255.0), entity.brightness)
        return _hex(rgb)
    if entity.kelvin:
        return _hex(_scale_brightness(_kelvin_rgb(entity.kelvin), entity.brightness))
    return _hex(_scale_brightness((201.0, 162.0, 39.0), entity.brightness))
