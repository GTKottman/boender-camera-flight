"""Flight physics: turns abstract input into smooth, game-like camera motion.

Nothing here touches ``bpy``. The session feeds a :class:`FlightInput` and an
optional *environment* (an object with a ``ray_cast(origin, direction,
distance) -> (hit, location, normal, object)`` method) into
:meth:`FlightController.step` and reads the resulting :class:`Pose` back.

Flight modes
------------
FREE   Noclip fly-cam (Unreal/Unity editor style). W flies where you look.
DRONE  Horizon-locked, planar thrust, momentum and automatic banking.
SPACE  Full 6DOF: no horizon, mouse turns around the camera's own axes.
WALK   Gravity, ground following, jumping and head bob.
ORBIT  Circle a pivot point while always looking at it.
"""

import math

from mathutils import Euler, Quaternion, Vector

from . import shake
from .mathx import (
    AXIS_X,
    AXIS_Y,
    AXIS_Z,
    TAU,
    Pose,
    angles_from_direction,
    clamp,
    damp_factor,
    forward_from_angles,
    quat_to_ypr,
    rate_factor,
    smootherstep,
    unwrap_near,
    wrap_pi,
    ypr_to_quat,
)

MODES = ("FREE", "DRONE", "SPACE", "WALK", "ORBIT")
ZOOM_STEP = math.log(1.12)  # one mouse-wheel notch


class FlightParams:
    """Tunables. The session copies the scene settings into this every tick."""

    def __init__(self):
        self.mode = "FREE"
        self.speed = 5.0
        self.boost = 4.0
        self.precision = 0.2
        self.accel = 6.0
        self.drag = 5.0
        self.look_smooth = 0.06
        self.track_smooth = 0.15
        self.turn_rate = math.radians(90.0)
        self.roll_rate = math.radians(60.0)
        self.bank = 0.0
        self.max_bank = math.radians(30.0)
        self.auto_level = False
        self.level_time = 0.5
        self.planar = False
        self.pitch_limit = math.radians(90.0)
        self.adaptive_speed = False
        self.collide = False
        self.radius = 0.3
        self.gravity = 9.81
        self.eye_height = 1.7
        self.jump_speed = 4.5
        self.head_bob = 0.5
        self.zoom_rate = 1.0
        self.zoom_smooth = 0.12
        self.hold_zoom = 2.0
        self.focus_smooth = 0.2
        self.lens_min = 1.0
        self.lens_max = 5000.0
        self.shake = False
        self.shake_intensity = 1.0
        self.shake_frequency = 1.2
        self.shake_rotation = math.radians(0.6)
        self.shake_position = 0.01


class FlightInput:
    """Controls for one tick. Axes are -1..1, ``look`` is in radians."""

    def __init__(self):
        self.move = Vector((0.0, 0.0, 0.0))  # x right, y forward, z up
        self.look = [0.0, 0.0]  # accumulated mouse look: yaw (+left), pitch (+up)
        self.look_rate = [0.0, 0.0]  # held look (arrow keys / right stick)
        self.roll = 0.0  # + banks left
        self.boost = 0.0  # 0..1 (analog trigger friendly)
        self.precision = False
        self.zoom = 0.0  # held zoom, + zooms in
        self.zoom_steps = 0.0  # wheel notches, + zooms in
        self.hold_zoom = False
        self.jump = False

    def clear_impulses(self):
        self.look[0] = self.look[1] = 0.0
        self.zoom_steps = 0.0

    def active(self):
        """True when the pilot is actively steering (used to break out of auto moves)."""
        return (
            self.move.length_squared > 1e-4
            or abs(self.look[0]) + abs(self.look[1]) > 2e-3
            or abs(self.look_rate[0]) + abs(self.look_rate[1]) > 1e-2
            or abs(self.roll) > 1e-2
        )


class Transition:
    """Eased automatic move (fly-to, bookmark recall)."""

    def __init__(self, ctrl, pos, rot, lens, duration):
        self.pos0 = ctrl.pos.copy()
        self.pos1 = Vector(pos)
        y1, p1, r1 = quat_to_ypr(rot)
        self.ang0 = (ctrl.yaw, ctrl.pitch, ctrl.roll + ctrl.bank)
        self.ang1 = (unwrap_near(y1, ctrl.yaw), p1, unwrap_near(r1, ctrl.roll + ctrl.bank))
        self.rot0 = ctrl.base_rotation()
        self.rot1 = Quaternion(rot)
        if self.rot0.dot(self.rot1) < 0.0:
            self.rot1.negate()
        self.lens0 = ctrl.lens
        self.lens1 = lens
        self.duration = max(duration, 1e-3)
        self.t = 0.0


class FlightController:
    def __init__(self, params=None):
        self.p = params if params is not None else FlightParams()
        self.mode = self.p.mode
        self.pos = Vector((0.0, 0.0, 0.0))
        self.vel = Vector((0.0, 0.0, 0.0))
        # Horizon-locked orientation (all modes except SPACE)
        self.yaw = self.pitch = self.roll = 0.0
        self.yaw_t = self.pitch_t = self.roll_t = 0.0
        self.bank = 0.0
        self.yaw_rate = 0.0
        # 6DOF orientation (SPACE)
        self.quat = ypr_to_quat(0.0, 0.0, 0.0)
        self.quat_t = self.quat.copy()
        # Lens
        self.lens = self.lens_t = 50.0
        self.focus = self.focus_t = 10.0
        self.zoom_mult = 1.0
        self.zoom_sign = 1.0  # -1 when "lens" is an orthographic scale
        self.dolly = None  # (subject point, lens/distance ratio)
        # Targets
        self.look_target = None
        self.lock_offset = [0.0, 0.0]
        self.pivot = None
        self.orbit_yaw = self.orbit_yaw_t = 0.0
        self.orbit_pitch = self.orbit_pitch_t = 0.0
        self.orbit_dist = self.orbit_dist_t = 5.0
        # Walk
        self.grounded = False
        self.hovering = False
        self.bob_phase = 0.0
        self.bob_amount = 0.0
        # Misc
        self.transition = None
        self.time = 0.0
        self.speed_limit = self.p.speed

    # ------------------------------------------------------------------ pose
    def set_pose(self, pos, rot, lens=None, focus=None, vel=None):
        self.pos = Vector(pos)
        rot = Quaternion(rot).normalized()
        yaw, pitch, roll = quat_to_ypr(rot)
        yaw = unwrap_near(yaw, self.yaw)
        self.yaw = self.yaw_t = yaw
        self.pitch = self.pitch_t = pitch
        self.roll = self.roll_t = roll
        self.bank = 0.0
        self.quat = rot.copy()
        self.quat_t = rot.copy()
        if lens is not None:
            self.lens = self.lens_t = float(lens)
            self.zoom_mult = 1.0
        if focus is not None:
            self.focus = self.focus_t = float(focus)
        self.vel = Vector(vel) if vel is not None else Vector((0.0, 0.0, 0.0))
        self.transition = None
        if self.mode == "ORBIT":
            self.sync_orbit()

    def base_rotation(self):
        if self.mode == "SPACE":
            return self.quat.copy()
        return ypr_to_quat(self.yaw, self.pitch, self.roll + self.bank)

    def forward(self):
        if self.mode == "SPACE":
            return self.quat @ Vector((0.0, 0.0, -1.0))
        return forward_from_angles(self.yaw, self.pitch)

    def right_h(self):
        return Vector((math.cos(self.yaw), math.sin(self.yaw), 0.0))

    def forward_h(self):
        return Vector((-math.sin(self.yaw), math.cos(self.yaw), 0.0))

    def speed(self):
        return self.vel.length

    # ------------------------------------------------------------------ modes
    def set_mode(self, mode, pivot=None):
        if mode not in MODES:
            mode = "FREE"
        if mode == self.mode:
            if mode == "ORBIT" and pivot is not None:
                self.pivot = Vector(pivot)
                self.sync_orbit()
            return
        if self.mode == "SPACE":
            yaw, pitch, roll = quat_to_ypr(self.quat)
            self.yaw = self.yaw_t = unwrap_near(yaw, self.yaw)
            self.pitch = self.pitch_t = pitch
            self.roll = self.roll_t = roll
        elif mode == "SPACE":
            self.quat = self.base_rotation()
            self.quat_t = self.quat.copy()
            self.bank = 0.0
        self.mode = mode
        self.p.mode = mode
        self.transition = None
        if mode == "ORBIT":
            if pivot is not None:
                self.pivot = Vector(pivot)
            self.sync_orbit()
        elif mode == "WALK":
            self.grounded = False

    def sync_orbit(self):
        if self.pivot is None:
            return
        d = self.pivot - self.pos
        dist = max(d.length, 0.05)
        yaw, pitch = angles_from_direction(d) if d.length > 1e-9 else (self.yaw, self.pitch)
        self.orbit_yaw = self.orbit_yaw_t = unwrap_near(yaw, self.yaw)
        self.orbit_pitch = self.orbit_pitch_t = pitch
        self.orbit_dist = self.orbit_dist_t = dist

    def set_look_target(self, point):
        self.look_target = Vector(point) if point is not None else None
        if point is None:
            self.lock_offset = [0.0, 0.0]

    def level(self):
        """Bring the horizon back to level (smoothly)."""
        if self.mode == "SPACE":
            yaw, pitch, _roll = quat_to_ypr(self.quat_t)
            self.quat_t = ypr_to_quat(yaw, pitch, 0.0)
        else:
            k = round(self.roll / TAU) * TAU
            self.roll -= k
            self.roll_t = 0.0

    def start_dolly_zoom(self, subject):
        """Lock subject size on screen while moving (the "Vertigo" effect)."""
        if self.zoom_sign < 0.0:
            return False
        d = (Vector(subject) - self.pos).dot(self.forward())
        if d <= 1e-4:
            return False
        self.dolly = (Vector(subject), self.lens / d)
        return True

    def fly_to(self, pos, rot=None, lens=None, duration=1.0):
        if rot is None:
            rot = ypr_to_quat(self.yaw, self.pitch, self.roll) if self.mode != "SPACE" else self.quat
        if duration <= 0.0:
            self.set_pose(pos, rot, lens=lens)
            return
        self.bank = 0.0
        self.transition = Transition(self, pos, rot, lens, duration)

    # ------------------------------------------------------------------ step
    def step(self, dt, inp, env=None):
        """Advance the simulation. Returns False if nothing was consumed (dt == 0)."""
        if dt <= 0.0:
            return False
        self.time += dt
        if self.transition is not None:
            if inp.active():
                self.transition = None
            else:
                self._step_transition(dt)
                self._step_lens(dt, inp)
                return True

        speed = self._current_speed(inp, env)
        if self.mode == "ORBIT" and self.pivot is not None:
            self._step_orbit(dt, inp, speed)
            self._step_look(dt, inp, self.pivot, free_look=False)
        elif self.mode == "WALK":
            self._step_look(dt, inp, self.look_target, free_look=True)
            self._step_walk(dt, inp, env, speed)
        else:
            self._step_look(dt, inp, self.look_target, free_look=True)
            self._step_fly(dt, inp, env, speed)
        self._step_lens(dt, inp)
        return True

    def _current_speed(self, inp, env):
        p = self.p
        s = p.speed * (1.0 + (p.boost - 1.0) * clamp(inp.boost, 0.0, 1.0))
        if inp.precision:
            s *= p.precision
        if p.adaptive_speed and env is not None and s > 0.0:
            hit, loc, _n, _o = env.ray_cast(self.pos, self.forward(), s * 4.0 + 1e-3)
            if hit:
                d = (Vector(loc) - self.pos).length
                s *= clamp(d / (s * 1.5), 0.08, 1.0)
        self.speed_limit = s
        return s

    def _step_look(self, dt, inp, look_at, free_look):
        p = self.p
        dyaw = dpitch = 0.0
        if free_look:
            dyaw = inp.look[0] + inp.look_rate[0] * p.turn_rate * dt
            dpitch = inp.look[1] + inp.look_rate[1] * p.turn_rate * dt
        droll = inp.roll * p.roll_rate * dt

        if self.mode == "SPACE":
            local = Quaternion(AXIS_Y, dyaw) @ Quaternion(AXIS_X, dpitch) @ Quaternion(AXIS_Z, droll)
            self.quat_t = (self.quat_t @ local).normalized()
            tau = p.look_smooth
            if look_at is not None:
                d = Vector(look_at) - self.pos
                if d.length_squared > 1e-10:
                    fwd = self.quat_t @ Vector((0.0, 0.0, -1.0))
                    self.quat_t = (fwd.rotation_difference(d.normalized()) @ self.quat_t).normalized()
                tau = p.track_smooth
            if self.quat.dot(self.quat_t) < 0.0:
                self.quat_t.negate()
            self.quat = self.quat.slerp(self.quat_t, damp_factor(tau, dt)).normalized()
            prev_yaw = self.yaw
            yaw, self.pitch, self.roll = quat_to_ypr(self.quat)
            self.yaw = self.yaw_t = unwrap_near(yaw, prev_yaw)
            self.pitch_t, self.roll_t = self.pitch, self.roll
            self.yaw_rate = (self.yaw - prev_yaw) / dt
            return

        lim = p.pitch_limit
        if look_at is not None:
            if free_look:
                # While locked on, the mouse nudges the framing instead of the aim.
                self.lock_offset[0] = clamp(self.lock_offset[0] + dyaw, -0.6, 0.6)
                self.lock_offset[1] = clamp(self.lock_offset[1] + dpitch, -0.5, 0.5)
            d = Vector(look_at) - self.pos
            if d.length_squared > 1e-10:
                yaw_d, pitch_d = angles_from_direction(d)
                if free_look:
                    yaw_d += self.lock_offset[0]
                    pitch_d += self.lock_offset[1]
                self.yaw_t = unwrap_near(yaw_d, self.yaw)
                self.pitch_t = clamp(pitch_d, -lim, lim)
            tau = p.track_smooth if free_look else 0.0
        else:
            self.yaw_t += dyaw
            self.pitch_t = clamp(self.pitch_t + dpitch, -lim, lim)
            tau = p.look_smooth

        if abs(inp.roll) > 1e-4:
            self.roll_t += droll
        elif p.auto_level:
            k = round(self.roll_t / TAU) * TAU
            self.roll_t -= k
            self.roll -= k
            self.roll_t -= self.roll_t * damp_factor(p.level_time, dt)

        a = damp_factor(tau, dt)
        prev_yaw = self.yaw
        self.yaw += (self.yaw_t - self.yaw) * a
        self.pitch += (self.pitch_t - self.pitch) * a
        self.roll += (self.roll_t - self.roll) * damp_factor(p.look_smooth, dt)
        self.yaw_rate = (self.yaw - prev_yaw) / dt

        if p.bank > 1e-4 and self.mode != "ORBIT":
            ref = max(self.speed_limit, 1e-6)
            lateral = self.vel.dot(self.right_h()) / ref
            target = clamp(self.yaw_rate * 0.35 - lateral * 0.6, -1.0, 1.0) * p.bank * p.max_bank
            self.bank += (target - self.bank) * damp_factor(0.35, dt)
        else:
            self.bank -= self.bank * damp_factor(0.2, dt)

    def _step_fly(self, dt, inp, env, speed):
        p = self.p
        if self.mode == "SPACE":
            q = self.quat
            right = q @ AXIS_X
            up = q @ AXIS_Y
            fwd = q @ Vector((0.0, 0.0, -1.0))
        else:
            right = self.right_h()
            if p.planar or self.mode == "DRONE":
                fwd = self.forward_h()
            else:
                fwd = forward_from_angles(self.yaw, self.pitch)
            up = AXIS_Z
        m = inp.move
        wish = right * m.x + fwd * m.y + up * m.z
        length = wish.length
        if length > 1.0:
            wish /= length
        rate = p.accel if length > 1e-3 else p.drag
        self.vel = self.vel.lerp(wish * speed, rate_factor(rate, dt))
        disp = self.vel * dt
        if p.collide and env is not None:
            disp = self._sweep(env, disp, (Vector((0.0, 0.0, 0.0)),))
        self.pos = self.pos + disp

    def _sweep(self, env, disp, offsets):
        """Move with collision: stop ``radius`` away from surfaces and slide along them."""
        p = self.p
        pos = self.pos.copy()
        remaining = disp.copy()
        for _ in range(3):
            length = remaining.length
            if length < 1e-9:
                break
            d = remaining / length
            best = None
            for off in offsets:
                origin = pos + off
                hit, loc, nrm, _o = env.ray_cast(origin, d, length + p.radius * 4.0)
                if not hit or nrm is None:
                    continue
                n = Vector(nrm)
                if n.length_squared < 1e-12:
                    continue
                n.normalize()
                if n.dot(d) > 0.0:
                    n.negate()
                approach = -d.dot(n)
                if approach < 1e-6:
                    continue
                # distance we may travel before the surface is ``radius`` away (perpendicular)
                perp = (origin - Vector(loc)).dot(n)
                travel = (perp - p.radius) / approach
                if travel < length and (best is None or travel < best[0]):
                    best = (travel, n)
            if best is None:
                pos += remaining
                break
            travel, n = best
            travel = clamp(travel, 0.0, length)
            pos += d * travel
            remaining = d * (length - travel)
            remaining -= n * remaining.dot(n)
            vn = self.vel.dot(n)
            if vn < 0.0:
                self.vel -= n * vn
        pos = self._depenetrate(env, pos)
        return pos - self.pos

    _PUSH_DIRS = (
        Vector((1.0, 0.0, 0.0)),
        Vector((-1.0, 0.0, 0.0)),
        Vector((0.0, 1.0, 0.0)),
        Vector((0.0, -1.0, 0.0)),
        Vector((0.0, 0.0, 1.0)),
        Vector((0.0, 0.0, -1.0)),
    )

    def _depenetrate(self, env, pos):
        """Push out of anything closer than ``radius`` (handles grazing slides)."""
        r = self.p.radius
        if r <= 0.0:
            return pos
        for d in self._PUSH_DIRS:
            hit, loc, nrm, _o = env.ray_cast(pos, d, r)
            if not hit or nrm is None:
                continue
            n = Vector(nrm)
            if n.length_squared < 1e-12:
                continue
            n.normalize()
            if n.dot(d) > 0.0:
                n.negate()
            perp = (pos - Vector(loc)).dot(n)
            if 0.0 <= perp < r:
                pos = pos + n * (r - perp)
                vn = self.vel.dot(n)
                if vn < 0.0:
                    self.vel -= n * vn
        return pos

    def _step_walk(self, dt, inp, env, speed):
        p = self.p
        m = inp.move
        wish = self.right_h() * m.x + self.forward_h() * m.y
        length = wish.length
        if length > 1.0:
            wish /= length
        rate = p.accel if length > 1e-3 else p.drag
        vh = Vector((self.vel.x, self.vel.y, 0.0)).lerp(wish * speed, rate_factor(rate, dt))
        disp = vh * dt
        if p.collide and env is not None:
            knee = Vector((0.0, 0.0, -p.eye_height * 0.6))
            disp = self._sweep(env, disp, (Vector((0.0, 0.0, 0.0)), knee))
            disp.z = 0.0
        self.pos = self.pos + disp
        vz = self.vel.z
        self.vel = Vector((vh.x, vh.y, 0.0))

        hit = False
        loc = None
        if env is not None:
            hit, loc, _n, _o = env.ray_cast(self.pos, Vector((0.0, 0.0, -1.0)), 1e5)
        if not hit:
            # Nothing underneath: hover instead of falling forever.
            self.grounded = False
            self.hovering = True
            vz = 0.0
        else:
            self.hovering = False
            ground = loc[2] + p.eye_height
            step_h = p.eye_height * 0.3
            if self.grounded:
                if self.pos.z - ground <= step_h and vz <= 0.0:
                    self.pos.z += (ground - self.pos.z) * damp_factor(0.05, dt)
                    vz = 0.0
                else:
                    self.grounded = False
            if not self.grounded:
                vz -= p.gravity * dt
                self.pos.z += vz * dt
                if self.pos.z <= ground:
                    self.pos.z = ground
                    vz = 0.0
                    self.grounded = True
            if inp.jump and self.grounded:
                vz = p.jump_speed
                self.grounded = False
        self.vel.z = vz

        # Head bob
        hs = math.hypot(self.vel.x, self.vel.y)
        ratio = min(hs / max(p.speed, 1e-6), 1.5) if self.grounded else 0.0
        self.bob_amount += (ratio - self.bob_amount) * damp_factor(0.2, dt)
        if self.bob_amount > 1e-3:
            self.bob_phase += dt * TAU * 0.95 * (0.6 + 0.4 * min(ratio, 1.5))

    def _step_orbit(self, dt, inp, speed):
        p = self.p
        k = speed / max(p.speed, 1e-6)
        rate = p.turn_rate * k
        lim = p.pitch_limit * 0.99
        self.orbit_yaw_t += inp.look[0] + (inp.look_rate[0] + inp.move.x) * rate * dt
        self.orbit_pitch_t = clamp(
            self.orbit_pitch_t + inp.look[1] + (inp.look_rate[1] - inp.move.z) * rate * dt, -lim, lim
        )
        self.orbit_dist_t = max(self.orbit_dist_t * math.exp(-inp.move.y * dt * 1.2 * k), 0.02)
        a = damp_factor(p.look_smooth, dt)
        self.orbit_yaw += (self.orbit_yaw_t - self.orbit_yaw) * a
        self.orbit_pitch += (self.orbit_pitch_t - self.orbit_pitch) * a
        self.orbit_dist += (self.orbit_dist_t - self.orbit_dist) * rate_factor(max(p.accel, 0.5), dt)
        new_pos = self.pivot - forward_from_angles(self.orbit_yaw, self.orbit_pitch) * self.orbit_dist
        self.vel = (new_pos - self.pos) / dt
        self.pos = new_pos

    def _step_lens(self, dt, inp):
        p = self.p
        if self.dolly is not None and self.zoom_sign > 0.0:
            subject, ratio = self.dolly
            d = (subject - self.pos).dot(self.forward())
            if d > 1e-4:
                self.lens = self.lens_t = clamp(ratio * d, p.lens_min, p.lens_max)
        else:
            k = inp.zoom * p.zoom_rate * dt + inp.zoom_steps * ZOOM_STEP
            if k:
                self.lens_t = clamp(self.lens_t * math.exp(k * self.zoom_sign), p.lens_min, p.lens_max)
            self.lens += (self.lens_t - self.lens) * damp_factor(p.zoom_smooth, dt)
        target = p.hold_zoom if inp.hold_zoom else 1.0
        self.zoom_mult += (target - self.zoom_mult) * damp_factor(0.07, dt)
        self.focus += (self.focus_t - self.focus) * damp_factor(p.focus_smooth, dt)

    def _step_transition(self, dt):
        tr = self.transition
        tr.t += dt
        u = smootherstep(tr.t / tr.duration)
        new_pos = tr.pos0.lerp(tr.pos1, u)
        self.vel = (new_pos - self.pos) / dt
        self.pos = new_pos
        if self.mode == "SPACE":
            self.quat = tr.rot0.slerp(tr.rot1, u).normalized()
            self.quat_t = self.quat.copy()
        else:
            a0, a1 = tr.ang0, tr.ang1
            self.yaw = self.yaw_t = a0[0] + (a1[0] - a0[0]) * u
            self.pitch = self.pitch_t = a0[1] + (a1[1] - a0[1]) * u
            self.roll = self.roll_t = a0[2] + (a1[2] - a0[2]) * u
            self.bank = 0.0
        if tr.lens1 is not None and tr.lens0 > 0.0 and tr.lens1 > 0.0:
            lens = math.exp(math.log(tr.lens0) + (math.log(tr.lens1) - math.log(tr.lens0)) * u)
            self.lens = self.lens_t = lens
        if tr.t >= tr.duration:
            self.transition = None
            self.vel = Vector((0.0, 0.0, 0.0))
            if self.mode == "ORBIT":
                self.sync_orbit()

    # ------------------------------------------------------------------ output
    def output(self):
        """Final pose including head bob and camera shake."""
        p = self.p
        rot = self.base_rotation()
        pos = self.pos.copy()
        if self.mode == "WALK" and p.head_bob > 0.0 and self.bob_amount > 1e-4:
            amp = p.head_bob * p.eye_height * 0.02 * self.bob_amount
            ph = self.bob_phase
            pos.z += math.sin(2.0 * ph) * amp
            pos += self.right_h() * (math.sin(ph) * amp * 0.5)
            rot = rot @ Quaternion(AXIS_Z, math.sin(ph) * math.radians(0.5) * p.head_bob * self.bob_amount)
        if p.shake and p.shake_intensity > 0.0:
            s = shake.sample(self.time, p.shake_frequency)
            ar = p.shake_rotation * p.shake_intensity
            rot = rot @ Euler((s[0] * ar, s[1] * ar, s[2] * ar * 0.6), "XYZ").to_quaternion()
            ap = p.shake_position * p.shake_intensity
            pos += rot @ Vector((s[3] * ap, s[4] * ap, s[5] * ap))
        lens = self.lens * (self.zoom_mult ** self.zoom_sign)
        return Pose(pos, rot, lens, self.focus)


__all__ = ["MODES", "FlightParams", "FlightInput", "FlightController", "Transition", "wrap_pi"]
