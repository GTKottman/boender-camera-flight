"""Takes: recorded camera moves stored as Actions.

A take is one Action for the camera object (location + rotation) and an
optional Action for the camera data (zoom + focus distance). Takes are listed
in ``scene.camera_flight.takes`` so they can be browsed, rated, compared,
edited and turned into cameras or curves.
"""

import math
import random
import time

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

from . import compat
from .core import smoothing
from .core.mathx import TAU, Pose, smootherstep

GROUP = "Object Transforms"
_suppress_activate = False


# --------------------------------------------------------------------------- channels
class Channels:
    """Which F-Curves a take uses for a particular object."""

    def __init__(self, obj, record_zoom=True, record_focus=True):
        mode = obj.rotation_mode
        self.rot_mode = "XYZ" if mode == "AXIS_ANGLE" else mode
        self.quat = self.rot_mode == "QUATERNION"
        self.rot_path = "rotation_quaternion" if self.quat else "rotation_euler"
        self.rot_len = 4 if self.quat else 3
        is_cam = obj.type == "CAMERA"
        self.zoom_path = None
        if is_cam and record_zoom:
            self.zoom_path = "ortho_scale" if obj.data.type == "ORTHO" else "lens"
        self.focus_path = "dof.focus_distance" if is_cam and record_focus else None

    @property
    def has_data(self):
        return bool(self.zoom_path or self.focus_path)


class Sample:
    __slots__ = ("frame", "loc", "rot", "zoom", "focus", "world")

    def __init__(self, frame, loc, rot, zoom=None, focus=None, world=None):
        self.frame = frame
        self.loc = tuple(loc)
        self.rot = tuple(rot)
        self.zoom = zoom
        self.focus = focus
        self.world = world


# --------------------------------------------------------------------------- transforms
def parent_matrix(obj, depsgraph=None):
    """World matrix the object's local channels are relative to (None if unparented)."""
    parent = obj.parent
    if parent is None:
        return None
    pm = parent.matrix_world
    if depsgraph is not None:
        try:
            pm = parent.evaluated_get(depsgraph).matrix_world
        except (ReferenceError, RuntimeError):
            pass
    return pm @ obj.matrix_parent_inverse


def delta_quat(obj):
    if obj.rotation_mode == "QUATERNION":
        return Quaternion(obj.delta_rotation_quaternion).normalized()
    return obj.delta_rotation_euler.to_quaternion()


def world_to_basis(obj, pos, rot, pmat=None):
    """World pose -> (location, rotation quaternion) for the object's own channels."""
    m = Matrix.LocRotScale(pos, rot, None)
    if pmat is not None:
        m = pmat.inverted_safe() @ m
    loc, q, _s = m.decompose()
    loc = loc - Vector(obj.delta_location)
    dq = delta_quat(obj)
    if dq.angle > 1e-9:
        q = dq.inverted() @ q
    return loc, q


def basis_to_world(obj, loc, q, pmat=None):
    loc = Vector(loc) + Vector(obj.delta_location)
    dq = delta_quat(obj)
    if dq.angle > 1e-9:
        q = dq @ q
    m = Matrix.LocRotScale(loc, q, None)
    if pmat is not None:
        m = pmat @ m
    wl, wq, _s = m.decompose()
    return wl, wq


def rot_values(q, channels, prev=None):
    """Quaternion -> channel values, continuous with ``prev`` (no euler flips, no sign flips)."""
    if channels.quat:
        q = q.normalized()
        if prev is not None and Quaternion(prev).dot(q) < 0.0:
            q.negate()
        return tuple(q)
    if prev is not None:
        return tuple(q.to_euler(channels.rot_mode, Euler(prev, channels.rot_mode)))
    return tuple(q.to_euler(channels.rot_mode))


def current_rot_values(obj, channels):
    if channels.quat:
        return tuple(obj.rotation_quaternion)
    return tuple(obj.rotation_euler)


# --------------------------------------------------------------------------- recorder
class Recorder:
    """Collects one sample per scene frame while flying."""

    def __init__(self, obj, channels, start_frame, basis_fn, prev_rot=None):
        self.obj = obj
        self.channels = channels
        self.start_frame = start_frame
        self.basis_fn = basis_fn
        self.prev_rot = prev_rot
        self.samples = []

    def add(self, frame, pose):
        loc, q = self.basis_fn(pose)
        rot = rot_values(q, self.channels, self.prev_rot)
        self.prev_rot = rot
        self.samples.append(
            Sample(
                frame,
                loc,
                rot,
                pose.lens if self.channels.zoom_path else None,
                pose.focus if self.channels.focus_path else None,
                pose.pos.copy(),
            )
        )

    def feed(self, f0, f1, pose0, pose1):
        """Record every whole frame in (f0, f1], interpolating between two poses."""
        if pose0 is None or f1 <= f0:
            return
        last = self.samples[-1].frame if self.samples else None
        for f in range(int(math.floor(f0)) + 1, int(math.floor(f1 + 1e-6)) + 1):
            if last is not None and f <= last:
                continue
            t = (f - f0) / (f1 - f0)
            self.add(f, pose0.lerp(pose1, t))

    @property
    def last_frame(self):
        return self.samples[-1].frame if self.samples else self.start_frame

    def __len__(self):
        return len(self.samples)


# --------------------------------------------------------------------------- F-Curve I/O
def _add_keys(fc, frames, values, interpolation):
    n = len(frames)
    if n == 0:
        return
    kps = fc.keyframe_points
    start = len(kps)
    kps.add(n)
    total = len(kps)
    co = [0.0] * (2 * total)
    kps.foreach_get("co", co)
    for i in range(n):
        co[2 * (start + i)] = frames[i]
        co[2 * (start + i) + 1] = values[i]
    kps.foreach_set("co", co)
    kps.foreach_set("handle_left", co)
    kps.foreach_set("handle_right", co)
    for i in range(start, total):
        kp = kps[i]
        kp.interpolation = interpolation
        kp.handle_left_type = "AUTO_CLAMPED"
        kp.handle_right_type = "AUTO_CLAMPED"
    fc.update()


def _remove_keys(fc, lo, hi):
    kps = fc.keyframe_points
    removed = False
    for i in range(len(kps) - 1, -1, -1):
        x = kps[i].co.x
        if lo - 1e-4 <= x <= hi + 1e-4:
            kps.remove(kps[i], fast=True)
            removed = True
    if removed:
        fc.update()


def _transform_tail(fc, after, scale=1.0, offset=0.0):
    """Apply value -> value * scale + offset to every key after frame ``after``."""
    changed = False
    for kp in fc.keyframe_points:
        if kp.co.x > after + 1e-4:
            kp.co.y = kp.co.y * scale + offset
            kp.handle_left.y = kp.handle_left.y * scale + offset
            kp.handle_right.y = kp.handle_right.y * scale + offset
            changed = True
    if changed:
        fc.update()


def _obj_channel_specs(channels):
    specs = [("location", i, lambda s, i=i: s.loc[i]) for i in range(3)]
    specs += [(channels.rot_path, i, lambda s, i=i: s.rot[i]) for i in range(channels.rot_len)]
    return specs


def _data_channel_specs(channels):
    specs = []
    if channels.zoom_path:
        specs.append((channels.zoom_path, 0, lambda s: s.zoom))
    if channels.focus_path:
        specs.append((channels.focus_path, 0, lambda s: s.focus))
    return specs


def _curves_for(take, obj, channels, create=True):
    """[(fcurve, getter, kind)] for every channel of a take."""
    out = []
    slot = compat.find_slot(take.action, obj)
    for path, index, getter in _obj_channel_specs(channels):
        kind = "loc" if path == "location" else ("quat" if channels.quat else "euler")
        if create:
            fc = compat.ensure_fcurve(take.action, path, index, slot, GROUP)
        else:
            fc = compat.find_fcurve(take.action, path, index, slot)
        out.append((fc, getter, kind))
    if channels.has_data and obj.type == "CAMERA":
        if take.data_action is None and create:
            take.data_action, _dslot = compat.new_action(obj.data, take.action.name + " Lens")
        if take.data_action is not None:
            dslot = compat.find_slot(take.data_action, obj.data)
            for path, index, getter in _data_channel_specs(channels):
                if create:
                    fc = compat.ensure_fcurve(take.data_action, path, index, dslot)
                else:
                    fc = compat.find_fcurve(take.data_action, path, index, dslot)
                out.append((fc, getter, "scalar"))
    return out


def take_fcurves(take):
    """All F-Curves (object + lens) of a take as [(fcurve, is_data)]."""
    out = []
    cam = take.camera
    if take.action is not None:
        slot = compat.find_slot(take.action, cam) if cam else None
        out += [(fc, False) for fc in compat.action_fcurves(take.action, slot)]
    if take.data_action is not None:
        slot = compat.find_slot(take.data_action, cam.data) if cam and cam.type == "CAMERA" else None
        out += [(fc, True) for fc in compat.action_fcurves(take.data_action, slot)]
    return out


def update_take_range(take):
    lo, hi = math.inf, -math.inf
    for fc, _is_data in take_fcurves(take):
        if len(fc.keyframe_points):
            r = fc.range()
            lo = min(lo, r[0])
            hi = max(hi, r[1])
    if lo <= hi:
        take.frame_start = int(math.floor(lo + 1e-4))
        take.frame_end = int(math.ceil(hi - 1e-4))


# --------------------------------------------------------------------------- registry
def settings(scene):
    return scene.camera_flight


def find_take(scene, action):
    if action is None:
        return -1
    for i, take in enumerate(settings(scene).takes):
        if take.action == action:
            return i
    return -1


def take_by_action(scene, action):
    i = find_take(scene, action)
    return settings(scene).takes[i] if i >= 0 else None


def is_take_action(scene, action):
    if action is None:
        return False
    for take in settings(scene).takes:
        if take.action == action or take.data_action == action:
            return True
    return False


def set_index_quiet(s, index):
    global _suppress_activate
    _suppress_activate = True
    try:
        s.take_index = index
    finally:
        _suppress_activate = False


def new_take(scene, obj, channels, source="Flight", label=None, activate=False):
    """Create an empty take for ``obj``. Returns its index."""
    s = settings(scene)
    s.take_counter += 1
    label = label or "Take %03d" % s.take_counter
    action, _slot = compat.new_action(obj, "%s %s" % (obj.name, label))
    data_action = None
    if channels.has_data and obj.type == "CAMERA":
        data_action, _dslot = compat.new_action(obj.data, "%s %s Lens" % (obj.name, label))
    take = s.takes.add()
    take.camera = obj
    take.action = action
    take.data_action = data_action
    take.name = label
    take.created = time.strftime("%Y-%m-%d %H:%M:%S")
    take.source = source
    take.rotation_mode = channels.rot_mode
    index = len(s.takes) - 1
    if activate:
        s.take_index = index
    else:
        set_index_quiet(s, index)
    return index


def record_take(scene, obj, samples, channels, interpolation="BEZIER", source="Flight", label=None):
    """Write recorded samples into a brand new take. Returns its index."""
    index = new_take(scene, obj, channels, source=source, label=label)
    take = settings(scene).takes[index]
    frames = [smp.frame for smp in samples]
    for fc, getter, _kind in _curves_for(take, obj, channels):
        _add_keys(fc, frames, [getter(smp) for smp in samples], interpolation)
    update_take_range(take)
    return index


def insert_keys(take, obj, sample, channels, interpolation="BEZIER"):
    """Insert (or replace) keys for a single sample (manual keyframing)."""
    for fc, getter, _kind in _curves_for(take, obj, channels):
        kp = fc.keyframe_points.insert(sample.frame, getter(sample), options={"FAST"})
        kp.interpolation = interpolation
        fc.update()
    update_take_range(take)


def punch_in(take, obj, samples, channels, interpolation="BEZIER", punch_out="TRUNCATE", blend=0):
    """Replace part of an existing take with newly recorded samples."""
    if len(samples) < 2:
        return
    f_in = samples[0].frame
    f_out = samples[-1].frame
    old_end = take.frame_end
    keep_tail = punch_out == "KEEP" and f_out < old_end
    curves = _curves_for(take, obj, channels)
    values = {id(fc): [getter(s) for s in samples] for fc, getter, _k in curves}

    if keep_tail:
        # Make the old tail continuous with where the new flight ends.
        transforms = {}
        euler = [(fc, k) for fc, _g, k in curves if k == "euler"]
        for fc, _k in euler:
            old = fc.evaluate(f_out)
            turns = round((values[id(fc)][-1] - old) / TAU)
            transforms[id(fc)] = (1.0, turns * TAU)
        quat = [fc for fc, _g, k in curves if k == "quat"]
        if len(quat) == 4:
            old_q = Quaternion([fc.evaluate(f_out) for fc in quat])
            new_q = Quaternion(samples[-1].rot)
            if old_q.dot(new_q) < 0.0:
                for fc in quat:
                    transforms[id(fc)] = (-1.0, 0.0)
        # Blend the end of the new flight into the (transformed) old take.
        window = min(int(blend), f_out - f_in)
        if window > 0:
            first = f_out - window
            for fc, _g, _k in curves:
                scale, offset = transforms.get(id(fc), (1.0, 0.0))
                vals = values[id(fc)]
                for i, smp in enumerate(samples):
                    if smp.frame <= first:
                        continue
                    w = smootherstep((smp.frame - first) / window)
                    target = fc.evaluate(smp.frame) * scale + offset
                    vals[i] = vals[i] + (target - vals[i]) * w
        for fc, _g, _k in curves:
            scale, offset = transforms.get(id(fc), (1.0, 0.0))
            if scale != 1.0 or offset != 0.0:
                _transform_tail(fc, f_out, scale, offset)

    hi = f_out if keep_tail else math.inf
    frames = [s.frame for s in samples]
    for fc, _g, _k in curves:
        _remove_keys(fc, f_in, hi)
        _add_keys(fc, frames, values[id(fc)], interpolation)
    update_take_range(take)


def activate_take(scene, index):
    """Assign a take's actions to its camera."""
    if _suppress_activate:
        return False
    s = settings(scene)
    if not 0 <= index < len(s.takes):
        return False
    take = s.takes[index]
    cam = take.camera
    if cam is None or take.action is None:
        return False
    from . import session

    active = session.ACTIVE
    if active is not None and active.obj == cam:
        active.set_active_action(take.action)
        return True
    if take.rotation_mode and cam.rotation_mode != take.rotation_mode:
        cam.rotation_mode = take.rotation_mode
    compat.assign_action(cam, take.action)
    if cam.type == "CAMERA":
        if take.data_action is not None:
            compat.assign_action(cam.data, take.data_action)
        elif is_take_action(scene, compat.get_action(cam.data)):
            compat.assign_action(cam.data, None)
    return True


def remove_take(scene, index, delete_actions=True):
    s = settings(scene)
    if not 0 <= index < len(s.takes):
        return
    take = s.takes[index]
    action, data_action, cam = take.action, take.data_action, take.camera
    if cam is not None:
        if action is not None and compat.get_action(cam) == action:
            compat.assign_action(cam, None)
        if data_action is not None and cam.type == "CAMERA" and compat.get_action(cam.data) == data_action:
            compat.assign_action(cam.data, None)
    s.takes.remove(index)
    if delete_actions:
        for act in (action, data_action):
            if act is not None and act.users <= (1 if act.use_fake_user else 0):
                bpy.data.actions.remove(act)
    set_index_quiet(s, min(index, len(s.takes) - 1) if len(s.takes) else 0)


def duplicate_take(scene, index):
    s = settings(scene)
    src = s.takes[index]
    s.take_counter += 1
    take = s.takes.add()
    take.camera = src.camera
    take.action = compat.copy_action(src.action) if src.action else None
    take.data_action = compat.copy_action(src.data_action) if src.data_action else None
    take.name = "%s copy" % src.name
    take.frame_start, take.frame_end = src.frame_start, src.frame_end
    take.rating, take.note = src.rating, src.note
    take.created = time.strftime("%Y-%m-%d %H:%M:%S")
    take.source = "Duplicate of " + src.name
    take.rotation_mode = src.rotation_mode
    set_index_quiet(s, len(s.takes) - 1)
    return len(s.takes) - 1


def adopt_action(scene, obj, action, data_action=None, label=None):
    """Register an existing animation as a take so it isn't lost."""
    idx = find_take(scene, action)
    if idx >= 0:
        return idx
    s = settings(scene)
    action.use_fake_user = True
    if data_action is not None:
        data_action.use_fake_user = True
    take = s.takes.add()
    take.camera = obj
    take.action = action
    take.data_action = data_action
    take.name = label or action.name
    take.created = time.strftime("%Y-%m-%d %H:%M:%S")
    take.source = "Existing animation"
    take.rotation_mode = obj.rotation_mode if obj.rotation_mode != "AXIS_ANGLE" else "XYZ"
    update_take_range(take)
    return len(s.takes) - 1


# --------------------------------------------------------------------------- evaluation
class TakeEvaluator:
    """Evaluates a take at arbitrary frames (for punch-in playback, ghosts, paths)."""

    def __init__(self, take, obj):
        self.obj = obj
        self.frame_start = take.frame_start
        self.frame_end = take.frame_end
        action = take.action
        slot = compat.find_slot(action, obj)
        self.loc = [compat.find_fcurve(action, "location", i, slot) for i in range(3)]
        quat = [compat.find_fcurve(action, "rotation_quaternion", i, slot) for i in range(4)]
        if any(quat):
            self.quat = True
            self.rot = quat
            self.order = "QUATERNION"
        else:
            self.quat = False
            self.rot = [compat.find_fcurve(action, "rotation_euler", i, slot) for i in range(3)]
            order = take.rotation_mode or obj.rotation_mode
            self.order = order if order not in ("QUATERNION", "AXIS_ANGLE") else "XYZ"
        self.zoom = self.focus = None
        if take.data_action is not None and obj.type == "CAMERA":
            dslot = compat.find_slot(take.data_action, obj.data)
            self.zoom = compat.find_fcurve(take.data_action, "lens", 0, dslot) or compat.find_fcurve(
                take.data_action, "ortho_scale", 0, dslot
            )
            self.focus = compat.find_fcurve(take.data_action, "dof.focus_distance", 0, dslot)

    @staticmethod
    def _eval(fc, frame, fallback):
        return fc.evaluate(frame) if fc is not None else fallback

    def raw_rotation(self, frame):
        obj = self.obj
        if self.quat:
            base = obj.rotation_quaternion
        else:
            base = obj.rotation_euler
        return tuple(self._eval(fc, frame, base[i]) for i, fc in enumerate(self.rot))

    def basis(self, frame):
        obj = self.obj
        loc = Vector([self._eval(fc, frame, obj.location[i]) for i, fc in enumerate(self.loc)])
        raw = self.raw_rotation(frame)
        q = Quaternion(raw).normalized() if self.quat else Euler(raw, self.order).to_quaternion()
        return loc, q

    def pose(self, frame, pmat=None, lens=50.0, focus=10.0):
        loc, q = self.basis(frame)
        wl, wq = basis_to_world(self.obj, loc, q, pmat)
        return Pose(wl, wq, self._eval(self.zoom, frame, lens), self._eval(self.focus, frame, focus))

    def path(self, step=1, pmat=None):
        pts = []
        f = self.frame_start
        while f <= self.frame_end:
            loc, q = self.basis(f)
            pts.append(basis_to_world(self.obj, loc, q, pmat)[0])
            f += step
        return pts


# --------------------------------------------------------------------------- editing tools
_ROT_PATHS = ("rotation_euler", "rotation_quaternion")
_ZOOM_PATHS = ("lens", "ortho_scale", "dof.focus_distance")


def _filter(fc, location, rotation, lens):
    path = fc.data_path
    if path == "location":
        return location
    if path in _ROT_PATHS:
        return rotation
    if path in _ZOOM_PATHS:
        return lens
    return False


def smooth_take(take, sigma, location=True, rotation=True, lens=True, pin_ends=True):
    """Gaussian-smooth the take's keys (works best on raw per-frame takes)."""
    count = 0
    for fc, _d in take_fcurves(take):
        if not _filter(fc, location, rotation, lens):
            continue
        kps = fc.keyframe_points
        n = len(kps)
        if n < 3:
            continue
        co = [0.0] * (2 * n)
        hl = [0.0] * (2 * n)
        hr = [0.0] * (2 * n)
        kps.foreach_get("co", co)
        kps.foreach_get("handle_left", hl)
        kps.foreach_get("handle_right", hr)
        ys = co[1::2]
        new = smoothing.gaussian_smooth(ys, sigma, pin_ends)
        for i in range(n):
            delta = new[i] - ys[i]
            co[2 * i + 1] += delta
            hl[2 * i + 1] += delta
            hr[2 * i + 1] += delta
        kps.foreach_set("co", co)
        kps.foreach_set("handle_left", hl)
        kps.foreach_set("handle_right", hr)
        fc.update()
        count += 1
    return count


def simplify_take(take, loc_tolerance, rot_tolerance, lens_tolerance):
    """Remove keys that aren't needed to stay within tolerance. Returns keys removed."""
    removed = 0
    for fc, _d in take_fcurves(take):
        path = fc.data_path
        if path == "location":
            tol = loc_tolerance
        elif path in _ROT_PATHS:
            tol = rot_tolerance
        elif path in _ZOOM_PATHS:
            tol = lens_tolerance
        else:
            continue
        kps = fc.keyframe_points
        n = len(kps)
        if n < 3 or tol <= 0.0:
            continue
        co = [0.0] * (2 * n)
        kps.foreach_get("co", co)
        xs, ys = co[0::2], co[1::2]
        interp = [kp.interpolation for kp in kps]
        keep = sorted(smoothing.rdp_indices(xs, ys, tol))
        kept = set(keep)
        for i in range(n - 1, -1, -1):
            if i not in kept:
                kps.remove(kps[i], fast=True)
        # Auto handles keep the curve smooth; Blender flattens them on the first and
        # last key though, so aim those inwards to avoid easing the ends of the move.
        for kp in kps:
            kp.handle_left_type = kp.handle_right_type = "AUTO"
        kps[0].handle_right_type = "VECTOR"
        kps[-1].handle_left_type = "VECTOR"
        fc.update()
        # RDP is exact for straight segments, but the Bezier handles curve them:
        # put back the worst sample of every segment until the real curve is in tolerance.
        for _ in range(n):
            worst = []
            for a, b in zip(keep, keep[1:]):
                best_i, best_e = None, tol
                for i in range(a + 1, b):
                    e = abs(fc.evaluate(xs[i]) - ys[i])
                    if e > best_e:
                        best_i, best_e = i, e
                if best_i is not None:
                    worst.append(best_i)
            if not worst:
                break
            for i in worst:
                kp = kps.insert(xs[i], ys[i], options={"FAST"})
                kp.interpolation = interp[i]
                kp.handle_left_type = kp.handle_right_type = "AUTO"
            fc.update()
            keep = sorted(set(keep).union(worst))
        removed += n - len(keep)
    return removed


def _remap_time(take, fn):
    for fc, _d in take_fcurves(take):
        for kp in fc.keyframe_points:
            x, hlx, hrx = fn(kp.co.x), fn(kp.handle_left.x), fn(kp.handle_right.x)
            if hlx > hrx:  # reversed: swap handles
                hl, hr = kp.handle_left.copy(), kp.handle_right.copy()
                lt, rt = kp.handle_left_type, kp.handle_right_type
                kp.co.x = x
                kp.handle_left = (hrx, hr.y)
                kp.handle_right = (hlx, hl.y)
                kp.handle_left_type, kp.handle_right_type = rt, lt
            else:
                kp.co.x = x
                kp.handle_left.x = hlx
                kp.handle_right.x = hrx
        fc.update()
    update_take_range(take)


def retime_take(take, factor):
    pivot = take.frame_start
    _remap_time(take, lambda x: pivot + (x - pivot) * factor)


def offset_take(take, new_start):
    delta = new_start - take.frame_start
    _remap_time(take, lambda x: x + delta)


def reverse_take(take):
    a, b = take.frame_start, take.frame_end
    _remap_time(take, lambda x: a + b - x)


def add_noise(take, rotation, location, scale, seed=0):
    """Non-destructive handheld wobble using F-Curve Noise modifiers."""
    rng = random.Random(seed)
    added = 0
    for fc, _d in take_fcurves(take):
        path = fc.data_path
        if path == "rotation_euler":
            strength = rotation * 2.0
        elif path == "rotation_quaternion":
            if fc.array_index == 0:
                continue
            strength = rotation  # quaternion components ~ sin(angle / 2)
        elif path == "location":
            strength = location * 2.0
        else:
            continue
        if strength <= 0.0:
            continue
        mod = fc.modifiers.new("NOISE")
        mod.scale = scale
        mod.strength = strength
        mod.phase = rng.uniform(0.0, 1000.0)
        mod.depth = 1
        added += 1
    return added


def clear_noise(take):
    removed = 0
    for fc, _d in take_fcurves(take):
        for mod in list(fc.modifiers):
            if mod.type == "NOISE":
                fc.modifiers.remove(mod)
                removed += 1
    return removed


def take_to_curve(scene, take, step=1, collection=None):
    cam = take.camera
    ev = TakeEvaluator(take, cam)
    pts = ev.path(max(1, step), parent_matrix(cam))
    if len(pts) < 2:
        return None
    name = "%s Path" % take.name
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.path_duration = max(1, take.frame_end - take.frame_start)
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(len(pts) - 1)
    for bp, p in zip(spline.bezier_points, pts):
        bp.co = p
        bp.handle_left_type = bp.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, curve)
    (collection or scene.collection).objects.link(obj)
    return obj


def take_to_camera(scene, take, bind_marker=True):
    src = take.camera
    data = src.data.copy()
    if data.animation_data is not None:
        data.animation_data_clear()
    obj = bpy.data.objects.new("%s Cam" % take.name, data)
    collections = list(src.users_collection) or [scene.collection]
    for coll in collections:
        coll.objects.link(obj)
    obj.rotation_mode = take.rotation_mode or src.rotation_mode
    obj.parent = src.parent
    obj.parent_type = src.parent_type
    obj.matrix_parent_inverse = src.matrix_parent_inverse.copy()
    obj.delta_location = src.delta_location
    obj.delta_rotation_euler = src.delta_rotation_euler
    obj.delta_rotation_quaternion = src.delta_rotation_quaternion
    obj.scale = src.scale
    compat.assign_action(obj, compat.copy_action(take.action))
    if take.data_action is not None:
        compat.assign_action(data, compat.copy_action(take.data_action))
    if bind_marker:
        marker = scene.timeline_markers.new(take.name, frame=take.frame_start)
        marker.camera = obj
    return obj


def bookmarks_to_take(scene, obj, bookmarks, frame_start, frames_per, interpolation="BEZIER"):
    channels = Channels(obj, record_zoom=True, record_focus=False)
    pmat = parent_matrix(obj)
    samples = []
    prev = current_rot_values(obj, channels)
    for i, bm in enumerate(bookmarks):
        q = Quaternion(bm.rotation).normalized()
        loc, qb = world_to_basis(obj, Vector(bm.location), q, pmat)
        rot = rot_values(qb, channels, prev)
        prev = rot
        samples.append(Sample(frame_start + i * frames_per, loc, rot, bm.lens, None, Vector(bm.location)))
    if len(samples) < 2:
        return -1
    return record_take(scene, obj, samples, channels, interpolation, source="Waypoints")
