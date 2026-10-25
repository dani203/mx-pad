from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

METRICS = [
    ("none", "None"),
    ("time", "Time"),
    ("date", "Date"),
    ("weekday", "Weekday"),
    ("battery", "Battery"),
    ("cpu_temp", "CPU temperature"),
    ("gpu_temp", "GPU temperature"),
    ("cpu", "CPU usage"),
    ("ram", "Memory usage"),
    ("disk", "Disk free"),
    ("volume", "Volume"),
    ("network", "Network"),
    ("ip", "IP address"),
    ("uptime", "Uptime"),
    ("load", "Load average"),
    ("brightness", "Display brightness"),
    ("media", "Now playing"),
    ("homeassistant", "Home Assistant"),
]


@dataclass(frozen=True)
class Reading:
    text: str
    icon: str | None = None
    color: str | None = None
    text_color: str | None = None


def apply_status(key: dict, snapshot: dict[str, Reading]) -> dict:
    metric = key.get("status")
    if not metric or metric == "none":
        return key
    reading = snapshot.get(metric)
    if not reading:
        return key
    painted = dict(key)
    painted["label"] = reading.text
    if reading.icon:
        painted["icon"] = reading.icon
    if key.get("status_color"):
        if reading.color:
            painted["color"] = reading.color
        if reading.text_color:
            painted["text_color"] = reading.text_color
    return painted


class StatusCollector:
    def __init__(self) -> None:
        self._cpu_idle = 0.0
        self._cpu_total = 0.0
        self._cache: dict[str, tuple[float, Reading]] = {}
        self._ha_map: dict[str, Reading] = {}

    def snapshot(self) -> dict[str, Reading]:
        data = {
            "time": self._time(),
            "date": self._date(),
            "weekday": self._weekday(),
            "battery": self._cached("battery", 5, self._battery),
            "cpu_temp": self._cached("cpu_temp", 2, self._cpu_temp),
            "gpu_temp": self._cached("gpu_temp", 2, self._gpu_temp),
            "cpu": self._cpu(),
            "ram": self._ram(),
            "disk": self._cached("disk", 15, self._disk),
            "volume": self._cached("volume", 1.5, self._volume),
            "network": self._cached("network", 4, self._network),
            "ip": self._cached("ip", 10, self._ip),
            "uptime": self._uptime(),
            "load": self._load(),
            "brightness": self._cached("brightness", 2, self._brightness),
            "media": self._cached("media", 3, self._media),
        }
        data.update(self._ha_readings())
        return data

    def _ha_readings(self) -> dict[str, Reading]:
        from mxpad.homeassistant import is_configured
        from mxpad.ha_stream import hub

        if not is_configured():
            return {}
        hub.ensure()
        latest = hub.readings()
        if latest:
            self._ha_map = {
                key: Reading(text, icon, color, text_color)
                for key, (text, icon, color, text_color) in latest.items()
            }
        return self._ha_map

    def _cached(self, key: str, ttl: float, fn) -> Reading:
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        value = fn()
        self._cache[key] = (now, value)
        return value

    def _time(self) -> Reading:
        return Reading(time.strftime("%H:%M"), "clock-outline")

    def _date(self) -> Reading:
        return Reading(time.strftime("%d %b"), "calendar")

    def _weekday(self) -> Reading:
        return Reading(time.strftime("%a"), "calendar-week")

    def _battery(self) -> Reading:
        best: tuple[int, str] | None = None
        root = Path("/sys/class/power_supply")
        if not root.exists():
            return Reading("n/a", "battery-unknown")
        for supply in root.iterdir():
            kind = _read(supply / "type")
            if kind != "Battery":
                continue
            name = supply.name.lower()
            if "hidpp" in name or "mouse" in name or "keyboard" in name:
                continue
            cap = _read_int(supply / "capacity")
            status = (_read(supply / "status") or "").lower()
            if cap is None:
                continue
            best = (cap, status)
            break
        if best is None:
            return Reading("AC", "power-plug")
        cap, status = best
        if status == "charging":
            icon = "battery-charging"
            text = f"{cap}%"
        elif cap <= 15:
            icon = "battery-alert"
            text = f"{cap}%"
        else:
            icon = "battery"
            text = f"{cap}%"
        return Reading(text, icon)

    def _cpu_temp(self) -> Reading:
        celsius = _hwmon_temp(("k10temp", "coretemp", "zenpower", "cpu"))
        if celsius is None:
            celsius = _thermal_zone()
        if celsius is None:
            return Reading("n/a", "thermometer")
        return Reading(f"{celsius:.0f}°", "thermometer")

    def _gpu_temp(self) -> Reading:
        celsius = _hwmon_temp(("amdgpu", "nvidia", "i915", "xe"))
        if celsius is None:
            return Reading("n/a", "expansion-card")
        return Reading(f"{celsius:.0f}°", "expansion-card")

    def _cpu(self) -> Reading:
        idle, total = _cpu_times()
        prev_idle, prev_total = self._cpu_idle, self._cpu_total
        self._cpu_idle, self._cpu_total = idle, total
        delta_total = total - prev_total
        if prev_total == 0 or delta_total <= 0:
            return Reading("…", "chip")
        busy = 1.0 - (idle - prev_idle) / delta_total
        pct = max(0, min(100, round(busy * 100)))
        return Reading(f"{pct}%", "chip")

    def _ram(self) -> Reading:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            parts = line.replace(":", " ").split()
            if len(parts) >= 2:
                info[parts[0]] = int(parts[1])
        total = info.get("MemTotal") or 1
        avail = info.get("MemAvailable") or info.get("MemFree") or 0
        used = max(0, round((total - avail) * 100 / total))
        return Reading(f"{used}%", "memory")

    def _disk(self) -> Reading:
        usage = shutil.disk_usage(Path.home())
        free_gb = usage.free / (1024**3)
        if free_gb >= 10:
            text = f"{free_gb:.0f}G"
        else:
            text = f"{free_gb:.1f}G"
        return Reading(text, "harddisk")

    def _volume(self) -> Reading:
        try:
            out = subprocess.check_output(
                ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                text=True,
                timeout=0.4,
            )
        except Exception:
            return Reading("n/a", "volume-off")
        muted = "MUTED" in out.upper()
        parts = out.split()
        try:
            value = float(parts[1])
        except (IndexError, ValueError):
            return Reading("n/a", "volume-off")
        pct = round(value * 100)
        if muted or pct == 0:
            return Reading("Mute", "volume-off")
        return Reading(f"{pct}%", "volume-high")

    def _network(self) -> Reading:
        try:
            out = subprocess.check_output(
                ["nmcli", "-t", "-f", "TYPE,STATE,CONNECTION", "device"],
                text=True,
                timeout=0.5,
            )
        except Exception:
            return Reading("n/a", "lan-disconnect")
        wifi = None
        ethernet = None
        for line in out.splitlines():
            bits = line.split(":")
            if len(bits) < 3:
                continue
            kind, state, name = bits[0], bits[1], bits[2]
            if "connected" not in state or "externally" in state:
                continue
            if kind == "wifi":
                wifi = name or "Wi-Fi"
            elif kind == "ethernet":
                ethernet = "LAN"
        if wifi:
            return Reading(_clip(wifi, 10), "wifi")
        if ethernet:
            return Reading(ethernet, "lan")
        return Reading("Off", "wifi-off")

    def _ip(self) -> Reading:
        addr = None
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("1.1.1.1", 80))
                candidate = sock.getsockname()[0]
            if candidate and not candidate.startswith("127."):
                addr = candidate
        except Exception:
            pass
        if not addr:
            try:
                out = subprocess.check_output(["hostname", "-I"], text=True, timeout=0.3)
                addr = next((item for item in out.split() if ":" not in item and not item.startswith("127.")), None)
            except Exception:
                addr = None
        if not addr:
            return Reading("n/a", "ip-network")
        return Reading(addr.split(".")[-1], "ip-network")

    def _uptime(self) -> Reading:
        try:
            seconds = float(Path("/proc/uptime").read_text().split()[0])
        except Exception:
            return Reading("n/a", "timer-outline")
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        if hours >= 24:
            days = hours // 24
            text = f"{days}d"
        elif hours:
            text = f"{hours}h"
        else:
            text = f"{minutes}m"
        return Reading(text, "timer-outline")

    def _load(self) -> Reading:
        try:
            load1 = os.getloadavg()[0]
        except OSError:
            return Reading("n/a", "chart-line")
        return Reading(f"{load1:.2f}", "chart-line")

    def _brightness(self) -> Reading:
        root = Path("/sys/class/backlight")
        if not root.exists():
            return Reading("n/a", "brightness-5")
        for device in root.iterdir():
            cur = _read_int(device / "brightness")
            mx = _read_int(device / "max_brightness")
            if cur is None or not mx:
                continue
            return Reading(f"{round(cur * 100 / mx)}%", "brightness-6")
        return Reading("n/a", "brightness-5")

    def _media(self) -> Reading:
        try:
            out = subprocess.check_output(
                ["playerctl", "metadata", "--format", "{{status}}|{{title}}"],
                text=True,
                timeout=0.4,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            return Reading("—", "music-off")
        if "|" not in out:
            return Reading("—", "music-off")
        status, title = out.split("|", 1)
        icon = "pause" if status.lower() == "playing" else "play"
        return Reading(_clip(title.strip() or "—", 10), icon)


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _read_int(path: Path) -> int | None:
    text = _read(path)
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _hwmon_temp(names: tuple[str, ...]) -> float | None:
    root = Path("/sys/class/hwmon")
    if not root.exists():
        return None
    for hwmon in root.iterdir():
        name = (_read(hwmon / "name") or "").lower()
        if name not in names:
            continue
        value = _read_int(hwmon / "temp1_input")
        if value is None:
            continue
        return value / 1000.0
    return None


def _thermal_zone() -> float | None:
    best = None
    root = Path("/sys/class/thermal")
    if not root.exists():
        return None
    for zone in root.glob("thermal_zone*"):
        value = _read_int(zone / "temp")
        if value is None or value <= 0:
            continue
        celsius = value / 1000.0
        if 20 <= celsius <= 110:
            best = celsius if best is None else max(best, celsius)
    return best


def _cpu_times() -> tuple[float, float]:
    for line in Path("/proc/stat").read_text().splitlines():
        if line.startswith("cpu "):
            parts = [float(x) for x in line.split()[1:]]
            idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
            return idle, sum(parts)
    return 0.0, 0.0


def _clip(text: str, width: int) -> str:
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"
