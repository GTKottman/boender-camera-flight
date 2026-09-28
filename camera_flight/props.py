"""Scene data: flight settings, recorded takes and camera bookmarks."""

import math

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

MODE_ITEMS = (
    ("FREE", "Free Fly", "Noclip fly camera: W flies wherever you look", "VIEW_CAMERA", 0),
    ("DRONE", "Drone", "Horizon-locked drone: planar thrust, momentum, automatic banking", "ORIENTATION_GIMBAL", 1),
    ("SPACE", "6DOF", "Six degrees of freedom: no horizon, the mouse turns around the camera's own axes", "ORIENTATION_GLOBAL", 2),
    ("WALK", "Walk", "First-person walking with gravity, ground following, jumping and head bob", "OUTLINER_OB_ARMATURE", 3),
    ("ORBIT", "Orbit", "Circle a target point while always looking at it", "PIVOT_CURSOR", 4),
)  # fmt: skip
MODE_ORDER = tuple(item[0] for item in MODE_ITEMS)
MODE_LABELS = {item[0]: item[1] for item in MODE_ITEMS}


def _on_take_index(self, context):
    if not self.auto_activate_takes:
        return
    from . import takes

    takes.activate_take(self.id_data, self.take_index)


def _on_take_name(self, context):
    cam = self.camera
    prefix = (cam.name + " ") if cam else ""
    if self.action is not None:
        self.action.name = prefix + self.name
    if self.data_action is not None:
        self.data_action.name = prefix + self.name + " Lens"


class CAMERA_FLIGHT_Take(PropertyGroup):
    name: StringProperty(name="Name", default="Take", update=_on_take_name)
    action: PointerProperty(name="Action", type=bpy.types.Action, description="Camera object animation")
    data_action: PointerProperty(name="Lens Action", type=bpy.types.Action, description="Lens / focus animation")
    camera: PointerProperty(name="Camera", type=bpy.types.Object)
    frame_start: IntProperty(name="Start")
    frame_end: IntProperty(name="End")
    rating: IntProperty(name="Rating", description="Star rating (circle the good takes)", min=0, max=5)
    note: StringProperty(name="Note")
    created: StringProperty(name="Recorded")
    source: StringProperty(name="Source", description="How the take was made")
    rotation_mode: StringProperty(name="Rotation Mode", description="Rotation channels the take was recorded with")


class CAMERA_FLIGHT_Bookmark(PropertyGroup):
    name: StringProperty(name="Name", default="Bookmark")
    slot: IntProperty(name="Hotkey", description="Number key (1-9) that recalls this bookmark; 0 = none", min=0, max=9)
    location: FloatVectorProperty(name="Location", size=3, subtype="TRANSLATION")
    rotation: FloatVectorProperty(name="Rotation", size=4, subtype="QUATERNION", default=(1.0, 0.0, 0.0, 0.0))
    lens: FloatProperty(name="Lens", default=50.0, min=0.001)


class CAMERA_FLIGHT_Settings(PropertyGroup):
    # Flight --------------------------------------------------------------------
    mode: EnumProperty(name="Flight Mode", items=MODE_ITEMS, default="FREE")
    speed: FloatProperty(
        name="Speed",
        description="Cruise speed (mouse wheel changes it while flying)",
        default=5.0,
        min=0.001,
        soft_max=200.0,
        unit="VELOCITY",
    )
    boost_multiplier: FloatProperty(name="Boost ×", description="Speed multiplier while boosting", default=4.0, min=1.0, soft_max=20.0)
    precision_multiplier: FloatProperty(
        name="Precision ×", description="Speed multiplier while holding the precision key", default=0.2, min=0.01, max=1.0
    )
    acceleration: FloatProperty(
        name="Responsiveness",
        description="How quickly you reach full speed (higher = snappier, lower = more inertia)",
        default=6.0,
        min=0.1,
        soft_max=30.0,
    )
    drag: FloatProperty(
        name="Braking", description="How quickly you coast to a stop when you let go", default=5.0, min=0.1, soft_max=30.0
    )
    look_smoothing: FloatProperty(
        name="Look Smoothing",
        description="Mouse-look smoothing time in seconds (0 = raw, 0.2+ = buttery pans)",
        default=0.06,
        min=0.0,
        soft_max=1.0,
    )
    track_smoothing: FloatProperty(
        name="Tracking Lag",
        description="How lazily the camera follows a locked-on target, in seconds (feels like a real operator)",
        default=0.15,
        min=0.0,
        soft_max=1.5,
    )
    turn_rate: FloatProperty(
        name="Turn Rate", description="Pan/tilt speed for arrow keys", subtype="ANGLE", default=math.radians(90.0), min=0.0
    )
    roll_rate: FloatProperty(name="Roll Rate", subtype="ANGLE", default=math.radians(60.0), min=0.0)
    bank_amount: FloatProperty(
        name="Auto Bank", description="Lean into turns and strafes like a drone or aircraft", default=0.0, min=0.0, max=1.0
    )
    max_bank: FloatProperty(name="Max Bank", subtype="ANGLE", default=math.radians(30.0), min=0.0, max=math.radians(90.0))
    auto_level: BoolProperty(name="Auto Level", description="Return the horizon to level when not rolling", default=False)
    level_time: FloatProperty(name="Level Time", description="Seconds to level out", default=0.5, min=0.01, soft_max=3.0)
    planar_movement: BoolProperty(
        name="Planar Movement", description="Forward/back keeps your altitude, like a dolly on a track", default=False
    )
    adaptive_speed: BoolProperty(
        name="Adaptive Speed", description="Automatically slow down as you approach whatever you're looking at", default=False
    )
    collisions: BoolProperty(name="Collisions", description="Bump into and slide along scene geometry", default=False)
    collision_radius: FloatProperty(name="Radius", default=0.3, min=0.0, soft_max=5.0, unit="LENGTH")
    follow_rotation: BoolProperty(
        name="Follow Rotation",
        description="When following a target, also turn with it (chase-cam)",
        default=False,
    )

    # Walk ------------------------------------------------------------------------
    gravity: FloatProperty(name="Gravity", default=9.81, min=0.0, soft_max=50.0, unit="ACCELERATION")
    eye_height: FloatProperty(name="Eye Height", default=1.7, min=0.01, soft_max=10.0, unit="LENGTH")
    jump_speed: FloatProperty(name="Jump", default=4.5, min=0.0, soft_max=30.0, unit="VELOCITY")
    head_bob: FloatProperty(name="Head Bob", default=0.5, min=0.0, max=2.0)

    # Lens ------------------------------------------------------------------------
    zoom_rate: FloatProperty(name="Zoom Speed", description="Zoom speed for held zoom keys", default=1.0, min=0.01, soft_max=5.0)
    zoom_smoothing: FloatProperty(name="Zoom Smoothing", description="Seconds", default=0.12, min=0.0, soft_max=1.0)
    hold_zoom_factor: FloatProperty(
        name="Hold-Zoom ×", description="Magnification while holding the hold-to-zoom button", default=2.0, min=1.0, soft_max=10.0
    )
    focus_smoothing: FloatProperty(name="Focus Pull Time", description="Seconds for autofocus to settle", default=0.2, min=0.0, soft_max=2.0)
    continuous_af: BoolProperty(
        name="Continuous Autofocus", description="Keep focusing on whatever is under the crosshair", default=False
    )

    # Shake -----------------------------------------------------------------------
    shake_enabled: BoolProperty(name="Camera Shake", default=False)
    shake_intensity: FloatProperty(name="Intensity", default=1.0, min=0.0, soft_max=5.0)
    shake_frequency: FloatProperty(name="Frequency", description="Hz", default=1.1, min=0.01, soft_max=20.0)
    shake_rotation: FloatProperty(name="Rotation", subtype="ANGLE", default=math.radians(0.5), min=0.0, soft_max=math.radians(10.0))
    shake_position: FloatProperty(name="Position", default=0.006, min=0.0, soft_max=1.0, unit="LENGTH")

    # Recording -------------------------------------------------------------------
    record_mode: EnumProperty(
        name="Record Mode",
        items=(
            ("NEW", "New Take", "Every recording creates a new take, starting at the current frame", "ADD", 0),
            ("PUNCH", "Punch In", "Play back the active take and press Record to take over and re-fly from that moment", "REC", 1),
        ),
        default="NEW",
    )
    timing: EnumProperty(
        name="Timing",
        items=(
            ("REALTIME", "Real Time", "Scene time follows the wall clock (scaled by Time Scale); frames are interpolated", 0),
            ("FRAME_LOCKED", "Every Frame", "Advance exactly one frame per update, never skipping (for heavy scenes)", 1),
            ("SCENE", "Scene Playback", "Use Blender's own playback, including audio sync (Time Scale is ignored)", 2),
        ),
        default="REALTIME",
    )
    time_scale: FloatProperty(
        name="Time Scale",
        description="Slow motion while recording: 0.5 = scene plays at half speed so you can fly with more precision",
        default=1.0,
        min=0.05,
        max=4.0,
    )
    countdown: FloatProperty(name="Countdown", description="Seconds of countdown before recording starts", default=0.0, min=0.0, max=10.0)
    stop_at_end: BoolProperty(name="Stop at End Frame", default=True)
    rewind_after_take: BoolProperty(
        name="Rewind After Take",
        description="After recording, jump back to where the take started (frame and camera) for a quick retake",
        default=True,
    )
    record_lens: BoolProperty(name="Record Zoom", default=True)
    record_focus: BoolProperty(name="Record Focus", default=True)
    key_interpolation: EnumProperty(
        name="Interpolation",
        items=(("BEZIER", "Bezier", "Smooth"), ("LINEAR", "Linear", "Exact")),
        default="BEZIER",
    )
    auto_smooth: FloatProperty(
        name="Auto Smooth", description="Smooth every new take (Gaussian sigma in frames, 0 = off)", default=0.0, min=0.0, soft_max=10.0
    )
    key_step: IntProperty(
        name="Key Step",
        description="After inserting a manual keyframe, advance this many frames (stop-motion style; 0 = stay)",
        default=0,
        min=0,
        soft_max=240,
    )
    punch_out: EnumProperty(
        name="Punch Out",
        items=(
            ("TRUNCATE", "Replace Rest", "Everything after the punch-in point is replaced by the new flight"),
            ("KEEP", "Keep Rest", "Only the re-flown range is replaced; the old take resumes afterwards (blended)"),
        ),
        default="TRUNCATE",
    )
    punch_blend: IntProperty(
        name="Blend Frames", description="Frames used to blend back into the old take when punching out", default=12, min=0, soft_max=96
    )
    bookmark_travel_time: FloatProperty(
        name="Travel Time", description="Seconds to glide to a bookmark (0 = teleport)", default=1.5, min=0.0, soft_max=10.0
    )
    start_from: EnumProperty(
        name="Start From",
        items=(
            ("AUTO", "Auto", "Start from the viewport view, unless you're already looking through the camera"),
            ("CAMERA", "Camera", "Always start from the camera's current position"),
            ("VIEW", "View", "Always move the camera to the current viewport view first"),
        ),
        default="AUTO",
    )
    auto_activate_takes: BoolProperty(
        name="Activate on Select", description="Selecting a take in the list assigns it to its camera", default=True
    )

    # Data ------------------------------------------------------------------------
    takes: CollectionProperty(type=CAMERA_FLIGHT_Take)
    take_index: IntProperty(name="Active Take", default=0, update=_on_take_index)
    take_counter: IntProperty(default=0)
    bookmarks: CollectionProperty(type=CAMERA_FLIGHT_Bookmark)
    bookmark_index: IntProperty(name="Active Bookmark", default=0)
    snapshot_counter: IntProperty(default=0)


classes = (CAMERA_FLIGHT_Take, CAMERA_FLIGHT_Bookmark, CAMERA_FLIGHT_Settings)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.camera_flight = PointerProperty(type=CAMERA_FLIGHT_Settings)


def unregister():
    del bpy.types.Scene.camera_flight
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
