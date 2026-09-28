"""Camera, bookmark and preset operators."""

import bpy
from bpy.props import EnumProperty, IntProperty
from mathutils import Matrix, Quaternion, Vector

from . import presets, session, takes


def create_flight_camera(context, matrix=None):
    """New camera named "FlightCam", made the scene camera."""
    scene = context.scene
    data = bpy.data.cameras.new("FlightCam")
    data.lens = 35.0
    data.passepartout_alpha = 0.85
    obj = bpy.data.objects.new("FlightCam", data)
    (context.collection or scene.collection).objects.link(obj)
    if matrix is not None:
        loc, rot, _s = matrix.decompose()
        obj.matrix_world = Matrix.LocRotScale(loc, rot, None)
    scene.camera = obj
    return obj


def _view_matrix(context):
    rv3d = context.region_data
    if rv3d is None and context.area is not None and context.area.type == "VIEW_3D":
        rv3d = context.area.spaces.active.region_3d
    return rv3d.view_matrix.inverted() if rv3d is not None else None


class CAMERA_FLIGHT_OT_new_camera(bpy.types.Operator):
    bl_idname = "camera_flight.new_camera"
    bl_label = "New Flight Camera"
    bl_description = "Add a camera at the current view and make it the scene camera"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        cam = create_flight_camera(context, _view_matrix(context))
        for ob in context.selected_objects:
            ob.select_set(False)
        cam.select_set(True)
        context.view_layer.objects.active = cam
        self.report({"INFO"}, "Created \"%s\"" % cam.name)
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_camera_to_view(bpy.types.Operator):
    bl_idname = "camera_flight.camera_to_view"
    bl_label = "Camera to View"
    bl_description = "Move the scene camera to the current viewport view"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene.camera is not None and session.ACTIVE is None

    def execute(self, context):
        m = _view_matrix(context)
        if m is None:
            self.report({"ERROR"}, "Run this from a 3D Viewport")
            return {"CANCELLED"}
        cam = context.scene.camera
        loc, rot, _s = m.decompose()
        cam.matrix_world = Matrix.LocRotScale(loc, rot, cam.matrix_world.to_scale())
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_apply_preset(bpy.types.Operator):
    bl_idname = "camera_flight.apply_preset"
    bl_label = "Handling Preset"
    bl_description = "Apply a handling preset (feel of the camera)"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(name="Preset", items=presets.HANDLING_ITEMS)

    def execute(self, context):
        label, _desc, values = presets.HANDLING[self.preset]
        presets.apply(context.scene.camera_flight, values)
        self.report({"INFO"}, "Handling: %s" % label)
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_shake_preset(bpy.types.Operator):
    bl_idname = "camera_flight.shake_preset"
    bl_label = "Shake Preset"
    bl_description = "Apply a camera shake preset"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(name="Preset", items=presets.SHAKE_ITEMS)

    def execute(self, context):
        s = context.scene.camera_flight
        presets.apply(s, presets.SHAKE[self.preset][1])
        s.shake_enabled = True
        return {"FINISHED"}


class CAMERA_FLIGHT_MT_handling_presets(bpy.types.Menu):
    bl_idname = "CAMERA_FLIGHT_MT_handling_presets"
    bl_label = "Handling Presets"

    def draw(self, context):
        for key, label, desc in presets.HANDLING_ITEMS:
            self.layout.operator("camera_flight.apply_preset", text=label).preset = key


# --------------------------------------------------------------------------- bookmarks
def _camera_state(context):
    cam = context.scene.camera
    if cam is None:
        return None
    loc, rot, _s = cam.matrix_world.decompose()
    lens = 50.0
    if cam.type == "CAMERA":
        lens = cam.data.ortho_scale if cam.data.type == "ORTHO" else cam.data.lens
    return loc, rot, lens


class CAMERA_FLIGHT_OT_bookmark_add(bpy.types.Operator):
    bl_idname = "camera_flight.bookmark_add"
    bl_label = "Add Bookmark"
    bl_description = "Save the scene camera's current view as a bookmark (Ctrl+1-9 while flying)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        state = _camera_state(context)
        if state is None:
            self.report({"ERROR"}, "No scene camera")
            return {"CANCELLED"}
        s = context.scene.camera_flight
        used = {b.slot for b in s.bookmarks}
        slot = next((n for n in range(1, 10) if n not in used), 0)
        bm = s.bookmarks.add()
        bm.name = "Bookmark %d" % slot if slot else "Bookmark"
        bm.slot = slot
        bm.location, bm.rotation, bm.lens = state
        s.bookmark_index = len(s.bookmarks) - 1
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_bookmark_update(bpy.types.Operator):
    bl_idname = "camera_flight.bookmark_update"
    bl_label = "Update Bookmark"
    bl_description = "Overwrite the selected bookmark with the scene camera's current view"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.camera_flight
        state = _camera_state(context)
        if state is None or not 0 <= s.bookmark_index < len(s.bookmarks):
            return {"CANCELLED"}
        bm = s.bookmarks[s.bookmark_index]
        bm.location, bm.rotation, bm.lens = state
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_bookmark_remove(bpy.types.Operator):
    bl_idname = "camera_flight.bookmark_remove"
    bl_label = "Remove Bookmark"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.camera_flight
        if 0 <= s.bookmark_index < len(s.bookmarks):
            s.bookmarks.remove(s.bookmark_index)
            s.bookmark_index = max(0, min(s.bookmark_index, len(s.bookmarks) - 1))
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_bookmark_move(bpy.types.Operator):
    bl_idname = "camera_flight.bookmark_move"
    bl_label = "Move Bookmark"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    direction: IntProperty(default=-1)

    def execute(self, context):
        s = context.scene.camera_flight
        i = s.bookmark_index
        j = i + self.direction
        if 0 <= i < len(s.bookmarks) and 0 <= j < len(s.bookmarks):
            s.bookmarks.move(i, j)
            s.bookmark_index = j
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_bookmark_goto(bpy.types.Operator):
    bl_idname = "camera_flight.bookmark_goto"
    bl_label = "Go to Bookmark"
    bl_description = "Move the scene camera to the selected bookmark"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene.camera is not None and session.ACTIVE is None

    def execute(self, context):
        s = context.scene.camera_flight
        if not 0 <= s.bookmark_index < len(s.bookmarks):
            return {"CANCELLED"}
        bm = s.bookmarks[s.bookmark_index]
        cam = context.scene.camera
        cam.matrix_world = Matrix.LocRotScale(Vector(bm.location), Quaternion(bm.rotation), cam.matrix_world.to_scale())
        if cam.type == "CAMERA":
            if cam.data.type == "ORTHO":
                cam.data.ortho_scale = bm.lens
            else:
                cam.data.lens = max(bm.lens, 1.0)
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_bookmarks_to_take(bpy.types.Operator):
    bl_idname = "camera_flight.bookmarks_to_take"
    bl_label = "Bookmarks to Take"
    bl_description = "Build a smooth camera move that glides through the bookmarks in list order"
    bl_options = {"REGISTER", "UNDO"}

    frames_per: IntProperty(name="Frames Between", default=48, min=1, soft_max=600)

    @classmethod
    def poll(cls, context):
        s = context.scene.camera_flight
        return context.scene.camera is not None and len(s.bookmarks) >= 2 and session.ACTIVE is None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        scene = context.scene
        index = takes.bookmarks_to_take(
            scene, scene.camera, list(scene.camera_flight.bookmarks), scene.frame_current, self.frames_per
        )
        if index < 0:
            return {"CANCELLED"}
        scene.camera_flight.take_index = index
        take = scene.camera_flight.takes[index]
        self.report({"INFO"}, "%s: %d-%d" % (take.name, take.frame_start, take.frame_end))
        return {"FINISHED"}


class CAMERA_FLIGHT_OT_open_prefs(bpy.types.Operator):
    bl_idname = "camera_flight.open_prefs"
    bl_label = "Key Bindings & Preferences"
    bl_description = "Open the Camera Flight preferences (remap keys, gamepad, HUD)"

    def execute(self, context):
        from .prefs import ADDON_ID

        prefs = context.preferences.addons.get(ADDON_ID)
        if prefs is not None:
            prefs.preferences.tab = "KEYS"
        bpy.ops.screen.userpref_show(section="ADDONS")
        try:
            bpy.ops.preferences.addon_show(module=ADDON_ID)
        except (RuntimeError, TypeError):
            pass
        return {"FINISHED"}


classes = (
    CAMERA_FLIGHT_OT_new_camera,
    CAMERA_FLIGHT_OT_camera_to_view,
    CAMERA_FLIGHT_OT_apply_preset,
    CAMERA_FLIGHT_OT_shake_preset,
    CAMERA_FLIGHT_MT_handling_presets,
    CAMERA_FLIGHT_OT_bookmark_add,
    CAMERA_FLIGHT_OT_bookmark_update,
    CAMERA_FLIGHT_OT_bookmark_remove,
    CAMERA_FLIGHT_OT_bookmark_move,
    CAMERA_FLIGHT_OT_bookmark_goto,
    CAMERA_FLIGHT_OT_bookmarks_to_take,
    CAMERA_FLIGHT_OT_open_prefs,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
