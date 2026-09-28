"""Shared helpers for the Blender-side tests."""

import math

import bpy

from camera_flight import session


class Ev:
    """Minimal stand-in for ``bpy.types.Event``."""

    def __init__(self, type, value="PRESS", shift=False, ctrl=False, alt=False, is_repeat=False, mouse_x=0, mouse_y=0):
        self.type = type
        self.value = value
        self.shift = shift
        self.ctrl = ctrl
        self.alt = alt
        self.oskey = False
        self.is_repeat = is_repeat
        self.mouse_x = mouse_x
        self.mouse_y = mouse_y


def reset_scene(fps=24, start=1, end=250):
    if session.ACTIVE is not None:
        session.ACTIVE = None
    for coll in (
        bpy.data.objects,
        bpy.data.actions,
        bpy.data.cameras,
        bpy.data.meshes,
        bpy.data.curves,
        bpy.data.collections,
        bpy.data.lights,
    ):
        for item in list(coll):
            coll.remove(item)
    scene = bpy.context.scene
    scene.property_unset("camera_flight")
    scene.render.fps = fps
    scene.render.fps_base = 1.0
    scene.frame_start = start
    scene.frame_end = end
    scene.frame_current = start
    scene.use_preview_range = False
    scene.timeline_markers.clear()
    scene.camera = None
    return scene


def make_camera(loc=(0.0, 0.0, 1.0), rot=(math.radians(90.0), 0.0, 0.0), lens=50.0, name="Cam", rotation_mode="XYZ"):
    data = bpy.data.cameras.new(name)
    data.lens = lens
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.rotation_mode = "XYZ"
    obj.location = loc
    obj.rotation_euler = rot
    if rotation_mode != "XYZ":
        obj.rotation_mode = rotation_mode
    bpy.context.scene.camera = obj
    bpy.context.view_layer.update()
    return obj


def make_box(location, size=2.0, name="Box"):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.active_object
    obj.name = name
    bpy.context.view_layer.update()
    return obj


def make_ground(z=0.0, size=400.0):
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0.0, 0.0, z))
    obj = bpy.context.active_object
    obj.name = "Ground"
    bpy.context.view_layer.update()
    return obj


class Flight:
    """Drives a headless FlightSession with fake events and a fake clock."""

    def __init__(self, cam, **settings):
        s = bpy.context.scene.camera_flight
        for key, value in settings.items():
            setattr(s, key, value)
        self.s = session.FlightSession(bpy.context, cam)
        self.s.captured = True
        self.s.start(bpy.context)
        self.t = 0.0
        self.mx = self.my = 0  # absolute cursor position (no warping headless)
        self.s.tick(now=self.t)

    def press(self, key, **kw):
        return self.s.handle_event(bpy.context, Ev(key, "PRESS", **kw))

    def release(self, key):
        return self.s.handle_event(bpy.context, Ev(key, "RELEASE"))

    def tap(self, key, **kw):
        self.press(key, **kw)
        self.release(key)

    def mouse(self, dx, dy):
        """Move the mouse by (dx, dy) pixels."""
        self.mx += dx
        self.my += dy
        return self.s.handle_event(bpy.context, Ev("MOUSEMOVE", "NOTHING", mouse_x=self.mx, mouse_y=self.my))

    def wheel(self, up=True, **kw):
        return self.s.handle_event(bpy.context, Ev("WHEELUPMOUSE" if up else "WHEELDOWNMOUSE", "PRESS", **kw))

    def run(self, seconds, dt=1.0 / 60.0):
        steps = int(round(seconds / dt))
        for _ in range(steps):
            self.t += dt
            self.s.tick(now=self.t)

    def hold(self, key, seconds, dt=1.0 / 60.0):
        self.press(key)
        self.run(seconds, dt)
        self.release(key)

    def end(self, cancel=False):
        self.s.end(bpy.context, cancel=cancel)
