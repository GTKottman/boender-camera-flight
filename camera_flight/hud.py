"""Heads-up display drawn over the flight viewport (gpu + blf)."""

import math
import traceback

import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import compat, keymap, takes
from .core.mathx import forward_from_angles
from .props import MODE_LABELS

FONT = 0
COL_TEXT = (1.0, 1.0, 1.0, 0.94)
COL_DIM = (1.0, 1.0, 1.0, 0.58)
COL_BG = (0.02, 0.03, 0.05, 0.58)
COL_REC = (1.0, 0.24, 0.22, 1.0)
COL_WARN = (1.0, 0.74, 0.22, 1.0)
COL_OK = (0.46, 0.96, 0.56, 1.0)
KIND_COLORS = {"INFO": COL_TEXT, "WARN": COL_WARN, "OK": COL_OK, "REC": COL_REC}

_shaders = {}
_reported = set()


# --------------------------------------------------------------------------- primitives
def _shader(name):
    sh = _shaders.get(name)
    if sh is None:
        sh = _shaders[name] = gpu.shader.from_builtin(name)
    return sh


def _with_alpha(color, alpha):
    return (color[0], color[1], color[2], color[3] * alpha)


def _rect(x, y, w, h, color):
    sh = _shader("UNIFORM_COLOR")
    coords = ((x, y, 0.0), (x + w, y, 0.0), (x + w, y + h, 0.0), (x, y + h, 0.0))
    batch = batch_for_shader(sh, "TRIS", {"pos": coords}, indices=((0, 1, 2), (0, 2, 3)))
    sh.bind()
    sh.uniform_float("color", color)
    batch.draw(sh)


def _lines(coords, color, width=1.5, strip=False):
    if len(coords) < 2:
        return
    coords = [(c[0], c[1], c[2] if len(c) > 2 else 0.0) for c in coords]
    sh = _shader("POLYLINE_UNIFORM_COLOR")
    batch = batch_for_shader(sh, "LINE_STRIP" if strip else "LINES", {"pos": coords})
    sh.bind()
    sh.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    sh.uniform_float("lineWidth", width)
    sh.uniform_float("color", color)
    batch.draw(sh)


def _outline(x, y, w, h, color, width=1.5):
    _lines(((x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)), color, width, strip=True)


def _circle(cx, cy, r, color, width=1.5, segments=24):
    pts = [(cx + r * math.cos(i * math.tau / segments), cy + r * math.sin(i * math.tau / segments)) for i in range(segments + 1)]
    _lines(pts, color, width, strip=True)


def _dot(cx, cy, r, color):
    _rect(cx - r, cy - r, 2 * r, 2 * r, color)


def _text(x, y, text, size, color, align="LEFT", shadow=True):
    blf.size(FONT, size)
    if align != "LEFT":
        w = blf.dimensions(FONT, text)[0]
        x -= w if align == "RIGHT" else w * 0.5
    if shadow:
        blf.enable(FONT, blf.SHADOW)
        blf.shadow(FONT, 3, 0.0, 0.0, 0.0, 0.85)
        blf.shadow_offset(FONT, 1, -1)
    blf.color(FONT, *color)
    blf.position(FONT, x, y, 0.0)
    blf.draw(FONT, text)
    if shadow:
        blf.disable(FONT, blf.SHADOW)


def _text_width(text, size):
    blf.size(FONT, size)
    return blf.dimensions(FONT, text)[0]


# --------------------------------------------------------------------------- helpers
def _is_our_region(session):
    region = bpy.context.region
    return region is not None and region.as_pointer() == session.region_ptr


def _report_once(tag):
    if tag not in _reported:
        _reported.add(tag)
        traceback.print_exc()


def _insets(session):
    """Space taken by overlapping toolbar / sidebar / headers."""
    left = right = top = 0
    area = session.area
    if area is None:
        return left, right, top
    overlap = bpy.context.preferences.system.use_region_overlap
    for r in area.regions:
        if r.width <= 1 or r.height <= 1:
            continue
        if r.type in ("HEADER", "TOOL_HEADER") and r.alignment == "TOP":
            top += r.height if overlap else 0
        elif overlap and r.type == "TOOLS":
            left = max(left, r.width)
        elif overlap and r.type == "UI":
            right = max(right, r.width)
    return left, right, top


def _project(session, region, co):
    return location_3d_to_region_2d(region, session.rv3d, co)


def camera_frame(session, region):
    """Camera border in region pixels, or None when not looking through the camera."""
    rv3d, pose = session.rv3d, session.last_pose
    if rv3d is None or pose is None or not session.is_camera or rv3d.view_perspective != "CAMERA":
        return None
    try:
        corners = session.obj.data.view_frame(scene=session.scene)
    except (RuntimeError, ReferenceError):
        return None
    m = pose.matrix()
    pts = [_project(session, region, m @ c) for c in corners]
    if any(p is None for p in pts):
        return None
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _accent(session, alpha=1.0):
    c = session.prefs.hud_color
    return (c[0], c[1], c[2], c[3] * alpha)


# --------------------------------------------------------------------------- 2D HUD
def draw_2d(session):
    try:
        if not _is_our_region(session):
            return
        _draw_2d(session, bpy.context.region)
    except Exception:  # noqa: BLE001 - never break drawing
        _report_once("2d")
    finally:
        gpu.state.blend_set("NONE")


def _draw_2d(session, region):
    prefs = session.prefs
    ui = (bpy.context.preferences.system.ui_scale or 1.0) * prefs.hud_scale
    gpu.state.blend_set("ALPHA")
    frame = camera_frame(session, region)
    if frame is not None:
        cx, cy = (frame[0] + frame[2]) * 0.5, (frame[1] + frame[3]) * 0.5
    else:
        cx, cy = region.width * 0.5, region.height * 0.5
    hud_on = session.hud_visible and prefs.show_hud

    if session.recorder is not None and not session.rec_paused:
        box = frame or (2, 2, region.width - 2, region.height - 2)
        pulse = 0.55 + 0.45 * math.sin(session.real_time * 5.0)
        _outline(box[0] + 1, box[1] + 1, box[2] - box[0] - 2, box[3] - box[1] - 2, _with_alpha(COL_REC, 0.35 + 0.4 * pulse), 2.0)

    if hud_on:
        if prefs.show_horizon and session.last_pose is not None and session.rv3d is not None:
            _draw_horizon(session, region, ui)
        if prefs.show_crosshair:
            _draw_crosshair(session, cx, cy, ui)
        if session.rv3d is not None:
            if prefs.show_bookmarks:
                _draw_bookmarks(session, region, ui)
            if prefs.show_ghost:
                _draw_ghost(session, region, ui)
            _draw_lock(session, region, ui)
        _draw_stats(session, region, ui)
        if prefs.show_hints:
            _draw_hints(session, region, ui)

    _draw_transport(session, region, ui)
    if session.countdown > 0.0:
        n = int(math.ceil(session.countdown))
        size = int(110 * ui)
        _text(cx, cy - size * 0.35, str(n), size, _with_alpha(COL_REC, 0.9), align="CENTER")
    _draw_toasts(session, region, ui)
    if not session.captured:
        _draw_banner(session, region, ui, "MOUSE RELEASED · %s to fly" % session.bindings.label("mouse"), COL_WARN)
    if session.help_visible:
        _draw_help(session, region, ui)


def _draw_crosshair(session, cx, cy, ui):
    col = _accent(session, 0.85)
    r = 9.0 * ui
    g = 3.5 * ui
    _lines(
        ((cx - r, cy), (cx - g, cy), (cx + g, cy), (cx + r, cy), (cx, cy - r), (cx, cy - g), (cx, cy + g), (cx, cy + r)),
        col,
        1.5,
    )
    _dot(cx, cy, 1.0 * ui, col)


def _draw_horizon(session, region, ui):
    pose = session.last_pose
    fwd = pose.forward()
    heading = math.atan2(-fwd.x, fwd.y)
    pos = pose.pos
    far = 50.0

    def pt(yaw_off, elev):
        d = forward_from_angles(heading + math.radians(yaw_off), math.radians(elev))
        return _project(session, region, pos + d * far)

    col = _accent(session, 0.55)
    dim = _accent(session, 0.32)
    for lo, hi in ((-26.0, -7.0), (7.0, 26.0)):
        a, b = pt(lo, 0.0), pt(hi, 0.0)
        if a is not None and b is not None:
            _lines(((a.x, a.y), (b.x, b.y)), col, 2.0)
    size = int(10 * ui)
    for elev in (-30, -20, -10, 10, 20, 30):
        for lo, hi, side in ((-9.0, -5.0, "RIGHT"), (5.0, 9.0, "LEFT")):
            a, b = pt(lo, elev), pt(hi, elev)
            if a is None or b is None:
                continue
            _lines(((a.x, a.y), (b.x, b.y)), dim, 1.2)
            anchor = a if side == "RIGHT" else b
            offset = -4 * ui if side == "RIGHT" else 4 * ui
            _text(anchor.x + offset, anchor.y - size * 0.35, str(abs(elev)), size, _with_alpha(COL_DIM, 0.7), align=side)


def _diamond(x, y, r, color, width=1.5):
    _lines(((x, y + r), (x + r, y), (x, y - r), (x - r, y), (x, y + r)), color, width, strip=True)


def _in_front(session, co):
    pose = session.last_pose
    return pose is not None and (Vector(co) - pose.pos).dot(pose.forward()) > 0.0


def _draw_bookmarks(session, region, ui):
    col = _accent(session, 0.8)
    size = int(11 * ui)
    for bm in session.settings.bookmarks:
        if not _in_front(session, bm.location):
            continue
        p = _project(session, region, Vector(bm.location))
        if p is None:
            continue
        _diamond(p.x, p.y, 6 * ui, col)
        label = str(bm.slot) if bm.slot else bm.name
        dist = (Vector(bm.location) - session.last_pose.pos).length
        _text(p.x + 9 * ui, p.y + 4 * ui, label, size, col)
        _text(p.x + 9 * ui, p.y - 9 * ui, compat.format_length(session.scene, dist, 1), int(9 * ui), COL_DIM)


def _draw_ghost(session, region, ui):
    if session.recorder is None and session._following_take():
        return
    result = session._take_pose(session.clock)
    if result is None:
        return
    pose, _ev = result
    if not _in_front(session, pose.pos):
        return
    p = _project(session, region, pose.pos)
    if p is None:
        return
    col = (0.85, 0.55, 1.0, 0.85)
    _circle(p.x, p.y, 7 * ui, col, 1.5)
    tip = _project(session, region, pose.pos + pose.forward() * max(0.5, (pose.pos - session.last_pose.pos).length * 0.08))
    if tip is not None:
        _lines(((p.x, p.y), (tip.x, tip.y)), col, 1.5)
    _text(p.x + 10 * ui, p.y + 4 * ui, "ghost", int(10 * ui), col)


def _draw_lock(session, region, ui):
    lock = session.lock
    if lock is None or lock.prev_point is None or not _in_front(session, lock.prev_point):
        return
    p = _project(session, region, lock.prev_point)
    if p is None:
        return
    col = _accent(session, 0.95)
    r = 16 * ui
    k = 6 * ui
    segs = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = p.x + sx * r, p.y + sy * r
            segs += [(x, y), (x - sx * k, y), (x, y), (x, y - sy * k)]
    _lines(segs, col, 2.0)
    label = lock.label + ("  · FOLLOW" if session.follow else "")
    _text(p.x, p.y - r - 14 * ui, label, int(11 * ui), col, align="CENTER")


def _draw_stats(session, region, ui):
    s = session.settings
    ctrl = session.ctrl
    scene = session.scene
    left, _right, top = _insets(session)
    size = int(12 * ui)
    line = size * 1.55
    x = left + 16 * ui
    y = region.height - top - 26 * ui

    rows = []
    speed_now = ctrl.speed()
    rows.append(("Mode", MODE_LABELS.get(ctrl.mode, ctrl.mode)))
    rows.append(("Speed", "%s/s" % compat.format_length(scene, s.speed, 2)))
    if session.is_camera:
        cam = session.obj.data
        lens = session.last_pose.lens if session.last_pose is not None else ctrl.lens
        lens_txt = ("scale %.2f" % lens) if cam.type == "ORTHO" else ("%.1f mm" % lens)
        if cam.dof.use_dof:
            lens_txt += "  f/%.1f  focus %s" % (cam.dof.aperture_fstop, compat.format_length(scene, ctrl.focus, 2))
        rows.append(("Lens", lens_txt))
    frame = int(math.floor(session.clock + 1e-6))
    time_txt = "%d  %s" % (frame, compat.timecode(scene, frame))
    if s.timing != "SCENE" and abs(s.time_scale - 1.0) > 1e-6:
        time_txt += "  ×%g" % s.time_scale
    rows.append(("Frame", time_txt))
    if abs(ctrl.roll + ctrl.bank) > math.radians(0.5) and ctrl.mode != "SPACE":
        rows.append(("Roll", "%+.1f°" % math.degrees(ctrl.roll + ctrl.bank)))
    take = session.active_take()
    if take is not None:
        rows.append(("Take", take.name + ("  " + "★" * take.rating if take.rating else "")))
    flags = session.status()
    if session.lock is not None:
        flags.insert(0, "LOCK")

    label_w = max(_text_width(r[0], size) for r in rows) + 12 * ui
    value_w = max(_text_width(r[1], size) for r in rows)
    width = label_w + max(value_w, 150 * ui) + 20 * ui
    height = line * (len(rows) + 1) + (line if flags else 0) + 10 * ui
    _rect(x - 10 * ui, y - height + line + 2 * ui, width, height, COL_BG)
    _rect(x - 10 * ui, y - height + line + 2 * ui, 3 * ui, height, _accent(session, 0.9))

    _text(x, y, "CAMERA FLIGHT", int(10 * ui), _accent(session, 0.95))
    y -= line
    for label, value in rows:
        _text(x, y, label, size, COL_DIM)
        _text(x + label_w, y, value, size, COL_TEXT)
        if label == "Speed":
            bar_x = x + label_w + _text_width(value, size) + 10 * ui
            bar_w = 60 * ui
            limit = max(ctrl.speed_limit, s.speed, 1e-6)
            fill = min(speed_now / (limit * 1.0001), 1.0)
            _rect(bar_x, y + 2 * ui, bar_w, 5 * ui, (1, 1, 1, 0.15))
            _rect(bar_x, y + 2 * ui, bar_w * fill, 5 * ui, _accent(session, 0.9))
        y -= line
    if flags:
        _text(x, y, "  ".join(flags), int(10 * ui), _accent(session, 0.85))


def _draw_transport(session, region, ui):
    """REC / playback indicator at the top center."""
    s = session.settings
    _left, _right, top = _insets(session)
    cx = region.width * 0.5
    y = region.height - top - 30 * ui
    size = int(13 * ui)
    rec = session.recorder
    if rec is not None:
        frames = rec.last_frame - rec.start_frame
        target = takes.take_by_action(session.scene, session.rec_target) if session.rec_target else None
        label = "PUNCH-IN %s" % target.name if target is not None else "NEW TAKE"
        if session.rec_paused:
            text = "PAUSED  %s  ·  %d fr  ·  %s" % (compat.timecode(session.scene, frames), frames, label)
            color = COL_WARN
        else:
            text = "REC  %s  ·  %d fr  ·  %s" % (compat.timecode(session.scene, frames), frames, label)
            color = COL_REC
    elif session.countdown > 0.0:
        text, color = "GET READY", COL_REC
    elif session.running:
        if session._following_take():
            text = "▶ TAKE PLAYBACK  ·  %s to punch in" % session.bindings.label("record")
        else:
            text = "▶ TIME RUNNING  ·  %s to record" % session.bindings.label("record")
        color = COL_TEXT
    else:
        return
    w = _text_width(text, size) + 44 * ui
    h = size + 14 * ui
    _rect(cx - w * 0.5, y - 7 * ui, w, h, COL_BG)
    if rec is not None and not session.rec_paused:
        alpha = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(session.real_time * 6.0))
        _dot(cx - w * 0.5 + 16 * ui, y + size * 0.35, 5 * ui, _with_alpha(COL_REC, alpha))
    _text(cx + 8 * ui, y, text, size, color, align="CENTER")
    if s.timing == "FRAME_LOCKED" and (rec is not None or session.running):
        _text(cx, y - 18 * ui, "every-frame timing", int(9 * ui), COL_DIM, align="CENTER")


def _draw_toasts(session, region, ui):
    size = int(13 * ui)
    y = 70 * ui
    for t in reversed(session.toasts):
        fade = min(1.0, (t.ttl - t.age) / 0.4, t.age / 0.12 + 0.2)
        color = _with_alpha(KIND_COLORS.get(t.kind, COL_TEXT), max(0.0, fade))
        w = _text_width(t.text, size) + 28 * ui
        _rect(region.width * 0.5 - w * 0.5, y - 7 * ui, w, size + 14 * ui, _with_alpha(COL_BG, max(0.0, fade)))
        _text(region.width * 0.5, y, t.text, size, color, align="CENTER")
        y += size + 20 * ui


def _draw_banner(session, region, ui, text, color):
    size = int(14 * ui)
    w = _text_width(text, size) + 36 * ui
    y = region.height * 0.5 - 60 * ui
    _rect(region.width * 0.5 - w * 0.5, y - 9 * ui, w, size + 18 * ui, COL_BG)
    _text(region.width * 0.5, y, text, size, color, align="CENTER")


def _draw_hints(session, region, ui):
    b = session.bindings
    hints = (
        "%s move" % "".join(b.first(a) for a in ("forward", "left", "back", "right")),
        "%s/%s sink/rise" % (b.first("down"), b.first("up")),
        "%s boost" % b.first("boost"),
        "wheel speed",
        "%s record" % b.first("record"),
        "%s play" % b.first("pause"),
        "%s help" % b.first("help"),
        "%s finish" % b.first("finish"),
        "%s cancel" % b.first("cancel"),
    )
    left, _right, _top = _insets(session)
    _text(left + 16 * ui, 16 * ui, "   ·   ".join(hints), int(10 * ui), COL_DIM)


def _draw_help(session, region, ui):
    b = session.bindings
    size = int(12 * ui)
    line = size * 1.45
    columns = (("Move", "Look"), ("Lens", "Mode", "Tools", "Exit"), ("Record", "View"))
    extra = {
        "Move": (("Wheel", "Speed"),),
        "Lens": (("Ctrl+Wheel", "Zoom"), ("Shift+Wheel", "Focus distance"), ("Alt+Wheel", "Aperture")),
        "Tools": (("1-9", "Fly to bookmark"), ("Ctrl+1-9", "Save bookmark")),
    }
    col_w = 280 * ui
    total_w = col_w * len(columns)
    rows_per_col = []
    for groups in columns:
        n = 0
        for g in groups:
            n += 1 + sum(1 for a in keymap.ACTIONS if a.group == g) + len(extra.get(g, ())) + 0.4
        rows_per_col.append(n)
    height = line * (max(rows_per_col) + 2)
    x0 = region.width * 0.5 - total_w * 0.5
    y0 = region.height * 0.5 + height * 0.5
    _rect(x0 - 20 * ui, y0 - height, total_w + 40 * ui, height + 16 * ui, (0.01, 0.015, 0.03, 0.86))
    _text(x0, y0 - line * 0.6, "CAMERA FLIGHT CONTROLS", int(14 * ui), _accent(session))
    _text(x0 + total_w, y0 - line * 0.6, "%s to close" % b.label("help"), size, COL_DIM, align="RIGHT")
    key_w = 105 * ui
    for ci, groups in enumerate(columns):
        x = x0 + ci * col_w
        y = y0 - line * 2.0
        for g in groups:
            _text(x, y, g.upper(), int(10 * ui), _accent(session, 0.9))
            y -= line
            for a in keymap.ACTIONS:
                if a.group != g:
                    continue
                _text(x + key_w - 10 * ui, y, b.label(a.id), size, COL_TEXT, align="RIGHT")
                _text(x + key_w, y, a.label, size, COL_DIM)
                y -= line
            for key, label in extra.get(g, ()):
                _text(x + key_w - 10 * ui, y, key, size, COL_TEXT, align="RIGHT")
                _text(x + key_w, y, label, size, COL_DIM)
                y -= line
            y -= line * 0.4


# --------------------------------------------------------------------------- 3D
def draw_3d(session):
    try:
        if not _is_our_region(session):
            return
        prefs = session.prefs
        if not (session.hud_visible and prefs.show_hud and prefs.show_path):
            return
        gpu.state.blend_set("ALPHA")
        pts = _take_path(session)
        if pts:
            _lines(pts, _accent(session, 0.55), 2.0, strip=True)
        rec = session.recorder
        if rec is not None and len(rec.samples) > 1:
            _lines([smp.world for smp in rec.samples[-900:]], _with_alpha(COL_REC, 0.8), 2.0, strip=True)
    except Exception:  # noqa: BLE001 - never break drawing
        _report_once("3d")
    finally:
        gpu.state.blend_set("NONE")


def _take_path(session):
    action = session.active_action
    if action is None:
        return None
    try:
        key = (action.as_pointer(), session.version)
    except ReferenceError:
        return None
    cache = getattr(session, "_path_cache", None)
    if cache is not None and cache[0] == key:
        return cache[1]
    ev = session._evaluator()
    pts = None
    if ev is not None and ev.frame_end > ev.frame_start:
        step = max(1, (ev.frame_end - ev.frame_start) // 1500)
        pts = [tuple(p) for p in ev.path(step, session._parent_matrix())]
    session._path_cache = (key, pts)
    return pts
