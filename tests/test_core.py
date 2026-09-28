"""Flight physics and filters (no scene needed, only mathutils)."""

import math
import random
import unittest

from mathutils import Vector

from camera_flight import gamepad
from camera_flight.core import shake, smoothing
from camera_flight.core.controller import FlightController, FlightInput, FlightParams
from camera_flight.core.mathx import (
    Pose,
    angles_from_direction,
    forward_from_angles,
    quat_to_ypr,
    unwrap_near,
    ypr_to_quat,
)

DT = 1.0 / 60.0


class FakeEnv:
    """Ground plane at z=0 plus an optional wall at y=wall_y."""

    def __init__(self, ground=True, wall_y=None):
        self.ground = ground
        self.wall_y = wall_y

    def ray_cast(self, origin, direction, distance):
        best = None
        if self.ground and direction.z < -1e-9 and origin.z > 0.0:
            t = -origin.z / direction.z
            if t <= distance:
                best = (t, Vector((0, 0, 1)))
        if self.wall_y is not None and direction.y > 1e-9 and origin.y < self.wall_y:
            t = (self.wall_y - origin.y) / direction.y
            if t <= distance and (best is None or t < best[0]):
                best = (t, Vector((0, -1, 0)))
        if best is None:
            return False, None, None, None
        return True, origin + direction * best[0], best[1], None


def run(ctrl, inp, seconds, env=None):
    for _ in range(int(round(seconds / DT))):
        ctrl.step(DT, inp, env)
        inp.clear_impulses()


class MathTests(unittest.TestCase):
    def test_ypr_roundtrip(self):
        rng = random.Random(4)
        for _ in range(200):
            yaw = rng.uniform(-math.pi, math.pi)
            pitch = rng.uniform(-1.5, 1.5)
            roll = rng.uniform(-math.pi, math.pi)
            q = ypr_to_quat(yaw, pitch, roll)
            y2, p2, r2 = quat_to_ypr(q)
            q2 = ypr_to_quat(y2, p2, r2)
            self.assertGreater(abs(q.dot(q2)), 1 - 1e-6)

    def test_forward_matches_quaternion(self):
        for yaw, pitch in ((0, 0), (0.7, 0.3), (-2.0, -1.0), (3.0, 1.2)):
            q = ypr_to_quat(yaw, pitch, 0.4)
            fwd = q @ Vector((0, 0, -1))
            self.assertLess((fwd - forward_from_angles(yaw, pitch)).length, 1e-6)
            y2, p2 = angles_from_direction(fwd)
            self.assertAlmostEqual(math.remainder(y2 - yaw, math.tau), 0.0, places=6)
            self.assertAlmostEqual(p2, pitch, places=6)

    def test_camera_convention(self):
        # yaw 0 / pitch 0 looks along +Y with +Z up, like a camera rotated (90, 0, 0)
        q = ypr_to_quat(0, 0, 0)
        self.assertLess((q @ Vector((0, 0, -1)) - Vector((0, 1, 0))).length, 1e-6)
        self.assertLess((q @ Vector((0, 1, 0)) - Vector((0, 0, 1))).length, 1e-6)
        # positive roll banks left: camera up leans towards -X
        up = ypr_to_quat(0, 0, 0.3) @ Vector((0, 1, 0))
        self.assertLess(up.x, 0)

    def test_straight_down(self):
        q = ypr_to_quat(0.5, -math.pi / 2, 0)
        yaw, pitch, roll = quat_to_ypr(q)
        self.assertGreater(abs(ypr_to_quat(yaw, pitch, roll).dot(q)), 1 - 1e-6)

    def test_unwrap(self):
        self.assertAlmostEqual(unwrap_near(-3.1, 3.1), -3.1 + math.tau)

    def test_pose_lerp(self):
        a = Pose((0, 0, 0), ypr_to_quat(0, 0, 0), 20, 1)
        b = Pose((10, 0, 0), ypr_to_quat(1, 0, 0), 80, 3)
        m = a.lerp(b, 0.5)
        self.assertAlmostEqual(m.pos.x, 5)
        self.assertAlmostEqual(m.lens, 40)  # zoom interpolates geometrically
        self.assertAlmostEqual(m.focus, 2)


class ControllerTests(unittest.TestCase):
    def make(self, **kw):
        p = FlightParams()
        for k, v in kw.items():
            setattr(p, k, v)
        c = FlightController(p)
        c.set_pose((0, 0, 1), ypr_to_quat(0, 0, 0), 50, 10)
        return c

    def test_forward_reaches_speed(self):
        c = self.make(speed=5.0, accel=6.0)
        inp = FlightInput()
        inp.move = Vector((0, 1, 0))
        run(c, inp, 2.0)
        self.assertAlmostEqual(c.vel.y, 5.0, delta=0.05)
        self.assertGreater(c.pos.y, 7.0)
        self.assertAlmostEqual(c.pos.x, 0.0, places=6)

    def test_braking(self):
        c = self.make(speed=5.0, drag=5.0)
        c.vel = Vector((0, 5, 0))
        run(c, FlightInput(), 2.0)
        self.assertLess(c.vel.length, 0.01)

    def test_boost_and_precision(self):
        c = self.make(speed=2.0, boost=4.0, accel=50.0)
        inp = FlightInput()
        inp.move = Vector((0, 1, 0))
        inp.boost = 1.0
        run(c, inp, 1.0)
        self.assertAlmostEqual(c.vel.length, 8.0, delta=0.05)
        inp.boost = 0.0
        inp.precision = True
        run(c, inp, 1.0)
        self.assertAlmostEqual(c.vel.length, 0.4, delta=0.05)

    def test_mouse_look_and_pitch_clamp(self):
        c = self.make(look_smooth=0.0)
        inp = FlightInput()
        inp.look = [math.radians(30), math.radians(200)]
        c.step(DT, inp)
        self.assertAlmostEqual(c.yaw, math.radians(30), places=5)
        self.assertAlmostEqual(c.pitch, math.radians(90), places=5)

    def test_look_smoothing_converges(self):
        c = self.make(look_smooth=0.2)
        inp = FlightInput()
        inp.look = [0.5, 0.0]
        c.step(DT, inp)
        self.assertLess(c.yaw, 0.1)
        inp.clear_impulses()
        run(c, inp, 2.0)
        self.assertAlmostEqual(c.yaw, 0.5, places=4)

    def test_planar_keeps_altitude(self):
        c = self.make(planar=True, look_smooth=0.0)
        c.set_pose((0, 0, 1), ypr_to_quat(0, 0.8, 0))
        inp = FlightInput()
        inp.move = Vector((0, 1, 0))
        run(c, inp, 1.0)
        self.assertAlmostEqual(c.pos.z, 1.0, places=6)
        self.assertGreater(c.pos.y, 1.0)

    def test_free_follows_pitch(self):
        c = self.make()
        c.set_pose((0, 0, 1), ypr_to_quat(0, 0.8, 0))
        inp = FlightInput()
        inp.move = Vector((0, 1, 0))
        run(c, inp, 1.0)
        self.assertGreater(c.pos.z, 2.0)

    def test_auto_level(self):
        c = self.make(auto_level=True, level_time=0.2, look_smooth=0.0)
        inp = FlightInput()
        inp.roll = 1.0
        run(c, inp, 0.5)
        self.assertGreater(c.roll, 0.3)
        inp.roll = 0.0
        run(c, inp, 3.0)
        self.assertAlmostEqual(c.roll, 0.0, places=3)

    def test_bank_into_turns(self):
        c = self.make(bank=1.0, look_smooth=0.0)
        inp = FlightInput()
        inp.look_rate = [1.0, 0.0]  # turning left
        run(c, inp, 1.0)
        self.assertGreater(c.bank, 0.05)

    def test_space_mode_rolls_freely(self):
        c = self.make(look_smooth=0.0)
        c.set_mode("SPACE")
        inp = FlightInput()
        inp.roll = 1.0
        run(c, inp, 3.0)
        inp.roll = 0.0
        inp.look = [0.0, math.radians(120)]  # loop past vertical: no clamp in 6DOF
        c.step(DT, inp)
        fwd = c.forward()
        self.assertLess(fwd.y, 0.2)

    def test_mode_switch_preserves_orientation(self):
        c = self.make()
        c.set_pose((0, 0, 1), ypr_to_quat(0.4, 0.2, 0.1))
        before = c.base_rotation()
        c.set_mode("SPACE")
        self.assertGreater(abs(c.base_rotation().dot(before)), 1 - 1e-6)
        c.set_mode("FREE")
        self.assertGreater(abs(c.base_rotation().dot(before)), 1 - 1e-6)

    def test_orbit_keeps_distance_and_aims(self):
        c = self.make(look_smooth=0.0)
        c.set_pose((0, -10, 0), ypr_to_quat(0, 0, 0))
        c.set_mode("ORBIT", pivot=Vector((0, 0, 0)))
        inp = FlightInput()
        inp.move = Vector((1, 0, 0))
        run(c, inp, 1.0)
        self.assertAlmostEqual(c.pos.length, 10.0, places=3)
        self.assertGreater(abs(c.pos.x), 3.0)
        aim = (Vector((0, 0, 0)) - c.pos).normalized()
        self.assertGreater(c.forward().dot(aim), 0.9999)

    def test_orbit_dolly(self):
        c = self.make(look_smooth=0.0)
        c.set_pose((0, -10, 0), ypr_to_quat(0, 0, 0))
        c.set_mode("ORBIT", pivot=Vector((0, 0, 0)))
        inp = FlightInput()
        inp.move = Vector((0, 1, 0))
        run(c, inp, 1.0)
        self.assertLess(c.pos.length, 5.0)

    def test_walk_falls_to_ground(self):
        c = self.make(eye_height=1.7)
        c.set_pose((0, 0, 6), ypr_to_quat(0, 0, 0))
        c.set_mode("WALK")
        run(c, FlightInput(), 2.0, FakeEnv())
        self.assertTrue(c.grounded)
        self.assertAlmostEqual(c.pos.z, 1.7, delta=0.01)

    def test_walk_jump(self):
        c = self.make(eye_height=1.7, jump_speed=5.0)
        c.set_pose((0, 0, 1.7), ypr_to_quat(0, 0, 0))
        c.set_mode("WALK")
        env = FakeEnv()
        run(c, FlightInput(), 0.2, env)
        inp = FlightInput()
        inp.jump = True
        c.step(DT, inp, env)
        inp.jump = False
        peak = 0.0
        for _ in range(60):
            c.step(DT, inp, env)
            peak = max(peak, c.pos.z)
        self.assertGreater(peak, 2.5)
        run(c, inp, 2.0, env)
        self.assertAlmostEqual(c.pos.z, 1.7, delta=0.01)

    def test_walk_hovers_without_ground(self):
        c = self.make()
        c.set_pose((0, 0, 5), ypr_to_quat(0, 0, 0))
        c.set_mode("WALK")
        run(c, FlightInput(), 1.0, FakeEnv(ground=False))
        self.assertAlmostEqual(c.pos.z, 5.0)
        self.assertTrue(c.hovering)

    def test_collision_stops_at_wall(self):
        c = self.make(collide=True, radius=0.5, speed=10.0, accel=20.0)
        inp = FlightInput()
        inp.move = Vector((0, 1, 0))
        run(c, inp, 3.0, FakeEnv(ground=False, wall_y=10.0))
        self.assertLessEqual(c.pos.y, 9.5 + 1e-4)
        self.assertGreater(c.pos.y, 9.3)

    def test_collision_slides(self):
        c = self.make(collide=True, radius=0.5, speed=5.0, accel=20.0)
        c.set_pose((0, 8, 1), ypr_to_quat(-0.6, 0, 0))  # heading diagonally into the wall
        inp = FlightInput()
        inp.move = Vector((0, 1, 0))
        run(c, inp, 3.0, FakeEnv(ground=False, wall_y=10.0))
        self.assertLessEqual(c.pos.y, 9.5 + 1e-4)
        self.assertGreater(c.pos.x, 3.0)  # kept sliding along the wall

    def test_adaptive_speed(self):
        c = self.make(adaptive_speed=True, speed=10.0, accel=50.0)
        inp = FlightInput()
        inp.move = Vector((0, 1, 0))
        env = FakeEnv(ground=False, wall_y=6.0)
        run(c, inp, 0.5, env)
        self.assertLess(c.speed_limit, 10.0)

    def test_fly_to_transition(self):
        c = self.make()
        c.fly_to(Vector((5, 5, 5)), ypr_to_quat(1.0, -0.3, 0), 30.0, duration=1.0)
        inp = FlightInput()
        run(c, inp, 0.5)
        self.assertIsNotNone(c.transition)
        run(c, inp, 0.6)
        self.assertIsNone(c.transition)
        self.assertLess((c.pos - Vector((5, 5, 5))).length, 1e-6)
        self.assertAlmostEqual(c.lens, 30.0, places=4)
        self.assertGreater(abs(c.base_rotation().dot(ypr_to_quat(1.0, -0.3, 0))), 1 - 1e-6)

    def test_input_cancels_transition(self):
        c = self.make()
        c.fly_to(Vector((50, 0, 0)), None, None, duration=2.0)
        inp = FlightInput()
        run(c, inp, 0.2)
        inp.move = Vector((0, 1, 0))
        c.step(DT, inp)
        self.assertIsNone(c.transition)

    def test_zoom_and_hold_zoom(self):
        c = self.make(zoom_smooth=0.0)
        inp = FlightInput()
        inp.zoom_steps = 2
        c.step(DT, inp)
        self.assertAlmostEqual(c.lens, 50 * 1.12 ** 2, places=3)
        inp.clear_impulses()
        inp.hold_zoom = True
        run(c, inp, 1.0)
        self.assertAlmostEqual(c.output().lens, c.lens * 2.0, delta=0.01)

    def test_ortho_zoom_inverts(self):
        c = self.make(zoom_smooth=0.0, lens_min=0.001, lens_max=1e5)
        c.zoom_sign = -1.0
        c.set_pose((0, 0, 1), ypr_to_quat(0, 0, 0), lens=6.0)
        inp = FlightInput()
        inp.zoom_steps = 1
        c.step(DT, inp)
        self.assertLess(c.lens, 6.0)

    def test_dolly_zoom_keeps_subject_size(self):
        c = self.make(accel=50.0)
        self.assertTrue(c.start_dolly_zoom(Vector((0, 10, 1))))
        inp = FlightInput()
        inp.move = Vector((0, -1, 0))
        run(c, inp, 1.0)
        d = 10.0 - c.pos.y
        self.assertAlmostEqual(c.lens / d, 50.0 / 10.0, places=4)

    def test_look_target_tracks(self):
        c = self.make(track_smooth=0.0)
        c.set_look_target(Vector((10, 10, 1)))
        c.step(DT, FlightInput())
        aim = (Vector((10, 10, 1)) - c.pos).normalized()
        self.assertGreater(c.forward().dot(aim), 0.9999)

    def test_shake_is_deterministic_and_bounded(self):
        a = shake.sample(1.234, 2.0)
        b = shake.sample(1.234, 2.0)
        self.assertEqual(a, b)
        for t in range(500):
            for v in shake.sample(t * 0.037, 3.0):
                self.assertLessEqual(abs(v), 1.0)

    def test_shake_changes_output_not_state(self):
        c = self.make(shake=True, shake_intensity=1.0)
        c.step(DT, FlightInput())
        out = c.output()
        self.assertGreater((out.pos - c.pos).length + abs(1 - abs(out.rot.dot(c.base_rotation()))), 0.0)


class FilterTests(unittest.TestCase):
    def test_gaussian_smooth_reduces_noise_and_pins_ends(self):
        rng = random.Random(1)
        vals = [i * 0.1 + rng.uniform(-0.2, 0.2) for i in range(200)]
        out = smoothing.gaussian_smooth(vals, 3.0)
        self.assertEqual(out[0], vals[0])
        self.assertEqual(out[-1], vals[-1])

        def roughness(v):
            return sum(abs(v[i - 1] - 2 * v[i] + v[i + 1]) for i in range(1, len(v) - 1))

        self.assertLess(roughness(out), roughness(vals) * 0.3)

    def test_rdp(self):
        xs = list(range(101))
        ys = [math.sin(x / 10.0) for x in xs]
        keep = smoothing.rdp_indices(xs, ys, 0.01)
        self.assertEqual(keep[0], 0)
        self.assertEqual(keep[-1], 100)
        self.assertLess(len(keep), 40)
        # every dropped sample is within tolerance of the kept polyline
        for i in range(101):
            j = max(k for k in keep if k <= i)
            k2 = min(k for k in keep if k >= i)
            if j == k2:
                continue
            t = (xs[i] - xs[j]) / (xs[k2] - xs[j])
            self.assertLessEqual(abs(ys[i] - (ys[j] + (ys[k2] - ys[j]) * t)), 0.01 + 1e-9)

    def test_rdp_straight_line(self):
        self.assertEqual(smoothing.rdp_indices([0, 1, 2, 3], [0, 1, 2, 3], 1e-6), [0, 3])


class LinuxJoystickTests(unittest.TestCase):
    """Feed raw js_event structs through a file and check the mapping."""

    def test_xbox_mapping(self):
        import os
        import struct
        import tempfile

        ev = struct.Struct("<IhBB")
        events = [
            ev.pack(0, 0, 0x02 | 0x80, 2),  # init: LT at rest
            ev.pack(0, 32767, 0x02, 0),  # LX full right
            ev.pack(0, -32767, 0x02, 1),  # LY up (js axis is down-positive)
            ev.pack(0, 32767, 0x02, 5),  # RT full
            ev.pack(0, 1, 0x01, 0),  # A
            ev.pack(0, 1, 0x01, 5),  # RB
            ev.pack(0, -32767, 0x02, 7),  # d-pad up
        ]
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            fh.write(b"".join(events))
            path = fh.name
        try:
            js = gamepad._LinuxJoystick(0, "XBOX")
            js.close()
            js.path = path
            js._open()
            pad = gamepad.Gamepad(js)
            st = pad.poll()
            self.assertTrue(pad.connected)
            self.assertAlmostEqual(st.lx, 1.0)
            self.assertAlmostEqual(st.ly, 1.0)
            self.assertAlmostEqual(st.lt, 0.5)
            self.assertAlmostEqual(st.rt, 1.0)
            self.assertEqual(st.buttons, {"A", "RB", "UP"})
            self.assertEqual(pad.pressed, {"A", "RB", "UP"})
            pad.close()
        finally:
            os.unlink(path)

    def test_missing_device(self):
        js = gamepad._LinuxJoystick(0, "XBOX")
        js.close()
        js.path = "/nonexistent/js9"
        js._open()
        self.assertIsNone(js.poll())
        self.assertFalse(js.connected)


class GamepadTests(unittest.TestCase):
    def test_deadzone(self):
        self.assertEqual(gamepad.shape_stick(0.05, 0.05, 0.12, 0.0), (0.0, 0.0))

    def test_full_deflection(self):
        x, y = gamepad.shape_stick(1.0, 0.0, 0.12, 0.5)
        self.assertAlmostEqual(x, 1.0)
        self.assertAlmostEqual(y, 0.0)

    def test_expo_softens_center(self):
        lin = gamepad.shape_stick(0.5, 0.0, 0.0, 0.0)[0]
        exp = gamepad.shape_stick(0.5, 0.0, 0.0, 0.8)[0]
        self.assertLess(exp, lin)

    def test_trigger(self):
        self.assertEqual(gamepad.shape_trigger(0.02), 0.0)
        self.assertAlmostEqual(gamepad.shape_trigger(1.0), 1.0)

    def test_edges(self):
        class Dev:
            connected = True
            name = "fake"

            def __init__(self):
                self.frames = []

            def poll(self):
                st = gamepad.PadState()
                st.buttons = frozenset(self.frames.pop(0)) if self.frames else frozenset()
                return st

            def close(self):
                pass

        dev = Dev()
        dev.frames = [{"A"}, {"A"}, set()]
        pad = gamepad.Gamepad(dev)
        pad.poll()
        self.assertEqual(pad.pressed, {"A"})
        pad.poll()
        self.assertEqual(pad.pressed, frozenset())
        pad.poll()
        self.assertEqual(pad.released, {"A"})


if __name__ == "__main__":
    unittest.main()
