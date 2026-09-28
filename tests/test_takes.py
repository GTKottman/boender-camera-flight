"""Take editing tools, called through the registered operators."""

import math
import random
import unittest

import bpy
from helpers import make_camera, reset_scene
from mathutils import Vector

from camera_flight import compat, takes
from camera_flight.core.mathx import ypr_to_quat


def fc(take, path, index=0):
    cam = take.camera
    if path in ("lens", "dof.focus_distance"):
        return compat.find_fcurve(take.data_action, path, index, compat.find_slot(take.data_action, cam.data))
    return compat.find_fcurve(take.action, path, index, compat.find_slot(take.action, cam))


def make_take(cam, frames=60, noise=0.0, seed=0):
    """A synthetic per-frame take: straight line along +Y with optional jitter."""
    rng = random.Random(seed)
    channels = takes.Channels(cam)
    samples = []
    prev = None
    for i in range(frames):
        pos = Vector((rng.uniform(-noise, noise), i * 0.1, 1.0 + rng.uniform(-noise, noise)))
        q = ypr_to_quat(i * 0.01, 0.0, 0.0)
        loc, qb = takes.world_to_basis(cam, pos, q)
        rot = takes.rot_values(qb, channels, prev)
        prev = rot
        samples.append(takes.Sample(1 + i, loc, rot, 50.0 + i * 0.1, 10.0, pos))
    index = takes.record_take(bpy.context.scene, cam, samples, channels)
    return bpy.context.scene.camera_flight.takes[index]


class TakeTools(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.cam = make_camera()
        self.s = bpy.context.scene.camera_flight

    def test_record_take_structure(self):
        take = make_take(self.cam)
        self.assertEqual((take.frame_start, take.frame_end), (1, 60))
        self.assertTrue(take.action.use_fake_user)
        self.assertEqual(len(fc(take, "location", 1).keyframe_points), 60)
        self.assertAlmostEqual(fc(take, "lens").evaluate(11), 51.0, places=4)
        self.assertEqual(take.name, "Take 001")
        self.assertEqual(take.action.name, "Cam Take 001")

    def test_rename_renames_actions(self):
        take = make_take(self.cam)
        take.name = "Hero shot"
        self.assertEqual(take.action.name, "Cam Hero shot")
        self.assertEqual(take.data_action.name, "Cam Hero shot Lens")

    def test_activate_on_select(self):
        a = make_take(self.cam)
        b = make_take(self.cam)
        self.s.take_index = 0
        self.assertEqual(self.cam.animation_data.action, a.action)
        self.assertEqual(self.cam.data.animation_data.action, a.data_action)
        self.s.take_index = 1
        self.assertEqual(self.cam.animation_data.action, b.action)

    def test_smooth(self):
        take = make_take(self.cam, noise=0.05)
        curve = fc(take, "location", 0)
        before = [kp.co.y for kp in curve.keyframe_points]
        self.s.take_index = 0
        self.assertEqual(bpy.ops.camera_flight.take_smooth(sigma=3.0), {"FINISHED"})
        after = [kp.co.y for kp in curve.keyframe_points]
        self.assertAlmostEqual(after[0], before[0], places=6)
        self.assertAlmostEqual(after[-1], before[-1], places=6)
        spread = lambda v: max(v[5:-5]) - min(v[5:-5])  # noqa: E731
        self.assertLess(spread(after), spread(before) * 0.5)

    def test_simplify(self):
        take = make_take(self.cam)
        curve = fc(take, "location", 1)
        tol = 0.001
        self.assertEqual(bpy.ops.camera_flight.take_simplify(location=tol, rotation=0.001, lens=0.01), {"FINISHED"})
        self.assertLess(len(curve.keyframe_points), 20)
        # the real (Bezier) curve stays within tolerance at every original frame
        for frame in range(1, 61):
            self.assertLessEqual(abs(curve.evaluate(frame) - (frame - 1) * 0.1), tol + 1e-6)

    def test_retime_offset_reverse(self):
        take = make_take(self.cam)
        bpy.ops.camera_flight.take_retime(factor=2.0)
        self.assertEqual((take.frame_start, take.frame_end), (1, 119))
        self.assertAlmostEqual(fc(take, "location", 1).evaluate(119), 5.9, places=4)
        bpy.context.scene.frame_current = 100
        bpy.ops.camera_flight.take_offset()
        self.assertEqual((take.frame_start, take.frame_end), (100, 218))
        bpy.ops.camera_flight.take_reverse()
        self.assertEqual((take.frame_start, take.frame_end), (100, 218))
        self.assertAlmostEqual(fc(take, "location", 1).evaluate(100), 5.9, places=3)
        self.assertAlmostEqual(fc(take, "location", 1).evaluate(218), 0.0, places=3)

    def test_noise(self):
        take = make_take(self.cam)
        bpy.ops.camera_flight.take_add_noise(rotation=math.radians(1.0))
        mods = [m for f, _d in takes.take_fcurves(take) for m in f.modifiers if m.type == "NOISE"]
        self.assertEqual(len(mods), 3)
        bpy.ops.camera_flight.take_clear_noise()
        mods = [m for f, _d in takes.take_fcurves(take) for m in f.modifiers]
        self.assertEqual(mods, [])

    def test_to_curve(self):
        make_take(self.cam)
        self.assertEqual(bpy.ops.camera_flight.take_to_curve(step=5), {"FINISHED"})
        curve = bpy.data.objects["Take 001 Path"]
        pts = curve.data.splines[0].bezier_points
        self.assertEqual(len(pts), 12)
        self.assertLess((pts[0].co - Vector((0, 0, 1))).length, 1e-4)

    def test_to_camera_with_marker(self):
        take = make_take(self.cam)
        bpy.ops.camera_flight.take_to_camera(bind_marker=True)
        new = bpy.data.objects["Take 001 Cam"]
        self.assertIsNot(new.animation_data.action, take.action)
        marker = bpy.context.scene.timeline_markers[0]
        self.assertEqual(marker.camera, new)
        self.assertEqual(marker.frame, 1)
        bpy.context.scene.frame_set(30)
        self.assertAlmostEqual(new.location.y, 2.9, places=4)

    def test_duplicate_and_remove(self):
        take = make_take(self.cam)
        bpy.ops.camera_flight.take_duplicate()
        self.assertEqual(len(self.s.takes), 2)
        self.assertIsNot(self.s.takes[1].action, take.action)
        name = self.s.takes[1].action.name
        self.s.take_index = 1
        bpy.ops.camera_flight.take_remove()
        self.assertEqual(len(self.s.takes), 1)
        self.assertIsNone(bpy.data.actions.get(name))

    def test_set_range(self):
        make_take(self.cam)
        bpy.ops.camera_flight.take_set_range()
        scene = bpy.context.scene
        self.assertEqual((scene.frame_start, scene.frame_end), (1, 60))

    def test_bookmarks_to_take(self):
        s = self.s
        points = [((0, 0, 1), 0.0), ((10, 0, 3), 1.0), ((10, 10, 2), 2.5)]
        for i, (loc, yaw) in enumerate(points):
            bm = s.bookmarks.add()
            bm.slot = i + 1
            bm.location = loc
            bm.rotation = ypr_to_quat(yaw, 0.0, 0.0)
            bm.lens = 35.0 + 10 * i
        bpy.context.scene.frame_current = 1
        self.assertEqual(bpy.ops.camera_flight.bookmarks_to_take(frames_per=24), {"FINISHED"})
        take = s.takes[0]
        self.assertEqual((take.frame_start, take.frame_end), (1, 49))
        ev = takes.TakeEvaluator(take, self.cam)
        for i, (loc, yaw) in enumerate(points):
            pose = ev.pose(1 + 24 * i)
            self.assertLess((pose.pos - Vector(loc)).length, 1e-4)
            self.assertGreater(abs(pose.rot.dot(ypr_to_quat(yaw, 0, 0))), 1 - 1e-6)
            self.assertAlmostEqual(pose.lens, 35.0 + 10 * i, places=3)

    def test_bookmark_operators(self):
        bpy.ops.camera_flight.bookmark_add()
        bm = self.s.bookmarks[0]
        self.assertEqual(bm.slot, 1)
        self.assertLess((Vector(bm.location) - Vector((0, 0, 1))).length, 1e-5)
        self.cam.location = (7, 7, 7)
        bpy.context.view_layer.update()
        bpy.ops.camera_flight.bookmark_goto()
        bpy.context.view_layer.update()
        self.assertLess((self.cam.location - Vector((0, 0, 1))).length, 1e-5)
        bpy.ops.camera_flight.bookmark_remove()
        self.assertEqual(len(self.s.bookmarks), 0)

    def test_presets(self):
        bpy.ops.camera_flight.apply_preset(preset="DRONE")
        self.assertEqual(self.s.mode, "DRONE")
        self.assertGreater(self.s.bank_amount, 0.0)
        bpy.ops.camera_flight.shake_preset(preset="VEHICLE")
        self.assertTrue(self.s.shake_enabled)
        self.assertAlmostEqual(self.s.shake_frequency, 6.0)

    def test_new_camera_operator(self):
        bpy.context.scene.camera = None
        bpy.ops.camera_flight.new_camera()
        self.assertEqual(bpy.context.scene.camera.name, "FlightCam")


if __name__ == "__main__":
    unittest.main()
