#!/usr/bin/env python3
"""Sense HAT – Slot machine: three spinning reels, three of a kind wins.

The computer plays a demo until you touch the joystick: then it's your turn.
Press the joystick (or push it any way) to spin, and press again to stop the
reels one by one, or let them stop by themselves. You start with 10 coins:
a spin costs 1 coin and a win pays out. After 30 seconds without input, or
when the coins run out, the demo starts again.
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
# The symbols on the reels
# ---------------------------------------------------------------------------
OFF = (0, 0, 0)
RED = (255, 0, 0)
PINK = (255, 20, 90)
GREEN = (0, 200, 0)
YELLOW = (255, 230, 0)
PURPLE = (150, 0, 255)
BLUE = (0, 110, 255)
GOLD = (255, 180, 0)
MARKER = (90, 0, 0)          # The little red marks that show the pay line

# Every symbol is a tiny 2x2 picture (two rows of two colours)
SYMBOLS = {
    "seven": [[RED, RED], [OFF, RED]],          # A red 7
    "cherry": [[OFF, GREEN], [PINK, PINK]],     # Two cherries on a green stem
    "lemon": [[YELLOW, YELLOW], [YELLOW, YELLOW]],
    "plum": [[PURPLE, PURPLE], [PURPLE, PURPLE]],
    "gem": [[BLUE, BLUE], [BLUE, BLUE]],
}
# Three of a kind on the pay line pays this many coins
PAYOUT = {"seven": 50, "gem": 15, "plum": 10, "lemon": 8, "cherry": 5}
# Two cherries at the start of the pay line pay a little
TWO_CHERRIES = 2
# How often each symbol is on a reel: the fewer, the rarer (and the bigger the win)
REEL = ["seven"] * 1 + ["gem"] * 2 + ["plum"] * 3 + ["lemon"] * 4 + ["cherry"] * 4

START_COINS = 10
TICK = 0.02                  # Seconds per animation step
SPIN_SPEED = 40.0            # Pixels per second when a reel spins at full speed
SPIN_UP = 3.0                # How fast it gets to full speed (fraction of full speed per second)
AUTO_STOP = [0.9, 1.5, 2.1]  # Seconds after the start at which the reels stop by themselves
SUSPENSE = 1.2               # Extra spinning for the last reel when the first two match
DEMO_PAUSE = 1.5             # Seconds between two spins in the demo
LINE = len(REEL) * 3         # Every symbol takes 3 pixel rows of the reel: 2 for it, 1 gap


class TakeOver(Exception):
    """Raised during the demo when someone touches the joystick."""


class PlayerLeft(Exception):
    """Raised when the player has not touched the joystick for IDLE_TIMEOUT seconds."""


def dim(color, amount):
    """The colour made darker: amount 1 keeps it, 0.5 is half as bright."""
    return tuple(int(c * amount) for c in color)


def rainbow(i):
    """A bright colour from the rainbow; i goes round the colour wheel."""
    a = (i % 24) / 24 * 2 * math.pi
    return tuple(int(127 + 127 * math.cos(a - k * 2 * math.pi / 3)) for k in range(3))


class Reel:
    """One reel: a long strip of symbols that turns round and round.

    The strip is LINE pixels long (2 rows per symbol plus a dark gap) and
    `pos` says which pixel row of it is at the top of the screen. Spinning
    makes `pos` smaller, so the symbols move down the screen.
    """

    def __init__(self):
        """A reel with the symbols in a random order, standing still."""
        self.strip = REEL[:]
        random.shuffle(self.strip)
        self.pos = 3.0 * random.randrange(len(self.strip))
        self.speed = 0.0
        self.state = "stopped"     # "spinning", "stopping", "bounce" or "stopped"
        self.target = 0.0

    def start(self):
        """Start spinning: it first jerks up a little, like a real lever pull."""
        self.pos += 1
        self.speed = 0.0
        self.state = "spinning"

    def stop(self):
        """Stop at the next symbol or a few later (so pressing doesn't let you
        pick the symbol: it stays a game of chance)."""
        if self.state != "spinning":
            return
        self.target = 3 * math.floor(self.pos / 3) - 3 * random.randint(1, 3)
        self.state = "stopping"

    def update(self):
        """Move one TICK further. Returns True at the moment the reel stops."""
        if self.state == "spinning":
            self.speed = min(SPIN_SPEED, self.speed + SPIN_SPEED * SPIN_UP * TICK)
            self.pos -= self.speed * TICK
        elif self.state == "stopping":
            self.pos -= SPIN_SPEED * TICK
            if self.pos <= self.target - 1:          # One pixel too far ...
                self.pos = self.target - 1
                self.state = "bounce"
        elif self.state == "bounce":
            self.pos += 0.25                         # ... and it springs back
            if self.pos >= self.target:
                self.pos = self.target % LINE        # Keep the number small
                self.state = "stopped"
                return True
        return False

    def symbol(self, row=1):
        """The symbol in a row of the window: 0 = top, 1 = the pay line, 2 = bottom."""
        return self.strip[(int(round(self.pos)) // 3 + row) % len(self.strip)]


def draw(sense, reels, light=None, blink=()):
    """Draw the three reels with the pay line in the middle.

    The reels are 2 pixels wide with a dark column between them
    (2 + 1 + 2 + 1 + 2 = 8). Rows 3 and 4 are the pay line; the symbols
    above and below it are dimmer.

    Args:
        sense: the SenseHat object.
        reels: the three Reels.
        light: a function (x, y) -> colour for the lights between the reels
               (used when you win), or None for the plain pay line marks.
        blink: the reels whose pay line symbol is blinked off right now.
    """
    frame = [[OFF] * 8 for _ in range(8)]
    for i, reel in enumerate(reels):
        top = int(math.floor(reel.pos))
        for y in range(8):
            strip_row = (top + y) % LINE
            index, part = divmod(strip_row, 3)
            if part == 2:
                continue                              # The gap between two symbols
            on_line = y in (3, 4)
            if on_line and i in blink:
                continue
            for dx in (0, 1):
                color = SYMBOLS[reel.strip[index]][part][dx]
                frame[y][3 * i + dx] = color if on_line else dim(color, 0.3)
    for x in (2, 5):                                  # The columns between the reels
        for y in range(8):
            if light:
                frame[y][x] = light(x, y)
            elif y in (3, 4):
                frame[y][x] = MARKER
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


def spin(sense, stick, human, reels):
    """Spin the reels until all three stand still again.

    They stop by themselves at AUTO_STOP; a person can press to stop them
    earlier, one at a time. When the first two show the same symbol on the
    pay line, the last reel keeps you waiting a little longer.
    """
    for reel in reels:
        reel.start()
    stops = AUTO_STOP[:]
    elapsed = 0.0
    while any(reel.state != "stopped" for reel in reels):
        keys = wait(stick, TICK, human)
        elapsed += TICK
        spinning = [i for i, reel in enumerate(reels) if reel.state == "spinning"]
        if spinning and elapsed > 0.3 and keys:
            # A press stops the next reel (not earlier than the one before it)
            i = spinning[0]
            stops[i] = min(stops[i], max(elapsed, stops[i - 1] + 0.25 if i else 0))
        for i, reel in enumerate(reels):
            if reel.state == "spinning" and elapsed >= stops[i] and (
                    i == 0 or reels[i - 1].state == "stopped"):
                reel.stop()
            if reel.update() and i == 1 and reels[0].symbol() == reel.symbol():
                stops[2] = max(stops[2], elapsed + SUSPENSE)   # Two the same: suspense!
        suspense = reels[2].state == "spinning" and reels[0].state == reels[1].state == "stopped" \
            and reels[0].symbol() == reels[1].symbol()
        if suspense:
            def flicker(x, y):
                """While you wait for the last reel, the pay line marks flash gold."""
                if y in (3, 4):
                    return GOLD if int(elapsed * 10) % 2 else MARKER
                return OFF
            draw(sense, reels, flicker)
        else:
            draw(sense, reels)


def celebrate(sense, stick, human, reels, win, jackpot):
    """Show a win: the pay line blinks and lights chase round the reels.
    Three sevens get a long rainbow show with a shower of gold coins."""
    steps = 150 if jackpot else 75 if win > TWO_CHERRIES else 30
    winners = (0, 1) if win == TWO_CHERRIES else (0, 1, 2)
    coins = []                                         # Falling gold coins (jackpot only)
    for step in range(steps):
        if jackpot:
            def light(x, y, s=step):
                return rainbow(s + y * 3 + x)
        else:
            def light(x, y, s=step):
                return GOLD if (y + s // 2) % 3 == 0 else OFF
        blink = winners if (step // 6) % 2 else ()
        draw(sense, reels, light, blink)
        if jackpot:
            if random.random() < 0.3:
                coins.append([random.randint(0, 7), 0.0])
            for coin in coins:
                coin[1] += 0.25
            coins = [c for c in coins if c[1] < 8]
            for x, y in coins:
                sense.set_pixel(x, int(y), GOLD)
        wait(stick, TICK * 2, human)
    draw(sense, reels)


def show_coins(sense, stick, human, coins):
    """Show how many coins you have, as a little stack of gold pixels
    (one pixel per coin, up to 64) that builds up from the bottom."""
    frame = [[OFF] * 8 for _ in range(8)]
    for i in range(min(coins, 64)):
        frame[7 - i // 8][i % 8] = GOLD
        sense.set_pixels(flatten(frame))
        wait(stick, 0.015, human)
    wait(stick, 0.6, human)


def play(sense, stick, human):
    """Play until the demo is interrupted, the player walks away or the coins
    run out. Returns the coins left. Raises TakeOver or PlayerLeft."""
    reels = [Reel(), Reel(), Reel()]
    coins = START_COINS
    draw(sense, reels)
    if human:
        show_coins(sense, stick, human, coins)
        draw(sense, reels)
    while coins > 0:
        if human:
            if not wait(stick, TICK, True):
                continue
        else:
            wait(stick, DEMO_PAUSE, False)
        coins -= 1
        spin(sense, stick, human, reels)
        stick.read()                                    # Forget presses made while spinning
        line = [reel.symbol() for reel in reels]
        win = 0
        if line[0] == line[1] == line[2]:
            win = PAYOUT[line[0]]
        elif line[0] == line[1] == "cherry":
            win = TWO_CHERRIES
        if win:
            coins += win
            celebrate(sense, stick, human, reels, win, line[0] == "seven" and win > TWO_CHERRIES)
            if human:
                show_coins(sense, stick, human, coins)
                draw(sense, reels)
        if not human:
            coins = START_COINS                           # The demo never runs out
    return coins


def main():
    """Keep playing: the computer spins until someone takes the joystick."""
    sense = init(hat)
    stick = Joystick(sense)
    human = False
    while True:
        try:
            play(sense, stick, human)
            # Out of coins
            sense.show_message("No coins", text_colour=GOLD, scroll_speed=0.06)
            human = False
        except TakeOver:
            # Someone touched the joystick during the demo: their turn
            show_play_sign(sense)
            stick.flush()
            human = True
        except PlayerLeft:
            # Nobody touched the joystick for a while: back to the demo
            human = False
        stick.flush()


if __name__ == "__main__":
    run(main)
