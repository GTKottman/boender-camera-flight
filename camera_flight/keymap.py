"""Flight key bindings.

Every action can be bound to two keys in the add-on preferences. The defaults
follow the conventions of game engines' editor cameras (Unreal / Unity):
WASD to move, Q/E to sink/rise, Shift to boost, mouse to look.
"""

import bpy


class Action:
    __slots__ = ("id", "label", "group", "primary", "secondary", "hold")

    def __init__(self, id, label, group, primary, secondary="NONE", hold=False):
        self.id = id
        self.label = label
        self.group = group
        self.primary = primary
        self.secondary = secondary
        self.hold = hold


A = Action
ACTIONS = (
    # Movement
    A("forward", "Move Forward", "Move", "W", hold=True),
    A("back", "Move Back", "Move", "S", hold=True),
    A("left", "Strafe Left", "Move", "A", hold=True),
    A("right", "Strafe Right", "Move", "D", hold=True),
    A("up", "Rise / Jump", "Move", "E", "SPACE", hold=True),
    A("down", "Sink", "Move", "Q", hold=True),
    A("boost", "Boost", "Move", "LEFT_SHIFT", "RIGHT_SHIFT", hold=True),
    A("precision", "Precision (slow)", "Move", "LEFT_CTRL", "RIGHT_CTRL", hold=True),
    A("speed_up", "Speed Up", "Move", "PAGE_UP"),
    A("speed_down", "Speed Down", "Move", "PAGE_DOWN"),
    A("fly_to", "Fly To Crosshair", "Move", "T"),
    # Look
    A("look_left", "Pan Left", "Look", "LEFT_ARROW", hold=True),
    A("look_right", "Pan Right", "Look", "RIGHT_ARROW", hold=True),
    A("look_up", "Tilt Up", "Look", "UP_ARROW", hold=True),
    A("look_down", "Tilt Down", "Look", "DOWN_ARROW", hold=True),
    A("roll_left", "Roll Left", "Look", "Z", hold=True),
    A("roll_right", "Roll Right", "Look", "C", hold=True),
    A("level", "Level Horizon", "Look", "X"),
    A("lock", "Lock On (Shift: active object)", "Look", "L"),
    A("follow", "Follow Locked Target", "Look", "Y"),
    # Lens
    A("zoom_in", "Zoom In", "Lens", "EQUAL", "NUMPAD_PLUS", hold=True),
    A("zoom_out", "Zoom Out", "Lens", "MINUS", "NUMPAD_MINUS", hold=True),
    A("hold_zoom", "Hold to Zoom", "Lens", "RIGHTMOUSE", hold=True),
    A("autofocus", "Autofocus (hold: track)", "Lens", "F", hold=True),
    A("dolly_zoom", "Dolly Zoom Lock", "Lens", "V"),
    # Recording
    A("record", "Record / Stop", "Record", "R"),
    A("pause", "Play / Pause Time", "Record", "P"),
    A("keyframe", "Insert Keyframe", "Record", "K"),
    A("discard_take", "Discard Take", "Record", "BACK_SPACE"),
    A("slower", "Slower Time", "Record", "LEFT_BRACKET"),
    A("faster", "Faster Time", "Record", "RIGHT_BRACKET"),
    A("frame_back", "Frame Back (Shift: 10)", "Record", "COMMA"),
    A("frame_fwd", "Frame Forward (Shift: 10)", "Record", "PERIOD"),
    A("frame_start", "Jump to Start", "Record", "HOME"),
    # Modes & tools
    A("mode", "Cycle Flight Mode", "Mode", "M"),
    A("orbit", "Orbit Mode", "Mode", "O"),
    A("walk", "Walk Mode (Gravity)", "Mode", "G"),
    A("snapshot", "Snapshot Camera", "Tools", "N"),
    A("shake", "Toggle Camera Shake", "Tools", "J"),
    # View
    A("guides", "Composition Guides", "View", "B"),
    A("hud", "Toggle HUD", "View", "H"),
    A("help", "Help Overlay", "View", "F1"),
    A("shading", "Cycle Shading", "View", "F2"),
    A("overlays", "Toggle Overlays", "View", "F3"),
    A("mouse", "Release / Capture Mouse", "View", "TAB"),
    A("finish", "Finish", "Exit", "RET", "NUMPAD_ENTER"),
    A("cancel", "Cancel", "Exit", "ESC"),
)
del A

ACTIONS_BY_ID = {a.id: a for a in ACTIONS}
GROUPS = ("Move", "Look", "Lens", "Record", "Mode", "Tools", "View", "Exit")

NUMBER_KEYS = {
    "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
    "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9,
    "NUMPAD_1": 1, "NUMPAD_2": 2, "NUMPAD_3": 3, "NUMPAD_4": 4, "NUMPAD_5": 5,
    "NUMPAD_6": 6, "NUMPAD_7": 7, "NUMPAD_8": 8, "NUMPAD_9": 9,
}  # fmt: skip

WHEEL_UP = {"WHEELUPMOUSE"}
WHEEL_DOWN = {"WHEELDOWNMOUSE"}


def _event_items():
    items = []
    seen = set()
    for item in bpy.types.Event.bl_rna.properties["type"].enum_items:
        if not item.identifier or item.identifier in seen:
            continue
        seen.add(item.identifier)
        items.append((item.identifier, item.name or item.identifier, "", item.value))
    return items


# Item numbers equal Blender's event values, so ``layout.prop(..., event=True)``
# can capture a key press straight into these enums.
EVENT_ITEMS = _event_items()
EVENT_NAMES = {identifier: name for identifier, name, _d, _v in EVENT_ITEMS}

_SHORT_NAMES = {
    "LEFT_SHIFT": "Shift", "RIGHT_SHIFT": "R-Shift", "LEFT_CTRL": "Ctrl", "RIGHT_CTRL": "R-Ctrl",
    "LEFT_ALT": "Alt", "RIGHT_ALT": "R-Alt", "LEFT_ARROW": "←", "RIGHT_ARROW": "→",
    "UP_ARROW": "↑", "DOWN_ARROW": "↓", "RET": "Enter", "NUMPAD_ENTER": "Num Enter",
    "ESC": "Esc", "SPACE": "Space", "TAB": "Tab", "BACK_SPACE": "Backspace",
    "LEFT_BRACKET": "[", "RIGHT_BRACKET": "]", "COMMA": ",", "PERIOD": ".", "EQUAL": "=",
    "MINUS": "-", "NUMPAD_PLUS": "Num +", "NUMPAD_MINUS": "Num -", "PAGE_UP": "PgUp",
    "PAGE_DOWN": "PgDn", "RIGHTMOUSE": "RMB", "LEFTMOUSE": "LMB", "MIDDLEMOUSE": "MMB",
    "HOME": "Home", "END": "End",
}  # fmt: skip


def key_name(event_type):
    if not event_type or event_type == "NONE":
        return ""
    return _SHORT_NAMES.get(event_type) or EVENT_NAMES.get(event_type, event_type)


class Bindings:
    """Resolved key map for a flight session."""

    def __init__(self, prefs):
        self.keys_for = {}
        self.by_event = {}
        for action in ACTIONS:
            keys = []
            for attr in ("key_" + action.id, "key2_" + action.id):
                key = getattr(prefs, attr, None) if prefs is not None else None
                if key is None:
                    key = action.primary if attr.startswith("key_") else action.secondary
                if key and key != "NONE" and key not in keys:
                    keys.append(key)
            self.keys_for[action.id] = keys
            for key in keys:
                self.by_event.setdefault(key, []).append(action.id)

    def actions(self, event_type):
        return self.by_event.get(event_type, ())

    def first(self, action_id):
        keys = self.keys_for.get(action_id, ())
        return key_name(keys[0]) if keys else "—"

    def label(self, action_id):
        return " / ".join(key_name(k) for k in self.keys_for.get(action_id, ())) or "—"

    def held(self, action_id, keys_down):
        for key in self.keys_for.get(action_id, ()):
            if key in keys_down:
                return True
        return False
