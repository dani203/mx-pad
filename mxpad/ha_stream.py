"""One Home Assistant websocket: live states without polling REST."""

from __future__ import annotations

import json
import os
import socket
import ssl
import struct
import threading
import time
import urllib.parse
from typing import Any, Callable

Listener = Callable[[str | None], None]


class HaHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._states: dict[str, Any] = {}
        self._ready = threading.Event()
        self._msg_id = 1
        self._states_id = 0
        self._listeners: list[Listener] = []
        self._send_lock = threading.Lock()

    def add_listener(self, fn: Listener) -> None:
        if fn not in self._listeners:
            self._listeners.append(fn)

    def ensure(self) -> None:
        from mxpad.homeassistant import is_configured

        if not is_configured():
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mxpad-ha", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        self._ready.clear()
        with self._lock:
            self._states.clear()

    def reconnect(self) -> None:
        self.stop()
        from mxpad.homeassistant import is_configured

        if is_configured():
            self._stop.clear()
            self.ensure()

    def cached_entities(self, kind: str):
        from mxpad.homeassistant import STATUS_SKIP, SWITCHABLE

        if not self._ready.is_set():
            return None
        with self._lock:
            items = list(self._states.values())
        if kind == "action":
            items = [ent for ent in items if ent.domain in SWITCHABLE]
        elif kind == "status":
            items = [ent for ent in items if ent.domain not in STATUS_SKIP]
        items.sort(key=lambda ent: (ent.domain, ent.name.lower()))
        return items

    def readings(self) -> dict[str, tuple[str, str | None, str | None, str | None]]:
        from mxpad.homeassistant import STATUS_SKIP

        with self._lock:
            items = list(self._states.values())
        out = {}
        for ent in items:
            if ent.domain in STATUS_SKIP:
                continue
            color, text_color = ent.status_colors()
            out[ent.status_key] = (ent.status_text(), ent.icon, color, text_color)
        return out

    def call_service(self, domain: str, service: str, entity_id: str) -> bool:
        self.ensure()
        self._optimistic(entity_id, service)
        if not self._ready.is_set():
            return False
        try:
            self._send(
                {
                    "id": self._next_id(),
                    "type": "call_service",
                    "domain": domain,
                    "service": service,
                    "service_data": {"entity_id": entity_id},
                }
            )
            return True
        except OSError:
            return False

    def _optimistic(self, entity_id: str, service: str) -> None:
        with self._lock:
            ent = self._states.get(entity_id)
            if ent is None:
                return
            state = (ent.state or "").lower()
            if service == "toggle":
                if state in ("on", "open", "unlocked", "playing"):
                    new = {"on": "off", "open": "closed", "unlocked": "locked", "playing": "paused"}.get(state, "off")
                else:
                    new = {"off": "on", "closed": "open", "locked": "unlocked", "paused": "playing"}.get(state, "on")
            elif service in ("turn_on", "open_cover", "unlock", "press"):
                new = "on" if service != "open_cover" else "open"
                if service == "unlock":
                    new = "unlocked"
            elif service in ("turn_off", "close_cover", "lock"):
                new = "off" if service != "close_cover" else "closed"
                if service == "lock":
                    new = "locked"
            else:
                return
            from mxpad.homeassistant import Entity

            updated = Entity(
                ent.entity_id,
                ent.name,
                ent.domain,
                new,
                ent.unit,
                ent.rgb,
                ent.hs,
                ent.kelvin,
                0 if new in ("off", "closed") else ent.brightness,
            )
            self._states[entity_id] = updated
        self._notify(entity_id)

    def _notify(self, entity_id: str | None) -> None:
        for fn in list(self._listeners):
            try:
                fn(entity_id)
            except Exception:
                pass

    def _next_id(self) -> int:
        with self._lock:
            self._msg_id += 1
            return self._msg_id

    def _run(self) -> None:
        delay = 2.0
        while not self._stop.is_set():
            from mxpad.homeassistant import is_configured

            if not is_configured():
                return
            try:
                self._session()
                delay = 2.0
            except Exception:
                self._ready.clear()
                self._close_sock()
            if self._stop.wait(delay):
                return
            delay = min(30.0, delay * 1.7)

    def _session(self) -> None:
        from mxpad.homeassistant import load_credentials, refresh_oauth

        creds = load_credentials()
        token = str(creds.get("token") or "")
        if creds.get("refresh_token") and creds.get("expires_at"):
            try:
                if time.time() >= float(creds.get("expires_at") or 0):
                    token = refresh_oauth()
            except Exception:
                pass
        sock = _connect(str(creds.get("url") or ""), bool(creds.get("insecure")))
        self._sock = sock
        hello = _recv_json(sock)
        if not hello or hello.get("type") != "auth_required":
            raise OSError("unexpected websocket hello")
        _send_json(sock, {"type": "auth", "access_token": token})
        ok = _recv_json(sock)
        if not ok or ok.get("type") != "auth_ok":
            raise OSError("Home Assistant websocket auth failed")
        get_id = self._next_id()
        self._states_id = get_id
        _send_json(sock, {"id": get_id, "type": "get_states"})
        _send_json(sock, {"id": self._next_id(), "type": "subscribe_events", "event_type": "state_changed"})
        last_ping = time.monotonic()
        while not self._stop.is_set():
            try:
                msg = _recv_json(sock, timeout=1.0)
            except TimeoutError:
                msg = None
            if msg:
                self._handle(msg)
            now = time.monotonic()
            if now - last_ping > 25:
                try:
                    _send_json(sock, {"id": self._next_id(), "type": "ping"})
                except OSError:
                    raise
                last_ping = now

    def _handle(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "pong":
            return
        if kind == "event":
            event = msg.get("event") or {}
            data = event.get("data") or {}
            from mxpad.homeassistant import entity_from_state

            new_state = data.get("new_state")
            ent = entity_from_state(new_state)
            entity_id = str((new_state or {}).get("entity_id") or data.get("entity_id") or "")
            with self._lock:
                if ent is None:
                    self._states.pop(entity_id, None)
                else:
                    self._states[ent.entity_id] = ent
            self._notify(ent.entity_id if ent else entity_id or None)
            return
        if kind == "result" and msg.get("id") == self._states_id:
            from mxpad.homeassistant import entity_from_state

            if not msg.get("success"):
                return
            states = {}
            for item in msg.get("result") or []:
                ent = entity_from_state(item)
                if ent:
                    states[ent.entity_id] = ent
            with self._lock:
                self._states = states
            self._ready.set()
            self._notify(None)

    def _send(self, payload: dict[str, Any]) -> None:
        sock = self._sock
        if sock is None:
            raise OSError("not connected")
        with self._send_lock:
            _send_json(sock, payload)

    def _close_sock(self) -> None:
        sock = self._sock
        self._sock = None
        if sock:
            try:
                sock.close()
            except OSError:
                pass


hub = HaHub()


def _connect(url: str, insecure: bool) -> socket.socket:
    parsed = urllib.parse.urlparse(url)
    secure = parsed.scheme == "https"
    host = parsed.hostname or "homeassistant.local"
    port = parsed.port or (443 if secure else 8123)
    path = (parsed.path.rstrip("/") or "") + "/api/websocket"
    raw = socket.create_connection((host, port), timeout=8)
    raw.settimeout(8)
    if secure:
        ctx = ssl._create_unverified_context() if insecure else ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
        sock: socket.socket = ctx.wrap_socket(raw, server_hostname=host)
    else:
        sock = raw
    import base64

    nonce = base64.b64encode(os.urandom(16)).decode()
    host_header = host if (port in (80, 443)) else f"{host}:{port}"
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {nonce}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise OSError("websocket handshake closed")
        buf += chunk
    head = buf.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1", "replace")
    if " 101 " not in head.split("\r\n", 1)[0]:
        raise OSError("websocket upgrade failed")
    sock.settimeout(1.0)
    return sock


def _send_json(sock: socket.socket, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, separators=(",", ":")).encode()
    header = bytearray([0x81])
    n = len(data)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", n))
    mask = os.urandom(4)
    header.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(bytes(header) + masked)


def _recvexact(sock: socket.socket, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise OSError("websocket closed")
        out.extend(chunk)
    return bytes(out)


def _recv_frame(sock: socket.socket) -> tuple[int, bytes]:
    hdr = _recvexact(sock, 2)
    opcode = hdr[0] & 0x0F
    length = hdr[1] & 0x7F
    masked = bool(hdr[1] & 0x80)
    if length == 126:
        length = struct.unpack("!H", _recvexact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recvexact(sock, 8))[0]
    mask = _recvexact(sock, 4) if masked else b""
    payload = _recvexact(sock, length)
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def _recv_json(sock: socket.socket, timeout: float | None = None) -> dict[str, Any] | None:
    if timeout is not None:
        sock.settimeout(timeout)
    try:
        while True:
            try:
                opcode, payload = _recv_frame(sock)
            except socket.timeout as exc:
                raise TimeoutError from exc
            if opcode == 0x8:
                raise OSError("websocket close")
            if opcode == 0x9:
                _send_pong(sock, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2):
                data = json.loads(payload.decode())
                return data if isinstance(data, dict) else None
    finally:
        if timeout is not None:
            sock.settimeout(1.0)
    return None


def _send_pong(sock: socket.socket, payload: bytes) -> None:
    header = bytearray([0x8A])
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    else:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", n))
    mask = os.urandom(4)
    header.extend(mask)
    sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
