from __future__ import annotations


def focused_app_name() -> str | None:
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except Exception:
        return None
    try:
        Atspi.init()
        desktop = Atspi.get_desktop(0)
        active = None
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            try:
                states = app.get_state_set()
                if states.contains(Atspi.StateType.ACTIVE):
                    active = app.get_name() or app.get_toolkit_name()
                    break
            except Exception:
                continue
        return active
    except Exception:
        return None


def profile_matches(profile: dict, app_name: str | None) -> bool:
    match = profile.get("match")
    if not match or not app_name:
        return False
    needle = (match.get("name") or match.get("wmclass") or "").lower()
    if not needle:
        return False
    hay = app_name.lower()
    return needle in hay or hay in needle
