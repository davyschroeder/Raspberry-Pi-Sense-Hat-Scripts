#!/usr/bin/env python3
"""Sense HAT – Rubik's Cube: a real, working cube to turn with the joystick.

The display shows the front face of the cube: 3x3 stickers of 2x2 pixels.
The computer plays a demo until you touch the joystick: it scrambles the cube
and then solves it again, using the same moves you can make. Then it's your
turn: every time you solve the cube, the next scramble is a bit longer.

Controls:
- Push the joystick to move the cursor (a blinking frame around a sticker).
  Push past the edge of the face to turn the whole cube and look at the
  next face.
- Press to grab the row and the column under the cursor (lines appear along
  them). Now push left / right to turn that row, up / down to turn that
  column. Press again to let go and move the cursor.
- Hold the press for 2 seconds: the computer solves the cube for you, and
  you get a new scramble.
After 30 seconds without input the demo starts again.
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

# 8x8 picture of a "play" triangle, shown when a human takes over ("#" = lit LED)
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


# ---------------------------------------------------------------------------
# Colours and timing
# ---------------------------------------------------------------------------
OFF = (0, 0, 0)
# The six standard colours: white opposite yellow, green opposite blue, red opposite orange
COLORS = {
    "white": (255, 255, 255),
    "yellow": (255, 210, 0),
    "green": (0, 190, 0),
    "blue": (0, 60, 255),
    "red": (220, 0, 0),
    "orange": (255, 85, 0),
}
CURSOR = (200, 200, 200)     # The blinking frame around the sticker under the cursor
RAIL = (110, 110, 110)       # The lines along the row and column you have grabbed

TICK = 0.02                  # Seconds per animation step
TURN_TIME = 0.25             # Seconds for turning a row or column
VIEW_TIME = 0.35             # Seconds for turning the whole cube
SCRAMBLE_TIME = 0.12         # Seconds per turn while scrambling (quick!)
DEMO_STEP = 0.14             # Seconds per cursor step when the computer plays
HOLD_TIME = 2.0              # Hold the press this long to let the computer solve it
DEMO_SCRAMBLE = 18           # Turns in the demo's scramble
LEVELS = [2, 3, 4, 5, 6, 8, 10, 12, 15, 20]    # Turns in your scrambles, one level after the other


# ---------------------------------------------------------------------------
# The cube itself
# ---------------------------------------------------------------------------
# The cube sits in the middle of a 3D grid: x goes to the right, y goes up and
# z comes out of the screen towards you. Each of the 27 little cubes ("cubies")
# sits at a position where x, y and z are -1, 0 or 1. A sticker is glued on one
# side of a cubie; that side is its "normal", a direction like (0, 0, 1) for
# a sticker that faces you. The 54 stickers are all the cube needs to know.
#
# A move turns one slice of the cube a quarter turn around one of the three
# axes (0 = x, 1 = y, 2 = z). It is written as (axis, layer, turns):
# - layer is -1, 0 or 1: which slice along that axis turns (None = all of
#   them, so the whole cube turns, which is how you look at another face),
# - turns is 1 or -1: a quarter turn one way or the other way round.

def rotate(v, axis, turns):
    """Turn the point or direction v = (x, y, z) a quarter turn around an axis.

    turns = 1 is anticlockwise when you look at the axis from its positive end
    (the "right-hand rule"), -1 is clockwise.
    """
    x, y, z = v
    for _ in range(turns % 4):                # -1 is the same as three quarter turns
        if axis == 0:
            x, y, z = x, -z, y
        elif axis == 1:
            x, y, z = z, y, -x
        else:
            x, y, z = -y, x, z
    return (x, y, z)


def inverse(move):
    """The move that undoes a move: the same slice, turned back."""
    axis, layer, turns = move
    return (axis, layer, -turns)


def carry(move, rotation):
    """The same move after the whole cube was turned by `rotation`.

    Turning the whole cube moves the slice too: what was the right column
    can now be the top row. This works out which axis and layer it is now.
    """
    axis, layer, turns = move
    direction = [0, 0, 0]
    direction[axis] = 1
    new = rotate(tuple(direction), rotation[0], rotation[2])
    new_axis = [abs(c) for c in new].index(1)
    if new[new_axis] < 0:
        # The axis now points the other way: that flips the layer and the turn
        return (new_axis, None if layer is None else -layer, -turns)
    return (new_axis, layer, turns)


class Cube:
    """The 54 stickers of the cube, each as [position, normal, colour]."""

    def __init__(self):
        """A solved cube: white on top, green at the front, red on the right."""
        sides = {
            (0, 1, 0): "white", (0, -1, 0): "yellow",
            (0, 0, 1): "green", (0, 0, -1): "blue",
            (1, 0, 0): "red", (-1, 0, 0): "orange",
        }
        self.stickers = []
        for normal, color in sides.items():
            axis = [abs(c) for c in normal].index(1)
            for a in (-1, 0, 1):
                for b in (-1, 0, 1):
                    # The cubie is on the side the normal points to; the other
                    # two coordinates (a and b) run over the 3x3 grid of that face
                    pos = [a, b]
                    pos.insert(axis, normal[axis])
                    self.stickers.append([tuple(pos), normal, color])

    def turn(self, move):
        """Make a move (see above): every sticker in the slice turns along."""
        axis, layer, turns = move
        for sticker in self.stickers:
            if layer is None or sticker[0][axis] == layer:
                sticker[0] = rotate(sticker[0], axis, turns)
                sticker[1] = rotate(sticker[1], axis, turns)

    def front(self):
        """The colours of the face towards you, as 3 rows of 3 (top row first)."""
        grid = [[None] * 3 for _ in range(3)]
        for (x, y, _), normal, color in self.stickers:
            if normal == (0, 0, 1):
                grid[1 - y][x + 1] = color
        return grid

    def solved(self):
        """True when every face has one colour."""
        faces = {}
        for _, normal, color in self.stickers:
            faces.setdefault(normal, set()).add(color)
        return all(len(colors) == 1 for colors in faces.values())

    def state(self):
        """Everything about the cube as one value (handy for testing)."""
        return sorted((pos, normal, color) for pos, normal, color in self.stickers)


def solution(history):
    """The moves that undo everything in `history`, from last to first.

    Turns of the whole cube are left out: undoing them only changes which
    face you look at, not whether the cube is solved. The moves after such a
    turn are carried over to the cube the way it is now. Moves that cancel
    out (a turn and its opposite) are dropped too.
    """
    moves, rotations = [], []
    for move in (inverse(m) for m in reversed(history)):
        if move[1] is None:
            rotations.append(move)
        else:
            for rotation in reversed(rotations):
                move = carry(move, inverse(rotation))
            moves.append(move)
    return simplify(moves)


def simplify(moves):
    """Join turns of the same slice that follow each other: a turn and its
    opposite cancel out, and three quarter turns are one the other way."""
    out = []                       # [axis, layer, quarter turns 0..3]
    for axis, layer, turns in moves:
        if out and out[-1][:2] == [axis, layer]:
            out[-1][2] = (out[-1][2] + turns) % 4
            if out[-1][2] == 0:
                out.pop()
        else:
            out.append([axis, layer, turns % 4])
    result = []
    for axis, layer, turns in out:
        if turns == 2:
            result += [(axis, layer, 1), (axis, layer, 1)]    # A half turn: two quarter turns
        else:
            result.append((axis, layer, 1 if turns == 1 else -1))
    return result


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
def dim(color, amount):
    """The colour made darker: amount 1 keeps it, 0.5 is half as bright."""
    return tuple(int(c * amount) for c in color)


def transpose(frame):
    """Swap rows and columns of an 8x8 picture (mirror it over the diagonal)."""
    return [list(row) for row in zip(*frame)]


def picture(cube):
    """The front face as an 8x8 picture: stickers of 2x2 pixels with dark
    lines of 1 pixel between them (2 + 1 + 2 + 1 + 2 = 8)."""
    frame = [[OFF] * 8 for _ in range(8)]
    for row, colors in enumerate(cube.front()):
        for col, color in enumerate(colors):
            for dy in (0, 1):
                for dx in (0, 1):
                    frame[3 * row + dy][3 * col + dx] = COLORS[color]
    return frame


def ease(t):
    """Smooth start and stop: 0 -> 0, 1 -> 1, slow at both ends."""
    return (1 - math.cos(math.pi * t)) / 2


def slide(old, new, forward, amount):
    """One line of 8 pixels sliding over to the next stickers.

    The old line moves out and the new one follows it in, with the dark gap
    between them, so it looks like the stickers really move along.

    Args:
        old, new: the 8 colours of the line before and after the turn.
        forward: True when the stickers move right (or down), False for left (or up).
        amount: 0 = only old, 1 = only new.
    """
    shift = int(round(9 * amount))           # 3 stickers of 3 pixels = 9 pixels to go
    if forward:
        strip = new + [OFF] + old            # The new stickers come in from the left
        return strip[9 - shift:17 - shift]
    strip = old + [OFF] + new                # The new stickers come in from the right
    return strip[shift:shift + 8]


def turn_frame(old, new, direction, amount):
    """One in-between picture while the whole cube turns.

    The cube is drawn like a box that turns: the face you were looking at
    gets narrower and darker as it turns away, while the next face comes into
    view from the opposite edge, wider and brighter.

    Args:
        old, new: the 8x8 pictures of the face before and after.
        direction: where the old face goes: "left", "right", "up" or "down".
        amount: how far it has turned, from 0 (only old) to 1 (only new).
    """
    if direction in ("up", "down"):
        # Up and down are left and right with rows and columns swapped
        side = "left" if direction == "up" else "right"
        return transpose(turn_frame(transpose(old), transpose(new), side, amount))
    width = 8 * amount                        # How many columns the new face takes
    frame = [[OFF] * 8 for _ in range(8)]
    for x in range(8):
        c = x + 0.5
        new_from = 0 if direction == "right" else 8 - width
        old_from = width if direction == "right" else 0
        if new_from <= c < new_from + width:
            src, col, light = new, int((c - new_from) / width * 8), 0.35 + 0.65 * amount
        else:
            src, col, light = old, int((c - old_from) / (8 - width) * 8), 1 - 0.65 * amount
        col = max(0, min(7, col))
        for y in range(8):
            frame[y][x] = dim(src[y][col], light)
    return frame


def move_direction(move):
    """Which way the stickers on the front face go for a move."""
    axis, _, turns = move
    if axis == 0:
        return "down" if turns > 0 else "up"
    return "right" if turns > 0 else "left"


# Pushing the cursor past an edge turns the whole cube to show the face behind
# that edge. E.g. past the left edge: the cube turns to the right, so the left
# face comes to the front.
EDGE_TURNS = {
    "left": (1, None, 1),
    "right": (1, None, -1),
    "up": (0, None, 1),
    "down": (0, None, -1),
}
STEPS = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}


class TakeOver(Exception):
    """Raised during the demo when someone touches the joystick."""


class PlayerLeft(Exception):
    """Raised when the player has not touched the joystick for IDLE_TIMEOUT seconds."""


class Game:
    """The cube on the display, with the cursor, the player and the demo."""

    def __init__(self, sense, stick):
        """A solved cube, the demo playing."""
        self.sense = sense
        self.stick = stick
        self.cube = Cube()
        self.history = []          # Every move since the cube was last solved
        self.human = False         # A person is playing (False = the demo)
        self.auto = False          # The computer is solving it for the player
        self.cursor = [1, 1]       # Row and column of the sticker under the cursor
        self.grabbed = False       # Holding a row and column (True) or moving the cursor
        self.show_cursor = False
        self.queue = []            # Joystick presses still to handle
        self.ticks = 0             # Counts every TICK, for the blinking
        self.hold = 0              # How many ticks the joystick has been held in (-1: wait for release)
        self.solved_count = 0      # Cubes the player solved this time

    # ---------- drawing and waiting ----------
    def frame(self, pic=None, cursor=True):
        """The picture to show: the front face plus the cursor."""
        frame = [row[:] for row in (pic or picture(self.cube))]
        if not (self.show_cursor and cursor):
            return frame
        r, c = self.cursor
        x0, y0 = 3 * c, 3 * r                 # Top-left pixel of the sticker
        if self.grabbed:
            # Lines along the grabbed row and column, in the dark gaps beside them
            for y in (y0 - 1, y0 + 2):
                if 0 <= y <= 7:
                    frame[y] = [RAIL] * 8
            for x in (x0 - 1, x0 + 2):
                if 0 <= x <= 7:
                    for y in range(8):
                        frame[y][x] = RAIL
        elif self.ticks % 25 < 17:
            # A blinking frame in the dark gaps around the sticker
            for y in range(y0 - 1, y0 + 3):
                for x in range(x0 - 1, x0 + 3):
                    edge = x in (x0 - 1, x0 + 2) or y in (y0 - 1, y0 + 2)
                    if edge and 0 <= x <= 7 and 0 <= y <= 7:
                        frame[y][x] = CURSOR
        return frame

    def show(self, pic=None, cursor=True):
        """Send the picture to the LEDs."""
        self.sense.set_pixels(flatten(self.frame(pic, cursor)))

    def wait(self, seconds, redraw=True):
        """Wait `seconds`, reading the joystick (and redrawing for the blink).

        During the demo any touch raises TakeOver; when the player walked
        away it raises PlayerLeft. A player's presses are kept in self.queue.
        """
        for _ in range(max(1, round(seconds / TICK))):
            keys = self.stick.read()
            if keys and not self.human:
                raise TakeOver()
            if self.human and not self.auto and self.stick.idle():
                raise PlayerLeft()
            self.queue += keys
            if "middle" not in self.stick.held:
                self.hold = 0                  # Let go (or never pressed)
            elif self.hold >= 0:
                self.hold += 1                 # Still holding it in
            # (self.hold is -1 after a hold did its work: it waits until you let go)
            self.ticks += 1
            if redraw:
                self.show()
            time.sleep(TICK)

    # ---------- making moves ----------
    def do(self, move, seconds):
        """Make a move, or turn the whole cube, with an animation.

        Rows and columns slide over; the whole cube turns like a box. Every
        move is remembered in self.history, so it can be undone later.
        """
        old = picture(self.cube)
        self.cube.turn(move)
        self.history.append(move)
        new = picture(self.cube)
        steps = max(2, round(seconds / TICK))
        for step in range(1, steps + 1):
            amount = ease(step / steps)
            if move[1] is None:
                # The whole cube: no cursor while it turns
                pic = turn_frame(old, new, move_direction(move), amount) if step < steps else new
                self.show(pic, cursor=False)
            else:
                pic = [row[:] for row in old]
                axis, layer, turns = move
                forward = turns > 0
                if axis == 0:                         # A column slides up or down
                    for x in (3 * (layer + 1), 3 * (layer + 1) + 1):
                        line = slide([r[x] for r in old], [r[x] for r in new], forward, amount)
                        for y in range(8):
                            pic[y][x] = line[y]
                else:                                 # A row slides left or right
                    for y in (3 * (1 - layer), 3 * (1 - layer) + 1):
                        pic[y] = slide(old[y], new[y], forward, amount)
                self.show(pic)
            self.wait(TICK, redraw=False)

    def scramble(self, count, seconds):
        """Mix the cube up with `count` random turns of rows and columns, and
        now and then a turn of the whole cube."""
        self.show_cursor = False
        self.grabbed = False
        last = None
        done = 0
        while done < count or self.cube.solved():
            if random.random() < 0.15:
                self.do(random.choice(list(EDGE_TURNS.values())), seconds * 2)
                continue
            move = (random.randint(0, 1), random.randint(-1, 1), random.choice((-1, 1)))
            if last and move[:2] == last[:2]:
                continue                       # Never the same slice twice in a row
            self.do(move, seconds)
            last = move
            done += 1

    # ---------- the cursor ----------
    def step_cursor(self, key):
        """Move the cursor one sticker; past the edge the whole cube turns."""
        r, c = self.cursor
        dr, dc = STEPS[key]
        if 0 <= r + dr <= 2 and 0 <= c + dc <= 2:
            self.cursor = [r + dr, c + dc]
        else:
            self.do(EDGE_TURNS[key], VIEW_TIME)
            # The cursor stays on the same cubie, which is now at the opposite edge
            self.cursor = [(r + dr) % 3, (c + dc) % 3]

    def walk_to(self, row, col):
        """The computer moves the cursor to (row, col), one step at a time."""
        if self.grabbed:
            self.grabbed = False
            self.wait(DEMO_STEP)
        while self.cursor != [row, col]:
            r, c = self.cursor
            if c != col:
                self.step_cursor("right" if col > c else "left")
            else:
                self.step_cursor("down" if row > r else "up")
            self.wait(DEMO_STEP)

    def on_line(self, move):
        """True when the move turns the row or column through the cursor."""
        axis, layer, _ = move
        if axis == 0:
            return layer + 1 == self.cursor[1]
        return axis == 1 and 1 - layer == self.cursor[0]

    def solve(self, moves, seconds=TURN_TIME):
        """The computer makes these moves the way a player would: walk the
        cursor to the right row or column, grab it and push."""
        self.show_cursor = True
        pending = list(moves)
        while pending:
            move = pending[0]
            if move[0] == 2:
                # A slice parallel to the front can't be turned from here: walk
                # off the left or right edge to look at the cube from the side.
                # Turning the cube carries all the moves still to come along.
                side = random.choice(("left", "right"))
                self.walk_to(self.cursor[0], 0 if side == "left" else 2)
                self.step_cursor(side)
                pending = [carry(m, EDGE_TURNS[side]) for m in pending]
                self.wait(DEMO_STEP)
                continue
            if not (self.grabbed and self.on_line(move)):
                axis, layer, _ = move
                if axis == 0:
                    self.walk_to(self.cursor[0], layer + 1)
                else:
                    self.walk_to(1 - layer, self.cursor[1])
                self.grabbed = True
                self.wait(0.3)
            self.do(move, seconds)
            pending.pop(0)
            self.wait(0.12)
        self.grabbed = False
        self.history = []

    def celebrate(self):
        """Solved! The cube spins round to show off all six faces, then sparkles."""
        self.show_cursor = False
        self.grabbed = False
        for turn in [EDGE_TURNS["left"]] * 4 + [EDGE_TURNS["up"]] * 4:
            self.do(turn, 0.25)
            self.wait(0.05)
        for step in range(60):
            frame = picture(self.cube)
            light = 0.5 + 0.5 * math.cos(step / 4)
            for y in range(8):
                for x in range(8):
                    if frame[y][x] != OFF:
                        frame[y][x] = dim(frame[y][x], 0.55 + 0.45 * light)
            for _ in range(4):                # Twinkles in the gaps and on the stickers
                frame[random.randint(0, 7)][random.randint(0, 7)] = (255, 255, 255)
            self.show(frame, cursor=False)
            self.wait(0.04, redraw=False)
        self.history = []

    # ---------- the player ----------
    def handle(self, key):
        """Do what a joystick press means right now."""
        if key == "middle":
            self.grabbed = not self.grabbed       # Grab or let go
        elif self.grabbed:
            r, c = self.cursor
            if key in ("left", "right"):
                self.do((1, 1 - r, 1 if key == "right" else -1), TURN_TIME)
            else:
                self.do((0, c - 1, 1 if key == "down" else -1), TURN_TIME)
        else:
            self.step_cursor(key)

    def play_human(self):
        """Scramble, let the player solve it, and the next scramble is longer.
        Runs until the player walks away (PlayerLeft)."""
        self.cube = Cube()
        self.history = []
        self.solved_count = 0
        level = 0
        self.show_cursor = False
        self.wait(0.8)
        while True:
            self.scramble(LEVELS[min(level, len(LEVELS) - 1)], SCRAMBLE_TIME * 1.5)
            self.cursor = [1, 1]
            self.show_cursor = True
            self.queue = []
            while True:
                self.wait(TICK)
                if self.hold * TICK >= HOLD_TIME:
                    # Held in: the computer shows how to solve it, then a new scramble
                    self.hold = -1
                    self.grabbed = False
                    self.auto = True
                    self.wait(0.4)
                    self.solve(solution(self.history))
                    self.auto = False
                    self.stick.last_input = time.monotonic()
                    break
                if self.queue:
                    self.handle(self.queue.pop(0))
                    if self.cube.solved() and self.history:
                        self.celebrate()
                        self.solved_count += 1
                        level += 1
                        break
            self.queue = []

    # ---------- the demo ----------
    def demo(self):
        """Scramble the cube, look around, solve it again, and so on."""
        self.show_cursor = False
        while True:
            if not self.cube.solved():
                self.wait(0.8)
                self.cursor = [1, 1]
                self.solve(solution(self.history))
                self.celebrate()
            self.show_cursor = False
            for _ in range(2):                    # Look round the solved cube
                self.wait(0.6)
                self.do(random.choice(list(EDGE_TURNS.values())), VIEW_TIME * 1.5)
            self.wait(0.8)
            self.scramble(DEMO_SCRAMBLE, SCRAMBLE_TIME)
            self.wait(1.0)


def main():
    """Run the demo, and let a person play whenever they take the joystick."""
    sense = init(hat)
    stick = Joystick(sense)
    game = Game(sense, stick)
    while True:
        try:
            if game.human:
                game.play_human()
            else:
                game.demo()
        except TakeOver:
            # Someone touched the joystick during the demo: their turn
            show_play_sign(sense)
            stick.flush()
            game.human = True
        except PlayerLeft:
            # Nobody touched the joystick for a while: say how many cubes they
            # solved, and the demo takes over (it solves this cube first)
            game.human = False
            game.auto = False
            sense.show_message(f"Solved {game.solved_count}", text_colour=COLORS["white"],
                               scroll_speed=0.06)
            stick.flush()


if __name__ == "__main__":
    run(main)
