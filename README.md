# MX Pad

Linux host for the Logitech MX Keypad (`046d:c354`). Same HID++ / LCD path
Logi Options+ uses, with a GNOME app for the things Options+ maps on the
keypad: pages, profiles, shortcuts, launchers, media, and system actions.

This app reimplements the keypad’s **press-action** model from the live HID
report descriptor and HID++ / LCD packet layout Options+ uses. It does not
bundle Options+ or vendor plugins (Photoshop etc. still work via keyboard
shortcuts).

<img width="1077" height="1221" alt="image" src="https://github.com/user-attachments/assets/65056e45-1516-4f18-b7fc-a6478ccc3afc" />



## Run

```bash
# hidraw + uinput access (once)
sudo cp udev/99-mx-keypad.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
# unplug/replug the keypad if hidraw is still root-only

python3 mxpad_app.py
```

Optional launcher (set `Path=` in the desktop file to this clone, then):

```bash
cp dev.mxpad.MxPad.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications/ 2>/dev/null || true
```

Needs Python 3, GTK 4, libadwaita, PyGObject, Pillow (`python3-gobject`,
`python3-pillow`, `libadwaita` on Fedora).

Config lives in `~/.config/mxpad/config.json`. Home Assistant URL and token
are stored separately in `~/.config/mxpad/homeassistant.json` (mode 0600).

## Home Assistant

Toolbar **Home Assistant**: discover `_home-assistant._tcp` on the LAN (Avahi)
or paste a URL such as `http://homeassistant.local:8123`. **Sign in with Home
Assistant** opens your browser on HA’s own login (OAuth). A long-lived access
token (HA profile → Security) still works as a fallback. Then set a key’s
action to Home Assistant and pick an entity (lights, locks, timers, vacuums,
covers, media players, …). The command list only shows services that fit that
entity (lock/unlock, brightness up/down, warmer/cooler, climate ±°, and so on).
**Live status → Home Assistant** paints a sensor, light, or other entity’s
current state on the LCD (Home Assistant websocket, no REST polling). **Color from status**
tints the key from on/off, RGB, and color temperature when the entity is a
light or switch.

## What you can assign to a key

| Action | Like Options+ |
|--------|----------------|
| Keyboard shortcut | System → Keyboard shortcut |
| Type text | Smart Action “type” |
| Launch application | Open application |
| Open file or URL | Open file / web page |
| Run command | Custom / Smart Action |
| Media playback | Play, next, mute, volume |
| System / settings | Screenshot, lock, night light, brightness, desktop |
| Switch to page | Jump to a named page (or previous / next) |
| Submenu | Temporary 9-key overlay; P1 or Back leaves it |
| Hold action | Optional long-press action on the same key |
| Home Assistant | Toggle lights, switches, scenes, and other entities |
| P1 / P2 | Page previous / next (P1 closes a submenu) |
| MDI icon | Material Design Icons on the LCD (Browse in the key editor) |

Pages sit under each profile (Media, Apps, Keys, **Status** out of the box). Extra profiles
can bind to the focused app (“Adapt to app”), matching Options+ application
profiles.

The low-level HID demo is still `python3 demo.py`.

## Doom easter egg

On the keypad (not the desktop window), enter the Konami code on the 3×3:
**up, up, down, down, left, right, left, right, fire, use** — the middle-top,
middle-bottom, middle-left, middle-right keys, then lower-right and lower-left.

The nine LCDs become a bezeled 3×3 view of Doom. The same keys are the
controller; **P1** quits, **P2** opens the automap. First launch needs `gcc`,
`git`, and network once to build [doomgeneric](https://github.com/ozkl/doomgeneric)
and fetch [Freedoom](https://freedoom.github.io/) into `~/.cache/mxpad/`.
Drop `doom1.wad` / `doom.wad` in `~/.config/mxpad/` if you already own Doom.
We do not ship commercial IWADs.
