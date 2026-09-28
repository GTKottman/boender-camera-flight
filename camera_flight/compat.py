"""Helpers that hide Blender API differences between 4.2 LTS and 5.x.

The big one is animation data: Blender 4.4 introduced *slotted actions*
(layers -> strips -> channelbags -> F-Curves) and Blender 5.0 removed the
legacy ``Action.fcurves`` shortcut entirely.
"""

import bpy

SLOTTED_ACTIONS = bpy.app.version >= (4, 4, 0)


# --------------------------------------------------------------------------- actions
def get_action(id_data):
    ad = id_data.animation_data
    return ad.action if ad else None


def get_action_slot(id_data):
    if not SLOTTED_ACTIONS:
        return None
    ad = id_data.animation_data
    return getattr(ad, "action_slot", None) if ad else None


def slot_id_type(slot):
    return getattr(slot, "target_id_type", None) or getattr(slot, "id_root", None)


def find_slot(action, id_data):
    """Best slot of ``action`` for animating ``id_data`` (None on legacy Blender)."""
    if not SLOTTED_ACTIONS or action is None or not len(action.slots):
        return None
    id_type = id_data.id_type
    candidates = [s for s in action.slots if slot_id_type(s) in (id_type, "UNSPECIFIED", None)]
    for slot in candidates:
        if getattr(slot, "name_display", None) == id_data.name:
            return slot
    return candidates[0] if candidates else None


def assign_action(id_data, action, slot=None):
    """Assign ``action`` (or None) and make sure the right slot is used."""
    if action is None:
        ad = id_data.animation_data
        if ad is not None and ad.action is not None:
            ad.action = None
        return
    ad = id_data.animation_data or id_data.animation_data_create()
    if ad.action != action:
        ad.action = action
    if SLOTTED_ACTIONS:
        slot = slot or find_slot(action, id_data)
        if slot is not None and ad.action_slot != slot:
            try:
                ad.action_slot = slot
            except (TypeError, RuntimeError, AttributeError):
                pass


def new_action(id_data, name):
    """Create an empty action ready to animate ``id_data``. Returns (action, slot)."""
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    slot = None
    if SLOTTED_ACTIONS:
        slot = action.slots.new(id_type=id_data.id_type, name=id_data.name)
    else:
        try:
            action.id_root = id_data.id_type
        except (TypeError, AttributeError):
            pass
    return action, slot


def _channelbag(action, slot, ensure=False):
    if slot is None:
        slot = action.slots[0] if len(action.slots) else None
        if slot is None:
            return None
    if not len(action.layers):
        if not ensure:
            return None
        action.layers.new("Layer")
    layer = action.layers[0]
    if not len(layer.strips):
        if not ensure:
            return None
        layer.strips.new(type="KEYFRAME")
    return layer.strips[0].channelbag(slot, ensure=ensure)


def action_fcurves(action, slot=None):
    """All F-Curves of ``action`` (for ``slot`` on slotted Blender)."""
    if action is None:
        return []
    if SLOTTED_ACTIONS:
        cb = _channelbag(action, slot)
        return list(cb.fcurves) if cb is not None else []
    return list(action.fcurves)


def find_fcurve(action, data_path, index=0, slot=None):
    if action is None:
        return None
    if SLOTTED_ACTIONS:
        cb = _channelbag(action, slot)
        return cb.fcurves.find(data_path, index=index) if cb is not None else None
    return action.fcurves.find(data_path, index=index)


def ensure_fcurve(action, data_path, index=0, slot=None, group=None):
    if SLOTTED_ACTIONS:
        if slot is None:
            slot = action.slots[0] if len(action.slots) else None
        cb = _channelbag(action, slot, ensure=True)
        fc = cb.fcurves.find(data_path, index=index)
        if fc is None:
            fc = cb.fcurves.new(data_path, index=index)
            if group:
                try:
                    grp = cb.groups.get(group) or cb.groups.new(group)
                    fc.group = grp
                except (AttributeError, TypeError, RuntimeError):
                    pass
        return fc
    fc = action.fcurves.find(data_path, index=index)
    if fc is None:
        fc = action.fcurves.new(data_path, index=index, action_group=group or "")
    return fc


def remove_fcurve(action, fcurve, slot=None):
    if SLOTTED_ACTIONS:
        cb = _channelbag(action, slot)
        if cb is not None:
            cb.fcurves.remove(fcurve)
        return
    action.fcurves.remove(fcurve)


def copy_action(action):
    """Duplicate an action (keeps slots on 4.4+)."""
    dup = action.copy()
    dup.use_fake_user = True
    return dup


# --------------------------------------------------------------------------- scene
def scene_range(scene):
    if scene.use_preview_range:
        return scene.frame_preview_start, scene.frame_preview_end
    return scene.frame_start, scene.frame_end


def scene_fps(scene):
    return scene.render.fps / (scene.render.fps_base or 1.0)


def format_length(scene, value, precision=2):
    """Distance formatted in the scene's unit system."""
    us = scene.unit_settings
    if us.system == "NONE":
        return "%.*f" % (precision, value)
    try:
        return bpy.utils.units.to_string(
            us.system, "LENGTH", value * us.scale_length, precision=precision, split_unit=False
        )
    except Exception:  # noqa: BLE001 - unit formatting is cosmetic only
        return "%.*f" % (precision, value)


def timecode(scene, frame):
    fps = scene_fps(scene)
    if fps <= 0:
        return str(frame)
    total = max(frame, 0) / fps
    h = int(total // 3600)
    m = int(total // 60) % 60
    s = int(total) % 60
    f = int(round(max(frame, 0) - int(total) * fps))
    if f >= int(round(fps)):
        f = 0
    return "%02d:%02d:%02d:%02d" % (h, m, s, f)
