# MX Pad

Linux host for the Logitech MX Keypad (`046d:c354`). Same HID++ / LCD path
Logi Options+ uses, with a GNOME app for the things Options+ maps on the
keypad: pages, profiles, shortcuts, launchers, media, and system actions.

This app reimplements the keypad’s **press-action** model from the live HID
report descriptor and HID++ / LCD packet layout Options+ uses. It does not
bundle Options+ or vendor plugins (Photoshop etc. still work via keyboard
shortcuts).

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

Config lives in `~/.config/mxpad/config.json`.

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
| Go to page | Navigate to page |
| P1 / P2 | Page previous / next (click P1/P2 in the UI to flip) |
| MDI icon | Material Design Icons on the LCD (Browse in the key editor) |

Pages sit under each profile (Media, Apps, Keys, **Status** out of the box). Extra profiles
can bind to the focused app (“Adapt to app”), matching Options+ application
profiles.

The low-level HID demo is still `python3 demo.py`.
