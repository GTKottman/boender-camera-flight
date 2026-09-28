"""UI code paths checked without a GPU.

Panels are drawn into a mock layout that validates every property name,
operator property and icon against Blender's RNA. The HUD is drawn with
mocked ``gpu``/``blf`` modules so every branch runs.
"""

import math
import unittest
from types import SimpleNamespace

import bpy
from helpers import Flight, make_box, make_camera, reset_scene
from mathutils import Vector

from camera_flight import hud, keymap, prefs, props, session, ui

ICONS = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())


class MockOperatorProps:
    def __init__(self, test, idname):
        module, name = idname.split(".")
        rna = getattr(getattr(bpy.ops, module), name).get_rna_type()
        object.__setattr__(self, "_names", set(rna.properties.keys()))
        object.__setattr__(self, "_test", test)
        object.__setattr__(self, "_idname", idname)

    def __setattr__(self, key, value):
        self._test.assertIn(key, self._names, "%s has no property %r" % (self._idname, key))


class MockLayout:
    def __init__(self, test):
        self.test = test
        self.calls = 0

    def __setattr__(self, key, value):
        object.__setattr__(self, key, value)

    def _icon(self, kw):
        icon = kw.get("icon")
        if icon:
            self.test.assertIn(icon, ICONS, "unknown icon %r" % icon)

    def _child(self, *args, **kw):
        return MockLayout(self.test)

    row = column = box = split = grid_flow = _child

    def prop(self, data, name, **kw):
        self._icon(kw)
        rna = getattr(data, "bl_rna", None)
        self.test.assertIsNotNone(rna, "prop() on non-RNA data for %r" % name)
        self.test.assertIn(name, rna.properties.keys(), "%s has no property %r" % (rna.identifier, name))

    def operator(self, idname, **kw):
        self._icon(kw)
        return MockOperatorProps(self.test, idname)

    def menu(self, name, **kw):
        self._icon(kw)
        self.test.assertTrue(hasattr(bpy.types, name), "unknown menu %r" % name)

    def label(self, **kw):
        self._icon(kw)

    def separator(self, **kw):
        pass

    def template_list(self, listtype, list_id, data, propname, active_data, active_prop, **kw):
        self.test.assertTrue(hasattr(bpy.types, listtype))
        self.test.assertIn(propname, data.bl_rna.properties.keys())
        self.test.assertIn(active_prop, active_data.bl_rna.properties.keys())


class PanelTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.cam = make_camera()

    def draw_all(self):
        for cls in ui.classes:
            if issubclass(cls, bpy.types.Panel):
                fake = SimpleNamespace(layout=MockLayout(self))
                if hasattr(cls, "draw_header"):
                    cls.draw_header(fake, bpy.context)
                cls.draw(fake, bpy.context)

    def test_panels_empty_scene(self):
        self.draw_all()

    def test_panels_with_data(self):
        s = bpy.context.scene.camera_flight
        s.record_mode = "PUNCH"
        bpy.ops.camera_flight.bookmark_add()
        from test_takes import make_take

        take = make_take(self.cam)
        take.rating = 3
        self.draw_all()
        fake = SimpleNamespace(bitflag_filter_item=1, filter_name="", use_filter_sort_alpha=False)
        for i, item in enumerate(s.takes):
            ui.CAMERA_FLIGHT_UL_takes.draw_item(fake, bpy.context, MockLayout(self), s, item, 0, s, "take_index", i)
        for i, item in enumerate(s.bookmarks):
            ui.CAMERA_FLIGHT_UL_bookmarks.draw_item(fake, bpy.context, MockLayout(self), s, item, 0, s, "bookmark_index", i)
        fake.filter_name = "take"
        flags, _order = ui.CAMERA_FLIGHT_UL_takes.filter_items(fake, bpy.context, s, "takes")
        self.assertEqual(flags, [1])

    def test_panels_while_flying(self):
        f = Flight(self.cam)
        f.tap("R")
        self.draw_all()
        f.end()

    def test_preferences_draw(self):
        real = prefs.get_prefs()
        for tab in ("GENERAL", "KEYS", "GAMEPAD", "HUD"):
            proxy = SimpleNamespace(layout=MockLayout(self), bl_rna=real.bl_rna)
            for name in real.bl_rna.properties.keys():
                if name != "rna_type":
                    setattr(proxy, name, getattr(real, name))
            proxy.tab = tab
            prefs.CAMERA_FLIGHT_AddonPreferences.draw(proxy, bpy.context)

    def test_enum_icons(self):
        for item in props.MODE_ITEMS:
            self.assertIn(item[3], ICONS)

    def test_default_keys_have_no_conflicts(self):
        seen = {}
        for action in keymap.ACTIONS:
            for key in (action.primary, action.secondary):
                if key == "NONE":
                    continue
                self.assertIn(key, keymap.EVENT_NAMES)
                self.assertNotIn(key, seen, "%s bound to both %s and %s" % (key, seen.get(key), action.id))
                seen[key] = action.id
            self.assertNotIn(action.primary, keymap.NUMBER_KEYS)

    def test_reset_keys(self):
        real = prefs.get_prefs()
        real.key_forward = "I"
        bpy.ops.camera_flight.reset_keys()
        self.assertEqual(real.key_forward, "W")

    def test_remapped_key_drives_flight(self):
        real = prefs.get_prefs()
        real.key_forward = "I"
        try:
            f = Flight(self.cam)
            f.hold("I", 0.5)
            self.assertGreater(self.cam.location.y, 0.5)
            f.end()
        finally:
            real.key_forward = "W"


# --------------------------------------------------------------------------- HUD with fake GPU
class _Shader:
    def bind(self):
        pass

    def uniform_float(self, name, value):
        assert name in ("color", "viewportSize", "lineWidth"), name


class _Batch:
    def __init__(self, shader, kind, content, indices=None):
        assert kind in ("TRIS", "LINES", "LINE_STRIP"), kind
        pos = content["pos"]
        assert len(pos) >= 2
        for p in pos:
            assert len(p) == 3 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in p), p

    def draw(self, shader):
        pass


class _FakeGPU:
    class shader:
        @staticmethod
        def from_builtin(name):
            assert name in ("UNIFORM_COLOR", "POLYLINE_UNIFORM_COLOR"), name
            return _Shader()

    class state:
        @staticmethod
        def blend_set(mode):
            assert mode in ("ALPHA", "NONE")

        @staticmethod
        def viewport_get():
            return (0, 0, 1280, 720)


class _FakeBLF:
    SHADOW = 1
    drawn = []

    @staticmethod
    def size(font, size):
        assert size > 0

    @staticmethod
    def dimensions(font, text):
        return (len(text) * 7.0, 12.0)

    @staticmethod
    def draw(font, text):
        assert isinstance(text, str)
        _FakeBLF.drawn.append(text)

    enable = disable = staticmethod(lambda *a: None)
    shadow = shadow_offset = color = position = staticmethod(lambda *a: None)


def _fake_project(region, rv3d, co):
    """Pinhole projection through the flying camera (``rv3d.session``)."""
    pose = rv3d.session.last_pose
    local = pose.rot.inverted() @ (Vector(co) - pose.pos)  # camera space: -Z forward, +Y up
    if local.z >= 0.0:
        return None
    return Vector((640 - local.x / local.z * 600, 360 - local.y / local.z * 600))


class HudTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.saved = (hud.gpu, hud.blf, hud.batch_for_shader, hud.location_3d_to_region_2d)
        hud.gpu, hud.blf, hud.batch_for_shader, hud.location_3d_to_region_2d = _FakeGPU, _FakeBLF, _Batch, _fake_project
        hud._shaders.clear()
        _FakeBLF.drawn = []

    def tearDown(self):
        hud.gpu, hud.blf, hud.batch_for_shader, hud.location_3d_to_region_2d = self.saved
        hud._shaders.clear()
        if session.ACTIVE is not None:
            session.ACTIVE.end(cancel=True)

    def test_draw_everything(self):
        make_box((0, 10, 1))
        cam = make_camera()
        cam.data.dof.use_dof = True
        f = Flight(cam)
        s = f.s
        s.rv3d = SimpleNamespace(view_perspective="CAMERA", session=s)
        region = SimpleNamespace(width=1280, height=720)
        f.press("ONE", ctrl=True)
        f.tap("R")
        f.hold("W", 0.5)
        f.tap("R")  # rewinds to the take's start
        f.hold("S", 0.5)  # back off so the take's ghost is in front of us
        hud._draw_2d(s, region)
        self.assertIn("ghost", _FakeBLF.drawn)
        f.tap("L")
        f.tap("Y")
        f.tap("J")
        f.tap("F1")
        s.captured = False
        s.countdown = 1.5
        f.s.settings.record_mode = "PUNCH"
        f.s.settings.time_scale = 0.5
        hud._draw_2d(s, region)
        s.countdown = 0.0
        s.captured = True
        f.tap("P")  # take playback
        f.run(0.2)
        hud._draw_2d(s, region)
        f.tap("R")  # punch-in recording
        f.run(0.2)
        hud._draw_2d(s, region)
        f.tap("P")  # paused recording
        hud._draw_2d(s, region)
        # 3D path + live trail
        self.assertTrue(hud._take_path(s))
        hud._lines(hud._take_path(s), (1, 1, 1, 1), 2.0, strip=True)
        text = " ".join(_FakeBLF.drawn)
        for needle in ("CAMERA FLIGHT", "REC", "PAUSED", "GET READY", "CONTROLS", "MOUSE RELEASED", "TAKE PLAYBACK"):
            self.assertIn(needle, text)
        f.end()

    def test_draw_non_camera_view(self):
        cam = make_camera()
        f = Flight(cam)
        f.s.rv3d = SimpleNamespace(view_perspective="PERSP", session=f.s)
        hud._draw_2d(f.s, SimpleNamespace(width=800, height=600))
        f.end()


if __name__ == "__main__":
    unittest.main()
