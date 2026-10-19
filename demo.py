#!/usr/bin/env python3
"""Paint the MX Keypad LCDs and react to button presses.

Run (after hidraw access):
  python3 demo.py
"""

from __future__ import annotations

import sys
import time

from mx_keypad import MXKeypad, default_palette

PALETTE = default_palette()
OFF = (20, 24, 28)
ON = (255, 255, 255)


def main() -> int:
    print("Opening MX Keypad…")
    with MXKeypad() as pad:
        print(f"hidraw: {pad.path}")
        pad.initialize()
        pad.paint_grid(PALETTE, [str(i) for i in range(9)])
        print("LCDs painted. Press keys (Ctrl+C to quit).")
        last_keep = time.monotonic()
        while True:
            now = time.monotonic()
            if now - last_keep > 2.5:
                pad.keepalive()
                last_keep = now
            for event in pad.read_events(0.2):
                print(f"{'DOWN' if event.pressed else 'UP  '} {event.key}")
                if event.index is not None:
                    color = ON if event.pressed else PALETTE[event.index]
                    pad.set_key_color(
                        event.index, color, str(event.index) if not event.pressed else "*"
                    )
                elif event.pressed:
                    # P1 / P2: flash the whole grid
                    flash = (0, 180, 80) if event.key == "P1" else (180, 80, 0)
                    pad.paint_grid([flash] * 9, [event.key] * 9)
                    time.sleep(0.15)
                    pad.paint_grid(PALETTE, [str(i) for i in range(9)])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        print(
            "If the device is plugged in, copy udev/99-mx-keypad.rules "
            "to /etc/udev/rules.d/ and replug, or run with sudo.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nbye")
        raise SystemExit(0)
