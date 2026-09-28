"""Handling and camera-shake presets (values are scene setting names)."""

from math import radians

HANDLING = {
    "SNAPPY": (
        "Snappy (Game)",
        "Instant, precise response like a shooter's noclip camera",
        dict(mode="FREE", acceleration=14.0, drag=12.0, look_smoothing=0.0, track_smoothing=0.08,
             bank_amount=0.0, auto_level=False, planar_movement=False, turn_rate=radians(120.0),
             roll_rate=radians(90.0)),
    ),
    "SMOOTH": (
        "Smooth (Default)",
        "Balanced feel with a touch of inertia",
        dict(mode="FREE", acceleration=6.0, drag=5.0, look_smoothing=0.06, track_smoothing=0.15,
             bank_amount=0.0, auto_level=False, planar_movement=False, turn_rate=radians(90.0),
             roll_rate=radians(60.0)),
    ),
    "CINEMATIC": (
        "Cinematic Glide",
        "Heavy, floaty moves that ease in and out on their own",
        dict(mode="FREE", acceleration=2.2, drag=1.6, look_smoothing=0.22, track_smoothing=0.35,
             bank_amount=0.0, auto_level=True, planar_movement=True, turn_rate=radians(45.0),
             roll_rate=radians(30.0)),
    ),
    "DRONE": (
        "Drone",
        "Horizon-locked drone with momentum and automatic banking",
        dict(mode="DRONE", acceleration=1.8, drag=1.1, look_smoothing=0.25, track_smoothing=0.3,
             bank_amount=0.6, auto_level=True, turn_rate=radians(70.0), roll_rate=radians(45.0)),
    ),
    "FPV": (
        "FPV Racer",
        "Full 6DOF acro flight: fast, twitchy, no auto-level",
        dict(mode="SPACE", acceleration=7.0, drag=2.0, look_smoothing=0.02, track_smoothing=0.1,
             bank_amount=0.0, auto_level=False, turn_rate=radians(180.0), roll_rate=radians(220.0)),
    ),
    "CRANE": (
        "Crane / Dolly",
        "Slow, deliberate and perfectly level",
        dict(mode="FREE", acceleration=1.2, drag=1.4, look_smoothing=0.4, track_smoothing=0.5,
             bank_amount=0.0, auto_level=True, planar_movement=True, turn_rate=radians(25.0),
             roll_rate=radians(15.0)),
    ),
    "HANDHELD": (
        "Handheld Walk",
        "A camera operator on foot: gravity, head bob and subtle shake",
        dict(mode="WALK", acceleration=5.0, drag=6.0, look_smoothing=0.12, track_smoothing=0.2,
             bank_amount=0.0, auto_level=True, head_bob=0.6, shake_enabled=True, shake_intensity=1.0,
             shake_frequency=1.1, shake_rotation=radians(0.5), shake_position=0.006),
    ),
}  # fmt: skip

SHAKE = {
    "HANDHELD": ("Handheld", dict(shake_frequency=1.1, shake_rotation=radians(0.5), shake_position=0.006)),
    "WALKING": ("Walking", dict(shake_frequency=1.8, shake_rotation=radians(0.9), shake_position=0.02)),
    "VEHICLE": ("Vehicle", dict(shake_frequency=6.0, shake_rotation=radians(0.35), shake_position=0.01)),
    "WIND": ("Drone in Wind", dict(shake_frequency=0.45, shake_rotation=radians(0.8), shake_position=0.05)),
    "QUAKE": ("Earthquake", dict(shake_frequency=9.0, shake_rotation=radians(1.8), shake_position=0.08)),
}

HANDLING_ITEMS = tuple((key, label, desc) for key, (label, desc, _v) in HANDLING.items())
SHAKE_ITEMS = tuple((key, label, "") for key, (label, _v) in SHAKE.items())


def apply(settings, values):
    for name, value in values.items():
        setattr(settings, name, value)
