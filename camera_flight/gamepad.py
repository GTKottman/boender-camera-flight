"""Optional game controller support without third-party modules.

* Windows: XInput (any Xbox-compatible controller) through ``ctypes``.
* Linux: the kernel joystick API (``/dev/input/js*``).
* macOS: not supported (there is no dependency-free API to poll).

All devices are normalised to :class:`PadState`.
"""

import math
import os
import struct
import sys
import time

BUTTONS = ("A", "B", "X", "Y", "LB", "RB", "BACK", "START", "LS", "RS", "UP", "DOWN", "LEFT", "RIGHT")
_RETRY_SECONDS = 1.5


class PadState:
    __slots__ = ("lx", "ly", "rx", "ry", "lt", "rt", "buttons")

    def __init__(self):
        self.lx = self.ly = self.rx = self.ry = 0.0
        self.lt = self.rt = 0.0
        self.buttons = frozenset()


def shape_stick(x, y, deadzone, expo):
    """Radial deadzone + RC-style expo curve."""
    mag = math.hypot(x, y)
    if mag <= deadzone or mag <= 1e-9:
        return 0.0, 0.0
    scaled = min((mag - deadzone) / max(1.0 - deadzone, 1e-6), 1.0)
    curved = (1.0 - expo) * scaled + expo * scaled ** 3
    k = curved / mag
    return x * k, y * k


def shape_trigger(v, deadzone=0.05):
    if v <= deadzone:
        return 0.0
    return min((v - deadzone) / (1.0 - deadzone), 1.0)


# --------------------------------------------------------------------------- Windows
class _XInput:
    _BITS = {
        0x0001: "UP", 0x0002: "DOWN", 0x0004: "LEFT", 0x0008: "RIGHT",
        0x0010: "START", 0x0020: "BACK", 0x0040: "LS", 0x0080: "RS",
        0x0100: "LB", 0x0200: "RB", 0x1000: "A", 0x2000: "B", 0x4000: "X", 0x8000: "Y",
    }  # fmt: skip

    def __init__(self, index):
        import ctypes

        class XINPUT_GAMEPAD(ctypes.Structure):
            _fields_ = [
                ("wButtons", ctypes.c_ushort),
                ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short),
                ("sThumbRY", ctypes.c_short),
            ]

        class XINPUT_STATE(ctypes.Structure):
            _fields_ = [("dwPacketNumber", ctypes.c_uint), ("Gamepad", XINPUT_GAMEPAD)]

        self._ctypes = ctypes
        self._state_type = XINPUT_STATE
        self.index = index
        self.dll = None
        for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
            try:
                self.dll = ctypes.WinDLL(name)
                break
            except OSError:
                continue
        self.name = "XInput controller %d" % (index + 1)
        self._next_try = 0.0
        self.connected = False

    def poll(self):
        if self.dll is None:
            return None
        now = time.monotonic()
        if not self.connected and now < self._next_try:
            return None
        state = self._state_type()
        if self.dll.XInputGetState(self.index, self._ctypes.byref(state)) != 0:
            self.connected = False
            self._next_try = now + _RETRY_SECONDS  # polling empty slots is slow
            return None
        self.connected = True
        g = state.Gamepad
        ps = PadState()
        ps.lx = max(-1.0, g.sThumbLX / 32767.0)
        ps.ly = max(-1.0, g.sThumbLY / 32767.0)
        ps.rx = max(-1.0, g.sThumbRX / 32767.0)
        ps.ry = max(-1.0, g.sThumbRY / 32767.0)
        ps.lt = g.bLeftTrigger / 255.0
        ps.rt = g.bRightTrigger / 255.0
        ps.buttons = frozenset(name for bit, name in self._BITS.items() if g.wButtons & bit)
        return ps

    def close(self):
        pass


# --------------------------------------------------------------------------- Linux
class _LinuxJoystick:
    _EVENT = struct.Struct("<IhBB")
    _LAYOUTS = {
        # axes: lx, ly, lt, rx, ry, rt, hat x, hat y   buttons: index -> name
        "XBOX": ((0, 1, 2, 3, 4, 5, 6, 7), {0: "A", 1: "B", 2: "X", 3: "Y", 4: "LB", 5: "RB", 6: "BACK", 7: "START", 9: "LS", 10: "RS"}),
        "PLAYSTATION": ((0, 1, 2, 3, 4, 5, 6, 7), {0: "A", 1: "B", 2: "Y", 3: "X", 4: "LB", 5: "RB", 8: "BACK", 9: "START", 11: "LS", 12: "RS"}),
    }  # fmt: skip

    def __init__(self, index, layout):
        self.path = "/dev/input/js%d" % index
        self.name = self.path
        self.layout = self._LAYOUTS.get(layout, self._LAYOUTS["XBOX"])
        self.fd = None
        self.axes = {}
        self.buttons = {}
        self._next_try = 0.0
        self._open()

    @property
    def connected(self):
        return self.fd is not None

    def _open(self):
        self._next_try = time.monotonic() + _RETRY_SECONDS
        try:
            self.fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            self.fd = None
            return
        axes = self.layout[0]
        # Triggers rest at -1 until the driver reports their real state.
        self.axes = {axes[2]: -1.0, axes[5]: -1.0}
        self.buttons = {}

    def poll(self):
        if self.fd is None:
            if time.monotonic() >= self._next_try:
                self._open()
            if self.fd is None:
                return None
        size = self._EVENT.size
        try:
            while True:
                data = os.read(self.fd, size * 64)
                if not data:
                    break
                for off in range(0, len(data) - size + 1, size):
                    _t, value, kind, number = self._EVENT.unpack_from(data, off)
                    kind &= ~0x80  # strip JS_EVENT_INIT
                    if kind == 0x01:
                        self.buttons[number] = value
                    elif kind == 0x02:
                        self.axes[number] = value / 32767.0
                if len(data) < size * 64:
                    break
        except BlockingIOError:
            pass
        except OSError:
            self.close()
            return None
        ax, bmap = self.layout
        a = self.axes.get
        ps = PadState()
        ps.lx = a(ax[0], 0.0)
        ps.ly = -a(ax[1], 0.0)
        ps.lt = (a(ax[2], -1.0) + 1.0) * 0.5
        ps.rx = a(ax[3], 0.0)
        ps.ry = -a(ax[4], 0.0)
        ps.rt = (a(ax[5], -1.0) + 1.0) * 0.5
        pressed = {name for num, name in bmap.items() if self.buttons.get(num)}
        hx, hy = a(ax[6], 0.0), a(ax[7], 0.0)
        if hx < -0.5:
            pressed.add("LEFT")
        elif hx > 0.5:
            pressed.add("RIGHT")
        if hy < -0.5:
            pressed.add("UP")
        elif hy > 0.5:
            pressed.add("DOWN")
        ps.buttons = frozenset(pressed)
        return ps

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.fd = None


# --------------------------------------------------------------------------- facade
class Gamepad:
    """Polls a device and tracks button edges."""

    def __init__(self, device):
        self.device = device
        self.state = PadState()
        self.pressed = frozenset()
        self.released = frozenset()

    @property
    def connected(self):
        return bool(getattr(self.device, "connected", False))

    @property
    def name(self):
        return getattr(self.device, "name", "Gamepad")

    def poll(self):
        new = self.device.poll()
        if new is None:
            new = PadState()
        self.pressed = new.buttons - self.state.buttons
        self.released = self.state.buttons - new.buttons
        self.state = new
        return new

    def close(self):
        self.device.close()


def open_gamepad(index=0, layout="XBOX"):
    """Return a :class:`Gamepad` or None when the platform isn't supported."""
    try:
        if sys.platform == "win32":
            return Gamepad(_XInput(index))
        if sys.platform.startswith("linux"):
            return Gamepad(_LinuxJoystick(index, layout))
    except Exception:  # noqa: BLE001 - never let controller probing break flight
        return None
    return None
