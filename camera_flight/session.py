"""A live flight: glues input, physics, the camera, recording and the viewport.

The modal operator (``ops_fly``) is a thin shell around :class:`FlightSession`
so the whole flight can also be driven headlessly (the test-suite feeds it
fake events and a fake clock).
"""

import math
import time

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import compat, gamepad, keymap, takes
from .core.controller import FlightController, FlightInput, FlightParams
from .core.mathx import AXIS_Z, clamp, wrap_pi
from .prefs import get_prefs
from .props import MODE_LABELS, MODE_ORDER

ACTIVE = None  # the running FlightSession (one at a time)

TIME_SCALES = (0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 4.0)
SHADING_CYCLE = ("SOLID", "MATERIAL", "RENDERED")
GUIDE_FLAGS = (
    "show_composition_thirds",
    "show_composition_center",
    "show_composition_center_diagonal",
    "show_composition_golden",
    "show_composition_golden_tria_a",
    "show_composition_golden_tria_b",
    "show_composition_harmony_tri_a",
    "show_composition_harmony_tri_b",
)
GUIDE_SETS = (
    ("Off", ()),
    ("Rule of Thirds", ("show_composition_thirds",)),
    ("Center + Diagonals", ("show_composition_center", "show_composition_center_diagonal")),
    ("Golden Ratio", ("show_composition_golden",)),
    ("Golden Triangles", ("show_composition_golden_tria_a", "show_composition_golden_tria_b")),
    ("Harmony Triangles", ("show_composition_harmony_tri_a", "show_composition_harmony_tri_b")),
)
CLEAN_OVERLAY_FLAGS = (
    "show_floor",
    "show_axis_x",
    "show_axis_y",
    "show_axis_z",
    "show_extras",
    "show_relationship_lines",
    "show_cursor",
    "show_object_origins",
    "show_outline_selected",
    "show_text",
    "show_stats",
    "show_motion_paths",
)
PAD_PRESS_ACTIONS = {
    "A": "record",
    "B": "level",
    "X": "autofocus",
    "Y": "mode",
    "RS": "lock",
    "START": "pause",
    "BACK": "keyframe",
    "UP": "speed_up",
    "DOWN": "speed_down",
}


class _SyntheticEvent:
    """Stand-in event for actions triggered by a gamepad."""

    shift = ctrl = alt = oskey = is_repeat = False


class LockTarget:
    def __init__(self, obj=None, local=None, point=None, label=""):
        self.obj = obj
        self.local = Vector(local) if local is not None else Vector((0.0, 0.0, 0.0))
        self.point = Vector(point) if point is not None else None
        self.label = label
        self.prev_point = None
        self.prev_yaw = None

    def _matrix(self, depsgraph):
        ob = self.obj
        if depsgraph is not None:
            ob = ob.evaluated_get(depsgraph)
        return ob.matrix_world

    def world_point(self, depsgraph):
        if self.obj is None:
            return self.point
        try:
            return self._matrix(depsgraph) @ self.local
        except (ReferenceError, RuntimeError):
            return None

    def world_yaw(self, depsgraph):
        if self.obj is None:
            return 0.0
        m = self._matrix(depsgraph)
        return math.atan2(m[1][0], m[0][0])


class Toast:
    __slots__ = ("key", "text", "kind", "ttl", "age")

    def __init__(self, key, text, kind, ttl):
        self.key = key
        self.text = text
        self.kind = kind
        self.ttl = ttl
        self.age = 0.0


class FlightSession:
    def __init__(self, context, obj, window=None, area=None, region=None, from_view=False):
        self.scene = context.scene
        self.obj = obj
        self.window = window
        self.area = area
        self.region = region
        self.space = area.spaces.active if area is not None else None
        self.rv3d = None
        if region is not None and getattr(region, "data", None) is not None:
            self.rv3d = region.data
        elif self.space is not None:
            self.rv3d = self.space.region_3d
        self.region_ptr = region.as_pointer() if region is not None else 0
        self.area_ptr = area.as_pointer() if area is not None else 0
        self.from_view = from_view
        self.settings = self.scene.camera_flight
        self.prefs = get_prefs(context)
        self.bindings = keymap.Bindings(self.prefs)
        self.params = FlightParams()
        self.ctrl = FlightController(self.params)
        self.inp = FlightInput()
        self.keys_down = set()
        self.captured = window is not None
        self.is_camera = obj.type == "CAMERA"
        self.fps = compat.scene_fps(self.scene)
        self.world_scale = obj.matrix_world.to_scale()

        self.last_time = None
        self.real_time = 0.0
        self.running = False
        self.clock = float(self.scene.frame_current)
        self.prev_clock = self.clock
        self.lock_accum = 0.0

        self.recorder = None
        self.rec_target = None
        self.rec_paused = False
        self.rec_started = 0.0
        self.rec_first_state = None
        self.stop_pending = False
        self.countdown = 0.0
        self.active_action = None
        self.undo_stack = []

        self.lock = None
        self.follow = False
        self.prev_mode = "FREE"
        self.guide_index = 0

        self.toasts = []
        self.hud_visible = True
        self.help_visible = False
        self.finished = None
        self.made_changes = False
        self.end_message = None

        self.pad = None
        self.pad_state = None
        self.pad_was_connected = False

        self._last_mouse = None
        self._warp_check = False
        self._warp_ok = True
        self._warp_misses = 0

        self.last_pose = None
        self.prev_pose = None
        self.version = 0
        self._dg = None
        self._pmat = None
        self._eval_cache = None
        self._saved = {}
        self._ended = False

    # ================================================================ lifecycle
    def start(self, context):
        global ACTIVE
        obj = self.obj
        s = self.settings
        if obj.rotation_mode == "AXIS_ANGLE":
            obj.rotation_mode = "XYZ"

        start_m = None
        rv3d = self.rv3d
        if rv3d is not None and rv3d.view_perspective != "CAMERA":
            if self.from_view or s.start_from == "VIEW" or (s.start_from == "AUTO" and self.is_camera):
                start_m = rv3d.view_matrix.inverted()
        if start_m is None:
            start_m = obj.matrix_world.copy()

        self._saved["matrix_basis"] = obj.matrix_basis.copy()
        self._saved["frame"] = self.scene.frame_current
        self._detach_animation()
        self._mute_constraints()

        zoom, focus = 50.0, 10.0
        if self.is_camera:
            cam = obj.data
            ortho = cam.type == "ORTHO"
            self.ctrl.zoom_sign = -1.0 if ortho else 1.0
            zoom = cam.ortho_scale if ortho else cam.lens
            focus = cam.dof.focus_distance
            if ortho:
                self.params.lens_min, self.params.lens_max = 0.001, 1.0e5
            self._saved["camera_data"] = {
                "lens": cam.lens,
                "ortho_scale": cam.ortho_scale,
                "focus_distance": cam.dof.focus_distance,
                "use_dof": cam.dof.use_dof,
                "aperture_fstop": cam.dof.aperture_fstop,
                "focus_object": cam.dof.focus_object,
            }
            self._saved["guides"] = {f: getattr(cam, f) for f in GUIDE_FLAGS if hasattr(cam, f)}

        loc, rot, _scale = start_m.decompose()
        self._sync_params()
        self.ctrl.set_pose(loc, rot, zoom, focus)
        if s.mode not in ("ORBIT", "WALK"):
            self.prev_mode = s.mode
        self._sync_mode(announce=False)
        self._setup_view()

        if self.window is not None:
            self.window.cursor_modal_set("NONE")
            self._warp_center()
        if self.prefs.gamepad_enabled:
            self.pad = gamepad.open_gamepad(self.prefs.gamepad_index, self.prefs.gamepad_layout)
            if self.pad is None:
                self.toast("Gamepad not supported on this platform", kind="WARN")

        pose = self.ctrl.output()
        self._apply_pose(pose)
        self.last_pose = self.prev_pose = pose
        ACTIVE = self
        self.toast(
            "Flight started · %s help · %s finish · %s cancel"
            % (self.bindings.first("help"), self.bindings.first("finish"), self.bindings.first("cancel")),
            ttl=4.0,
        )

    def end(self, context=None, cancel=False):
        global ACTIVE
        if self._ended:
            return
        self._ended = True
        if self.recorder is not None:
            if self._alive():
                self.stop_recording()
            else:
                self.recorder = None
        if self.running and self.settings.timing == "SCENE":
            self._scene_play(False)
        self.running = False
        if self.window is not None:
            try:
                self.window.cursor_modal_restore()
            except (RuntimeError, ReferenceError):
                pass
        ACTIVE = None
        if cancel:
            self.finished = "CANCEL"
        self._restore_view()
        if not self._alive():
            return
        if cancel and "scene_camera" in self._saved and not self.made_changes:
            self.scene.camera = self._saved["scene_camera"]
        self._restore_constraints()
        obj = self.obj
        scene = self.scene
        final = takes.find_take(scene, self.active_action)
        if final >= 0:
            takes.set_index_quiet(self.settings, final)
            takes.activate_take(scene, final)
        else:
            action, slot = self._saved.get("action", (None, None))
            compat.assign_action(obj, action, slot)
            if cancel:
                obj.matrix_basis = self._saved["matrix_basis"]
            elif action is not None:
                self.end_message = "The camera is animated, so its flown position isn't kept. Record a take (R) or insert keys (K)."
        if self.is_camera:
            data_action, data_slot = self._saved.get("data_action", (None, None))
            if final < 0:
                compat.assign_action(obj.data, data_action, data_slot)
            if cancel and final < 0:
                cd = self._saved.get("camera_data")
                if cd:
                    cam = obj.data
                    cam.lens = cd["lens"]
                    cam.ortho_scale = cd["ortho_scale"]
                    cam.dof.focus_distance = cd["focus_distance"]
                    cam.dof.use_dof = cd["use_dof"]
                    cam.dof.aperture_fstop = cd["aperture_fstop"]
                    cam.dof.focus_object = cd["focus_object"]
            for flag, value in self._saved.get("guides", {}).items():
                setattr(obj.data, flag, value)
        for id_data, key in ((obj, "use_nla"), (obj.data if self.is_camera else None, "data_use_nla")):
            value = self._saved.get(key)
            if id_data is not None and value is not None and id_data.animation_data is not None:
                id_data.animation_data.use_nla = value
        if cancel and not self.made_changes:
            scene.frame_current = self._saved.get("frame", scene.frame_current)
        for entry in self.undo_stack:
            if entry[0] == "punch":
                for act in (entry[2], entry[3]):
                    if act is not None:
                        bpy.data.actions.remove(act)
        self.undo_stack.clear()
        if self.pad is not None:
            self.pad.close()
            self.pad = None

    def _alive(self):
        try:
            self.obj.name
            return True
        except ReferenceError:
            return False

    def _view_alive(self):
        """False once our viewport area/region no longer exist (layout changed)."""
        if self.window is None or self.area is None:
            return True
        try:
            for area in self.window.screen.areas:
                if area.as_pointer() == self.area_ptr:
                    return any(r.as_pointer() == self.region_ptr for r in area.regions)
        except (ReferenceError, AttributeError):
            pass
        # Forget the dead pointers so nothing touches freed UI data.
        self.area = self.region = self.space = self.rv3d = None
        return False

    # ================================================================ saved state
    def _detach_animation(self):
        """Take the camera off its animation so flying isn't overridden by F-Curves."""
        obj = self.obj
        scene = self.scene
        targets = [(obj, "action", "use_nla")]
        if self.is_camera:
            targets.append((obj.data, "data_action", "data_use_nla"))
        for id_data, key, nla_key in targets:
            ad = id_data.animation_data
            self._saved[key] = (ad.action, compat.get_action_slot(id_data)) if ad else (None, None)
            self._saved[nla_key] = ad.use_nla if ad else None
            if ad is None:
                continue
            try:
                if ad.action is not None:
                    ad.action = None
                ad.use_nla = False
            except (RuntimeError, AttributeError, TypeError):
                self.toast("Couldn't detach animation (NLA tweak mode?)", kind="WARN")
        action = self._saved["action"][0]
        if action is not None:
            if takes.find_take(scene, action) < 0:
                data_action = self._saved.get("data_action", (None,))[0]
                if takes.is_take_action(scene, data_action):
                    data_action = None
                takes.adopt_action(scene, obj, action, data_action, label="%s (original)" % action.name)
            self.active_action = action

    def _mute_constraints(self):
        cons = [(c.name, c.mute) for c in self.obj.constraints]
        self._saved["constraints"] = cons
        muted = 0
        for c in self.obj.constraints:
            if not c.mute:
                c.mute = True
                muted += 1
        if muted:
            self.toast("Muted %d constraint(s) while flying" % muted, kind="WARN")

    def _restore_constraints(self):
        for name, mute in self._saved.get("constraints", ()):
            c = self.obj.constraints.get(name)
            if c is not None:
                c.mute = mute

    def _setup_view(self):
        sp, rv3d, sv = self.space, self.rv3d, self._saved
        if self.scene.camera != self.obj and self.is_camera:
            sv["scene_camera"] = self.scene.camera
            self.scene.camera = self.obj
        if sp is None or rv3d is None:
            return
        sv["use_local_camera"] = sp.use_local_camera
        sp.use_local_camera = False
        sv["view_perspective"] = rv3d.view_perspective
        if self.is_camera:
            rv3d.view_perspective = "CAMERA"
        sv["lock_camera"] = sp.lock_camera
        sp.lock_camera = False
        sv["shading"] = sp.shading.type
        sv["shading_dof"] = sp.shading.use_dof
        sv["show_overlays"] = sp.overlay.show_overlays
        if self.prefs.clean_viewport:
            ov = sp.overlay
            sv["overlay"] = {f: getattr(ov, f) for f in CLEAN_OVERLAY_FLAGS if hasattr(ov, f)}
            for f in sv["overlay"]:
                setattr(ov, f, False)
            sv["show_gizmo"] = sp.show_gizmo
            sp.show_gizmo = False
        if self.prefs.hide_header:
            names = ("show_region_header", "show_region_toolbar", "show_region_tool_header")
            sv["regions"] = {n: getattr(sp, n) for n in names if hasattr(sp, n)}
            for n in sv["regions"]:
                setattr(sp, n, False)

    def _restore_view(self):
        sp, rv3d, sv = self.space, self.rv3d, self._saved
        try:
            if sp is None or rv3d is None:
                return
            for f, v in sv.get("overlay", {}).items():
                setattr(sp.overlay, f, v)
            for n, v in sv.get("regions", {}).items():
                setattr(sp, n, v)
            if "show_gizmo" in sv:
                sp.show_gizmo = sv["show_gizmo"]
            if "show_overlays" in sv:
                sp.overlay.show_overlays = sv["show_overlays"]
            if "shading" in sv:
                sp.shading.type = sv["shading"]
                sp.shading.use_dof = sv["shading_dof"]
            if "lock_camera" in sv:
                sp.lock_camera = sv["lock_camera"]
            if "use_local_camera" in sv:
                sp.use_local_camera = sv["use_local_camera"]
            if self.finished == "CANCEL" and "view_perspective" in sv:
                rv3d.view_perspective = sv["view_perspective"]
        except ReferenceError:
            pass

    # ================================================================ per-tick
    def tick(self, context=None, now=None):
        if now is None:
            now = time.perf_counter()
        if self.last_time is None:
            self.last_time = now
            return
        real_dt = clamp(now - self.last_time, 0.0, 0.25)
        self.last_time = now
        if real_dt <= 0.0:
            return
        self.real_time += real_dt
        if not self._alive():
            self.end_message = "The flight camera was deleted."
            self.finished = "CANCEL"
            return
        if not self._view_alive():
            self.end_message = "The viewport layout changed, so the flight ended."
            self.finished = "FINISH"
            return
        self._dg = None
        self._pmat = None
        self._sync_params()
        self._sync_mode()
        self._poll_gamepad()
        self._gather_input()

        if self.countdown > 0.0:
            self.countdown -= real_dt
            if self.countdown <= 0.0:
                self.countdown = 0.0
                self._begin_recording()

        sim_dt = self._advance_time(real_dt)
        if self._following_take():
            self._drive_from_take(sim_dt)
        else:
            self._update_targets()
            if self.ctrl.step(sim_dt, self.inp, self):
                self.inp.clear_impulses()
        if self._af_active():
            self._autofocus(quiet=True)

        pose = self.ctrl.output()
        self._apply_pose(pose)
        if self.recorder is not None and not self.rec_paused:
            self.recorder.feed(self.prev_clock, self.clock, self.prev_pose, pose)
        self.prev_pose = pose
        self.prev_clock = self.clock
        self.last_pose = pose
        if self.stop_pending:
            self.stop_pending = False
            self.stop_recording(reason="Reached the end frame")
        self._age_toasts(real_dt)
        if self.area is not None:
            self.area.tag_redraw()

    def _sync_params(self):
        s, p = self.settings, self.params
        p.speed = s.speed
        p.boost = s.boost_multiplier
        p.precision = s.precision_multiplier
        p.accel = s.acceleration
        p.drag = s.drag
        p.look_smooth = s.look_smoothing
        p.track_smooth = s.track_smoothing
        p.turn_rate = s.turn_rate
        p.roll_rate = s.roll_rate
        p.bank = s.bank_amount
        p.max_bank = s.max_bank
        p.auto_level = s.auto_level
        p.level_time = s.level_time
        p.planar = s.planar_movement
        p.adaptive_speed = s.adaptive_speed
        p.collide = s.collisions
        p.radius = s.collision_radius
        p.gravity = s.gravity
        p.eye_height = s.eye_height
        p.jump_speed = s.jump_speed
        p.head_bob = s.head_bob
        p.zoom_rate = s.zoom_rate
        p.zoom_smooth = s.zoom_smoothing
        p.hold_zoom = s.hold_zoom_factor
        p.focus_smooth = s.focus_smoothing
        p.shake = s.shake_enabled
        p.shake_intensity = s.shake_intensity
        p.shake_frequency = s.shake_frequency
        p.shake_rotation = s.shake_rotation
        p.shake_position = s.shake_position

    def _sync_mode(self, announce=True):
        want = self.settings.mode
        if want == self.ctrl.mode:
            return
        pivot = self._orbit_pivot() if want == "ORBIT" else None
        self.ctrl.set_mode(want, pivot)
        if announce:
            text = MODE_LABELS.get(want, want)
            if want == "WALK" and not self.settings.collisions:
                text += " · tip: enable Collisions to bump into walls"
            self.toast(text, key="mode")

    def _set_mode(self, mode):
        cur = self.settings.mode
        if mode == cur:
            return
        if cur not in ("ORBIT", "WALK"):
            self.prev_mode = cur
        self.settings.mode = mode
        self._sync_mode()

    def _orbit_pivot(self):
        if self.lock is not None:
            p = self.lock.world_point(self._depsgraph())
            if p is not None:
                return p
        hit = self._crosshair_hit()
        if hit is not None:
            return hit[0]
        return self.scene.cursor.location.copy()

    def _advance_time(self, real_dt):
        s = self.settings
        if not self.running or (self.recorder is not None and self.rec_paused):
            return real_dt
        scene = self.scene
        start, end = compat.scene_range(scene)
        rec = self.recorder
        if s.timing == "SCENE":
            f = float(scene.frame_current)
            if rec is not None:
                if f < self.clock - 0.5:
                    self.stop_pending = True  # Blender's playback wrapped around
                else:
                    self.clock = f
                    if s.stop_at_end and self.clock >= end and rec.start_frame < end:
                        self.stop_pending = True
            else:
                self.clock = f
            return real_dt
        if s.timing == "FRAME_LOCKED":
            step = 1.0 / self.fps
            self.lock_accum += real_dt * s.time_scale
            if self.lock_accum + 1e-9 < step:
                return 0.0
            self.lock_accum = min(self.lock_accum - step, step)
            self.clock += 1.0
            sim_dt = step
        else:
            sim_dt = real_dt * s.time_scale
            self.clock += sim_dt * self.fps
        if rec is not None:
            if s.stop_at_end and self.clock >= end and rec.start_frame < end:
                self.clock = float(end)
                self.stop_pending = True
        elif self.clock >= end + 1:
            self.clock = float(start)
            self.prev_clock = self.clock
        self._set_frame(int(math.floor(self.clock + 1e-6)))
        return sim_dt

    def _set_frame(self, frame):
        if self.scene.frame_current != frame:
            self.scene.frame_current = frame

    def _is_playing(self):
        if self.window is None:
            return False
        try:
            return self.window.screen.is_animation_playing
        except (AttributeError, ReferenceError):
            return False

    def _scene_play(self, play):
        if self.window is None or play == self._is_playing():
            return
        try:
            with bpy.context.temp_override(window=self.window, area=self.area, region=self.region):
                if play:
                    bpy.ops.screen.animation_play()
                else:
                    bpy.ops.screen.animation_cancel(restore_frame=False)
        except RuntimeError as ex:
            self.toast("Playback: %s" % ex, kind="WARN")

    # ================================================================ take playback
    def _evaluator(self):
        action = self.active_action
        if action is None:
            return None
        try:
            key = (action.as_pointer(), self.version)
        except ReferenceError:
            self.active_action = None
            return None
        if self._eval_cache is not None and self._eval_cache[0] == key:
            return self._eval_cache[1]
        take = takes.take_by_action(self.scene, action)
        ev = takes.TakeEvaluator(take, self.obj) if take is not None and take.camera == self.obj else None
        self._eval_cache = (key, ev)
        return ev

    def active_take(self):
        return takes.take_by_action(self.scene, self.active_action)

    def _following_take(self):
        return (
            self.running
            and self.recorder is None
            and self.settings.record_mode == "PUNCH"
            and self._evaluator() is not None
        )

    def _take_pose(self, frame):
        ev = self._evaluator()
        if ev is None:
            return None
        return ev.pose(frame, self._parent_matrix(), lens=self.ctrl.lens, focus=self.ctrl.focus), ev

    def _drive_from_take(self, sim_dt):
        result = self._take_pose(self.clock)
        if result is None:
            return
        pose, ev = result
        vel = (pose.pos - self.ctrl.pos) / sim_dt if sim_dt > 0.0 else self.ctrl.vel.copy()
        self.ctrl.set_pose(
            pose.pos, pose.rot, pose.lens if ev.zoom is not None else None, pose.focus if ev.focus is not None else None, vel
        )
        self.inp.clear_impulses()

    def _snap_to_take(self):
        if self.settings.record_mode != "PUNCH":
            return
        result = self._take_pose(self.clock)
        if result is not None:
            pose, ev = result
            self.ctrl.set_pose(
                pose.pos, pose.rot, pose.lens if ev.zoom is not None else None, pose.focus if ev.focus is not None else None
            )

    def set_active_action(self, action):
        self.active_action = action
        self.version += 1
        take = self.active_take()
        if take is not None:
            self.toast("Active take: %s" % take.name, key="take")
        if not self.running:
            self._snap_to_take()

    # ================================================================ targets & env
    def _depsgraph(self):
        if self._dg is None:
            self._dg = bpy.context.evaluated_depsgraph_get()
        return self._dg

    def _parent_matrix(self):
        if self.obj.parent is None:
            return None
        if self._pmat is None:
            self._pmat = takes.parent_matrix(self.obj, self._depsgraph())
        return self._pmat

    def ray_cast(self, origin, direction, distance):
        """Environment callback for the flight controller."""
        try:
            hit, loc, nrm, _i, ob, _m = self.scene.ray_cast(
                self._depsgraph(), Vector(origin), Vector(direction), distance=distance
            )
        except (RuntimeError, ValueError):
            return False, None, None, None
        if not hit:
            return False, None, None, None
        return True, loc, nrm, getattr(ob, "original", ob)

    def _crosshair_hit(self):
        pose = self.last_pose
        origin = pose.pos if pose is not None else self.ctrl.pos
        direction = pose.forward() if pose is not None else self.ctrl.forward()
        far = self.obj.data.clip_end if self.is_camera else 1.0e5
        hit, loc, nrm, ob = self.ray_cast(origin, direction, far)
        return (loc, nrm, ob) if hit else None

    def _update_targets(self):
        lock = self.lock
        if lock is None:
            self.ctrl.look_target = None
            return
        dg = self._depsgraph()
        p = lock.world_point(dg)
        if p is None:
            self._release_lock()
            self.toast("Lock target lost", kind="WARN")
            return
        ctrl = self.ctrl
        if self.follow and lock.prev_point is not None:
            if ctrl.mode != "ORBIT":
                ctrl.pos = ctrl.pos + (p - lock.prev_point)
            if self.settings.follow_rotation and lock.obj is not None:
                yaw = lock.world_yaw(dg)
                dyaw = wrap_pi(yaw - lock.prev_yaw) if lock.prev_yaw is not None else 0.0
                if abs(dyaw) > 1e-7:
                    q = Quaternion(AXIS_Z, dyaw)
                    if ctrl.mode == "ORBIT":
                        ctrl.orbit_yaw += dyaw
                        ctrl.orbit_yaw_t += dyaw
                    else:
                        ctrl.pos = p + q @ (ctrl.pos - p)
                    ctrl.yaw += dyaw
                    ctrl.yaw_t += dyaw
                    ctrl.quat = q @ ctrl.quat
                    ctrl.quat_t = q @ ctrl.quat_t
                lock.prev_yaw = yaw
        lock.prev_point = p.copy()
        if ctrl.mode == "ORBIT":
            ctrl.pivot = p.copy()
        else:
            ctrl.look_target = p.copy()

    def _release_lock(self):
        self.lock = None
        self.follow = False
        self.ctrl.set_look_target(None)

    # ================================================================ camera I/O
    def _basis(self, pose):
        return takes.world_to_basis(self.obj, pose.pos, pose.rot, self._parent_matrix())

    def _apply_pose(self, pose):
        obj = self.obj
        loc, q = self._basis(pose)
        obj.location = loc
        if obj.rotation_mode == "QUATERNION":
            if Quaternion(obj.rotation_quaternion).dot(q) < 0.0:
                q.negate()
            obj.rotation_quaternion = q
        else:
            obj.rotation_euler = q.to_euler(obj.rotation_mode, obj.rotation_euler)
        if self.is_camera:
            cam = obj.data
            if cam.type == "ORTHO":
                if abs(cam.ortho_scale - pose.lens) > 1e-7:
                    cam.ortho_scale = max(pose.lens, 0.001)
            elif abs(cam.lens - pose.lens) > 1e-7:
                cam.lens = max(pose.lens, 1.0)
            if abs(cam.dof.focus_distance - pose.focus) > 1e-6:
                cam.dof.focus_distance = max(pose.focus, 0.0)

    def _ensure_dof(self):
        dof = self.obj.data.dof
        if dof.focus_object is not None:
            dof.focus_object = None
            self.toast("Cleared the focus object so autofocus can drive focus", kind="WARN")
        if not dof.use_dof:
            dof.use_dof = True
            self.toast("Depth of field enabled")
        sp = self.space
        if sp is not None and sp.shading.type == "SOLID" and not sp.shading.use_dof:
            sp.shading.use_dof = True

    def _af_active(self):
        if not self.is_camera:
            return False
        if self.settings.continuous_af:
            return True
        if self.captured and self.bindings.held("autofocus", self.keys_down):
            return True
        return self.pad_state is not None and "X" in self.pad_state[6]

    def _autofocus(self, quiet=False):
        if not self.is_camera:
            return
        fwd = self.ctrl.forward()
        d = None
        if self.lock is not None and self.lock.prev_point is not None:
            d = (self.lock.prev_point - self.ctrl.pos).dot(fwd)
        else:
            hit = self._crosshair_hit()
            if hit is not None:
                d = (hit[0] - self.ctrl.pos).dot(fwd)
        if d is None or d <= 0.0:
            if not quiet:
                self.toast("Autofocus: nothing under the crosshair", key="focus", kind="WARN")
            return
        self.ctrl.focus_t = d
        self._ensure_dof()
        if not quiet:
            self.toast("Focus %s" % compat.format_length(self.scene, d), key="focus")

    # ================================================================ input
    def _center(self):
        r = self.region
        if r is None:
            return 0, 0
        return r.x + r.width // 2, r.y + r.height // 2

    def _warp_center(self):
        if self.window is not None and self._warp_ok:
            cx, cy = self._center()
            self.window.cursor_warp(cx, cy)
            self._warp_check = True

    def _zoom_sensitivity(self):
        if not (self.prefs.zoom_scaled_sensitivity and self.is_camera) or self.ctrl.zoom_sign < 0.0:
            return 1.0
        lens = self.ctrl.lens * self.ctrl.zoom_mult
        return clamp(50.0 / max(lens, 1.0), 0.02, 1.5)

    def _mouse_move(self, event):
        """Relative mouse look.

        The cursor is warped back to the centre when it strays, so it never hits a
        screen edge. Where warping isn't possible (e.g. Wayland without a pointer
        grab) that's detected and we keep going with plain relative motion.
        """
        x, y = event.mouse_x, event.mouse_y
        cx, cy = self._center()
        last = self._last_mouse if self._last_mouse is not None else (cx, cy)
        if self._warp_check:
            self._warp_check = False
            if abs(x - last[0]) + abs(y - last[1]) < abs(x - cx) + abs(y - cy):
                self._warp_misses += 1
                if self._warp_misses >= 3:
                    self._warp_ok = False
                    self.toast("Cursor warping unsupported here; using relative mouse", key="mouse", kind="WARN")
            else:
                self._warp_misses = 0
                last = (cx, cy)
        self._last_mouse = (x, y)
        dx, dy = x - last[0], y - last[1]
        r = self.region
        if (dx or dy) and (r is None or (abs(dx) < r.width * 0.45 and abs(dy) < r.height * 0.45)):
            sens = math.radians(self.prefs.mouse_sensitivity) * self._zoom_sensitivity()
            self.inp.look[0] -= dx * sens
            self.inp.look[1] += dy * sens * (-1.0 if self.prefs.invert_mouse_y else 1.0)
        if r is not None and (abs(x - cx) > r.width * 0.25 or abs(y - cy) > r.height * 0.25):
            self._warp_center()

    def handle_event(self, context, event):
        """Returns 'HANDLED' or 'PASS'."""
        et = event.type
        val = event.value
        if et in ("MOUSEMOVE", "INBETWEEN_MOUSEMOVE"):
            if not self.captured:
                return "PASS"
            if et == "MOUSEMOVE":
                self._mouse_move(event)
            return "HANDLED"
        if et == "WINDOW_DEACTIVATE":
            self.keys_down.clear()
            return "PASS"
        actions = self.bindings.actions(et)
        if not self.captured:
            if val == "PRESS" and "mouse" in actions and not event.is_repeat:
                self._act_mouse(event)
                return "HANDLED"
            if val == "PRESS" and (event.ctrl or event.oskey) and et in ("Z", "Y", "S"):
                # Undo would swap the data out from under the flight, and saving now
                # would store the camera with its animation detached.
                self.toast("Finish the flight (%s) before undo or save" % self.bindings.first("finish"), key="blocked", kind="WARN")
                return "HANDLED"
            return "PASS"
        if et in keymap.WHEEL_UP or et in keymap.WHEEL_DOWN:
            if val == "PRESS":
                self._wheel(event, 1 if et in keymap.WHEEL_UP else -1)
            return "HANDLED"
        if val in ("PRESS", "DOUBLE_CLICK"):
            self.keys_down.add(et)
            if not event.is_repeat:
                for action in actions:
                    fn = getattr(self, "_act_" + action, None)
                    if fn is not None:
                        fn(event)
                if not actions and et in keymap.NUMBER_KEYS:
                    self._bookmark_key(keymap.NUMBER_KEYS[et], event)
        elif val == "RELEASE":
            self.keys_down.discard(et)
        return "HANDLED"

    def _gather_input(self):
        b, kd = self.bindings, self.keys_down
        inp = self.inp
        if not self.captured:
            kd = ()

        def h(action):
            return 1.0 if b.held(action, kd) else 0.0

        mx = h("right") - h("left")
        my = h("forward") - h("back")
        mz = h("up") - h("down")
        lx = h("look_left") - h("look_right")
        ly = h("look_up") - h("look_down")
        roll = h("roll_left") - h("roll_right")
        zoom = h("zoom_in") - h("zoom_out")
        boost = h("boost")
        ps = self.pad_state
        if ps is not None:
            plx, ply, prx, pry, plt, prt, buttons = ps
            k = self.prefs.gamepad_look_speed / max(self.params.turn_rate, 1e-6)
            mx += plx
            my += ply
            mz += prt - plt
            lx -= prx * k
            ly += pry * k * (-1.0 if self.prefs.gamepad_invert_y else 1.0)
            roll += ("LB" in buttons) - ("RB" in buttons)
            zoom += ("RIGHT" in buttons) - ("LEFT" in buttons)
            if "LS" in buttons:
                boost = 1.0
        inp.move = Vector((clamp(mx, -1.0, 1.0), clamp(my, -1.0, 1.0), clamp(mz, -1.0, 1.0)))
        inp.look_rate[0] = lx
        inp.look_rate[1] = ly
        inp.roll = clamp(roll, -1.0, 1.0)
        inp.zoom = clamp(zoom, -1.0, 1.0)
        inp.boost = boost
        inp.precision = bool(h("precision"))
        inp.hold_zoom = bool(h("hold_zoom"))
        inp.jump = mz > 0.5

    def _poll_gamepad(self):
        pad = self.pad
        if pad is None:
            self.pad_state = None
            return
        st = pad.poll()
        if not pad.connected:
            if self.pad_was_connected:
                self.toast("Gamepad disconnected", kind="WARN")
                self.pad_was_connected = False
            self.pad_state = None
            return
        if not self.pad_was_connected:
            self.pad_was_connected = True
            self.toast("Gamepad connected: %s" % pad.name)
        pr = self.prefs
        lx, ly = gamepad.shape_stick(st.lx, st.ly, pr.gamepad_deadzone, pr.gamepad_expo)
        rx, ry = gamepad.shape_stick(st.rx, st.ry, pr.gamepad_deadzone, pr.gamepad_expo)
        self.pad_state = (lx, ly, rx, ry, gamepad.shape_trigger(st.lt), gamepad.shape_trigger(st.rt), st.buttons)
        for button in pad.pressed:
            action = PAD_PRESS_ACTIONS.get(button)
            if action is not None:
                getattr(self, "_act_" + action)(_SyntheticEvent())

    def _wheel(self, event, n):
        s = self.settings
        if event.ctrl:
            self.inp.zoom_steps += n
        elif event.shift:
            if self.is_camera:
                self.ctrl.focus_t = clamp(self.ctrl.focus_t * (1.15 ** n), 0.001, 1.0e6)
                self._ensure_dof()
                self.toast("Focus %s" % compat.format_length(self.scene, self.ctrl.focus_t), key="focus")
        elif event.alt:
            if self.is_camera:
                dof = self.obj.data.dof
                dof.aperture_fstop = clamp(dof.aperture_fstop * (2.0 ** (-0.25 * n)), 0.1, 128.0)
                self._ensure_dof()
                self.toast("Aperture f/%.1f" % dof.aperture_fstop, key="fstop")
        else:
            s.speed = clamp(s.speed * (1.25 ** n), 0.001, 10000.0)
            self._toast_speed()

    def _toast_speed(self):
        self.toast("Speed %s/s" % compat.format_length(self.scene, self.settings.speed), key="speed")

    # ================================================================ actions
    def _act_record(self, event):
        if self.recorder is not None:
            self.stop_recording()
        elif self.countdown > 0.0:
            self.countdown = 0.0
            self.toast("Countdown cancelled", key="rec")
        elif self.settings.countdown > 0.0:
            self.countdown = self.settings.countdown
            self.toast("Get ready…", key="rec")
        else:
            self._begin_recording()

    def _act_pause(self, event):
        s = self.settings
        if self.recorder is not None:
            self.rec_paused = not self.rec_paused
            if s.timing == "SCENE":
                self._scene_play(not self.rec_paused)
            self.lock_accum = 0.0
            if self.rec_paused:
                self.toast("Recording paused: time is frozen, fly freely · P resumes", key="rec", kind="WARN")
            else:
                self.toast("Recording resumed", key="rec", kind="REC")
            return
        self.running = not self.running
        self.lock_accum = 0.0
        self.prev_clock = self.clock
        if s.timing == "SCENE":
            self._scene_play(self.running)
        if self.running:
            if s.record_mode == "PUNCH" and self._evaluator() is not None:
                self.toast("Playing the take · %s to punch in" % self.bindings.label("record"), key="time")
            else:
                self.toast("Time running · %s to record" % self.bindings.label("record"), key="time")
        else:
            self.toast("Time paused", key="time")

    def _act_keyframe(self, event):
        s = self.settings
        obj = self.obj
        channels = takes.Channels(obj, s.record_lens, s.record_focus)
        take = self.active_take()
        if take is None or take.camera != obj:
            prev_active = self.active_action
            index = takes.new_take(self.scene, obj, channels, source="Keyframes")
            take = s.takes[index]
            self.active_action = take.action
            self.undo_stack.append(("new", take.action, None, None, prev_active))
        frame = int(math.floor(self.clock + 1e-6))
        ev = takes.TakeEvaluator(take, obj)
        has_keys = take.frame_end > take.frame_start or any(len(fc.keyframe_points) for fc, _d in takes.take_fcurves(take))
        prev = ev.raw_rotation(frame) if has_keys else takes.current_rot_values(obj, channels)
        pose = self.last_pose
        loc, q = self._basis(pose)
        sample = takes.Sample(frame, loc, takes.rot_values(q, channels, prev), pose.lens, pose.focus, pose.pos)
        takes.insert_keys(take, obj, sample, channels, s.key_interpolation)
        self.version += 1
        self.made_changes = True
        msg = "Keyframe at frame %d" % frame
        if s.key_step > 0 and self.recorder is None:
            self.clock = float(frame + s.key_step)
            self.prev_clock = self.clock
            self._set_frame(frame + s.key_step)
            msg += " → frame %d" % (frame + s.key_step)
        self.toast(msg, key="key", kind="OK")

    def _act_discard_take(self, event):
        s = self.settings
        if self.recorder is not None:
            rec = self.recorder
            self.recorder = None
            self.rec_target = None
            self.running = False
            if s.timing == "SCENE":
                self._scene_play(False)
            self._rewind(rec)
            self.toast("Recording discarded", key="rec", kind="WARN")
            return
        if not self.undo_stack:
            self.toast("Nothing to discard", key="rec")
            return
        kind, action, backup, dbackup, prev_active = self.undo_stack.pop()
        index = takes.find_take(self.scene, action)
        if index < 0:
            return
        take = s.takes[index]
        name = take.name
        if kind == "new":
            takes.remove_take(self.scene, index)
            self.active_action = prev_active
            self.toast("Discarded %s" % name, key="rec", kind="WARN")
        else:
            old, old_data = take.action, take.data_action
            take.action = backup
            take.data_action = dbackup
            for act, dup in ((old, backup), (old_data, dbackup)):
                if act is None:
                    continue
                act_name = act.name
                bpy.data.actions.remove(act)
                if dup is not None:
                    dup.name = act_name
                    dup.use_fake_user = True
            takes.update_take_range(take)
            self.active_action = backup
            self.toast("Punch-in on %s undone" % name, key="rec", kind="WARN")
        self.version += 1
        self._snap_to_take()

    def _act_slower(self, event):
        self._step_time_scale(-1)

    def _act_faster(self, event):
        self._step_time_scale(1)

    def _step_time_scale(self, direction):
        s = self.settings
        cur = s.time_scale
        if direction > 0:
            nxt = next((v for v in TIME_SCALES if v > cur + 1e-6), TIME_SCALES[-1])
        else:
            nxt = next((v for v in reversed(TIME_SCALES) if v < cur - 1e-6), TIME_SCALES[0])
        s.time_scale = nxt
        text = "Time ×%g" % nxt
        if nxt < 1.0:
            text += " (slow motion)"
        if s.timing == "SCENE":
            text += " · has no effect with Scene Playback timing"
        self.toast(text, key="time")

    def _step_frame(self, delta):
        if self.recorder is not None:
            self.toast("Can't scrub while recording", key="frame", kind="WARN")
            return
        frame = int(math.floor(self.clock + 1e-6)) + delta
        self._jump_to(frame)

    def _jump_to(self, frame):
        self.clock = float(frame)
        self.prev_clock = self.clock
        self._set_frame(frame)
        self._snap_to_take()
        self.toast("Frame %d" % frame, key="frame")

    def _act_frame_back(self, event):
        self._step_frame(-10 if event.shift else -1)

    def _act_frame_fwd(self, event):
        self._step_frame(10 if event.shift else 1)

    def _act_frame_start(self, event):
        if self.recorder is None:
            self._jump_to(compat.scene_range(self.scene)[0])

    def _act_mode(self, event):
        cur = self.settings.mode
        order = MODE_ORDER
        self._set_mode(order[(order.index(cur) + 1) % len(order)] if cur in order else "FREE")

    def _act_orbit(self, event):
        self._set_mode(self.prev_mode if self.settings.mode == "ORBIT" else "ORBIT")

    def _act_walk(self, event):
        self._set_mode(self.prev_mode if self.settings.mode == "WALK" else "WALK")

    def _act_level(self, event):
        self.ctrl.level()
        self.toast("Horizon levelled", key="level")

    def _act_speed_up(self, event):
        s = self.settings
        s.speed = clamp(s.speed * 1.25, 0.001, 10000.0)
        self._toast_speed()

    def _act_speed_down(self, event):
        s = self.settings
        s.speed = clamp(s.speed / 1.25, 0.001, 10000.0)
        self._toast_speed()

    def _act_fly_to(self, event):
        hit = self._crosshair_hit()
        if hit is None:
            self.toast("Nothing under the crosshair", key="fly", kind="WARN")
            return
        loc = Vector(hit[0])
        dist = (loc - self.ctrl.pos).length
        standoff = max(dist * 0.15, self.params.radius * 2.0)
        target = loc - self.ctrl.forward() * standoff
        duration = clamp(dist / max(self.params.speed * 1.5, 1e-3), 0.35, 5.0)
        self.ctrl.fly_to(target, None, None, duration)
        self.toast("Flying to %s" % (hit[2].name if hit[2] is not None else "target"), key="fly")

    def _act_lock(self, event):
        if self.lock is not None:
            self._release_lock()
            self.toast("Lock released", key="lock")
            return
        dg = self._depsgraph()
        target = None
        active = bpy.context.view_layer.objects.active if bpy.context.view_layer else None
        if event.shift and active is not None and active != self.obj:
            target = LockTarget(obj=active, label=active.name)
        else:
            hit = self._crosshair_hit()
            if hit is not None and hit[2] is not None:
                ob = hit[2]
                local = ob.evaluated_get(dg).matrix_world.inverted_safe() @ Vector(hit[0])
                target = LockTarget(obj=ob, local=local, label=ob.name)
            elif hit is not None:
                target = LockTarget(point=hit[0], label="point")
            else:
                target = LockTarget(point=self.scene.cursor.location, label="3D cursor")
        p = target.world_point(dg)
        if p is None:
            return
        target.prev_point = p.copy()
        target.prev_yaw = target.world_yaw(dg)
        self.lock = target
        self.ctrl.lock_offset = [0.0, 0.0]
        if self.ctrl.mode == "ORBIT":
            self.ctrl.pivot = p.copy()
            self.ctrl.sync_orbit()
        else:
            self.ctrl.look_target = p.copy()
        self.toast("Locked on %s · mouse nudges framing" % target.label, key="lock")

    def _act_follow(self, event):
        if self.lock is None or self.lock.obj is None:
            self.toast("Lock onto an object first (%s)" % self.bindings.label("lock"), key="lock", kind="WARN")
            return
        self.follow = not self.follow
        self.toast("Following %s" % self.lock.label if self.follow else "Follow off", key="lock")

    def _act_autofocus(self, event):
        self._autofocus()

    def _act_dolly_zoom(self, event):
        if self.ctrl.dolly is not None:
            self.ctrl.dolly = None
            self.toast("Dolly zoom off", key="dolly")
            return
        if not self.is_camera or self.ctrl.zoom_sign < 0.0:
            self.toast("Dolly zoom needs a perspective camera", key="dolly", kind="WARN")
            return
        if self.lock is not None and self.lock.prev_point is not None:
            subject = self.lock.prev_point
        else:
            hit = self._crosshair_hit()
            subject = Vector(hit[0]) if hit is not None else self.ctrl.pos + self.ctrl.forward() * self.ctrl.focus
        if self.ctrl.start_dolly_zoom(subject):
            self.toast("Dolly zoom locked · move forward/back for the Vertigo effect", key="dolly")

    def _act_snapshot(self, event):
        if not self.is_camera:
            return
        s = self.settings
        s.snapshot_counter += 1
        name = "Shot %03d" % s.snapshot_counter
        data = self.obj.data.copy()
        if data.animation_data is not None:
            data.animation_data_clear()
        pose = self.last_pose
        if data.type == "ORTHO":
            data.ortho_scale = pose.lens
        else:
            data.lens = max(pose.lens, 1.0)
        data.dof.focus_distance = pose.focus
        for flag, value in self._saved.get("guides", {}).items():
            setattr(data, flag, value)
        shot = bpy.data.objects.new(name, data)
        self._shots_collection().objects.link(shot)
        shot.rotation_mode = self.obj.rotation_mode
        shot.matrix_world = Matrix.LocRotScale(pose.pos, pose.rot, self.world_scale)
        self.made_changes = True
        self.toast("Snapshot saved as camera \"%s\"" % name, key="snap", kind="OK")

    def _shots_collection(self):
        scene = self.scene
        coll = bpy.data.collections.get("Flight Shots")
        if coll is None:
            coll = bpy.data.collections.new("Flight Shots")
        if coll not in scene.collection.children_recursive:
            scene.collection.children.link(coll)
        return coll

    def _act_shake(self, event):
        s = self.settings
        s.shake_enabled = not s.shake_enabled
        self.toast("Camera shake on" if s.shake_enabled else "Camera shake off", key="shake")

    def _act_guides(self, event):
        if not self.is_camera:
            return
        self.guide_index = (self.guide_index + 1) % len(GUIDE_SETS)
        name, flags = GUIDE_SETS[self.guide_index]
        cam = self.obj.data
        for flag in GUIDE_FLAGS:
            if hasattr(cam, flag):
                setattr(cam, flag, flag in flags)
        self.toast("Guides: %s" % name, key="guides")

    def _act_hud(self, event):
        self.hud_visible = not self.hud_visible

    def _act_help(self, event):
        self.help_visible = not self.help_visible

    def _act_shading(self, event):
        sp = self.space
        if sp is None:
            return
        cur = sp.shading.type
        nxt = SHADING_CYCLE[(SHADING_CYCLE.index(cur) + 1) % len(SHADING_CYCLE)] if cur in SHADING_CYCLE else "SOLID"
        sp.shading.type = nxt
        self.toast("Shading: %s" % nxt.title(), key="shading")

    def _act_overlays(self, event):
        sp = self.space
        if sp is not None:
            sp.overlay.show_overlays = not sp.overlay.show_overlays

    def _act_mouse(self, event):
        if self.window is None:
            self.captured = not self.captured
            return
        if self.captured:
            self.captured = False
            self.keys_down.clear()
            self.window.cursor_modal_restore()
            self.toast("Mouse released: tweak settings in the sidebar · %s to fly again" % self.bindings.label("mouse"), key="mouse", ttl=4.0)
        else:
            self.captured = True
            self._last_mouse = None
            self.window.cursor_modal_set("NONE")
            self._warp_center()
            self.toast("Mouse captured", key="mouse")

    def _act_finish(self, event):
        self.finished = "FINISH"

    def _act_cancel(self, event):
        self.finished = "CANCEL"

    def _bookmark_key(self, slot, event):
        s = self.settings
        existing = next((b for b in s.bookmarks if b.slot == slot), None)
        if event.ctrl:
            bm = existing
            if bm is None:
                bm = s.bookmarks.add()
                bm.slot = slot
                bm.name = "Bookmark %d" % slot
            bm.location = self.ctrl.pos
            bm.rotation = self.ctrl.base_rotation()
            bm.lens = self.ctrl.lens
            self.made_changes = True
            self.toast("Bookmark %d saved" % slot, key="bookmark", kind="OK")
            return
        if existing is None:
            self.toast("No bookmark on %d · Ctrl+%d saves one" % (slot, slot), key="bookmark", kind="WARN")
            return
        lens = existing.lens if self.is_camera else None
        self.ctrl.fly_to(Vector(existing.location), Quaternion(existing.rotation), lens, s.bookmark_travel_time)
        self.toast("→ %s" % existing.name, key="bookmark")

    # ================================================================ recording
    @property
    def recording(self):
        return self.recorder is not None

    def _begin_recording(self):
        s = self.settings
        obj = self.obj
        channels = takes.Channels(obj, s.record_lens, s.record_focus)
        start = int(math.floor(self.clock + 1e-6))
        self.clock = float(start)
        self._set_frame(start)
        target = None
        prev_rot = takes.current_rot_values(obj, channels)
        if s.record_mode == "PUNCH":
            ev = self._evaluator()
            if ev is None:
                self.toast("No take to punch into, so recording a new take", kind="WARN")
            else:
                target = self.active_action
                prev_rot = ev.raw_rotation(start)
                if self.running:
                    self._drive_from_take(0.0)  # hand over exactly on the take's frame
        pose = self.ctrl.output()
        self._apply_pose(pose)
        self.recorder = takes.Recorder(obj, channels, start, self._basis, prev_rot)
        self.recorder.add(start, pose)
        self.rec_target = target
        self.rec_first_state = (self.ctrl.pos.copy(), self.ctrl.base_rotation(), self.ctrl.lens, self.ctrl.focus)
        self.prev_pose = self.last_pose = pose
        self.prev_clock = self.clock
        self.rec_paused = False
        self.stop_pending = False
        self.lock_accum = 0.0
        self.rec_started = self.real_time
        self.running = True
        if s.timing == "SCENE":
            self._scene_play(True)
        self.toast("Recording%s" % (" (punch-in)" if target is not None else ""), key="rec", kind="REC")

    def stop_recording(self, reason=None):
        rec = self.recorder
        if rec is None:
            return None
        self.recorder = None
        s = self.settings
        self.running = False
        self.stop_pending = False
        if s.timing == "SCENE":
            self._scene_play(False)
        target = self.rec_target
        self.rec_target = None
        samples = rec.samples
        if len(samples) < 2:
            self.toast("Take too short, discarded", key="rec", kind="WARN")
            self._rewind(rec)
            return None
        obj = self.obj
        index = takes.find_take(self.scene, target) if target is not None else -1
        if index >= 0:
            take = s.takes[index]
            backup = compat.copy_action(take.action)
            backup.name = take.action.name + " (before punch)"
            backup.use_fake_user = False
            dbackup = None
            if take.data_action is not None:
                dbackup = compat.copy_action(take.data_action)
                dbackup.use_fake_user = False
            takes.punch_in(take, obj, samples, rec.channels, s.key_interpolation, s.punch_out, s.punch_blend)
            self.undo_stack.append(("punch", take.action, backup, dbackup, None))
            msg = "Punched in frames %d-%d of %s" % (samples[0].frame, samples[-1].frame, take.name)
        else:
            prev_active = self.active_action
            index = takes.record_take(
                self.scene, obj, samples, rec.channels, s.key_interpolation, source=MODE_LABELS.get(s.mode, s.mode)
            )
            take = s.takes[index]
            self.undo_stack.append(("new", take.action, None, None, prev_active))
            self.active_action = take.action
            msg = "%s saved: %d frames" % (take.name, len(samples))
        if s.auto_smooth > 0.0:
            takes.smooth_take(take, s.auto_smooth)
        self.version += 1
        self.made_changes = True
        self.toast(("%s. " % reason if reason else "") + msg, key="rec", kind="OK", ttl=3.5)
        self._rewind(rec)
        return index

    def _rewind(self, rec):
        if not self.settings.rewind_after_take or rec is None:
            return
        self.clock = float(rec.start_frame)
        self.prev_clock = self.clock
        self._set_frame(rec.start_frame)
        if self.rec_first_state is not None:
            pos, rot, lens, focus = self.rec_first_state
            self.ctrl.set_pose(pos, rot, lens, focus)
            pose = self.ctrl.output()
            self._apply_pose(pose)
            self.prev_pose = self.last_pose = pose

    # ================================================================ toasts
    def toast(self, text, key=None, kind="INFO", ttl=2.2):
        if key is not None:
            for t in self.toasts:
                if t.key == key:
                    t.text, t.kind, t.ttl, t.age = text, kind, ttl, 0.0
                    return
        self.toasts.append(Toast(key, text, kind, ttl))
        del self.toasts[:-5]

    def _age_toasts(self, dt):
        for t in self.toasts:
            t.age += dt
        self.toasts = [t for t in self.toasts if t.age < t.ttl]

    # ================================================================ HUD helpers
    def status(self):
        """Compact state summary used by the HUD and the sidebar."""
        s = self.settings
        flags = []
        if s.collisions:
            flags.append("COLLIDE")
        if s.adaptive_speed:
            flags.append("ADAPTIVE")
        if s.shake_enabled:
            flags.append("SHAKE")
        if self._af_active():
            flags.append("AF")
        if self.ctrl.dolly is not None:
            flags.append("DOLLY")
        if self.follow:
            flags.append("FOLLOW")
        if self.ctrl.transition is not None:
            flags.append("AUTO")
        if self.pad_state is not None:
            flags.append("PAD")
        return flags
