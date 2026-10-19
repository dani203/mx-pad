# Reverse-engineering notes

## Artifacts examined (REA)

- Windows Options+ installer (PE x86-64, WiX Burn 2.8.1479): Ghidra string
  search for Keypad / HID++ / `c354` returned **no matches**. The binary is an
  installer UI that downloads Options+; it is not the device stack.
- macOS Options+ installer `.app` stub: same downloader role
  (`updates.optionsplus.logitechg.com`). Universal Mach-O; Ghidra refused the
  fat binary (`architecture_unsupported`).

## Live device (this computer)

- USB `046d:c354`, hid name `Logitech MX Keypad`
- Vendor usage page `0xFF43`
  - usage `0x1A02` report `0x11` (20-byte HID++ short, Input+Output)
  - usage `0x1A08` report `0x13` (32-byte HID++ long, Output+Feature)
  - usage `0x1A10` report `0x14` (4094+1 byte LCD Feature)
  - plus System Control and Consumer Control collections

## How Options+ talks to the pad

Host HID++ 2.0 on reports `0x11`/`0x13`, LCD JPEG stream on `0x14`.

Init (ReprogramControlsV4, feature index `0x0b`):

```
11 ff 0b 3b 01 a1 03  …  subscribe P1 (hidId 0x01a1)
11 ff 0b 3b 01 a2 03  …  subscribe P2 (hidId 0x01a2)
```

Keepalive (feature index `0x04`, 3000 ms):

```
11 ff 04 1d 0b b8 …
```

LCD first fragment:

```
14 ff 02 2b [flags] 01 00 01 00  [x][y][w][h]  00  [jpeg_len]  JPEG…
```

Flags: index | 0x20 | 0x80 if first | 0x40 if last.
Geometry for key `i`: x = 23 + col*(118+40), y = 6 + row*(118+40).

Grid presses arrive as HID++ long (`0x13`) hidIds 1..9, or a short-report quirk
with the 1..9 id at byte 6. P1/P2 arrive on short report `11 ff 0b 00 01 a1/a2`.
