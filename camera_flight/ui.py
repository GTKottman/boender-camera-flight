"""3D Viewport sidebar: Flight tab."""

import bpy

from . import keymap, session
from .prefs import get_prefs

CATEGORY = "Flight"


def _settings(context):
    return context.scene.camera_flight


class _Panel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY


# --------------------------------------------------------------------------- lists
class CAMERA_FLIGHT_UL_takes(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        broken = item.action is None or item.camera is None
        cam = item.camera
        in_use = (
            not broken
            and cam.animation_data is not None
            and cam.animation_data.action == item.action
            and session.ACTIVE is None
        )
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon="ERROR" if broken else ("PLAY" if in_use else "REC"))
        sub = row.row(align=True)
        sub.alignment = "RIGHT"
        sub.label(text="%d–%d" % (item.frame_start, item.frame_end))
        if item.rating:
            sub.label(text="★" * item.rating)

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item] * len(items)
        if self.filter_name:
            needle = self.filter_name.lower()
            for i, item in enumerate(items):
                hay = (item.name + " " + item.note + " " + (item.camera.name if item.camera else "")).lower()
                if needle not in hay:
                    flags[i] = 0
        order = []
        if self.use_filter_sort_alpha:
            order = bpy.types.UI_UL_list.sort_items_by_name(items, "name")
        return flags, order


class CAMERA_FLIGHT_UL_bookmarks(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon="BOOKMARKS")
        sub = row.row(align=True)
        sub.alignment = "RIGHT"
        sub.label(text=("key %d" % item.slot) if item.slot else "")


# --------------------------------------------------------------------------- main
class CAMERA_FLIGHT_PT_main(_Panel, bpy.types.Panel):
    bl_label = "Camera Flight"

    def draw(self, context):
        layout = self.layout
        s = _settings(context)
        scene = context.scene
        active = session.ACTIVE

        if active is not None:
            box = layout.box()
            box.alert = active.recording
            col = box.column(align=True)
            if active.recording:
                col.label(text="Recording…", icon="REC")
            else:
                col.label(text="Flying", icon="VIEW_CAMERA")
            col.label(text="%s: capture mouse  ·  %s: finish" % (active.bindings.label("mouse"), active.bindings.label("finish")))
        else:
            col = layout.column(align=True)
            col.scale_y = 1.6
            col.operator("camera_flight.fly", text="Start Flight  (Shift F)", icon="VIEW_CAMERA")
            row = layout.row(align=True)
            row.prop(s, "start_from", text="From")
            row.operator("camera_flight.camera_to_view", text="", icon="CON_CAMERASOLVER")

        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(scene, "camera", text="")
        row.operator("camera_flight.new_camera", text="", icon="ADD")
        layout.prop(s, "mode", text="")
        row = layout.row(align=True)
        row.prop(s, "speed")
        row.menu("CAMERA_FLIGHT_MT_handling_presets", text="", icon="PRESET")


class CAMERA_FLIGHT_PT_handling(_Panel, bpy.types.Panel):
    bl_label = "Handling"
    bl_parent_id = "CAMERA_FLIGHT_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        s = _settings(context)
        layout.menu("CAMERA_FLIGHT_MT_handling_presets", text="Apply Preset", icon="PRESET")
        col = layout.column(align=True)
        col.prop(s, "acceleration")
        col.prop(s, "drag")
        col = layout.column(align=True)
        col.prop(s, "look_smoothing")
        col.prop(s, "track_smoothing")
        col = layout.column(align=True)
        col.prop(s, "turn_rate")
        col.prop(s, "roll_rate")
        col = layout.column(align=True)
        col.prop(s, "boost_multiplier")
        col.prop(s, "precision_multiplier")
        col = layout.column(align=True)
        col.prop(s, "bank_amount", slider=True)
        sub = col.column(align=True)
        sub.active = s.bank_amount > 0.0
        sub.prop(s, "max_bank")
        col = layout.column(heading="Horizon")
        row = col.row(align=True)
        row.prop(s, "auto_level", text="Auto Level")
        sub = row.row(align=True)
        sub.active = s.auto_level
        sub.prop(s, "level_time", text="")
        col = layout.column(heading="Movement")
        col.prop(s, "planar_movement")
        col.prop(s, "adaptive_speed")
        row = col.row(align=True)
        row.prop(s, "collisions")
        sub = row.row(align=True)
        sub.active = s.collisions
        sub.prop(s, "collision_radius", text="")
        col.prop(s, "follow_rotation")


class CAMERA_FLIGHT_PT_walk(_Panel, bpy.types.Panel):
    bl_label = "Walk Mode"
    bl_parent_id = "CAMERA_FLIGHT_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        s = _settings(context)
        col = layout.column(align=True)
        col.prop(s, "gravity")
        col.prop(s, "eye_height")
        col.prop(s, "jump_speed")
        col.prop(s, "head_bob", slider=True)


class CAMERA_FLIGHT_PT_lens(_Panel, bpy.types.Panel):
    bl_label = "Lens & Focus"
    bl_parent_id = "CAMERA_FLIGHT_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        s = _settings(context)
        cam = context.scene.camera
        if cam is not None and cam.type == "CAMERA":
            col = layout.column(align=True)
            if cam.data.type == "ORTHO":
                col.prop(cam.data, "ortho_scale")
            else:
                col.prop(cam.data, "lens")
            col.prop(cam.data.dof, "use_dof", text="Depth of Field")
            sub = col.column(align=True)
            sub.active = cam.data.dof.use_dof
            sub.prop(cam.data.dof, "focus_distance")
            sub.prop(cam.data.dof, "aperture_fstop")
        col = layout.column(align=True)
        col.prop(s, "zoom_rate")
        col.prop(s, "zoom_smoothing")
        col.prop(s, "hold_zoom_factor")
        col = layout.column(align=True)
        col.prop(s, "focus_smoothing")
        col.prop(s, "continuous_af")


class CAMERA_FLIGHT_PT_shake(_Panel, bpy.types.Panel):
    bl_label = "Camera Shake"
    bl_parent_id = "CAMERA_FLIGHT_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.prop(_settings(context), "shake_enabled", text="")

    def draw(self, context):
        layout = self.layout
        s = _settings(context)
        flow = layout.grid_flow(columns=3, align=True, even_columns=True)
        from .presets import SHAKE_ITEMS

        for key, label, _d in SHAKE_ITEMS:
            flow.operator("camera_flight.shake_preset", text=label).preset = key
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column(align=True)
        col.active = s.shake_enabled
        col.prop(s, "shake_intensity")
        col.prop(s, "shake_frequency")
        col.prop(s, "shake_rotation")
        col.prop(s, "shake_position")


class CAMERA_FLIGHT_PT_recording(_Panel, bpy.types.Panel):
    bl_label = "Recording"
    bl_parent_id = "CAMERA_FLIGHT_PT_main"

    def draw(self, context):
        layout = self.layout
        s = _settings(context)
        layout.row().prop(s, "record_mode", expand=True)
        layout.use_property_split = True
        layout.use_property_decorate = False
        if s.record_mode == "PUNCH":
            col = layout.column(align=True)
            col.prop(s, "punch_out")
            sub = col.column(align=True)
            sub.active = s.punch_out == "KEEP"
            sub.prop(s, "punch_blend")
        col = layout.column(align=True)
        col.prop(s, "timing")
        sub = col.column(align=True)
        sub.active = s.timing != "SCENE"
        sub.prop(s, "time_scale")
        col = layout.column(align=True)
        col.prop(s, "countdown")
        col.prop(s, "key_step")
        col.prop(s, "auto_smooth")
        col.prop(s, "key_interpolation")
        col = layout.column(heading="Options")
        col.prop(s, "stop_at_end")
        col.prop(s, "rewind_after_take")
        col.prop(s, "record_lens")
        col.prop(s, "record_focus")


class CAMERA_FLIGHT_PT_takes(_Panel, bpy.types.Panel):
    bl_label = "Takes"
    bl_parent_id = "CAMERA_FLIGHT_PT_main"

    def draw(self, context):
        layout = self.layout
        s = _settings(context)
        row = layout.row()
        row.template_list("CAMERA_FLIGHT_UL_takes", "", s, "takes", s, "take_index", rows=4)
        col = row.column(align=True)
        col.operator("camera_flight.take_review", text="", icon="PLAY")
        col.operator("camera_flight.take_duplicate", text="", icon="DUPLICATE")
        col.operator("camera_flight.take_remove", text="", icon="REMOVE")
        col.separator()
        col.prop(s, "auto_activate_takes", text="", icon="RESTRICT_SELECT_OFF")
        if not 0 <= s.take_index < len(s.takes):
            layout.label(text="Press R while flying to record a take", icon="INFO")
            return
        take = s.takes[s.take_index]
        box = layout.box()
        col = box.column(align=True)
        row = col.row(align=True)
        row.prop(take, "rating", text="Rating")
        row.operator("camera_flight.take_activate", text="Use", icon="CHECKMARK")
        col.prop(take, "note", text="", icon="TEXT")
        info = col.row()
        info.enabled = False
        info.label(text="%s · %s" % (take.camera.name if take.camera else "no camera", take.source or "?"))
        info = col.row()
        info.enabled = False
        info.label(text="Frames %d–%d · %s" % (take.frame_start, take.frame_end, take.created))

        col = layout.column(align=True)
        grid = col.grid_flow(columns=2, align=True, even_columns=True)
        grid.operator("camera_flight.take_smooth", text="Smooth", icon="SMOOTHCURVE")
        grid.operator("camera_flight.take_simplify", text="Simplify", icon="IPO_LINEAR")
        grid.operator("camera_flight.take_add_noise", text="Handheld", icon="RNDCURVE")
        grid.operator("camera_flight.take_clear_noise", text="Clear Noise", icon="X")
        grid.operator("camera_flight.take_retime", text="Retime", icon="TIME")
        grid.operator("camera_flight.take_reverse", text="Reverse", icon="ARROW_LEFTRIGHT")
        grid.operator("camera_flight.take_offset", text="To Frame", icon="NEXT_KEYFRAME")
        grid.operator("camera_flight.take_set_range", text="Set Range", icon="PREVIEW_RANGE")
        grid.operator("camera_flight.take_to_camera", text="New Camera", icon="OUTLINER_OB_CAMERA")
        grid.operator("camera_flight.take_to_curve", text="To Curve", icon="CURVE_BEZCURVE")


class CAMERA_FLIGHT_PT_bookmarks(_Panel, bpy.types.Panel):
    bl_label = "Bookmarks"
    bl_parent_id = "CAMERA_FLIGHT_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        s = _settings(context)
        row = layout.row()
        row.template_list("CAMERA_FLIGHT_UL_bookmarks", "", s, "bookmarks", s, "bookmark_index", rows=3)
        col = row.column(align=True)
        col.operator("camera_flight.bookmark_add", text="", icon="ADD")
        col.operator("camera_flight.bookmark_remove", text="", icon="REMOVE")
        col.separator()
        col.operator("camera_flight.bookmark_move", text="", icon="TRIA_UP").direction = -1
        col.operator("camera_flight.bookmark_move", text="", icon="TRIA_DOWN").direction = 1
        if 0 <= s.bookmark_index < len(s.bookmarks):
            bm = s.bookmarks[s.bookmark_index]
            row = layout.row(align=True)
            row.prop(bm, "slot")
            row.operator("camera_flight.bookmark_goto", text="Go", icon="VIEW_CAMERA")
            row.operator("camera_flight.bookmark_update", text="Update", icon="FILE_REFRESH")
        layout.use_property_split = True
        layout.prop(s, "bookmark_travel_time")
        layout.operator("camera_flight.bookmarks_to_take", icon="IPO_BEZIER")


class CAMERA_FLIGHT_PT_controls(_Panel, bpy.types.Panel):
    bl_label = "Controls"
    bl_parent_id = "CAMERA_FLIGHT_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        bindings = keymap.Bindings(get_prefs(context))
        layout.operator("camera_flight.open_prefs", icon="PREFERENCES")
        for group in keymap.GROUPS:
            col = layout.column(align=True)
            col.label(text=group, icon="DOT")
            for action in keymap.ACTIONS:
                if action.group != group:
                    continue
                split = col.split(factor=0.38, align=True)
                split.label(text=bindings.label(action.id))
                split.label(text=action.label)
        col = layout.column(align=True)
        col.label(text="Mouse", icon="DOT")
        for key, label in (
            ("Move", "Look around"),
            ("Wheel", "Speed"),
            ("Ctrl+Wheel", "Zoom"),
            ("Shift+Wheel", "Focus distance"),
            ("Alt+Wheel", "Aperture"),
            ("1-9", "Fly to bookmark"),
            ("Ctrl+1-9", "Save bookmark"),
        ):
            split = col.split(factor=0.38, align=True)
            split.label(text=key)
            split.label(text=label)


classes = (
    CAMERA_FLIGHT_UL_takes,
    CAMERA_FLIGHT_UL_bookmarks,
    CAMERA_FLIGHT_PT_main,
    CAMERA_FLIGHT_PT_recording,
    CAMERA_FLIGHT_PT_takes,
    CAMERA_FLIGHT_PT_handling,
    CAMERA_FLIGHT_PT_lens,
    CAMERA_FLIGHT_PT_shake,
    CAMERA_FLIGHT_PT_walk,
    CAMERA_FLIGHT_PT_bookmarks,
    CAMERA_FLIGHT_PT_controls,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
