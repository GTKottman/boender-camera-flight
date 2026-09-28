"""End-to-end flights: a headless FlightSession driven by fake events and a fake clock."""

import math
import unittest

import bpy
from helpers import Ev, Flight, make_box, make_camera, make_ground, reset_scene
from mathutils import Euler, Quaternion, Vector

from camera_flight import compat, session, takes


def fcurve(action, path, index, id_data):
    return compat.find_fcurve(action, path, index, compat.find_slot(action, id_data))


def key_frames(fc):
    return [kp.co.x for kp in fc.keyframe_points]


class FlightTestCase(unittest.TestCase):
    def setUp(self):
        self.scene = reset_scene()

    def tearDown(self):
        if session.ACTIVE is not None:
            session.ACTIVE.end(cancel=True)


class BasicFlight(FlightTestCase):
    def test_fly_forward(self):
        cam = make_camera()
        f = Flight(cam, speed=5.0, acceleration=6.0)
        f.hold("W", 1.0)
        self.assertGreater(cam.location.y, 3.5)
        self.assertLess(cam.location.y, 5.0)
        self.assertAlmostEqual(cam.location.x, 0.0, places=4)
        self.assertAlmostEqual(cam.location.z, 1.0, places=4)
        f.end()

    def test_strafe_rise_and_sink(self):
        cam = make_camera()
        f = Flight(cam, acceleration=20.0)
        f.hold("D", 0.5)
        self.assertGreater(cam.location.x, 1.0)
        f.hold("E", 0.5)
        self.assertGreater(cam.location.z, 2.0)
        z = cam.location.z
        f.hold("Q", 0.5)
        self.assertLess(cam.location.z, z)
        f.end()

    def test_mouse_look_turns_right(self):
        cam = make_camera()
        f = Flight(cam, look_smoothing=0.0)
        f.mouse(100, 0)  # 100 px right at 0.1 deg/px
        f.run(0.1)
        self.assertAlmostEqual(math.degrees(cam.rotation_euler.z), -10.0, places=3)
        f.mouse(0, 50)  # up
        f.run(0.1)
        self.assertAlmostEqual(math.degrees(cam.rotation_euler.x), 95.0, places=3)
        f.end()

    def test_wheel_changes_speed_and_zoom(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam, zoom_smoothing=0.0)
        f.wheel(True)
        self.assertAlmostEqual(s.speed, 5.0 * 1.25, places=4)
        f.wheel(True, ctrl=True)
        f.run(0.05)
        self.assertAlmostEqual(cam.data.lens, 50.0 * 1.12, places=2)
        f.end()

    def test_cancel_restores_camera(self):
        cam = make_camera(loc=(1, 2, 3))
        f = Flight(cam)
        f.hold("W", 1.0)
        f.press("ESC")
        self.assertEqual(f.s.finished, "CANCEL")
        f.end(cancel=True)
        self.assertLess((cam.location - Vector((1, 2, 3))).length, 1e-5)
        self.assertIsNone(session.ACTIVE)

    def test_finish_keeps_position(self):
        cam = make_camera()
        f = Flight(cam)
        f.hold("W", 1.0)
        y = cam.location.y
        f.press("RET")
        self.assertEqual(f.s.finished, "FINISH")
        f.end()
        self.assertAlmostEqual(cam.location.y, y, places=5)

    def test_released_mouse_passes_events_through(self):
        cam = make_camera()
        f = Flight(cam)
        f.press("TAB")
        self.assertFalse(f.s.captured)
        self.assertEqual(f.press("W"), "PASS")
        f.run(0.5)
        self.assertAlmostEqual(cam.location.y, 0.0, places=5)
        f.press("TAB")
        self.assertTrue(f.s.captured)
        f.end()

    def test_mode_cycle_and_toggles(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam)
        f.tap("M")
        f.run(0.05)
        self.assertEqual(s.mode, "DRONE")
        self.assertEqual(f.s.ctrl.mode, "DRONE")
        f.tap("G")
        f.run(0.05)
        self.assertEqual(s.mode, "WALK")
        f.tap("G")
        f.run(0.05)
        self.assertEqual(s.mode, "DRONE")
        f.end()

    def test_level_and_roll(self):
        cam = make_camera()
        f = Flight(cam, look_smoothing=0.0)
        f.hold("Z", 0.5)
        self.assertGreater(abs(cam.rotation_euler.y), 0.2)
        f.tap("X")
        f.run(1.0)
        self.assertAlmostEqual(cam.rotation_euler.y, 0.0, places=3)
        f.end()


class Recording(FlightTestCase):
    def record(self, f, key="W", seconds=2.0):
        f.tap("R")
        f.hold(key, seconds)
        f.tap("R")

    def test_record_new_take(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam, speed=5.0)
        f.run(0.5)  # fly around first; recording starts mid-flight
        self.record(f)
        self.assertEqual(len(s.takes), 1)
        take = s.takes[0]
        self.assertEqual(take.frame_start, 1)
        self.assertAlmostEqual(take.frame_end, 49, delta=1)
        fc = fcurve(take.action, "location", 1, cam)
        ys = [kp.co.y for kp in fc.keyframe_points]
        self.assertEqual(len(ys), take.frame_end - take.frame_start + 1)
        self.assertTrue(all(b >= a - 1e-6 for a, b in zip(ys, ys[1:])))
        self.assertGreater(ys[-1], 5.0)
        self.assertEqual(key_frames(fc), list(range(take.frame_start, take.frame_end + 1)))
        # lens + focus recorded on the camera data
        self.assertIsNotNone(fcurve(take.data_action, "lens", 0, cam.data))
        # rewind after take: frame and camera back at the start
        self.assertEqual(self.scene.frame_current, 1)
        self.assertAlmostEqual(cam.location.y, 0.0, places=4)
        f.end()
        # after the flight the take drives the camera
        self.assertEqual(cam.animation_data.action, take.action)
        self.scene.frame_set(25)
        self.assertAlmostEqual(cam.location.y, fc.evaluate(25), places=4)

    def test_recorded_motion_matches_speed(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam, speed=4.0, acceleration=100.0, rewind_after_take=False)
        f.press("W")
        f.run(1.0)
        f.tap("R")
        f.run(1.0)  # exactly one second = 24 frames at full speed
        f.tap("R")
        f.release("W")
        take = s.takes[0]
        fc = fcurve(take.action, "location", 1, cam)
        dist = fc.evaluate(take.frame_start + 24) - fc.evaluate(take.frame_start)
        self.assertAlmostEqual(dist, 4.0, delta=0.05)
        f.end()

    def test_slow_motion_recording(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam, speed=4.0, acceleration=100.0, time_scale=0.5)
        f.press("W")
        f.run(0.5)
        f.tap("R")
        f.run(2.0)  # 2 real seconds at half speed = 1 scene second = 24 frames
        f.tap("R")
        take = s.takes[0]
        self.assertAlmostEqual(take.frame_end - take.frame_start, 24, delta=1)
        fc = fcurve(take.action, "location", 1, cam)
        # speed is in scene time, so the move still covers ~4 units per scene second
        self.assertAlmostEqual(fc.evaluate(take.frame_start + 24) - fc.evaluate(take.frame_start), 4.0, delta=0.1)
        f.end()

    def test_frame_locked_never_skips(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam, timing="FRAME_LOCKED")
        f.tap("R")
        f.run(0.5, dt=0.2)  # a very slow viewport: 5 fps
        f.tap("R")
        take = s.takes[0]
        self.assertAlmostEqual(take.frame_end - take.frame_start, 2, delta=1)
        f.end()

    def test_stop_at_end(self):
        self.scene.frame_end = 12
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam)
        f.tap("R")
        f.run(2.0)
        self.assertFalse(f.s.recording)
        self.assertEqual(s.takes[0].frame_end, 12)
        f.end()

    def test_countdown(self):
        cam = make_camera()
        f = Flight(cam, countdown=1.0)
        f.tap("R")
        f.run(0.5)
        self.assertFalse(f.s.recording)
        f.run(0.6)
        self.assertTrue(f.s.recording)
        f.end()

    def test_pause_freezes_time(self):
        cam = make_camera()
        f = Flight(cam)
        f.tap("R")
        f.run(0.5)
        frame = self.scene.frame_current
        f.tap("P")
        f.hold("W", 1.0)
        self.assertEqual(self.scene.frame_current, frame)
        f.tap("P")
        f.run(0.5)
        self.assertGreater(self.scene.frame_current, frame + 5)
        f.end()

    def test_discard_take(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam)
        self.record(f)
        name = s.takes[0].action.name
        f.tap("BACK_SPACE")
        self.assertEqual(len(s.takes), 0)
        self.assertIsNone(bpy.data.actions.get(name))
        f.end()

    def test_discard_while_recording(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam)
        f.tap("R")
        f.run(0.5)
        f.tap("BACK_SPACE")
        self.assertFalse(f.s.recording)
        self.assertEqual(len(s.takes), 0)
        f.end()

    def test_multiple_takes_and_final_assignment(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam)
        self.record(f, "W", 1.0)
        self.record(f, "D", 1.0)
        self.assertEqual(len(s.takes), 2)
        f.end()
        self.assertEqual(cam.animation_data.action, s.takes[1].action)
        self.assertEqual(s.take_index, 1)

    def test_shake_is_recorded(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam, shake_enabled=True, shake_intensity=2.0)
        self.record(f, "W", 1.0)
        fc = fcurve(s.takes[0].action, "rotation_euler", 2, cam)
        vals = [kp.co.y for kp in fc.keyframe_points]
        self.assertGreater(max(vals) - min(vals), math.radians(0.1))
        f.end()

    def test_manual_keyframes(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam, key_step=10)
        f.tap("K")
        f.hold("W", 0.5)
        f.tap("K")
        f.hold("D", 0.5)
        f.tap("K")
        take = s.takes[0]
        self.assertEqual(take.source, "Keyframes")
        fc = fcurve(take.action, "location", 1, cam)
        self.assertEqual(key_frames(fc), [1.0, 11.0, 21.0])
        self.assertEqual(self.scene.frame_current, 31)
        f.end()

    def test_quaternion_camera(self):
        cam = make_camera(rotation_mode="QUATERNION")
        s = self.scene.camera_flight
        f = Flight(cam, look_smoothing=0.0)
        f.tap("R")
        for _ in range(20):
            f.mouse(60, 0)
            f.run(0.1)
        f.tap("R")
        take = s.takes[0]
        fcs = [fcurve(take.action, "rotation_quaternion", i, cam) for i in range(4)]
        self.assertTrue(all(fcs))
        # continuous: consecutive keys never flip sign
        n = len(fcs[0].keyframe_points)
        prev = None
        for k in range(n):
            q = Quaternion([fc.keyframe_points[k].co.y for fc in fcs])
            if prev is not None:
                self.assertGreater(q.dot(prev), 0.9)
            prev = q
        f.end()

    def test_euler_continuity_through_full_turn(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam, look_smoothing=0.0)
        f.tap("R")
        for _ in range(40):
            f.mouse(120, 0)  # 12 degrees per step, 480 degrees total
            f.run(1.0 / 24.0)
        f.tap("R")
        fc = fcurve(s.takes[0].action, "rotation_euler", 2, cam)
        vals = [kp.co.y for kp in fc.keyframe_points]
        steps = [abs(b - a) for a, b in zip(vals, vals[1:])]
        self.assertLess(max(steps), math.radians(30))
        self.assertLess(vals[-1], -math.radians(400))
        f.end()

    def test_parented_camera_roundtrip(self):
        rig = bpy.data.objects.new("Rig", None)
        self.scene.collection.objects.link(rig)
        rig.location = (5, -3, 2)
        rig.rotation_euler = (0.2, 0.1, 0.7)
        rig.scale = (2, 2, 2)
        cam = make_camera(loc=(0, 0, 0))
        cam.parent = rig
        bpy.context.view_layer.update()
        s = self.scene.camera_flight
        f = Flight(cam, rewind_after_take=False)
        f.tap("R")
        f.press("W")
        f.run(1.0)
        world_mid = f.s.last_pose.pos.copy()
        mid_frame = self.scene.frame_current
        f.run(0.5)
        f.release("W")
        f.tap("R")
        f.end()
        self.scene.frame_set(mid_frame)
        self.assertLess((cam.matrix_world.translation - world_mid).length, 0.35)
        take = s.takes[0]
        ev = takes.TakeEvaluator(take, cam)
        pose = ev.pose(mid_frame, takes.parent_matrix(cam))
        self.assertLess((pose.pos - cam.matrix_world.translation).length, 1e-3)


class PunchIn(FlightTestCase):
    def base_take(self, f):
        f.tap("R")
        f.hold("W", 2.0)
        f.tap("R")

    def test_punch_in_replaces_rest(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam, speed=5.0, acceleration=50.0)
        self.base_take(f)
        take = s.takes[0]
        fc_y = fcurve(take.action, "location", 1, cam)
        before = {int(kp.co.x): kp.co.y for kp in fc_y.keyframe_points}
        old_end = take.frame_end

        s.record_mode = "PUNCH"
        f.tap("P")  # play the take
        f.run(0.5)
        # camera follows the take during playback
        clock = f.s.clock
        ev = takes.TakeEvaluator(take, cam)
        self.assertLess((f.s.ctrl.pos - ev.pose(clock).pos).length, 0.2)
        f.tap("R")  # punch in
        punch_frame = f.s.recorder.start_frame
        f.hold("D", 0.5)
        f.tap("R")
        self.assertEqual(len(s.takes), 1)
        take = s.takes[0]
        fc_y = fcurve(take.action, "location", 1, cam)
        fc_x = fcurve(take.action, "location", 0, cam)
        for kp in fc_y.keyframe_points:
            fr = int(kp.co.x)
            if fr < punch_frame:
                self.assertAlmostEqual(kp.co.y, before[fr], places=5)
        self.assertGreater(fc_x.evaluate(take.frame_end), 0.5)
        self.assertLess(take.frame_end, old_end)
        # seamless hand-off: no jump at the punch-in frame
        self.assertAlmostEqual(fc_y.evaluate(punch_frame), before[punch_frame], delta=0.25)
        f.end()

    def test_punch_keep_rest_blends_back(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam, speed=5.0, acceleration=50.0)
        self.base_take(f)
        take = s.takes[0]
        fc_x = fcurve(take.action, "location", 0, cam)
        fc_y = fcurve(take.action, "location", 1, cam)
        tail = {int(kp.co.x): kp.co.y for kp in fc_y.keyframe_points}
        old_end = take.frame_end
        s.record_mode = "PUNCH"
        s.punch_out = "KEEP"
        s.punch_blend = 6
        f.tap("P")
        f.run(0.3)
        f.tap("R")
        f.hold("E", 0.6)
        f.tap("R")
        take = s.takes[0]
        fc_z = fcurve(take.action, "location", 2, cam)
        punch_out = max(int(kp.co.x) for kp in fc_z.keyframe_points if kp.co.y > 1.0 + 1e-3)
        self.assertEqual(take.frame_end, old_end)  # the old tail survived
        for fr in range(punch_out + 1, old_end + 1):
            self.assertAlmostEqual(fc_y.evaluate(fr), tail[fr], places=4)
        self.assertAlmostEqual(fc_x.evaluate(old_end), 0.0, places=4)
        f.end()

    def test_undo_punch(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam, acceleration=50.0)
        self.base_take(f)
        take = s.takes[0]
        name = take.action.name
        fc = fcurve(take.action, "location", 1, cam)
        before = [tuple(kp.co) for kp in fc.keyframe_points]
        s.record_mode = "PUNCH"
        f.tap("P")
        f.run(0.4)
        f.tap("R")
        f.hold("D", 0.4)
        f.tap("R")
        f.tap("BACK_SPACE")
        take = s.takes[0]
        self.assertEqual(take.action.name, name)
        fc = fcurve(take.action, "location", 1, cam)
        self.assertEqual([tuple(kp.co) for kp in fc.keyframe_points], before)
        f.end()
        self.assertEqual(len([a for a in bpy.data.actions if "before punch" in a.name]), 0)

    def test_scrub_snaps_to_take(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam, acceleration=50.0)
        self.base_take(f)
        s.record_mode = "PUNCH"
        for _ in range(20):
            f.tap("PERIOD")
        f.run(0.05)
        ev = takes.TakeEvaluator(s.takes[0], cam)
        self.assertEqual(self.scene.frame_current, 21)
        self.assertLess((f.s.ctrl.pos - ev.pose(21).pos).length, 1e-3)
        f.end()


class Tools(FlightTestCase):
    def test_bookmarks(self):
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam, acceleration=50.0, bookmark_travel_time=0.5)
        f.press("ONE", ctrl=True)
        self.assertEqual(len(s.bookmarks), 1)
        self.assertEqual(s.bookmarks[0].slot, 1)
        f.hold("W", 1.0)
        f.hold("D", 1.0)
        f.run(1.0)
        self.assertGreater(cam.location.length, 3.0)
        f.tap("ONE")
        f.run(0.7)
        self.assertLess((cam.location - Vector((0, 0, 1))).length, 1e-3)
        f.end()

    def test_orbit_around_crosshair_target(self):
        make_box((0, 10, 1), size=2.0)
        cam = make_camera()
        s = self.scene.camera_flight
        f = Flight(cam, look_smoothing=0.0)
        f.tap("O")
        f.run(0.05)
        self.assertEqual(s.mode, "ORBIT")
        pivot = f.s.ctrl.pivot.copy()
        self.assertAlmostEqual(pivot.y, 9.0, places=3)  # front face of the box
        f.hold("D", 1.0)
        pos = f.s.ctrl.pos
        self.assertAlmostEqual((pos - pivot).length, 9.0, places=2)
        self.assertGreater(abs(pos.x), 2.0)
        f.end()

    def test_walk_mode_lands_on_ground(self):
        make_ground()
        cam = make_camera(loc=(0, 0, 6))
        s = self.scene.camera_flight
        f = Flight(cam, eye_height=1.6)
        f.tap("G")
        f.run(2.0)
        self.assertEqual(s.mode, "WALK")
        self.assertTrue(f.s.ctrl.grounded)
        self.assertAlmostEqual(cam.location.z, 1.6, delta=0.02)
        f.end()

    def test_collision_with_scene_geometry(self):
        make_box((0, 10, 1), size=2.0)
        cam = make_camera()
        f = Flight(cam, collisions=True, collision_radius=0.5, speed=8.0, acceleration=20.0)
        f.hold("W", 3.0)
        self.assertLess(cam.location.y, 8.5 + 1e-3)
        self.assertGreater(cam.location.y, 8.3)
        f.end()

    def test_autofocus(self):
        make_box((0, 10, 1), size=2.0)
        cam = make_camera()
        f = Flight(cam, focus_smoothing=0.0)
        f.tap("F")
        f.run(0.1)
        self.assertTrue(cam.data.dof.use_dof)
        self.assertAlmostEqual(cam.data.dof.focus_distance, 9.0, places=3)
        f.end()

    def test_fly_to_crosshair(self):
        make_box((0, 20, 1), size=2.0)
        cam = make_camera()
        f = Flight(cam, speed=10.0)
        f.tap("T")
        f.run(3.0)
        self.assertGreater(cam.location.y, 14.0)
        self.assertLess(cam.location.y, 19.0)
        f.end()

    def test_lock_and_follow_moving_target(self):
        box = make_box((0, 10, 1), size=2.0)
        box.keyframe_insert("location", frame=1)
        box.location.x = 24.0
        box.keyframe_insert("location", frame=49)
        for fc in compat.action_fcurves(box.animation_data.action, compat.get_action_slot(box)):
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"
        self.scene.frame_set(1)
        cam = make_camera()
        f = Flight(cam, track_smoothing=0.0)
        f.tap("L")
        self.assertIsNotNone(f.s.lock)
        self.assertEqual(f.s.lock.label, box.name)
        f.tap("Y")
        self.assertTrue(f.s.follow)
        f.tap("P")
        f.run(1.0)
        f.tap("P")
        f.run(0.05)
        # followed ~12 units along x and still looking at the box
        self.assertAlmostEqual(cam.location.x, box.matrix_world.translation.x, delta=0.6)
        aim = (f.s.lock.prev_point - f.s.ctrl.pos).normalized()
        self.assertGreater(f.s.ctrl.forward().dot(aim), 0.999)
        f.end()

    def test_snapshot_camera(self):
        cam = make_camera()
        f = Flight(cam)
        f.hold("W", 0.5)
        f.tap("N")
        shot = bpy.data.objects.get("Shot 001")
        self.assertIsNotNone(shot)
        self.assertEqual(shot.type, "CAMERA")
        self.assertIn(shot.name, bpy.data.collections["Flight Shots"].objects)
        bpy.context.view_layer.update()
        self.assertLess((shot.matrix_world.translation - cam.matrix_world.translation).length, 1e-4)
        f.end()

    def test_dolly_zoom(self):
        make_box((0, 10, 1), size=2.0)
        cam = make_camera()
        f = Flight(cam, acceleration=50.0)
        f.tap("V")
        self.assertIsNotNone(f.s.ctrl.dolly)
        f.hold("S", 1.0)
        d = 9.0 - cam.location.y
        self.assertAlmostEqual(cam.data.lens / d, 50.0 / 9.0, places=3)
        f.end()

    def test_existing_animation_is_adopted_and_restored(self):
        cam = make_camera()
        cam.keyframe_insert("location", frame=1)
        cam.location.x = 5
        cam.keyframe_insert("location", frame=20)
        original = cam.animation_data.action
        s = self.scene.camera_flight
        f = Flight(cam)
        self.assertEqual(len(s.takes), 1)
        self.assertEqual(s.takes[0].action, original)
        f.hold("W", 0.5)
        f.end()
        self.assertEqual(cam.animation_data.action, original)

    def test_start_from_view(self):
        from types import SimpleNamespace

        from mathutils import Matrix

        cam = make_camera(loc=(50, 50, 50))
        view = Matrix.LocRotScale(Vector((1, 2, 3)), Euler((math.radians(80), 0, 0.3)), None)
        for start_from, expected in (("AUTO", (1, 2, 3)), ("CAMERA", (50, 50, 50)), ("VIEW", (1, 2, 3))):
            self.scene.camera_flight.start_from = start_from
            cam.location = (50, 50, 50)
            bpy.context.view_layer.update()
            s = session.FlightSession(bpy.context, cam)
            s.rv3d = SimpleNamespace(view_perspective="PERSP", view_matrix=view.inverted())
            s.start(bpy.context)
            self.assertLess((cam.location - Vector(expected)).length, 1e-4, start_from)
            s.end(cancel=True)
            s.end(cancel=True)  # idempotent

    def test_undo_and_save_blocked_while_released(self):
        cam = make_camera()
        f = Flight(cam)
        f.press("TAB")
        self.assertEqual(f.press("Z", ctrl=True), "HANDLED")
        self.assertEqual(f.press("S", ctrl=True), "HANDLED")
        self.assertEqual(f.press("A"), "PASS")
        f.end()

    def test_camera_deleted_mid_flight(self):
        cam = make_camera()
        f = Flight(cam)
        f.tap("R")
        f.run(0.2)
        bpy.data.objects.remove(cam)
        f.run(0.1)
        self.assertEqual(f.s.finished, "CANCEL")
        f.end(cancel=True)
        self.assertIsNone(session.ACTIVE)

    def test_mouse_warp_detection(self):
        from types import SimpleNamespace

        class Win:
            def __init__(self, works):
                self.works = works
                self.cursor = (500, 400)

            def cursor_warp(self, x, y):
                if self.works:
                    self.cursor = (x, y)

            def cursor_modal_set(self, name):
                pass

            def cursor_modal_restore(self):
                pass

        for works in (True, False):
            reset_scene()
            cam = make_camera()
            self.scene.camera_flight.look_smoothing = 0.0
            win = Win(works)
            s = session.FlightSession(bpy.context, cam)
            s.window = win
            s.region = SimpleNamespace(x=0, y=0, width=1000, height=800)
            s.captured = True
            s.start(bpy.context)
            s.tick(now=0.0)
            total = 0
            t = 0.0
            for _ in range(40):  # sweep the mouse right in 30px steps
                x, y = win.cursor
                win.cursor = (x + 30, y)
                total += 30
                s.handle_event(bpy.context, Ev("MOUSEMOVE", "NOTHING", mouse_x=win.cursor[0], mouse_y=win.cursor[1]))
                t += 1 / 60
                s.tick(now=t)
            # every pixel turned the camera, warping or not, with no jumps
            self.assertAlmostEqual(math.degrees(-s.ctrl.yaw), total * 0.1, delta=0.01)
            self.assertEqual(s._warp_ok, works)
            s.end(cancel=True)

    def test_help_hud_guides_toggles(self):
        cam = make_camera()
        f = Flight(cam)
        f.tap("F1")
        self.assertTrue(f.s.help_visible)
        f.tap("H")
        self.assertFalse(f.s.hud_visible)
        f.tap("B")
        self.assertTrue(cam.data.show_composition_thirds)
        f.end()
        self.assertFalse(cam.data.show_composition_thirds)  # restored


if __name__ == "__main__":
    unittest.main()
