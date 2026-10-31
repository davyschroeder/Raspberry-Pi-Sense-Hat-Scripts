#!/usr/bin/env python3
"""Sense HAT – Heads or tails: flip a spinning coin.

Press the joystick (or push it left or right) to flip; push it up or down to
see the tally of heads and tails. The coin waits for you: there is no demo.
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
# The two sides of the coin
# ---------------------------------------------------------------------------
OFF = (0, 0, 0)
HEADS = "heads"
TAILS = "tails"

# Each side as an 8x8 picture: "r" = the rim, "f" = the face of the coin,
# "i" = the picture stamped on it, "." = no coin there.
# Heads is the golden side with a smiling head on it ...
HEADS_PICTURE = [
    "..rrrr..",
    ".rffffr.",
    "rfiffifr",
    "rffffffr",
    "rfiffifr",
    "rffiiffr",
    ".rffffr.",
    "..rrrr..",
]
# ... tails is the silver side with the value: a big 1
TAILS_PICTURE = [
    "..rrrr..",
    ".rfiifr.",
    "rfiiiffr",
    "rffiiffr",
    "rffiiffr",
    "rffiiffr",
    ".riiiir.",
    "..rrrr..",
]
# The colours of each side: rim, face and picture
COLORS = {
    HEADS: {"r": (90, 45, 0), "f": (150, 80, 0), "i": (255, 235, 90)},
    TAILS: {"r": (50, 53, 65), "f": (95, 100, 115), "i": (255, 255, 255)},
}
PICTURES = {HEADS: HEADS_PICTURE, TAILS: TAILS_PICTURE}
# The tally screen: one pixel per flip, heads on the left, tails on the right
# (lit in the colour of the picture; the empty places glow faintly)
TALLY_EMPTY = {HEADS: (16, 8, 0), TAILS: (9, 9, 11)}

TICK = 0.02                 # Seconds per step of the animation
FLIP_FRAMES = 70            # Steps the coin is in the air (70 x 0.02 = 1.4 seconds)
TOSS_HEIGHT = 3.0           # How many pixels the coin flies up
TALLY_TIME = 2.0            # Seconds the tally stays on screen
FLIP_KEYS = ("middle", "left", "right")


def dim(color, amount):
    """The colour made darker: amount 1 keeps it, 0.5 is half as bright."""
    return tuple(int(c * amount) for c in color)


def other(side):
    """The other side of the coin."""
    return TAILS if side == HEADS else HEADS


def side_picture(side):
    """The 8x8 colours of one side of the coin, lying flat."""
    colors = COLORS[side]
    return [[colors.get(c, OFF) for c in row] for row in PICTURES[side]]


def coin_frame(side, height, centre, light=1.0):
    """The coin seen at an angle while it spins.

    A spinning coin looks squashed: seen flat it is 8 pixels tall, seen from
    the edge only a thin line. Every row of the screen picks the row of the
    coin picture that lands there.

    Args:
        side: the side facing us, HEADS or TAILS.
        height: how tall the coin looks, in pixels (8 = flat).
        centre: the screen row (as a float) where the middle of the coin is.
        light: brightness, lower while the coin is turned away from us.
    """
    picture = side_picture(side)
    frame = [[OFF] * 8 for _ in range(8)]
    if height < 1.2:
        # Edge on: just the rim, as a thin line
        y = int(centre)
        if 0 <= y <= 7:
            frame[y] = [OFF] + [COLORS[side]["r"]] * 6 + [OFF]
        return frame
    for y in range(8):
        v = (y + 0.5 - centre) / (height / 2)     # -1 at the top of the coin, 1 at the bottom
        if -1 <= v < 1:
            row = picture[int((v + 1) / 2 * 8)]
            frame[y] = [dim(c, light) for c in row]
    return frame


def glint_frame(side, step):
    """The coin lying still with a diagonal glint of light sweeping over it."""
    frame = side_picture(side)
    for y in range(8):
        for x in range(8):
            d = abs(x + y - step)             # Distance from the glint, a diagonal line
            if frame[y][x] != OFF and d < 2:
                mix = 0.6 if d == 0 else 0.25
                frame[y][x] = tuple(int(c + (255 - c) * mix) for c in frame[y][x])
    return frame


def tally_frame(tally):
    """The tally: one lit pixel per flip, gold for heads (left half), silver for
    tails (right half), filling up from the bottom. Up to 32 of each fit."""
    frame = [[OFF] * 8 for _ in range(8)]
    for side, left in ((HEADS, 0), (TAILS, 4)):
        for i in range(32):
            x, y = left + i % 4, 7 - i // 4
            frame[y][x] = COLORS[side]["i"] if i < tally[side] else TALLY_EMPTY[side]
    return frame


def wait(stick, seconds):
    """Wait `seconds` while listening to the joystick; returns what was pressed."""
    pressed = []
    for _ in range(max(1, round(seconds / TICK))):
        pressed += stick.read()
        time.sleep(TICK)
    return pressed


def flip(sense, stick, side):
    """Toss the coin: it flies up spinning, falls back, wobbles and lies still.

    The result is decided first (a fair 50/50 chance); then the number of half
    turns is chosen so the coin really lands on that side.

    Returns the side it landed on.
    """
    result = random.choice((HEADS, TAILS))
    half_turns = random.randint(9, 13)
    if (half_turns % 2 == 1) != (result != side):
        half_turns += 1                       # An odd number of half turns swaps the side
    for i in range(1, FLIP_FRAMES + 1):
        t = i / FLIP_FRAMES                   # 0 at the toss, 1 when it lands
        angle = half_turns * math.pi * (1 - (1 - t) ** 2)    # Spins fast at first, slower later
        up = TOSS_HEIGHT * math.sin(math.pi * t)              # Up and down again
        turn = math.cos(angle)                # 1 = facing us, 0 = edge on, -1 = the back
        showing = side if turn >= 0 else other(side)
        sense.set_pixels(flatten(coin_frame(showing, 8 * abs(turn), 4 - up,
                                            0.45 + 0.55 * abs(turn))))
        wait(stick, TICK)
    # It lands with a few wobbles, smaller every time
    for squash in (0.3, 0.15, 0.05):
        for height in (8 - 8 * squash, 8):
            sense.set_pixels(flatten(coin_frame(result, height, 4)))
            wait(stick, 0.06)
    # And a glint of light sweeps over it
    for step in range(-2, 17):
        sense.set_pixels(flatten(glint_frame(result, step)))
        wait(stick, 0.03)
    sense.set_pixels(flatten(side_picture(result)))
    return result


def show_tally(sense, stick, tally, side):
    """Show the tally for TALLY_TIME seconds (or until the next press)."""
    sense.set_pixels(flatten(tally_frame(tally)))
    for _ in range(round(TALLY_TIME / TICK)):
        keys = wait(stick, TICK)
        if keys:
            sense.set_pixels(flatten(side_picture(side)))
            return keys
    sense.set_pixels(flatten(side_picture(side)))
    return []


def play(sense, stick):
    """Wait for the joystick and flip the coin, forever."""
    side = random.choice((HEADS, TAILS))
    tally = {HEADS: 0, TAILS: 0}
    sense.set_pixels(flatten(side_picture(side)))
    while True:
        for key in wait(stick, TICK):
            if key in FLIP_KEYS:
                side = flip(sense, stick, side)
                tally[side] += 1
                stick.read()                  # Forget presses made while the coin flew
                break
            if key in ("up", "down"):
                # Showing the tally; a press during it is handled right away
                more = show_tally(sense, stick, tally, side)
                if any(k in FLIP_KEYS for k in more):
                    side = flip(sense, stick, side)
                    tally[side] += 1
                    stick.read()
                break


def main():
    """Show the coin and flip it whenever the joystick is pressed."""
    sense = init(hat)
    stick = Joystick(sense)
    play(sense, stick)


if __name__ == "__main__":
    run(main)
