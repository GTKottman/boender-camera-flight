"""Add-on preferences: feel (mouse, gamepad), HUD look and key bindings."""

import math
from types import SimpleNamespace

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty

from . import keymap

ADDON_ID = __package__


class CAMERA_FLIGHT_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    tab: EnumProperty(
        name="Tab",
        items=(
            ("GENERAL", "General", "Mouse and viewport"),
            ("KEYS", "Key Bindings", "Remap every flight control"),
            ("GAMEPAD", "Gamepad", "Controller support"),
            ("HUD", "HUD", "Heads-up display"),
        ),
        default="GENERAL",
    )

    # Mouse -----------------------------------------------------------------
    mouse_sensitivity: FloatProperty(
        name="Mouse Sensitivity",
        description="Degrees of camera rotation per pixel of mouse movement",
        default=0.1,
        min=0.001,
        soft_max=0.5,
        precision=3,
    )
    invert_mouse_y: BoolProperty(name="Invert Mouse Y", default=False)
    zoom_scaled_sensitivity: BoolProperty(
        name="Scale Sensitivity With Zoom",
        description="Slow the mouse down when zoomed in, like aiming down sights in a game",
        default=True,
    )

    # Viewport ----------------------------------------------------------------
    clean_viewport: BoolProperty(
        name="Clean Viewport While Flying",
        description="Hide grid, gizmos, origins, and other clutter while flying (restored afterwards)",
        default=True,
    )
    hide_header: BoolProperty(
        name="Hide Header & Toolbar",
        description="Hide the viewport header and toolbar while flying for a cinematic view",
        default=False,
    )

    # HUD ---------------------------------------------------------------------
    show_hud: BoolProperty(name="Show HUD", default=True)
    hud_scale: FloatProperty(name="HUD Scale", default=1.0, min=0.5, max=3.0)
    hud_color: FloatVectorProperty(
        name="Accent Color", subtype="COLOR", size=4, min=0.0, max=1.0, default=(0.35, 0.85, 1.0, 1.0)
    )
    show_crosshair: BoolProperty(name="Crosshair", default=True)
    show_horizon: BoolProperty(name="Artificial Horizon", default=True)
    show_path: BoolProperty(name="Take Path", description="Draw the active take's flight path", default=True)
    show_ghost: BoolProperty(
        name="Ghost", description="Show where the active take's camera is at the current frame", default=True
    )
    show_bookmarks: BoolProperty(name="Bookmark Markers", default=True)
    show_hints: BoolProperty(name="Key Hints", default=True)

    # Gamepad -----------------------------------------------------------------
    gamepad_enabled: BoolProperty(
        name="Enable Gamepad",
        description="Use a game controller (XInput on Windows, joystick device on Linux)",
        default=False,
    )
    gamepad_index: IntProperty(name="Controller Index", default=0, min=0, max=7)
    gamepad_layout: EnumProperty(
        name="Button Layout (Linux)",
        items=(("XBOX", "Xbox", "xpad / Xbox style button numbering"), ("PLAYSTATION", "PlayStation", "DualShock / DualSense")),
        default="XBOX",
    )
    gamepad_deadzone: FloatProperty(name="Stick Deadzone", default=0.12, min=0.0, max=0.6)
    gamepad_expo: FloatProperty(
        name="Stick Expo",
        description="Soften the center of the sticks for fine control (0 = linear)",
        default=0.45,
        min=0.0,
        max=1.0,
    )
    gamepad_look_speed: FloatProperty(
        name="Look Speed", subtype="ANGLE", default=math.radians(140.0), min=math.radians(5.0), max=math.radians(720.0)
    )
    gamepad_invert_y: BoolProperty(name="Invert Look Y", default=False)

    def draw(self, context):
        layout = self.layout
        layout.row().prop(self, "tab", expand=True)
        box = layout.box()
        if self.tab == "GENERAL":
            col = box.column()
            col.prop(self, "mouse_sensitivity")
            col.prop(self, "invert_mouse_y")
            col.prop(self, "zoom_scaled_sensitivity")
            col.separator()
            col.prop(self, "clean_viewport")
            col.prop(self, "hide_header")
        elif self.tab == "HUD":
            col = box.column()
            col.prop(self, "show_hud")
            sub = col.column()
            sub.active = self.show_hud
            sub.prop(self, "hud_scale")
            sub.prop(self, "hud_color")
            grid = sub.grid_flow(columns=2, even_columns=True)
            for name in ("show_crosshair", "show_horizon", "show_path", "show_ghost", "show_bookmarks", "show_hints"):
                grid.prop(self, name)
        elif self.tab == "GAMEPAD":
            col = box.column()
            col.prop(self, "gamepad_enabled")
            sub = col.column()
            sub.active = self.gamepad_enabled
            sub.prop(self, "gamepad_index")
            sub.prop(self, "gamepad_layout")
            sub.prop(self, "gamepad_deadzone")
            sub.prop(self, "gamepad_expo")
            sub.prop(self, "gamepad_look_speed")
            sub.prop(self, "gamepad_invert_y")
            sub.separator()
            for line in (
                "Left stick: move   ·   Right stick: look   ·   Triggers: sink / rise",
                "Bumpers: roll   ·   Left stick click: boost   ·   Right stick click: lock on",
                "A: record   ·   B: level horizon   ·   X: autofocus   ·   Y: cycle mode",
                "D-pad ↑↓: speed   ·   D-pad ←→: zoom   ·   Start: play/pause   ·   Back: keyframe",
                "Windows: any XInput controller.  Linux: /dev/input/js*.  macOS: not supported.",
            ):
                sub.label(text=line)
        else:
            row = box.row()
            row.label(text="Click a key field, then press the key to bind it.", icon="INFO")
            row.operator("camera_flight.reset_keys", text="Reset to Defaults", icon="LOOP_BACK")
            for group in keymap.GROUPS:
                col = box.column(align=True)
                col.label(text=group)
                for action in keymap.ACTIONS:
                    if action.group != group:
                        continue
                    row = col.row(align=True)
                    row.label(text=action.label)
                    row.prop(self, "key_" + action.id, text="", event=True)
                    row.prop(self, "key2_" + action.id, text="", event=True)
            box.label(text="Fixed: 1-9 recall bookmark · Ctrl+1-9 save bookmark · Wheel: speed", icon="KEYINGSET")
            box.label(text="Ctrl+Wheel: zoom · Shift+Wheel: focus distance · Alt+Wheel: aperture", icon="BLANK1")


# Key binding properties are generated from the action table.
for _action in keymap.ACTIONS:
    CAMERA_FLIGHT_AddonPreferences.__annotations__["key_" + _action.id] = EnumProperty(
        name=_action.label, items=keymap.EVENT_ITEMS, default=_action.primary
    )
    CAMERA_FLIGHT_AddonPreferences.__annotations__["key2_" + _action.id] = EnumProperty(
        name=_action.label + " (alt)", items=keymap.EVENT_ITEMS, default=_action.secondary
    )
del _action


def _defaults():
    values = {}
    for name, prop in CAMERA_FLIGHT_AddonPreferences.__annotations__.items():
        keywords = getattr(prop, "keywords", {})
        values[name] = keywords.get("default")
    return SimpleNamespace(**values)


_DEFAULTS = _defaults()


def get_prefs(context=None):
    """The add-on preferences, or defaults when the add-on isn't enabled (tests, scripts)."""
    context = context or bpy.context
    addon = context.preferences.addons.get(ADDON_ID)
    if addon is not None and addon.preferences is not None:
        return addon.preferences
    return _DEFAULTS


class CAMERA_FLIGHT_OT_reset_keys(bpy.types.Operator):
    bl_idname = "camera_flight.reset_keys"
    bl_label = "Reset Key Bindings"
    bl_description = "Restore all flight key bindings to their defaults"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        prefs = get_prefs(context)
        for action in keymap.ACTIONS:
            setattr(prefs, "key_" + action.id, action.primary)
            setattr(prefs, "key2_" + action.id, action.secondary)
        return {"FINISHED"}


classes = (CAMERA_FLIGHT_AddonPreferences, CAMERA_FLIGHT_OT_reset_keys)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
