# Boender Camera Flight

**Fly Blender's camera like a game.** WASD + mouse (or a gamepad) with the kind of
tuned, smooth handling you get from a good game's free-cam, and a record button you
can hit *mid-flight* to capture the move as a keyframed take.

Works in **Blender 4.2 LTS → 5.x** as an extension (or a legacy add-on).

---

## Highlights

- **Game controls**: WASD / Q-E / mouse-look, Shift boost, Ctrl precision, wheel for speed,
  fully remappable keys, optional **gamepad** with deadzone and expo curves.
- **Five flight modes**: Free Fly (noclip), Drone (momentum + auto-bank), 6DOF (true roll,
  loops), Walk (gravity, jumping, head bob) and Orbit (circle any target).
- **Record any time**: press **R** mid-flight and the move is captured from that frame on.
  Every recording is a **take** you can rate, compare, edit, bake to its own camera or turn
  into a curve.
- **Punch-in re-recording**: play a take back, press R at the moment it goes wrong, and
  re-fly just that part. The hand-off is seamless (same pose, same velocity) and the
  rest of the take can be kept and blended back in.
- **Slow-motion recording**: run the scene at ¼ or ½ speed while you fly for surgical
  precision; the result plays back at full speed.
- **Operator tools**: lock-on tracking with lazy "operator" lag, follow/chase a moving
  object, dolly zoom (Vertigo), autofocus with focus pulls, hold-to-zoom, procedural
  camera shake, composition guides, bookmarks with smooth glides, fly-to-crosshair,
  collisions, adaptive speed, snapshot cameras.
- **HUD**: crosshair, artificial horizon + pitch ladder, speed bar, lens/focus/frame/
  timecode readout, REC indicator, lock-on reticle, bookmark waypoints, a **ghost** of
  the previous take to race against, and the take's flight path drawn in 3D.

## Install

1. Download (or build) `camera_flight-1.0.0.zip`:
   `python build.py` writes it to `dist/`.
2. Blender 4.2+: **Edit ▸ Preferences ▸ Get Extensions ▸ ⌄ ▸ Install from Disk…** and pick the zip.
   (It also installs as a legacy add-on via **Add-ons ▸ Install…**.)
3. In the 3D Viewport press **N** and open the **Flight** tab.

## Quick start

1. In a 3D Viewport press **Shift F** (or click **Start Flight** in the Flight tab).
   You start from what you're looking at; the scene camera is created if there isn't one.
2. Fly: **WASD** to move, mouse to look, **E/Q** up/down, **Shift** to boost, wheel to change speed.
3. When the shot looks good, press **R**. You're recording (red frame + REC).
   Press **R** again to stop. The take is saved and you're rewound to its start, ready for another go.
4. Press **Enter** to finish. The last take drives the camera; scrub the timeline to review.
   (**Esc** cancels and puts the camera back where it was, but keeps any takes.)

Press **F1** while flying for the full on-screen control sheet.

## Controls

All keys can be remapped in *Preferences ▸ Add-ons ▸ Camera Flight ▸ Key Bindings*
(click a field and press the new key).

| Move | | Look | |
|---|---|---|---|
| W A S D | move / strafe | Mouse | look |
| E / Space | rise (jump in Walk) | ← → ↑ ↓ | smooth pan / tilt |
| Q | sink | Z / C | roll left / right |
| Shift | boost | X | level the horizon |
| Ctrl | precision (slow) | L | lock on to what's under the crosshair (Shift+L: active object) |
| Wheel, PgUp/PgDn | speed | Y | follow the locked target |
| T | fly to the crosshair | | |

| Lens | | Record & time | |
|---|---|---|---|
| Ctrl+Wheel, = / - | zoom | R | record / stop |
| RMB (hold) | hold-to-zoom | P | play/pause time (while recording: pause) |
| F | autofocus (hold = track) | K | insert a keyframe (manual keying) |
| Shift+Wheel | focus distance | Backspace | discard the current or last take / undo last punch-in |
| Alt+Wheel | aperture (f-stop) | [ / ] | slower / faster time (slow-motion recording) |
| V | dolly-zoom lock | , / . | step a frame (Shift: 10) |
| | | Home | jump to the start frame |

| Modes & tools | | View & exit | |
|---|---|---|---|
| M | cycle flight mode | B | cycle composition guides |
| O | orbit mode | H | toggle HUD |
| G | walk mode (gravity) | F1 | help overlay |
| N | snapshot: new camera here | F2 / F3 | cycle shading / toggle overlays |
| J | camera shake on/off | Tab | release the mouse to use the sidebar mid-flight |
| 1-9 | glide to bookmark | Enter | finish |
| Ctrl+1-9 | save bookmark | Esc | cancel |

**Tab** frees the mouse while the flight keeps running, so you can tweak any setting in the
sidebar (handling, shake, time scale…) and press Tab to fly on. Undo/save are blocked until
you finish so the scene can't change under an active flight.

## Flight modes

| Mode | Feel |
|---|---|
| **Free Fly** | Unreal/Unity-style noclip camera. W flies where you look. |
| **Drone** | Horizon-locked, thrust stays level, momentum, banks into turns and strafes. |
| **6DOF** | No horizon: the mouse turns around the camera's own axes, loops and barrel rolls work. |
| **Walk** | Gravity, ground following, steps, jumping, head bob. Hovers if there's no ground. |
| **Orbit** | Circles a pivot (lock-on target, whatever's under the crosshair, or the 3D cursor). Mouse/A-D orbit, W/S dolly in/out, E/Q raise/lower. |

**Handling presets** (sidebar ▸ Handling, or the preset button next to Speed) set the feel in one click:
*Snappy (Game)*, *Smooth*, *Cinematic Glide*, *Drone*, *FPV Racer*, *Crane / Dolly* and *Handheld Walk*.
Every value is also tweakable: responsiveness, braking, look smoothing, tracking lag, turn/roll rates,
auto-bank, auto-level, planar movement, adaptive speed and collisions.

## Recording

- **New Take** mode (default): each **R** creates a new take starting at the current frame.
  Scene time only runs while recording (or after **P**), so you can line up a shot, then hit R.
- **Punch In** mode: press **P** to play the active take (the camera rides along), press **R**
  where you want to take over, fly, press **R** again. *Replace Rest* re-flies everything
  after the punch-in; *Keep Rest* replaces only the re-flown part and blends back into the
  old take over *Blend Frames*. **Backspace** restores the take as it was.
- **Timing**:
  - *Real Time*: frames follow the clock (× **Time Scale** for slow motion); motion between
    frames is interpolated, so even uneven frame rates produce clean keys.
  - *Every Frame*: exactly one frame per update and never skipped, for heavy scenes that
    can't play in real time. What you saw is what you get.
  - *Scene Playback*: uses Blender's own playback, including **audio sync**, to time a move to music.
- **Countdown**, **stop at end frame**, **rewind after take**, **record zoom/focus**,
  Bezier/linear keys, and optional **auto-smooth** of every new take.
- Everything you see is recorded: banking, head bob, shake, zoom, focus pulls.
- **K** inserts single keys into the active take (with an optional auto-advance of N frames),
  for pose-to-pose moves.

Takes are ordinary Actions, one for the camera object and one for the lens and focus, so they
work with the Graph Editor, NLA, and everything else in Blender.

### Takes panel

Select a take to assign it to its camera (so you can click through takes to compare), rate
it ★–★★★★★, add a note, and:

| Tool | What it does |
|---|---|
| ▶ Review | assign, set the scene range and play it |
| Smooth | Gaussian smoothing that keeps the start and end exact |
| Simplify | removes keys while the real curve stays within a tolerance |
| Handheld / Clear Noise | non-destructive F-Curve noise wobble |
| Retime · Reverse · To Frame | stretch, flip, or slide the take in time |
| Set Range | make the scene range match the take |
| New Camera | bake the take onto its own camera and bind a timeline marker (for multi-cam edits) |
| To Curve | trace the flight path as a Bezier curve (usable with *Follow Path*) |

## Bookmarks

**Ctrl+1-9** saves the current view, **1-9** glides there (with an eased move you can record).
Bookmarks show as numbered waypoints in the HUD. In the sidebar you can reorder them and use
**Bookmarks to Take** to generate a smooth move through all of them.

## Gamepad

Enable it in *Preferences ▸ Camera Flight ▸ Gamepad*.
Left stick moves, right stick looks, triggers sink/rise, bumpers roll, left-stick click boosts,
right-stick click locks on. **A** record, **B** level, **X** autofocus, **Y** cycle mode,
D-pad ↑↓ speed, D-pad ←→ zoom, **Start** play/pause, **Back** keyframe.

- Windows: any XInput (Xbox-compatible) controller.
- Linux: `/dev/input/js*` (Xbox and PlayStation button layouts).
- macOS: not supported (there's no dependency-free way to read controllers).

## Good to know

- The camera's existing animation is set aside while flying and restored afterwards. If it
  had animation that isn't a take yet, it's added to the takes list as "(original)" so it
  can't get lost.
- Constraints on the flight camera are muted while flying (restored after).
- Parented cameras work; takes are recorded in the parent's space.
- On Linux Wayland the cursor can't be warped without a pointer grab. That's detected, and
  mouse-look falls back to plain relative motion.
- Esc restores the camera's start position and lens (unless you recorded something).

## Development

```
camera_flight/
  core/            pure-Python flight physics, shake and curve filters (mathutils only)
  session.py       a live flight: input, time, recording, camera I/O (UI-independent)
  ops_fly.py       the modal operator (thin shell around the session)
  takes.py         take recording, punch-in merging, evaluation and editing tools
  hud.py           GPU/BLF heads-up display
  ui.py, prefs.py  sidebar panels, preferences and key bindings
  compat.py        Blender 4.2 ↔ 5.x API differences (slotted actions)
tests/             unit + end-to-end tests
build.py           packages dist/camera_flight-<version>.zip
```

The test suite drives complete flights headlessly (simulated keys, mouse and clock) and
checks the resulting keyframes. Run it against the `bpy` module from PyPI:

```
pip install "bpy==4.2.*"      # or 4.5.*, 5.0.*  (Python 3.11)
python tests/run_tests.py
```

or with a Blender binary: `blender -b --factory-startup --python tests/run_tests.py`.
