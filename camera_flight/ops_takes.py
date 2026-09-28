"""Operators that manage and edit recorded takes."""

import math

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty

from . import session, takes


def _take(context, index=-1):
    s = context.scene.camera_flight
    i = s.take_index if index < 0 else index
    if 0 <= i < len(s.takes):
        take = s.takes[i]
        if take.action is not None and take.camera is not None:
            return i, take
    return -1, None


class _TakeOp:
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1, options={"SKIP_SAVE", "HIDDEN"})

    @classmethod
    def poll(cls, context):
        if session.ACTIVE is not None:
            cls.poll_message_set("Finish flying first")
            return False
        return _take(context)[1] is not None


class CAMERA_FLIGHT_OT_take_activate(_TakeOp, bpy.types.Operator):
    bl_idname = "camera_flight.take_activate"
    bl_label = "Use Take"
    bl_description = "Assign this take to its camera"

    @classmethod
    def poll(cls, context):
        return _take(context)[1] is not None

    def execute(self, context):
        i, _t = _take(context, self.index)
        takes.activate_take(context.scene, i)
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_take_review(_TakeOp, bpy.types.Operator):
    bl_idname = "camera_flight.take_review"
    bl_label = "Review Take"
    bl_description = "Use this take, set the scene range to it and play it back through the camera"

    def execute(self, context):
        i, take = _take(context, self.index)
        scene = context.scene
        takes.activate_take(scene, i)
        scene.frame_start, scene.frame_end = take.frame_start, max(take.frame_end, take.frame_start + 1)
        scene.frame_current = take.frame_start
        screen = context.screen
        if screen is not None:
            for area in screen.areas:
                if area.type == "VIEW_3D":
                    area.spaces.active.region_3d.view_perspective = "CAMERA"
                    break
            if not screen.is_animation_playing:
                bpy.ops.screen.animation_play()
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_take_remove(_TakeOp, bpy.types.Operator):
    bl_idname = "camera_flight.take_remove"
    bl_label = "Delete Take"
    bl_description = "Remove the take (and its actions, unless used elsewhere)"

    delete_actions: BoolProperty(name="Delete Actions", default=True)

    @classmethod
    def poll(cls, context):
        s = context.scene.camera_flight
        return session.ACTIVE is None and 0 <= s.take_index < len(s.takes)

    def execute(self, context):
        s = context.scene.camera_flight
        i = s.take_index if self.index < 0 else self.index
        takes.remove_take(context.scene, i, self.delete_actions)
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_take_duplicate(_TakeOp, bpy.types.Operator):
    bl_idname = "camera_flight.take_duplicate"
    bl_label = "Duplicate Take"
    bl_description = "Copy the take so you can edit it without losing the original"

    def execute(self, context):
        i, _t = _take(context, self.index)
        context.scene.camera_flight.take_index = takes.duplicate_take(context.scene, i)
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_take_smooth(_TakeOp, bpy.types.Operator):
    bl_idname = "camera_flight.take_smooth"
    bl_label = "Smooth Take"
    bl_description = "Remove jitter with a Gaussian filter (start and end stay put)"

    sigma: FloatProperty(name="Strength (frames)", default=3.0, min=0.1, soft_max=24.0)
    location: BoolProperty(name="Location", default=True)
    rotation: BoolProperty(name="Rotation", default=True)
    lens: BoolProperty(name="Zoom & Focus", default=True)
    pin_ends: BoolProperty(name="Keep Start/End", default=True)

    def execute(self, context):
        _i, take = _take(context, self.index)
        n = takes.smooth_take(take, self.sigma, self.location, self.rotation, self.lens, self.pin_ends)
        self.report({"INFO"}, "Smoothed %d curves" % n)
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class CAMERA_FLIGHT_OT_take_simplify(_TakeOp, bpy.types.Operator):
    bl_idname = "camera_flight.take_simplify"
    bl_label = "Simplify Take"
    bl_description = "Reduce the number of keyframes while staying within a tolerance (easier to hand-edit)"

    location: FloatProperty(name="Location Tolerance", default=0.005, min=0.0, soft_max=1.0, unit="LENGTH", precision=4)
    rotation: FloatProperty(name="Rotation Tolerance", default=math.radians(0.25), min=0.0, subtype="ANGLE")
    lens: FloatProperty(name="Lens Tolerance", default=0.1, min=0.0)

    def execute(self, context):
        _i, take = _take(context, self.index)
        n = takes.simplify_take(take, self.location, self.rotation, self.lens)
        self.report({"INFO"}, "Removed %d keyframes" % n)
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class CAMERA_FLIGHT_OT_take_add_noise(_TakeOp, bpy.types.Operator):
    bl_idname = "camera_flight.take_add_noise"
    bl_label = "Add Handheld Noise"
    bl_description = "Add non-destructive handheld wobble (F-Curve Noise modifiers)"

    rotation: FloatProperty(name="Rotation", default=math.radians(0.4), min=0.0, subtype="ANGLE")
    location: FloatProperty(name="Location", default=0.0, min=0.0, unit="LENGTH")
    scale: FloatProperty(name="Scale (frames)", description="Length of one wobble", default=24.0, min=0.5)
    seed: IntProperty(name="Seed", default=0)

    def execute(self, context):
        _i, take = _take(context, self.index)
        n = takes.add_noise(take, self.rotation, self.location, self.scale, self.seed)
        self.report({"INFO"}, "Added noise to %d curves" % n)
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class CAMERA_FLIGHT_OT_take_clear_noise(_TakeOp, bpy.types.Operator):
    bl_idname = "camera_flight.take_clear_noise"
    bl_label = "Clear Noise"
    bl_description = "Remove all Noise modifiers from the take"

    def execute(self, context):
        _i, take = _take(context, self.index)
        self.report({"INFO"}, "Removed %d noise modifiers" % takes.clear_noise(take))
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_take_retime(_TakeOp, bpy.types.Operator):
    bl_idname = "camera_flight.take_retime"
    bl_label = "Retime Take"
    bl_description = "Stretch or squash the take in time (2 = half speed, 0.5 = double speed)"

    factor: FloatProperty(name="Duration ×", default=1.5, min=0.05, soft_max=10.0)

    def execute(self, context):
        _i, take = _take(context, self.index)
        takes.retime_take(take, self.factor)
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class CAMERA_FLIGHT_OT_take_offset(_TakeOp, bpy.types.Operator):
    bl_idname = "camera_flight.take_offset"
    bl_label = "Move Take to Current Frame"
    bl_description = "Slide the take in time so it starts at the current frame"

    def execute(self, context):
        _i, take = _take(context, self.index)
        takes.offset_take(take, context.scene.frame_current)
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_take_reverse(_TakeOp, bpy.types.Operator):
    bl_idname = "camera_flight.take_reverse"
    bl_label = "Reverse Take"
    bl_description = "Play the move backwards (fly-in becomes fly-out)"

    def execute(self, context):
        _i, take = _take(context, self.index)
        takes.reverse_take(take)
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_take_to_curve(_TakeOp, bpy.types.Operator):
    bl_idname = "camera_flight.take_to_curve"
    bl_label = "Take to Curve"
    bl_description = "Create a curve object that traces the flight path (usable with Follow Path)"

    step: IntProperty(name="Frame Step", default=4, min=1, soft_max=48)

    def execute(self, context):
        _i, take = _take(context, self.index)
        obj = takes.take_to_curve(context.scene, take, self.step, context.collection)
        if obj is None:
            self.report({"WARNING"}, "Take is too short")
            return {"CANCELLED"}
        self.report({"INFO"}, "Created \"%s\"" % obj.name)
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_take_to_camera(_TakeOp, bpy.types.Operator):
    bl_idname = "camera_flight.take_to_camera"
    bl_label = "Take to New Camera"
    bl_description = "Bake this take onto its own camera (great for multi-camera edits with timeline markers)"

    bind_marker: BoolProperty(
        name="Bind Timeline Marker", description="Add a marker at the take's start that switches to the new camera", default=True
    )

    def execute(self, context):
        _i, take = _take(context, self.index)
        obj = takes.take_to_camera(context.scene, take, self.bind_marker)
        self.report({"INFO"}, "Created \"%s\"" % obj.name)
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_take_set_range(_TakeOp, bpy.types.Operator):
    bl_idname = "camera_flight.take_set_range"
    bl_label = "Set Scene Range to Take"

    def execute(self, context):
        _i, take = _take(context, self.index)
        scene = context.scene
        scene.frame_start = take.frame_start
        scene.frame_end = max(take.frame_end, take.frame_start + 1)
        return {"FINISHED"}


classes = (
    CAMERA_FLIGHT_OT_take_activate,
    CAMERA_FLIGHT_OT_take_review,
    CAMERA_FLIGHT_OT_take_remove,
    CAMERA_FLIGHT_OT_take_duplicate,
    CAMERA_FLIGHT_OT_take_smooth,
    CAMERA_FLIGHT_OT_take_simplify,
    CAMERA_FLIGHT_OT_take_add_noise,
    CAMERA_FLIGHT_OT_take_clear_noise,
    CAMERA_FLIGHT_OT_take_retime,
    CAMERA_FLIGHT_OT_take_offset,
    CAMERA_FLIGHT_OT_take_reverse,
    CAMERA_FLIGHT_OT_take_to_curve,
    CAMERA_FLIGHT_OT_take_to_camera,
    CAMERA_FLIGHT_OT_take_set_range,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
