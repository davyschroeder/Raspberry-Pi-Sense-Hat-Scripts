#!/usr/bin/env python3
"""Sense HAT – Peg drop: drop a ball through a board of pegs into the slots.

The board is twice as tall as the display; the view follows the ball down.
Every slot at the bottom is worth points (gold 10, purple 5, green 2, blue 1)
and you get 5 balls per game. The computer plays a demo until you touch the
joystick: then you aim with left / right and drop the ball with a press (or
down). After the game, or 30 seconds without input, the demo starts again.
"""
import atexit
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
# The board
# ---------------------------------------------------------------------------
# The whole board, 16 rows tall: "o" = a peg. Row 0 shows how many balls you
# have left, the ball waits in row 1 until you drop it, and the slots are the
# bottom row (row 15).
BOARD = [
    "........",
    "........",
    "........",
    ".o.o.o.o",
    "........",
    "o.o.o.o.",
    "........",
    ".o.o.o.o",
    "........",
    "o.o.o.o.",
    "........",
    ".o.o.o.o",
    "........",
    "o.o.o.o.",
    "........",
    "........",
]
HEIGHT = len(BOARD)
SLOT_ROW = HEIGHT - 1
VALUES = [10, 2, 5, 1, 1, 5, 2, 10]          # Points for each slot, left to right

OFF = (0, 0, 0)
PEG = (45, 45, 80)
PEG_HIT = (255, 255, 255)                    # A peg flashes when the ball hits it
BALL = (255, 240, 170)
BALL_LEFT = (90, 80, 40)                     # The balls you still have, in row 0
SLOT_COLORS = {10: (255, 180, 0), 5: (200, 0, 200), 2: (0, 200, 60), 1: (0, 60, 220)}

BALLS = 5                                    # Balls per game
TICK = 0.02                                  # Seconds per step while waiting
AIM_STEP = 0.15                              # Seconds per step when the computer aims
FALL_START = 0.09                            # Seconds per row when the ball starts falling ...
FALL_FASTEST = 0.035                         # ... it speeds up to this ...
FALL_SPEEDUP = 0.01                          # ... a little faster every row
FALL_AFTER_HIT = 0.07                        # Hitting a peg slows it down again


class TakeOver(Exception):
    """Raised during the demo when someone touches the joystick."""


class PlayerLeft(Exception):
    """Raised when the player has not touched the joystick for IDLE_TIMEOUT seconds."""


def is_peg(x, y):
    """True when there is a peg at (x, y) of the board."""
    return 0 <= y < HEIGHT and 0 <= x <= 7 and BOARD[y][x] == "o"


def draw(sense, view, ball=None, hit=None, balls_left=0, flash=None):
    """Draw the 8 rows of the board starting at row `view`.

    Args:
        sense: the SenseHat object.
        view: the board row shown at the top of the screen (0 to HEIGHT - 8).
        ball: the (x, y) board position of the ball, or None.
        hit: the (x, y) of a peg that was just hit (drawn white), or None.
        balls_left: how many balls to show waiting in row 0.
        flash: (x, colour) to draw one slot in another colour, or None.
    """
    frame = [[OFF] * 8 for _ in range(8)]
    for sy in range(8):
        y = view + sy                         # Screen row sy shows board row y
        for x in range(8):
            if y == SLOT_ROW:
                frame[sy][x] = SLOT_COLORS[VALUES[x]]
            elif is_peg(x, y):
                frame[sy][x] = PEG_HIT if (x, y) == hit else PEG
            elif y == 0 and x < balls_left:
                frame[sy][x] = BALL_LEFT
    if flash and view + 7 >= SLOT_ROW:
        frame[SLOT_ROW - view][flash[0]] = flash[1]
    if ball and 0 <= ball[1] - view < 8:
        frame[ball[1] - view][ball[0]] = BALL
    sense.set_pixels(flatten(frame))


def wait(stick, seconds, human):
    """Wait `seconds` while listening to the joystick; returns what was pressed.

    During the demo any touch raises TakeOver, and when the player has walked
    away it raises PlayerLeft, so the game can switch over at once.
    """
    pressed = []
    for _ in range(max(1, round(seconds / TICK))):
        keys = stick.read()
        if keys and not human:
            raise TakeOver()
        if human and stick.idle():
            raise PlayerLeft()
        pressed += keys
        time.sleep(TICK)
    return pressed


def aim(sense, stick, human, x, balls_left):
    """Let the player (or the computer) pick the column to drop the ball from.

    Returns the column. The computer picks a random column and walks there.
    """
    target = random.randint(0, 7)
    while True:
        draw(sense, 0, (x, 1), balls_left=balls_left)
        if human:
            for key in wait(stick, TICK, True):
                if key == "left":
                    x = max(0, x - 1)
                elif key == "right":
                    x = min(7, x + 1)
                elif key in ("middle", "down"):
                    return x
        else:
            wait(stick, AIM_STEP, False)
            if x == target:
                wait(stick, 0.3, False)
                return x
            x += 1 if target > x else -1


def drop(sense, stick, human, x, balls_left):
    """Let the ball fall from column x down to the slots.

    When the ball lands on a peg it bounces off to the left or to the right
    (a 50/50 chance; next to the wall it can only go one way). The view
    follows the ball down. Returns the slot (column) it ends up in.
    """
    y, view, delay = 1, 0, FALL_START
    while y < SLOT_ROW - 1:
        hit = None
        if is_peg(x, y + 1):
            # The ball lands on a peg: the peg lights up ...
            hit = (x, y + 1)
            draw(sense, view, (x, y), hit, balls_left)
            wait(stick, 0.04, human)
            # ... and the ball bounces off to one side of it
            dx = random.choice((-1, 1))
            if not 0 <= x + dx <= 7:
                dx = -dx
            x += dx
            delay = FALL_AFTER_HIT
        y += 1
        # The view moves along so the ball stays near the top third of the screen
        view = max(view, min(HEIGHT - 8, y - 3))
        draw(sense, view, (x, y), hit, balls_left)
        wait(stick, delay, human)
        delay = max(FALL_FASTEST, delay - FALL_SPEEDUP)
    # At the bottom it rolls a little before it drops into a slot
    roll = random.choice((-1, 0, 1))
    if roll and 0 <= x + roll <= 7:
        x += roll
        draw(sense, view, (x, y), None, balls_left)
        wait(stick, 0.15, human)
    return x


def play_game(sense, stick, human):
    """Play one game of BALLS balls. Returns the score."""
    score = 0
    x = 3
    for ball in range(BALLS):
        left = BALLS - ball - 1               # Balls still waiting after this one
        x = aim(sense, stick, human, x, left)
        slot = drop(sense, stick, human, x, left)
        value = VALUES[slot]
        score += value
        # The slot flashes, once for every point (up to 5 times)
        for _ in range(min(5, value)):
            draw(sense, HEIGHT - 8, None, None, left, (slot, (255, 255, 255)))
            wait(stick, 0.08, human)
            draw(sense, HEIGHT - 8, None, None, left)
            wait(stick, 0.08, human)
        # Scroll back up to the top for the next ball
        for view in range(HEIGHT - 9, -1, -1):
            draw(sense, view, None, None, left)
            wait(stick, 0.03, human)
    return score


def main():
    """Keep playing: demo games until someone takes the joystick.

    After a person's game the score scrolls by and the demo starts again.
    """
    sense = init(hat)
    stick = Joystick(sense)
    human = False
    while True:
        try:
            score = play_game(sense, stick, human)
            if human:
                sense.show_message(f"Score {score}", text_colour=BALL, scroll_speed=0.06)
            human = False
            time.sleep(0.5)
        except TakeOver:
            # Someone touched the joystick during the demo: their game starts
            show_play_sign(sense)
            human = True
        except PlayerLeft:
            # Nobody touched the joystick for a while: back to the demo
            human = False
        stick.flush()


if __name__ == "__main__":
    run(main)
