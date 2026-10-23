#!/usr/bin/env python3
"""Sense HAT – Pinball: a self-playing pinball machine.

The playfield is 8 wide x 16 tall. The 8x8 LED matrix shows a window that
follows the ball (scrolling camera), just like a real, tall pinball table.

Every ball is shot up a launch lane on the right, like a real plunger, and
enters the playfield from the top.

On the playfield:
  - 3 pop bumpers close together (red, flash yellow)          100 points
  - a dark grey ramp: only reachable with a shot straight off a flipper;
    the ball rides over the top and drops onto the left flipper  1000 points
  - every ramp shot lights a third of the ramp orange; at 3 -> MULTIBALL:
    2 extra balls are launched and every ramp shot is a jackpot  3000 points
  - 2 slingshots above the flippers (orange) that kick away balls coming from
    above; shots from the flippers pass through them from behind
  - 2 top lanes (green)                                        50 / 500 bonus
  - long flippers with a 2-pixel gap in the middle; the AI sometimes catches
    the ball, lets it settle and then aims at the ramp, bumpers or lanes

The computer plays a demo until you touch the joystick: then you play a new
game of 3 balls yourself. After game over, or when you leave the joystick
alone for a while, the computer takes over again.

Controls:
  joystick left/right   -> left/right flipper (hold to keep it up)
  joystick up/down      -> "tilt": nudge the balls
  press joystick        -> toggle between follow camera and overview
  Ctrl+C                -> quit

Run:  python3 pinball.py
"""
import atexit
import math
import os
import random
import signal
import sys
import time

try:
    from sense_hat import SenseHat
    hat = SenseHat()
except (ImportError, OSError):  # No Sense HAT library or board found: use the emulator
    from sense_emu import SenseHat
    hat = SenseHat()


# ---------------------------------------------------------------------------
# Sense HAT setup (the same in every script of this collection)
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))


def stop_other_scripts():
    """Stop any other script from this folder that is still driving the LEDs."""
    me = os.getpid()
    stopped = False
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                args = [a.decode(errors="ignore") for a in f.read().split(b"\0") if a]
            cwd = os.readlink(f"/proc/{entry}/cwd")
        except OSError:
            continue
        if not args or "python" not in os.path.basename(args[0]):
            continue
        script = next((a for a in args[1:] if a.endswith(".py")), None)
        if script and os.path.dirname(os.path.realpath(os.path.join(cwd, script))) == SCRIPT_DIR:
            try:
                os.kill(int(entry), signal.SIGTERM)
                stopped = True
            except OSError:
                pass  # Already gone, or owned by another user
    if stopped:
        time.sleep(0.2)  # Let the old script clear the display before we draw


def init(sense, rotation=0):
    """Prepare the Sense HAT: stop other scripts, set the rotation (override with
    the SENSE_HAT_ROTATION environment variable) and clear the LEDs on exit."""
    stop_other_scripts()
    sense.set_rotation(int(os.environ.get("SENSE_HAT_ROTATION", rotation)))
    sense.clear()
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    atexit.register(sense.clear)
    return sense


def flatten(frame):
    """Turn an 8x8 list of rows into the flat 64-pixel list set_pixels() wants."""
    return [pixel for row in frame for pixel in row]


def run(main):
    """Run the main loop and exit quietly on Ctrl+C."""
    try:
        main()
    except KeyboardInterrupt:
        pass
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Joystick: the computer plays a demo until someone touches the joystick
# ---------------------------------------------------------------------------
IDLE_TIMEOUT = 30   # Seconds without joystick input before the computer takes over again

# 8x8 picture of a "play" triangle, shown when a human takes over.
# "#" = a lit (green) pixel, "." = an unlit pixel.
PLAY_SIGN = [
    "..#.....",
    "..##....",
    "..###...",
    "..####..",
    "..####..",
    "..###...",
    "..##....",
    "..#.....",
]


class Joystick:
    """Keeps track of the Sense HAT joystick (or the emulator's: arrow keys + Enter),
    and of a USB or Bluetooth gamepad if one is connected.

    The Sense HAT library reports joystick "events": a direction ("up", "down",
    "left", "right" or "middle" for pushing it in) plus an action ("pressed",
    "held" while you keep it pushed, and "released"). This class turns those
    events into two handy things for the games:

    - read() gives the new presses since last time (good for single steps),
    - self.held is the set of directions that are pushed right now (good for
      moving as long as you hold the stick).

    A gamepad works the same way: the d-pad or the left stick steers, and A, B
    or Start count as pushing the joystick in. Set GAMEPAD_SHOULDERS = True in a
    game to also use the shoulder buttons and triggers as left and right (the
    pinball flippers do). Gamepads need the evdev library
    (sudo apt install python3-evdev); without it only the Sense HAT joystick works.

    It also remembers when the joystick was last touched, so the game can tell
    whether the player has walked away (see idle()).
    """

    PAD_RESCAN = 3.0             # Seconds between looks for a newly plugged-in gamepad

    def __init__(self, sense):
        """Remember the SenseHat object; nothing is held and no input seen yet."""
        self.sense = sense
        self.held = set()        # Directions held down right now
        self.last_input = 0.0    # time.monotonic() of the last joystick event (0 = never)
        self.pads = []           # Connected gamepads (evdev devices)
        self.pad_state = {}      # Per gamepad: which directions it holds right now
        self.pad_axes = {}       # Per gamepad: latest position of the stick, d-pad and triggers
        self.next_scan = 0.0     # When to look for gamepads again

    def find_pads(self):
        """Look for gamepads (once every PAD_RESCAN seconds while none is found)."""
        if self.pads or time.monotonic() < self.next_scan:
            return
        self.next_scan = time.monotonic() + self.PAD_RESCAN
        try:
            import evdev
        except ImportError:
            self.next_scan = float("inf")          # No evdev library: never look again
            return
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                keys = dev.capabilities().get(evdev.ecodes.EV_KEY, [])
                if evdev.ecodes.BTN_SOUTH in keys or evdev.ecodes.BTN_GAMEPAD in keys:
                    self.pads.append(dev)          # Has an A button: it's a gamepad
                else:
                    dev.close()
            except OSError:
                pass                               # Not allowed to read it, or it went away

    def pad_directions(self, dev):
        """Which directions this gamepad holds right now, from its buttons and axes."""
        import evdev
        e, ax = evdev.ecodes, self.pad_axes.setdefault(dev.path, {})
        keys = set(dev.active_keys())
        state = set()

        def stick(code):
            """Stick position from -1 (left/up) to 1 (right/down)."""
            if code not in ax:
                return 0.0
            info = dev.absinfo(code)
            mid = (info.max + info.min) / 2
            return (ax[code] - mid) / max(1, (info.max - info.min) / 2)

        def trigger(code):
            """How far a trigger is pulled, from 0 to 1."""
            if code not in ax:
                return 0.0
            info = dev.absinfo(code)
            return (ax[code] - info.min) / max(1, info.max - info.min)

        x = stick(e.ABS_X) + ax.get(e.ABS_HAT0X, 0)   # Left stick plus d-pad (hat: -1, 0 or 1)
        y = stick(e.ABS_Y) + ax.get(e.ABS_HAT0Y, 0)
        if x < -0.5 or e.BTN_DPAD_LEFT in keys:
            state.add("left")
        if x > 0.5 or e.BTN_DPAD_RIGHT in keys:
            state.add("right")
        if y < -0.5 or e.BTN_DPAD_UP in keys:
            state.add("up")
        if y > 0.5 or e.BTN_DPAD_DOWN in keys:
            state.add("down")
        if keys & {e.BTN_SOUTH, e.BTN_EAST, e.BTN_START}:
            state.add("middle")
        if globals().get("GAMEPAD_SHOULDERS"):         # e.g. the pinball flippers
            if e.BTN_TL in keys or e.BTN_TL2 in keys or trigger(e.ABS_Z) > 0.3:
                state.add("left")
            if e.BTN_TR in keys or e.BTN_TR2 in keys or trigger(e.ABS_RZ) > 0.3:
                state.add("right")
        return state

    def read_pads(self, pressed):
        """Turn gamepad changes into joystick presses and releases."""
        self.find_pads()
        for dev in list(self.pads):
            try:
                import evdev
                event = dev.read_one()
                while event is not None:              # Remember the newest axis positions
                    if event.type == evdev.ecodes.EV_ABS:
                        self.pad_axes.setdefault(dev.path, {})[event.code] = event.value
                    event = dev.read_one()
                now = self.pad_directions(dev)
            except OSError:                           # Unplugged: forget it
                self.pads.remove(dev)
                for d in self.pad_state.pop(dev.path, set()):
                    self.held.discard(d)
                continue
            before = self.pad_state.get(dev.path, set())
            for d in now - before:                    # Newly pushed
                pressed.append(d)
                self.held.add(d)
                self.last_input = time.monotonic()
            for d in before - now:                    # Let go
                self.held.discard(d)
                self.last_input = time.monotonic()
            if now:
                self.last_input = time.monotonic()    # Still holding: still playing
            self.pad_state[dev.path] = now

    def read(self):
        """Return the directions pressed since the last call ("up", "left", "middle", ...).

        Also updates self.held and the time of the last input. Returns an empty
        list when there is no joystick to read (for example a broken connection).
        """
        pressed = []
        self.read_pads(pressed)
        # Fetch all events that queued up since the last call
        try:
            events = self.sense.stick.get_events()
        except (AttributeError, OSError):
            return pressed
        for event in events:
            # Any event at all counts as "someone is playing"
            self.last_input = time.monotonic()
            if event.action == "pressed":
                pressed.append(event.direction)
                self.held.add(event.direction)
            elif event.action == "held":
                self.held.add(event.direction)
            elif event.action == "released":
                self.held.discard(event.direction)
        return pressed

    def idle(self):
        """True when nobody touched the joystick for IDLE_TIMEOUT seconds."""
        return time.monotonic() - self.last_input > IDLE_TIMEOUT

    def flush(self):
        """Forget presses that are still waiting, e.g. during a game-over message."""
        self.read()
        self.held.clear()



def show_play_sign(sense):
    """A green play symbol: you're in control now.

    Each "#" in PLAY_SIGN becomes a green pixel, every other character a dark
    one. The sign stays up for 0.8 seconds so the player can get ready.
    """
    sense.set_pixels([(0, 255, 0) if c == "#" else (0, 0, 0) for row in PLAY_SIGN for c in row])
    time.sleep(0.8)
# ---------------------------------------------------------------------------


# ---------- settings ----------
# Distances are measured in "cells": one cell is one LED pixel. Pixel column i covers
# x from i to i+1, and pixel row j covers y from j to j+1. y grows downwards (y=0 is
# the top of the table, y=16 the bottom where balls drain).
W, H = 8, 16            # playfield
FPS = 30                # frames (screen updates) per second
GAMEPAD_SHOULDERS = True   # Gamepad: shoulder buttons and triggers work the flippers
DT = 1.0 / FPS          # seconds per frame
SUB = 4                 # physics substeps per frame (prevents tunnelling through walls)
GRAVITY = 9.0           # cells / s^2
R = 0.5                 # ball radius
MAX_SPEED = 22.0        # cells / s; faster balls are slowed down to this speed
BALLS_PER_GAME = 3
AI_SKILL = 0.97         # chance the AI hits an incoming ball correctly
AI_CATCH = 0.85        # chance the AI catches a ball and then takes an aimed shot
AI_CATCH_RAMP = 0.7     # same, for a ball coming off the ramp
RAMP_SPEED = 11.0       # cells / s along the ramp
RAMP_MIN_SPEED = 5.0    # how fast a flipper shot must travel upwards to make the ramp
RAMP_SHOTS_FOR_MULTIBALL = 3
MULTIBALL_EXTRA = 2     # extra balls during multiball
JACKPOT = 3000          # points for a ramp shot during multiball
BALL_SAVE = 8.0         # for this long after multiball starts, a drained ball is returned

# ---------- colours ----------
# (red, green, blue), each 0-255. The "_HIT" colours are flashed when something is hit.
BLACK = (0, 0, 0)
BALL_COL = (255, 255, 255)
BUMPER_COL = (200, 0, 0)
BUMPER_HIT = (255, 220, 0)
FLIPPER_COL = (0, 120, 255)
FLIPPER_UP = (0, 220, 255)
WALL_COL = (70, 0, 90)
SLING_COL = (200, 70, 0)
SLING_HIT = (255, 160, 40)
RAMP_COL = (45, 45, 45)
RAMP_HIT = (130, 130, 130)
RAMP_LIT = (255, 120, 0)
TOP_LANE_COL = (0, 90, 0)
TOP_LANE_HIT = (0, 255, 80)
LAUNCH_COL = (60, 50, 0)

# ---------- playfield ----------
# pop bumpers in a tight triangle: (x, y) centre (pixel i spans i to i+1)
BUMPERS = [(3.0, 2.2), (5.0, 2.2), (4.0, 3.6)]   # high up, out of the way of flipper shots
BUMPER_RADIUS = 0.5     # cells; a bumper is a circle this big
BUMPER_BOOST = 4.5      # extra speed (cells / s) a bumper adds when it bounces the ball away

# fixed angled walls (rounded top corners)
# Each wall is a line segment ((x1, y1), (x2, y2)).
WALLS = [
    ((0.5, 2.5), (2.5, 0.5)),
    ((5.5, 0.5), (7.5, 2.5)),
]

# slingshots above the flippers (leaving an inlane along the outer wall).
# Only their upper (inner) face is solid: a ball coming from above is kicked away,
# a ball shot up from a flipper passes straight through from behind.
SLINGS = [
    ((1.3, 8.2), (2.3, 9.8)),
    ((6.7, 8.2), (5.7, 9.8)),
]
SLING_KICK = 7.0        # extra speed (cells / s) a slingshot gives the ball; higher = wilder

# ramp: entrance on the right (the ball must pass through it going up), runs over
# the top and drops the ball on the left, above the left flipper
# RAMP_PATH is a list of (x, y) points; the ball follows the straight lines between them.
RAMP_PATH = [(6.4, 7.2), (7.0, 5.0), (6.6, 1.4), (4.0, 0.6),
             (1.4, 1.4), (1.0, 5.0), (0.8, 9.0)]
RAMP_ENTRY_X = (5.0, 7.5)   # the ramp entrance spans these x values ...
RAMP_ENTRY_Y = 7.2          # ... at this height; the ball must cross it moving upwards

# launch lane: like a real plunger, every new ball first shoots up the right
# side, curves over the top and enters the playfield from above. The lane is
# only drawn while a ball is in it.
LAUNCH_PATH = [(7.5, 15.5), (7.5, 3.0), (7.3, 1.3), (6.5, 0.6), (5.2, 0.7)]
LAUNCH_SPEED = 15.0     # cells / s
LAUNCH_WAIT = 0.6       # seconds the ball waits at the bottom (pulling the plunger)

# top lanes: bonus points when the ball passes through
# (the numbers are pixel columns in the top row)
TOP_LANES = [3, 4]


def clamp(v, lo, hi):
    """Limit a value to a range.

    Args:
        v: the value.
        lo, hi: the smallest and largest allowed value.

    Returns:
        lo if v is below lo, hi if v is above hi, otherwise v itself.
    """
    return max(lo, min(hi, v))


def lerp(a, b, t):
    """Linear interpolation: the value a fraction t of the way from a to b.

    t=0 gives a, t=1 gives b, t=0.5 gives the point halfway. Used to find points
    along lines, e.g. a flipper tip moving between its rest and up position.

    Args:
        a, b: start and end value.
        t: how far along (usually 0.0 - 1.0).

    Returns:
        The interpolated number.
    """
    return a + (b - a) * t


def closest_on_segment(px, py, ax, ay, bx, by):
    """Find the point on line segment A-B that is closest to point P.

    This is the core of every wall/flipper collision: if that closest point is
    nearer to the ball's centre than the ball's radius, the ball is touching
    the segment.

    How it works: project P onto the infinite line through A and B (using the dot
    product), which gives a position t along the line (0 = at A, 1 = at B). Then
    clamp t to 0-1 so the point stays on the segment itself, not beyond its ends.

    Args:
        px, py: the point (usually the ball's centre).
        ax, ay, bx, by: the two ends of the segment.

    Returns:
        (x, y, t): the closest point, and how far along the segment it lies (0-1).
    """
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy      # squared length of the segment
    # l2 == 0 means A and B are the same point: then A itself is the answer
    t = 0.0 if l2 == 0 else clamp(((px - ax) * dx + (py - ay) * dy) / l2, 0.0, 1.0)
    return ax + t * dx, ay + t * dy, t


class Ramp:
    """A path made of straight pieces that a ball can ride along at a steady speed.

    Used both for the real ramp and for the launch lane. Instead of simulating
    physics on the ramp, the ball simply moves a distance `s` along the path each
    step, and `point(s)` tells where on the table that is.
    """

    def __init__(self, path):
        """Store the path and pre-calculate the distance to each corner point.

        Args:
            path: list of (x, y) points; the ramp runs from the first to the last.
        """
        self.path = path
        # cum[i] = distance along the path from the start to point i
        # ("cumulative length"), so we can quickly find which piece a distance falls on
        self.cum = [0.0]
        for (x1, y1), (x2, y2) in zip(path, path[1:]):
            self.cum.append(self.cum[-1] + math.hypot(x2 - x1, y2 - y1))
        self.length = self.cum[-1]   # total length of the whole path

    def point(self, s):
        """Return the (x, y) position at distance s from the start of the path.

        Args:
            s: distance along the path in cells. Values below 0 or beyond the end
               are clamped, so a negative s keeps the ball waiting at the start.

        Returns:
            An (x, y) tuple on the path.
        """
        s = clamp(s, 0.0, self.length)
        # Find the piece of the path that contains distance s ...
        for i in range(len(self.path) - 1):
            if s <= self.cum[i + 1]:
                # ... and how far along that piece we are (0-1)
                seg = self.cum[i + 1] - self.cum[i]
                t = 0.0 if seg == 0 else (s - self.cum[i]) / seg
                (x1, y1), (x2, y2) = self.path[i], self.path[i + 1]
                return lerp(x1, x2, t), lerp(y1, y2, t)
        return self.path[-1]

    def pixels(self):
        """{(x, y): part} with part 0, 1 or 2 (third of the ramp, counted from the entrance).

        Walks along the path in small steps of 0.25 cells and records every LED
        pixel it passes over. The part number is used to light up the ramp one
        third at a time as the player makes ramp shots.

        Returns:
            A dict mapping (column, row) to the part number of that pixel.
        """
        pix = {}
        s = 0.0
        while s <= self.length:
            x, y = self.point(s)
            key = (int(clamp(x, 0, W - 1)), int(clamp(y, 0, H - 1)))
            # setdefault: if a pixel is passed twice, keep the part it was first seen in
            pix.setdefault(key, min(2, int(3 * s / self.length)))
            s += 0.25
        return pix


class Ball:
    """One pinball: its position, speed and some bookkeeping for the rules and the AI.

    A ball is either rolling freely on the playfield (normal physics), or riding a
    fixed path (`on_ramp` is True): the launch lane or the ramp.
    """

    def __init__(self):
        """Create a new ball, waiting at the bottom of the launch lane."""
        # starts in the launch lane at the bottom right; it rides the lane like a ramp
        self.x, self.y = LAUNCH_PATH[0]
        self.vx = self.vy = 0.0      # speed in cells / s (vy > 0 = moving down)
        self.slow_time = 0.0         # how long the ball has been (nearly) standing still
        self.on_ramp = True
        self.ride = "launch"        # "launch" = in the launch lane, "ramp" = on the ramp
        # distance along the lane; starting below 0 makes the ball wait LAUNCH_WAIT
        # seconds at the bottom before it starts moving (point() clamps s to 0)
        self.ramp_s = -LAUNCH_SPEED * LAUNCH_WAIT
        self.flipper_shot = False   # just shot by a flipper (required to make the ramp)
        # per-ball AI memory
        self.armed = True            # True while the ball is high up: AI decides on next approach
        self.will_hit = True         # AI decided to hit this ball correctly this time
        self.catch = False           # AI decided to try to catch this ball on a flipper

    def speed(self):
        """Return the ball's speed (length of its velocity) in cells per second."""
        return math.hypot(self.vx, self.vy)


class Ghost:
    """Lightweight ball used to work out aimed shots.

    The AI fires many of these "imaginary" balls in a quick simulation to see
    which shot would hit the target. It only needs a position and speed, so
    __slots__ keeps it small and fast.
    """
    __slots__ = ("x", "y", "vx", "vy")

    def __init__(self, x, y, vx, vy):
        """Create a ghost ball at (x, y) moving with speed (vx, vy) in cells / s."""
        self.x, self.y, self.vx, self.vy = x, y, vx, vy


def on_front(ball, a, c):
    """Does the ball come at a slingshot from above, onto its upper (front) side?

    Slingshots are one-sided: only balls falling onto their top face bounce off.
    This is checked in two steps: the ball must be moving down, and it must be
    on the upper side of the slingshot's line.

    Args:
        ball: a Ball or Ghost.
        a, c: the two end points of the slingshot segment.

    Returns:
        True if the ball should collide with the slingshot.
    """
    if ball.vy <= 0:
        return False                         # on its way up (a flipper shot): passes through
    # A "normal" is a direction at right angles to the segment. Rotating the
    # segment's direction by 90 degrees gives one; we pick the one pointing up.
    nx, ny = c[1] - a[1], a[0] - c[0]      # a normal of the segment ...
    if ny > 0:
        nx, ny = -nx, -ny                    # ... the one that points up
    # Dot product > 0: the ball is on the same side as the normal, i.e. above the line
    return (ball.x - a[0]) * nx + (ball.y - a[1]) * ny > 0


def collide_segment(ball, a, b, restitution=0.55):
    """Ball <-> line segment collision. Returns (t, nx, ny) or None.

    If the ball overlaps the segment, it is pushed back out so it just touches,
    and its speed towards the segment is reversed (it bounces).

    Args:
        ball: a Ball or Ghost; its position and speed are changed in place.
        a, b: the two end points of the segment (walls, slingshots, flippers).
        restitution: bounciness. 0 = the ball stops dead against the segment,
            1 = a perfect bounce that keeps all its speed.

    Returns:
        None if there was no contact. Otherwise (t, nx, ny): where along the
        segment the contact was (0 = at a, 1 = at b) and the unit normal
        (direction pointing from the segment towards the ball).
    """
    # Distance from the ball's centre to the nearest point of the segment
    cx, cy, t = closest_on_segment(ball.x, ball.y, a[0], a[1], b[0], b[1])
    nx, ny = ball.x - cx, ball.y - cy
    d = math.hypot(nx, ny)
    if d >= R:
        return None                 # further away than the radius: not touching
    if d < 1e-6:
        # Centre exactly on the line: no direction to push in, so just push upwards
        nx, ny, d = 0.0, -1.0, 1.0
    # Make the normal a unit vector (length 1)
    nx /= d
    ny /= d
    # Push the ball out so it touches the segment instead of overlapping it
    ball.x = cx + nx * R
    ball.y = cy + ny * R
    # vn = speed along the normal; negative means the ball moves into the segment
    vn = ball.vx * nx + ball.vy * ny
    if vn < 0:
        # Bounce: remove the "into the wall" speed and add some back the other way
        ball.vx -= (1 + restitution) * vn * nx
        ball.vy -= (1 + restitution) * vn * ny
    return t, nx, ny


class Flipper:
    """One flipper: a line segment from a fixed pivot to a tip that swings up and down.

    `pos` says how far up the flipper is (0 = resting, 1 = fully up); the tip
    position is interpolated between `rest_tip` and `up_tip`. A flip sets a timer:
    while the timer runs the flipper swings up quickly, afterwards it falls back
    more slowly. The AI can also put a flipper in a special mode to catch and
    aim the ball.
    """

    def __init__(self, pivot, rest_tip, up_tip, side, pixel_range):
        """Create a flipper.

        Args:
            pivot: (x, y) of the fixed end, against the outer wall.
            rest_tip: (x, y) of the tip when the flipper is down.
            up_tip: (x, y) of the tip when the flipper is fully up.
            side: -1 for the left flipper, +1 for the right one.
            pixel_range: (first, last) LED columns the flipper is drawn in.
        """
        self.pivot = pivot
        self.rest_tip = rest_tip
        self.up_tip = up_tip
        self.side = side              # -1 = left, +1 = right
        self.pixel_range = pixel_range  # columns it is drawn in
        self.pos = 0.0                # 0 = at rest, 1 = up
        self.prev_pos = 0.0           # pos in the previous step (to see if it's moving up)
        self.timer = 0.0              # seconds the flipper should still be pushed up
        self.cooldown = 0.0           # seconds before the AI may flip again
        self.mode = "idle"            # idle / hold (cradling the ball) / aim (aimed shot)
        self.mode_time = 0.0          # seconds spent in the current mode
        self.ball = None              # the ball being held / aimed
        self.hold_for = 1.0           # seconds to hold the ball before aiming
        self.aimed = False            # True: the next hit is an aimed shot
        self.kicked = False           # already kicked during this stroke

    def tip(self):
        """Return the current (x, y) of the flipper tip, based on how far up it is."""
        return (lerp(self.rest_tip[0], self.up_tip[0], self.pos),
                lerp(self.rest_tip[1], self.up_tip[1], self.pos))

    def flip(self, force=False):
        """Start a flip: the flipper swings up for a short moment.

        Args:
            force: flip even if the cooldown hasn't run out yet. The AI uses
                the cooldown so it doesn't flap nervously; the human player and
                the AI's aimed shots use force=True.
        """
        if self.cooldown <= 0 or force:
            self.timer = 0.22         # seconds to keep pushing the flipper up
            self.cooldown = 0.45      # seconds before the next (unforced) flip

    def rising(self):
        """True while the flipper is moving up (only then does it give the ball a kick)."""
        return self.pos > self.prev_pos

    def release(self):
        """Stop any AI catch/aim manoeuvre and forget the ball it was about."""
        self.mode = "idle"
        self.ball = None

    def update(self, dt):
        """Move the flipper for one (sub)step.

        Args:
            dt: length of the step in seconds.
        """
        self.prev_pos = self.pos
        if self.mode == "hold":
            self.timer = max(self.timer, 0.1)   # keep the flipper up
        self.timer -= dt
        self.cooldown -= dt
        # Swing up fast (fully up in 1/12 s) while the timer runs, drop back at half speed
        if self.timer > 0:
            self.pos = min(1.0, self.pos + 12.0 * dt)
        else:
            self.pos = max(0.0, self.pos - 6.0 * dt)
        # Once the stroke is over, the flipper may kick a ball again next time
        if not self.rising():
            self.kicked = False


class Pinball:
    """The whole pinball machine: playfield state, physics, AI, drawing and game flow.

    The main loop in `play()` repeats, 30 times a second: read the joystick,
    update the game (`update()`), and draw it on the LEDs (`render()`).
    """

    def __init__(self, sense):
        """Build the table and start a demo game.

        Args:
            sense: the SenseHat object (already prepared by init()).
        """
        self.sense = sense
        try:
            self.sense.low_light = False
        except (AttributeError, OSError):
            pass    # Not every Sense HAT version (or the emulator) supports this
        # flipper tips at x=3.5 and x=4.5: the gap between them is exactly one ball wide.
        # They're drawn in columns 0-2 and 5-7, plus a half-lit "half pixel" for the tip
        # in columns 3 and 4.
        self.left = Flipper((0.0, 11.4), (3.5, 14.55), (3.7, 10.75), -1, (0, 2))
        self.right = Flipper((8.0, 11.4), (4.5, 14.55), (4.3, 10.75), +1, (5, 7))
        self.flippers = [self.left, self.right]
        # The ramp and launch lane, plus the LED pixels they cover (worked out once)
        self.ramp = Ramp(RAMP_PATH)
        self.ramp_pixels = self.ramp.pixels()
        self.launch_lane = Ramp(LAUNCH_PATH)
        self.launch_pixels = self.launch_lane.pixels()
        self.balls = [Ball()]
        self.launch_queue = []        # delays before extra multiball balls launch
        # Flash timers (seconds left) that make things light up after a hit
        self.bumper_flash = [0.0] * len(BUMPERS)
        self.sling_flash = [0.0] * len(SLINGS)
        self.ramp_flash = 0.0
        self.ramp_count = 0           # ramp shots so far towards multiball
        self.multiball = False
        self.multiball_flash = 0.0
        self.ball_save = 0.0          # seconds of ball save left
        self.lane_lit = [False] * len(TOP_LANES)
        self.lane_flash = 0.0
        self.score = 0
        self.balls_left = BALLS_PER_GAME
        self.cam = 8.0                # top row of the playfield shown on the LEDs
        self.overview = False         # True: show the whole table squeezed into 8 rows
        self.stick = Joystick(sense)
        self.human = False            # True: you play the flippers

    # ---------- AI ----------
    def ai(self):
        """The computer player: decide for this frame whether to flip either flipper.

        Works in two passes:
        1. For every ball, update its "AI memory": decide whether to try a catch,
           and whether this time the AI will react correctly (AI_SKILL), so the
           computer occasionally misses like a human would.
        2. For every flipper, either continue a catch/aim manoeuvre (ai_special),
           or flip when a ball comes close enough to it.
        """
        # Catching only makes sense with one ball; with several, just keep hitting
        single = len(self.balls) == 1 and not self.launch_queue
        for b in self.balls:
            if b.on_ramp:
                continue
            # sometimes raise the flipper early to catch the ball (single ball only)
            if single and b.catch and b.vy > 1.0 and 6.0 < b.y < 10.6:
                f = self.predict_landing(b)
                if f is not None and f.mode == "idle":
                    b.catch = False
                    f.mode, f.mode_time, f.ball = "hold", 0.0, b
                    f.hold_for = random.uniform(0.6, 1.4)
                    f.flip(force=True)
            # ball high on the playfield: get ready for its next approach
            if b.y < 9.5:
                if not b.armed:
                    b.armed = True
                    b.catch = random.random() < AI_CATCH
                continue
            # ball coming down: decide once whether the AI reacts correctly this time
            if b.armed:
                b.armed = False
                b.will_hit = random.random() < AI_SKILL

        for f in self.flippers:
            f.mode_time += DT
            # Busy catching or aiming: that has its own logic
            if f.mode != "idle":
                self.ai_special(f)
                continue
            # Normal play: look for a ball near this flipper
            for b in self.balls:
                if b.on_ramp or b.y < 9.5 or not b.will_hit:
                    continue
                dist, t = self.flipper_distance(f, b)
                # reaction distance grows with speed (the flipper needs a moment to swing up)
                reach = 0.9 + b.speed() * random.uniform(0.05, 0.09)
                # Close enough, not flying away upwards, and not right at the pivot
                # (t > 0.2: a hit near the pivot would be too weak to be useful)
                if dist < reach and b.vy > -2.0 and t > 0.2:
                    f.flip()
                    break

    def ai_special(self, f):
        """Continue a catch-and-aim manoeuvre on flipper f.

        "hold": the flipper stays up while the caught ball settles against it.
        When the ball lies still, switch to "aim": the flipper drops, the ball
        starts rolling down the flipper towards the tip, and at the right moment
        the AI fires an aimed shot. If anything goes wrong (ball rolls away,
        takes too long), the manoeuvre is abandoned.

        Args:
            f: the Flipper in "hold" or "aim" mode.
        """
        b = f.ball
        # The ball is gone (drained or on the ramp): nothing to do any more
        if b is None or b not in self.balls or b.on_ramp:
            f.release()
            return
        dist, t = self.flipper_distance(f, b)
        if f.mode == "hold":
            # ball resting on the raised flipper? then release and aim
            if f.mode_time > f.hold_for and dist < 0.8 and b.speed() < 2.0:
                f.mode, f.mode_time = "aim", 0.0
            elif f.mode_time > 3.0 or dist > 2.5:
                f.release()             # catch failed or took too long: give up
        elif f.mode == "aim":
            # flipper drops, ball rolls towards the tip: fire at the right moment
            if dist < 0.9 and t > random.uniform(0.75, 0.9) and f.pos < 0.1:
                f.flip(force=True)
                f.aimed = True          # the collision code will now call aimed_shot()
                f.release()
            elif f.mode_time > 2.0 or dist > 2.5:
                f.release()

    @staticmethod
    def flipper_distance(f, b):
        """How far is ball b from flipper f, and where along the flipper is it?

        Args:
            f: a Flipper.
            b: a Ball.

        Returns:
            (distance, t): distance from the ball's centre to the flipper line in
            cells, and the position along the flipper (0 = pivot, 1 = tip).
        """
        tip = f.tip()
        cx, cy, t = closest_on_segment(b.x, b.y, f.pivot[0], f.pivot[1], tip[0], tip[1])
        return math.hypot(b.x - cx, b.y - cy), t

    def predict_landing(self, b):
        """Will the ball land on a flipper past a slingshot? Which one?

        Calculates when the falling ball reaches height y=10 (just below the
        slingshots) using the standard equation for motion under gravity, then
        where it will be sideways at that moment. Balls that come down through
        an inlane or just inside a slingshot will roll onto a flipper and can
        be caught; balls in the middle head straight for the gap.

        Args:
            b: a Ball.

        Returns:
            The Flipper the ball will land on, or None.
        """
        # Solve y(t) = b.y + b.vy*t + GRAVITY*t^2/2 = 10 for t (the quadratic formula);
        # disc is the part under the square root
        dy = 10.0 - b.y
        disc = b.vy * b.vy + 2 * GRAVITY * dy
        if dy <= 0 or disc < 0:
            return None
        t = (-b.vy + math.sqrt(disc)) / GRAVITY
        x = b.x + b.vx * t              # sideways speed doesn't change (no air resistance)
        # outside (inlane) or inside of the slingshot
        if x < 1.1 or 2.4 < x < 3.3:
            return self.left
        if x > W - 1.1 or W - 3.3 < x < W - 2.4:
            return self.right
        return None

    # ---------- physics ----------
    def ride_ramp(self, b, dt):
        """Move a ball that is in the launch lane or on the ramp.

        There is no real physics here: the ball just moves along the path at a
        fixed speed. At the end of the path it is released onto the playfield
        with a starting speed.

        Args:
            b: a Ball with on_ramp True.
            dt: length of the step in seconds.
        """
        if b.ride == "launch":
            b.ramp_s += LAUNCH_SPEED * dt
            b.x, b.y = self.launch_lane.point(b.ramp_s)
            if b.ramp_s >= self.launch_lane.length:
                # out of the launch lane: into the top of the playfield, heading left
                b.on_ramp = False
                b.vx = -random.uniform(3.0, 6.5)
                b.vy = random.uniform(0.0, 2.0)
                b.armed = True
            return
        b.ramp_s += RAMP_SPEED * dt
        b.x, b.y = self.ramp.point(b.ramp_s)
        if b.ramp_s >= self.ramp.length:
            # off the ramp: gently onto the left flipper
            b.on_ramp = False
            b.vx = random.uniform(-0.3, 0.2)
            b.vy = 2.0
            b.armed = True
            b.catch = random.random() < AI_CATCH_RAMP
            # The third ramp shot starts multiball once the ball has come down
            if self.ramp_count >= RAMP_SHOTS_FOR_MULTIBALL and not self.multiball:
                self.start_multiball()

    def ramp_hit(self, b):
        """A ball made it into the ramp: put it on the ramp and award points.

        Outside multiball a ramp shot scores 1000 and counts towards multiball;
        during multiball every ramp shot is a JACKPOT.

        Args:
            b: the Ball that entered the ramp.
        """
        b.on_ramp = True
        b.ride = "ramp"
        b.ramp_s = 0.0
        b.flipper_shot = False
        self.ramp_flash = 1.2           # seconds the ramp blinks
        if self.multiball:
            self.score += JACKPOT
        else:
            self.score += 1000
            self.ramp_count = min(RAMP_SHOTS_FOR_MULTIBALL, self.ramp_count + 1)

    def start_multiball(self):
        """Start multiball: queue the extra balls and switch on the ball save."""
        self.multiball = True
        self.multiball_flash = 2.0
        # Extra balls launch 0.4 s, 1.0 s, ... from now (the delays count down in update())
        self.launch_queue = [0.4 + 0.6 * i for i in range(MULTIBALL_EXTRA)]
        self.ball_save = BALL_SAVE

    def step(self, b, dt):
        """Move one ball for one physics substep and handle everything it hits.

        Order: gravity and movement, ramp entrance, outer walls, top lanes,
        corner walls, slingshots, bumpers and finally the flippers.

        Args:
            b: the Ball to move.
            dt: length of the substep in seconds (DT / SUB).
        """
        if b.on_ramp:
            self.ride_ramp(b, dt)
            return

        # Gravity pulls the ball down; then move it by its speed (simple Euler steps)
        prev_y = b.y
        b.vy += GRAVITY * dt
        # Speed limit, keeping the direction: scale both parts of the velocity down
        sp = b.speed()
        if sp > MAX_SPEED:
            b.vx *= MAX_SPEED / sp
            b.vy *= MAX_SPEED / sp
        b.x += b.vx * dt
        b.y += b.vy * dt
        # Once the ball falls again it is no longer a "fresh" flipper shot
        if b.vy > 0:
            b.flipper_shot = False

        # ramp entrance: only a hard upward flipper shot makes the ramp;
        # balls coming from above or out of the bumpers simply pass underneath
        # (prev_y >= RAMP_ENTRY_Y > b.y: the ball crossed the entrance line this step)
        if (b.flipper_shot and b.vy < -RAMP_MIN_SPEED
                and prev_y >= RAMP_ENTRY_Y > b.y
                and RAMP_ENTRY_X[0] <= b.x <= RAMP_ENTRY_X[1]):
            self.ramp_hit(b)
            return

        # outer walls
        # (bounce back with 80% of the speed; there is no wall at the bottom: the drain)
        if b.x < 0.5:
            b.x, b.vx = 0.5, abs(b.vx) * 0.8
        elif b.x > W - 0.5:
            b.x, b.vx = W - 0.5, -abs(b.vx) * 0.8
        if b.y < 0.5:
            b.y, b.vy = 0.5, abs(b.vy) * 0.8

        # top-lanes
        # 50 points for lighting a lane; when all lanes are lit: 500 bonus, then reset
        if b.y < 1.2:
            col = int(b.x)
            if col in TOP_LANES:
                i = TOP_LANES.index(col)
                if not self.lane_lit[i]:
                    self.lane_lit[i] = True
                    self.score += 50
                    if all(self.lane_lit):
                        self.score += 500
                        self.lane_flash = 1.0
                        self.lane_lit = [False] * len(TOP_LANES)

        # angled corner walls at the top
        for a, c in WALLS:
            collide_segment(b, a, c, restitution=0.6)

        # slingshots: active kick when the ball hits them hard enough
        for i, (a, c) in enumerate(SLINGS):
            if not on_front(b, a, c):
                continue                     # from behind: the ball passes through
            v_before = b.speed()
            hit = collide_segment(b, a, c, restitution=0.5)
            if hit is not None:
                b.flipper_shot = False
                # Only kick on a real hit (not a ball gently rolling along it), and
                # not again while the slingshot is still flashing from the last kick
                if v_before > 2.0 and self.sling_flash[i] <= 0:
                    _, nx, ny = hit
                    # Push the ball away from the slingshot, with a bit of extra lift
                    b.vx += nx * SLING_KICK
                    b.vy += ny * SLING_KICK - 2.0
                    self.sling_flash[i] = 0.2
                    self.score += 10

        # pop bumpers
        # Circle-circle collision: touching when the centres are closer than both radii
        for i, (bx, by) in enumerate(BUMPERS):
            dx, dy = b.x - bx, b.y - by
            d = math.hypot(dx, dy)
            min_d = R + BUMPER_RADIUS
            if d < min_d:
                if d < 1e-6:
                    dx, dy, d = 0.0, -1.0, 1.0
                # Unit normal from the bumper centre to the ball
                nx, ny = dx / d, dy / d
                # Push the ball out to the bumper's edge
                b.x, b.y = bx + nx * min_d, by + ny * min_d
                # Mirror the speed towards the bumper (a perfect bounce) ...
                vn = b.vx * nx + b.vy * ny
                if vn < 0:
                    b.vx -= 2 * vn * nx
                    b.vy -= 2 * vn * ny
                # ... and the bumper actively kicks the ball away a bit more
                b.vx += nx * BUMPER_BOOST
                b.vy += ny * BUMPER_BOOST
                b.flipper_shot = False
                self.bumper_flash[i] = 0.25
                self.score += 100

        # flippers
        for f in self.flippers:
            # A ball being caught bounces almost not at all, so it settles on the flipper
            holding = f.mode == "hold" and f.ball is b
            hit = collide_segment(b, f.pivot, f.tip(), restitution=0.05 if holding else 0.35)
            if hit is None:
                continue
            if holding:
                # catching: the ball loses speed and rolls into the corner by the pivot
                b.vx *= 0.9
                b.vy *= 0.9
            elif f.rising() and not f.kicked:
                # The flipper is swinging up into the ball: shoot it (once per stroke).
                # Rather than simulating the swing exactly, the shot speed is chosen.
                f.kicked = True
                self.snap_to_flipper(b, f, hit[0])
                if f.aimed:
                    self.aimed_shot(b, f)
                else:
                    t = hit[0]
                    power = 0.55 + 0.45 * t          # closer to the tip = harder
                    b.vy = -random.uniform(15.0, 19.5) * power
                    # Sideways: towards the other side of the table, plus some randomness
                    b.vx = -f.side * random.uniform(1.0, 7.0) + random.uniform(-1.5, 1.5)
                b.flipper_shot = True
                self.score += 10

    @staticmethod
    def collide_balls(a, b):
        """Bounce two balls off each other (during multiball).

        If they overlap, both are pushed apart by half the overlap. Then the
        part of their speeds along the line between their centres is
        exchanged (like billiard balls of equal weight), losing 5% of it.

        Args:
            a, b: the two Balls; changed in place.
        """
        if a.on_ramp or b.on_ramp:
            return
        dx, dy = b.x - a.x, b.y - a.y
        d = math.hypot(dx, dy)
        if d >= 2 * R or d < 1e-6:
            return
        # Unit vector from a to b, and how much each ball must move to stop overlapping
        nx, ny = dx / d, dy / d
        push = (2 * R - d) / 2
        a.x -= nx * push
        a.y -= ny * push
        b.x += nx * push
        b.y += ny * push
        # rel > 0: the balls are moving towards each other, so they bounce
        rel = (a.vx - b.vx) * nx + (a.vy - b.vy) * ny
        if rel > 0:
            a.vx -= rel * nx * 0.95
            a.vy -= rel * ny * 0.95
            b.vx += rel * nx * 0.95
            b.vy += rel * ny * 0.95

    def snap_to_flipper(self, b, f, t):
        """The flipper snaps up in one go; place the ball on top of the raised flipper
        so the fast-moving flipper can't overtake it and push it underneath.

        Args:
            b: the Ball being hit.
            f: the Flipper hitting it.
            t: where along the flipper the ball touched it (0 = pivot, 1 = tip).
        """
        # Put the flipper fully up straight away
        f.pos = 1.0
        f.timer = max(f.timer, 0.15)
        # Same spot along the now-raised flipper
        (ax, ay), (cx, cy) = f.pivot, f.tip()
        px, py = lerp(ax, cx, t), lerp(ay, cy, t)
        # Unit normal of the flipper (direction at right angles to it), pointing up
        dx, dy = cx - ax, cy - ay
        length = math.hypot(dx, dy)
        nx, ny = dy / length, -dx / length
        if ny > 0:
            nx, ny = -nx, -ny          # normal pointing up
        # Place the ball just above the flipper surface (0.02 extra so it isn't touching)
        b.x, b.y = px + nx * (R + 0.02), py + ny * (R + 0.02)
        b.x = clamp(b.x, 0.5, W - 0.5)

    def aimed_shot(self, b, f):
        """Aimed shot: try out a range of shots in simulation and pick one that
        hits the target (ramp, bumpers or top lanes).

        About 90 possible shots (two upward speeds x 45 sideways speeds) are
        simulated with trace(). They're grouped by what they hit first; then the
        AI picks a goal and chooses the middle shot of the ones that reach it.
        If no shot reaches a goal, a normal random flipper shot is used.

        Args:
            b: the Ball being shot; its speed is set in place.
            f: the Flipper that is shooting.
        """
        f.aimed = False
        # Aim for the ramp or the top lanes; the bumpers sit higher up and get
        # hit on the way back down, so they are only a last resort
        if f.side < 0:
            goals = random.choice([["ramp", "lane"], ["lane", "ramp"]])
        else:
            goals = random.choice([["lane", "ramp"], ["ramp", "lane"]])
        # outcomes: {"ramp": [(vx, vy), ...], "lane": [...], None: [...], ...}
        outcomes = {}
        for vy in (-16.5, -18.5):
            for i in range(-22, 23):
                vx = i * 0.5                # sideways speeds from -11 to +11 in steps of 0.5
                outcomes.setdefault(self.trace(b.x, b.y, vx, vy), []).append((vx, vy))
        # Take the first goal (in order of preference) that some shot can reach
        hits = []
        for goal in goals:
            hits = outcomes.get(goal, [])
            if hits:
                break
        if hits:
            # middle of the hitting shots = the safest choice, plus a little human error
            hits.sort()
            vx, vy = hits[len(hits) // 2]
            b.vx = vx + random.uniform(-0.3, 0.3)
            b.vy = vy
        else:
            b.vy = -random.uniform(15.0, 19.5)
            b.vx = -f.side * random.uniform(1.0, 7.0)

    def trace(self, x, y, vx, vy, max_time=1.2):
        """Ghost ball: what does a shot hit first? ('ramp', 'bumper', 'lane', 'sling' or None).

        Runs a simplified copy of the physics in step() for an imaginary ball,
        without changing the real game: gravity, the ramp entrance, the outer
        walls, the corner walls, slingshots and bumpers. It stops as soon as the
        ghost hits something interesting.

        Args:
            x, y: starting position of the shot.
            vx, vy: starting speed of the shot in cells / s.
            max_time: give up after this many simulated seconds.

        Returns:
            'ramp', 'lane', 'sling' or 'bumper' for the first thing hit, or None
            if the ghost falls back down (or time runs out) without hitting anything.
        """
        dt = DT / SUB                       # same step size as the real physics
        ghost = Ghost(x, y, vx, vy)
        t = 0.0
        while t < max_time:
            t += dt
            # Gravity and movement, just like in step()
            prev_y = ghost.y
            ghost.vy += GRAVITY * dt
            ghost.x += ghost.vx * dt
            ghost.y += ghost.vy * dt
            # Same ramp entrance test as in step()
            if (ghost.vy < -RAMP_MIN_SPEED and prev_y >= RAMP_ENTRY_Y > ghost.y
                    and RAMP_ENTRY_X[0] <= ghost.x <= RAMP_ENTRY_X[1]):
                return "ramp"
            # Side walls
            if ghost.x < 0.5:
                ghost.x, ghost.vx = 0.5, abs(ghost.vx) * 0.8
            elif ghost.x > W - 0.5:
                ghost.x, ghost.vx = W - 0.5, -abs(ghost.vx) * 0.8
            if ghost.y < 1.2 and int(ghost.x) in TOP_LANES:
                return "lane"
            # Top wall
            if ghost.y < 0.5:
                ghost.y, ghost.vy = 0.5, abs(ghost.vy) * 0.8
            # The corner walls are only near the top, so only check them there
            if ghost.y < 3.0:
                for a, c in WALLS:
                    collide_segment(ghost, a, c, restitution=0.6)
            for a, c in SLINGS:
                if on_front(ghost, a, c) and collide_segment(ghost, a, c) is not None:
                    return "sling"
            for bx, by in BUMPERS:
                if math.hypot(ghost.x - bx, ghost.y - by) < R + BUMPER_RADIUS:
                    return "bumper"
            if ghost.vy > 0 and ghost.y > 10.0:
                return None   # falls back without hitting anything
        return None

    def update(self):
        """One frame. Returns True when the last ball has drained.

        Steps: launch queued multiball balls, let the AI decide (in demo mode),
        run the physics in SUB small substeps, nudge stuck balls, remove drained
        balls (with ball save), count down the flash timers and move the camera.
        """
        # multiball: launch extra balls
        # Count all waiting delays down; when the first reaches 0, add a new ball
        if self.launch_queue:
            self.launch_queue = [t - DT for t in self.launch_queue]
            if self.launch_queue[0] <= 0:
                self.launch_queue.pop(0)
                self.balls.append(Ball())

        if not self.human:
            self.ai()
        # Physics in SUB small steps per frame: a fast ball moves less per step,
        # so it can't jump straight through a thin wall or flipper
        for _ in range(SUB):
            # move the flippers in substeps too, otherwise the ball can pass through them
            for f in self.flippers:
                f.update(DT / SUB)
            for b in self.balls:
                self.step(b, DT / SUB)
            # Check every pair of balls once (i < j) for ball-ball collisions
            for i in range(len(self.balls)):
                for j in range(i + 1, len(self.balls)):
                    self.collide_balls(self.balls[i], self.balls[j])

        for b in self.balls:
            # stuck ball? automatic nudge
            # (a ball held on a flipper on purpose doesn't count as stuck)
            held = any(f.ball is b for f in self.flippers)
            if not b.on_ramp and not held and b.speed() < 1.0:
                b.slow_time += DT
                if b.slow_time > 2.5:
                    self.nudge_ball(b)
            else:
                b.slow_time = 0.0

        # remove drained balls
        # (a ball has drained when it falls below the bottom of the table)
        self.ball_save = max(0.0, self.ball_save - DT)
        for b in [b for b in self.balls if b.y > H + 0.5]:
            self.balls.remove(b)
            for f in self.flippers:
                if f.ball is b:
                    f.release()
            if self.ball_save > 0:
                self.launch_queue.append(0.3)   # ball save: the ball comes straight back
        # Multiball ends when only one ball is left (in play or waiting to launch)
        if self.multiball and len(self.balls) + len(self.launch_queue) <= 1:
            self.multiball = False
            self.ramp_count = 0

        # Count down all flash timers, stopping at 0
        self.bumper_flash = [max(0.0, x - DT) for x in self.bumper_flash]
        self.sling_flash = [max(0.0, x - DT) for x in self.sling_flash]
        self.ramp_flash = max(0.0, self.ramp_flash - DT)
        self.lane_flash = max(0.0, self.lane_flash - DT)
        self.multiball_flash = max(0.0, self.multiball_flash - DT)

        # camera follows the lowest ball (the most dangerous one)
        # Balls on the ramp/launch lane sort first (False < True), so they're only
        # followed when no ball is free on the playfield.
        if self.balls:
            low = max(self.balls, key=lambda b: (not b.on_ramp, b.y))
            # Keep the ball about 4 rows from the top of the 8-row window,
            # without scrolling past the top or bottom of the table
            target = clamp(low.y - 4.0, 0.0, H - 8.0)
            # Move a quarter of the way there each frame: a smooth glide, not a jump
            self.cam += (target - self.cam) * 0.25

        return not self.balls and not self.launch_queue

    def nudge_ball(self, b, direction=None):
        """Give one ball a random push sideways and upwards ("tilt").

        Args:
            b: the Ball to nudge (balls on the ramp or in the launch lane are skipped).
            direction: -1 (left) or +1 (right); None picks a random side.
        """
        if b.on_ramp:
            return
        b.vx += (direction if direction else random.choice([-1, 1])) * random.uniform(3, 6)
        b.vy -= random.uniform(4, 8)
        b.slow_time = 0.0

    def nudge(self, direction):
        """Nudge all balls in the same direction (-1 = left, +1 = right)."""
        for b in self.balls:
            self.nudge_ball(b, direction)

    # ---------- drawing ----------
    def draw_segment(self, field, a, c, color, xr=(0, W - 1), mirrored=False):
        """Draw a line segment. mirrored=True: draw it as the mirror image of the left side,
        so left and right parts are symmetrical pixel for pixel.

        The line is sampled at 17 evenly spaced points; each point lights the
        pixel it falls in. (Without mirroring, rounding would make the left and
        right versions of a shape look slightly different.)

        Args:
            field: the 16x8 grid of colours being drawn (changed in place).
            a, c: (x, y) end points of the segment in playfield cells.
            color: (r, g, b) colour.
            xr: (first, last) columns the line may be drawn in.
            mirrored: see above.
        """
        if mirrored:
            # Flip the segment and the allowed columns to the left side ...
            a, c = (W - a[0], a[1]), (W - c[0], c[1])
            xr = (W - 1 - xr[1], W - 1 - xr[0])
        for i in range(17):
            t = i / 16
            x = int(clamp(lerp(a[0], c[0], t), xr[0], xr[1] + 0.999))
            y = int(clamp(lerp(a[1], c[1], t), 0, H - 1))
            if mirrored:
                x = W - 1 - x               # ... and flip the pixel back to the right side
            field[y][x] = color

    def render(self):
        """Draw the current state of the table on the LED matrix.

        First the whole 8x16 playfield is drawn into `field` (a list of 16 rows),
        layer by layer: things drawn later sit on top. Then either the 8 rows
        around the camera, or a squeezed overview of the whole table, is sent
        to the LEDs.
        """
        # Start with an all-black table: 16 rows of 8 pixels
        field = [[BLACK] * W for _ in range(H)]

        for i, (a, c) in enumerate(WALLS):
            self.draw_segment(field, a, c, WALL_COL, mirrored=i == 1)

        # the launch lane is only there while a ball is in it
        if any(b.on_ramp and b.ride == "launch" for b in self.balls):
            for (x, y) in self.launch_pixels:
                field[y][x] = LAUNCH_COL

        # the ramp sits above the playfield; every part already "earned" lights orange
        # blink / mb_blink switch between 0 and 1 several times a second while a flash runs
        blink = int(self.ramp_flash * 10) % 2 if self.ramp_flash > 0 else 0
        mb_blink = int(self.multiball_flash * 8) % 2 if self.multiball_flash > 0 else 0
        for (x, y), part in self.ramp_pixels.items():
            if self.multiball:
                col = RAMP_LIT if (mb_blink or self.multiball_flash == 0) else RAMP_HIT
            elif part < self.ramp_count:
                col = RAMP_LIT
            else:
                col = RAMP_HIT if blink else RAMP_COL
            field[y][x] = col

        for i, (a, c) in enumerate(SLINGS):
            self.draw_segment(field, a, c, SLING_HIT if self.sling_flash[i] > 0 else SLING_COL,
                              mirrored=i == 1)

        # Top lanes in the top row: bright when lit, all blinking after the bonus
        for i, col in enumerate(TOP_LANES):
            on = self.lane_lit[i] or (self.lane_flash > 0 and int(self.lane_flash * 10) % 2)
            field[0][col] = TOP_LANE_HIT if on else TOP_LANE_COL

        # Each bumper is one pixel (the one its centre is in)
        for i, (bx, by) in enumerate(BUMPERS):
            field[int(by)][int(bx)] = BUMPER_HIT if self.bumper_flash[i] > 0 else BUMPER_COL

        for f in self.flippers:
            color = FLIPPER_UP if f.pos > 0.3 else FLIPPER_COL
            self.draw_segment(field, f.pivot, f.tip(), color, f.pixel_range,
                              mirrored=f.side > 0)
            # the tip: half a pixel, drawn at half brightness next to the flipper
            tip_x = 3 if f.side < 0 else 4
            tip_y = int(clamp(f.tip()[1], 0, H - 1))
            if field[tip_y][tip_x] == BLACK:
                field[tip_y][tip_x] = tuple(c // 2 for c in color)

        # ball save active: the gap between the flippers blinks green
        if self.ball_save > 0 and int(self.ball_save * 4) % 2 == 0:
            field[H - 1][3] = field[H - 1][4] = TOP_LANE_HIT

        # Balls last, so they're always visible on top of everything else
        for b in self.balls:
            bx, by = int(b.x), int(b.y)
            if 0 <= bx < W and 0 <= by < H:
                field[by][bx] = BALL_COL

        if self.overview:
            # Overview: squeeze 16 rows into 8 by combining each pair of rows,
            # keeping the brighter pixel (largest r+g+b) so balls stay visible
            rows = []
            for r in range(8):
                rows.append([max(field[2 * r][x], field[2 * r + 1][x], key=sum) for x in range(W)])
        else:
            # Follow camera: show the 8 rows starting at the camera position
            top = int(round(self.cam))
            rows = field[top:top + 8]

        self.sense.set_pixels([p for row in rows for p in row])

    # ---------- joystick ----------
    def handle_input(self):
        """React to the joystick for this frame.

        In demo mode any press starts a game for the human player. While a
        human plays: left/right flip, up/down nudge, middle toggles the
        overview, and holding a direction keeps that flipper up. If the player
        doesn't touch the joystick for IDLE_TIMEOUT seconds, the demo resumes.
        """
        pressed = self.stick.read()
        if not self.human:
            if pressed:
                self.start_player_game()
            return
        if self.stick.idle():                 # Player walked away: computer takes over
            self.human = False
            return
        for key in pressed:
            if key == "left":
                self.left.flip(force=True)
            elif key == "right":
                self.right.flip(force=True)
            elif key in ("up", "down"):
                self.nudge(random.choice([-1, 1]))
            elif key == "middle":
                self.overview = not self.overview
        # Keep a flipper up for as long as its direction is held
        for key, f in (("left", self.left), ("right", self.right)):
            if key in self.stick.held:
                f.timer = max(f.timer, 0.08)

    def new_game(self):
        """Reset score, balls, lanes, ramp progress and multiball for a fresh game."""
        self.score = 0
        self.balls_left = BALLS_PER_GAME
        self.lane_lit = [False] * len(TOP_LANES)
        self.ramp_count = 0
        self.multiball = False
        self.ball_save = 0.0
        self.launch_queue = []
        for f in self.flippers:
            f.release()
        self.balls = [Ball()]
        self.cam = 8.0

    def start_player_game(self):
        """Hand control to the human: show the play sign and start a new game."""
        show_play_sign(self.sense)
        self.stick.flush()          # the press that woke us up shouldn't flip a flipper
        self.human = True
        self.overview = False
        self.new_game()

    # ---------- game flow ----------
    def ball_lost(self):
        """Called when the last ball has drained.

        Flashes the screen red and takes away a ball. With balls left, it shows
        how many remain and launches a new ball (ramp progress and lit lanes
        stay). At game over it scrolls the score, starts a new game and goes
        back to demo mode.
        """
        # Short red flash
        self.sense.clear(255, 0, 0)
        time.sleep(0.15)
        self.sense.clear()
        self.balls_left -= 1
        if self.balls_left <= 0:
            # Game over: show the score
            self.sense.show_message(f"Score {self.score}", scroll_speed=0.06,
                                    text_colour=[255, 200, 0])
            self.new_game()
            self.human = False        # Back to the demo; touch the joystick to play again
            self.stick.flush()
            return
        # Show the number of balls left, then serve the next ball
        self.sense.show_letter(str(self.balls_left), text_colour=[0, 150, 255])
        time.sleep(0.8)
        for f in self.flippers:
            f.release()
        self.multiball = False
        self.balls = [Ball()]
        self.cam = 8.0

    def play(self):
        """The main loop: input, update and draw, FPS times per second, forever.

        `next_frame` is the time the next frame is due. After each frame we sleep
        until then; this keeps a steady pace even if a frame takes a bit longer
        to compute. If we fall behind (or after a pause such as a ball-lost
        message), the schedule is reset to "now" instead of rushing to catch up.
        """
        next_frame = time.monotonic()
        while True:
            self.handle_input()
            if self.update():
                self.ball_lost()
                next_frame = time.monotonic()
                continue
            self.render()
            next_frame += DT
            delay = next_frame - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_frame = time.monotonic()


def main():
    """Prepare the Sense HAT and start the pinball machine."""
    Pinball(init(hat)).play()


if __name__ == "__main__":
    run(main)
