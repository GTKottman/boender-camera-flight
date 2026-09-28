# SPDX-License-Identifier: GPL-3.0-or-later
"""Camera Flight: game-style WASD camera flying and live take recording for Blender."""

bl_info = {
    "name": "Camera Flight",
    "author": "GTKottman",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > Flight  ·  Shift+F in the 3D Viewport",
    "description": "Fly the camera with game controls (WASD + mouse / gamepad) and record takes mid-flight",
    "doc_url": "https://github.com/GTKottman/boender-camera-flight",
    "category": "3D View",
}

if "bpy" in locals():
    import importlib

    for _mod in _modules:  # noqa: F821 - defined on first load
        importlib.reload(_mod)

import bpy

from . import (
    compat,
    gamepad,
    hud,
    keymap,
    ops_fly,
    ops_takes,
    ops_tools,
    prefs,
    presets,
    props,
    session,
    takes,
    ui,
)
from .core import controller, mathx, shake, smoothing

_modules = (
    mathx,
    shake,
    smoothing,
    controller,
    compat,
    keymap,
    prefs,
    presets,
    props,
    takes,
    gamepad,
    session,
    hud,
    ops_tools,
    ops_takes,
    ops_fly,
    ui,
)
_registered = (prefs, props, ops_tools, ops_takes, ops_fly, ui)
_addon_keymaps = []


def _register_keymap():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
    kmi = km.keymap_items.new(ops_fly.CAMERA_FLIGHT_OT_fly.bl_idname, "F", "PRESS", shift=True)
    _addon_keymaps.append((km, kmi))


def _unregister_keymap():
    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _addon_keymaps.clear()


@bpy.app.handlers.persistent
def _end_flight_on_load(*_args):
    if session.ACTIVE is not None:
        try:
            session.ACTIVE.end(cancel=True)
        except Exception:  # noqa: BLE001 - the old file's data is going away anyway
            pass
        session.ACTIVE = None


def register():
    for mod in _registered:
        mod.register()
    _register_keymap()
    bpy.app.handlers.load_pre.append(_end_flight_on_load)


def unregister():
    if _end_flight_on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_end_flight_on_load)
    _end_flight_on_load()
    _unregister_keymap()
    for mod in reversed(_registered):
        mod.unregister()
