"""Procedural camera shake.

A handful of detuned sine octaves per channel gives smooth, organic,
non-repeating motion that is fully deterministic (same time -> same shake),
so recorded takes and live preview always match.
"""

import math
import random

_OCTAVE_FREQS = (1.0, 2.17, 4.41, 8.93)
_OCTAVE_AMPS = (1.0, 0.5, 0.25, 0.12)
_NORM = 1.0 / sum(_OCTAVE_AMPS)
CHANNELS = 6  # pitch, yaw, roll, x, y, z

_rng = random.Random(0xF11647)
_PHASES = [[_rng.uniform(0.0, math.tau) for _ in _OCTAVE_FREQS] for _ in range(CHANNELS)]
_DETUNE = [[_rng.uniform(0.87, 1.13) for _ in _OCTAVE_FREQS] for _ in range(CHANNELS)]


def sample(t, frequency):
    """Return six values in roughly [-1, 1] for time ``t`` (seconds)."""
    out = []
    w = math.tau * frequency * t
    for ch in range(CHANNELS):
        phases = _PHASES[ch]
        detune = _DETUNE[ch]
        v = 0.0
        for i, f in enumerate(_OCTAVE_FREQS):
            v += _OCTAVE_AMPS[i] * math.sin(w * f * detune[i] + phases[i])
        out.append(v * _NORM)
    return out
