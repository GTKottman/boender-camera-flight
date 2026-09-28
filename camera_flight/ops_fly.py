"""The modal operator that runs a flight in the 3D viewport."""

import traceback

import bpy
from bpy.props import BoolProperty

from . import hud, session
from .ops_tools import create_flight_camera

TICK_SECONDS = 1.0 / 120.0


def _window_region(context):
    region = context.region
    if region is not None and region.type == "WINDOW":
        return region
    for r in context.area.regions:
        if r.type == "WINDOW":
            return r
    return None


class CAMERA_FLIGHT_OT_fly(bpy.types.Operator):
    bl_idname = "camera_flight.fly"
    bl_label = "Fly Camera"
    bl_description = (
        "Fly the scene camera with game controls (WASD + mouse) and record takes on the fly. "
        "Press F1 while flying for all controls"
    )
    bl_options = {"UNDO", "BLOCKING"}

    from_view: BoolProperty(
        name="Start From View",
        description="Start from the current viewport view instead of the camera's position",
        default=False,
        options={"SKIP_SAVE"},
    )

    @classmethod
    def poll(cls, context):
        if session.ACTIVE is not None:
            cls.poll_message_set("Already flying")
            return False
        area = context.area
        if area is None or area.type != "VIEW_3D":
            cls.poll_message_set("Run this from a 3D Viewport")
            return False
        return True

    def invoke(self, context, event):
        region = _window_region(context)
        if region is None:
            self.report({"ERROR"}, "No 3D viewport region found")
            return {"CANCELLED"}
        scene = context.scene
        cam = scene.camera
        if cam is None or cam.type != "CAMERA":
            rv3d = region.data
            cam = create_flight_camera(context, rv3d.view_matrix.inverted() if rv3d else None)
            self.report({"INFO"}, "Created camera \"%s\"" % cam.name)
        if cam.library is not None or (cam.override_library is not None and not cam.is_editable):
            self.report({"ERROR"}, "Camera \"%s\" is linked and can't be animated" % cam.name)
            return {"CANCELLED"}

        self._session = session.FlightSession(
            context, cam, window=context.window, area=context.area, region=region, from_view=self.from_view
        )
        try:
            self._session.start(context)
        except Exception as ex:  # noqa: BLE001
            traceback.print_exc()
            self._session.end(context, cancel=True)
            self.report({"ERROR"}, "Couldn't start flight: %s" % ex)
            return {"CANCELLED"}
        wm = context.window_manager
        self._timer = wm.event_timer_add(TICK_SECONDS, window=context.window)
        args = (self._session,)
        self._h2d = bpy.types.SpaceView3D.draw_handler_add(hud.draw_2d, args, "WINDOW", "POST_PIXEL")
        self._h3d = bpy.types.SpaceView3D.draw_handler_add(hud.draw_3d, args, "WINDOW", "POST_VIEW")
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        s = self._session
        try:
            if event.type == "TIMER":
                s.tick(context)
                result = {"PASS_THROUGH"}  # let Blender's playback timers through
            else:
                handled = s.handle_event(context, event)
                result = {"RUNNING_MODAL"} if handled == "HANDLED" else {"PASS_THROUGH"}
            if s.finished:
                return self._finish(context, cancel=s.finished == "CANCEL")
            return result
        except Exception as ex:  # noqa: BLE001 - always give the user their viewport back
            traceback.print_exc()
            self._finish(context, cancel=False)
            self.report({"ERROR"}, "Camera Flight stopped after an error: %s (see system console)" % ex)
            return {"CANCELLED"}

    def cancel(self, context):
        # Blender is closing the window or loading a file: clean up as best we can.
        try:
            self._finish(context, cancel=False)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def _finish(self, context, cancel):
        s = getattr(self, "_session", None)
        if s is None:
            return {"CANCELLED"}
        self._session = None
        try:
            s.end(context, cancel=cancel)
        finally:
            wm = context.window_manager
            if getattr(self, "_timer", None) is not None:
                wm.event_timer_remove(self._timer)
                self._timer = None
            for attr in ("_h2d", "_h3d"):
                handle = getattr(self, attr, None)
                if handle is not None:
                    bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
                    setattr(self, attr, None)
            if context.area is not None:
                context.area.tag_redraw()
        if s.end_message:
            self.report({"WARNING"}, s.end_message)
        return {"FINISHED"} if s.made_changes or not cancel else {"CANCELLED"}


classes = (CAMERA_FLIGHT_OT_fly,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
