"""Small math helpers shared by the flight controller and the session."""

import math

from mathutils import Matrix, Quaternion, Vector

HALF_PI = math.pi * 0.5
TAU = math.tau

AXIS_X = Vector((1.0, 0.0, 0.0))
AXIS_Y = Vector((0.0, 1.0, 0.0))
AXIS_Z = Vector((0.0, 0.0, 1.0))


def clamp(value, lo, hi):
    return lo if value < lo else hi if value > hi else value


def damp_factor(tau, dt):
    """Frame-rate independent exponential smoothing factor.

    ``tau`` is the time constant in seconds (0 = snap instantly).
    """
    if dt <= 0.0:
        return 0.0
    if tau <= 1e-6:
        return 1.0
    return 1.0 - math.exp(-dt / tau)


def rate_factor(rate, dt):
    """Like :func:`damp_factor` but expressed as a rate (1/s)."""
    if dt <= 0.0 or rate <= 0.0:
        return 0.0
    return 1.0 - math.exp(-rate * dt)


def smootherstep(t):
    t = clamp(t, 0.0, 1.0)
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def wrap_pi(angle):
    return (angle + math.pi) % TAU - math.pi


def unwrap_near(angle, reference):
    """Return ``angle`` shifted by whole turns so it is closest to ``reference``."""
    return reference + wrap_pi(angle - reference)


def forward_from_angles(yaw, pitch):
    """View direction for a camera with the given yaw/pitch (yaw 0 looks along +Y)."""
    cp = math.cos(pitch)
    return Vector((-math.sin(yaw) * cp, math.cos(yaw) * cp, math.sin(pitch)))


def angles_from_direction(direction):
    """Inverse of :func:`forward_from_angles` (returns yaw, pitch)."""
    horiz = math.hypot(direction.x, direction.y)
    return math.atan2(-direction.x, direction.y), math.atan2(direction.z, horiz)


def ypr_to_quat(yaw, pitch, roll=0.0):
    """Camera orientation from horizon-relative angles.

    Blender cameras look down their local -Z with +Y up. Yaw spins around the
    world Z axis, pitch tilts up/down, roll turns around the viewing axis
    (positive roll banks the camera to the left).
    """
    return (
        Quaternion(AXIS_Z, yaw)
        @ Quaternion(AXIS_X, HALF_PI + pitch)
        @ Quaternion(AXIS_Z, roll)
    )


def quat_to_ypr(quat):
    """Decompose a camera orientation into (yaw, pitch, roll)."""
    m = quat.to_matrix()
    fwd = -m.col[2]
    fz = clamp(fwd.z, -1.0, 1.0)
    pitch = math.asin(fz)
    if math.hypot(fwd.x, fwd.y) < 1e-6:
        # Looking straight up or down: yaw is carried by the up vector.
        up = m.col[1]
        if fz > 0.0:
            yaw = math.atan2(up.x, -up.y)
        else:
            yaw = math.atan2(-up.x, up.y)
        return yaw, pitch, 0.0
    yaw = math.atan2(-fwd.x, fwd.y)
    rel = (ypr_to_quat(yaw, pitch, 0.0).inverted() @ quat).to_matrix()
    roll = math.atan2(rel[1][0], rel[0][0])
    return yaw, pitch, roll


class Pose:
    """World-space camera pose plus the lens values that travel with it."""

    __slots__ = ("pos", "rot", "lens", "focus")

    def __init__(self, pos, rot, lens=50.0, focus=10.0):
        self.pos = Vector(pos)
        self.rot = Quaternion(rot)
        self.lens = float(lens)
        self.focus = float(focus)

    def copy(self):
        return Pose(self.pos, self.rot, self.lens, self.focus)

    def lerp(self, other, t):
        if t <= 0.0:
            return self.copy()
        if t >= 1.0:
            return other.copy()
        lens = self.lens
        if self.lens > 0.0 and other.lens > 0.0:
            lens = math.exp(math.log(self.lens) + (math.log(other.lens) - math.log(self.lens)) * t)
        return Pose(
            self.pos.lerp(other.pos, t),
            self.rot.slerp(other.rot, t),
            lens,
            self.focus + (other.focus - self.focus) * t,
        )

    def matrix(self, scale=None):
        return Matrix.LocRotScale(self.pos, self.rot, scale)

    def forward(self):
        return self.rot @ Vector((0.0, 0.0, -1.0))

    def __repr__(self):
        return "Pose(pos=%s, rot=%s, lens=%.3f, focus=%.3f)" % (
            tuple(round(v, 4) for v in self.pos),
            tuple(round(v, 4) for v in self.rot),
            self.lens,
            self.focus,
        )
