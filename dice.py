#!/usr/bin/env python3
"""Sense HAT – Dice: roll a D4, D6, D8, D10, D12 or D20, or two six-sided dice.

Push the joystick left or right to pick a die (its name scrolls by
first, like "D12", and every die you pick gets a new random colour), up or
down to change its colour, and press it to roll. The die tumbles over and
over, slower and slower, before it lands; rolling the highest number makes it
flash gold, and so does a double with two dice. The dice wait for you: there
is no demo.
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
# Colours and timing
# ---------------------------------------------------------------------------
OFF = (0, 0, 0)
PIP = (255, 255, 255)                     # The dots and numbers on the dice
GOLD = (255, 190, 0)                      # The highest roll (and a double) flashes gold
TICK = 0.02                               # Seconds per step while waiting
ROLL_STEPS = 22                           # How many times the big die tumbles over
TUMBLE_STEPS = 28                         # How many hops the two small dice make
SCROLL_STEP = 0.055                       # Seconds per pixel while a die's name scrolls by
SIDES = ("left", "right", "up", "down")   # Directions a die can tumble in

# The dice you can pick with left and right: the number of sides, and 0 for
# two six-sided dice thrown together
DICE = [4, 6, 8, 10, 12, 20, 0]
START_DIE = 1                             # Start with the D6

# Colours for the body of a die (dark enough for the white pips and numbers to
# stand out). Up and down go through them; every newly picked die gets a
# random one.
COLORS = [
    (190, 0, 20),      # red
    (0, 140, 60),      # green
    (0, 70, 200),      # blue
    (150, 0, 170),     # purple
    (200, 90, 0),      # orange
    (0, 130, 150),     # teal
    (200, 0, 110),     # pink
    (120, 70, 20),     # brown
    (90, 90, 110),     # grey
]

# Where the pips go on the big six-sided die, which fills the whole display.
# Each pair is the top-left pixel of a 2x2 pip. The six uses the top and
# bottom rows, so its three pairs of pips stay apart.
BIG_PIPS = {
    1: [(3, 3)],
    2: [(5, 1), (1, 5)],
    3: [(5, 1), (3, 3), (1, 5)],
    4: [(1, 1), (5, 1), (1, 5), (5, 5)],
    5: [(1, 1), (5, 1), (3, 3), (1, 5), (5, 5)],
    6: [(1, 0), (5, 0), (1, 3), (5, 3), (1, 6), (5, 6)],
}

# The pips of a small 3x3 die: one pixel each, in the usual pattern
SMALL_PIPS = {
    1: [(1, 1)],
    2: [(2, 0), (0, 2)],
    3: [(2, 0), (1, 1), (0, 2)],
    4: [(0, 0), (2, 0), (0, 2), (2, 2)],
    5: [(0, 0), (2, 0), (1, 1), (0, 2), (2, 2)],
    6: [(0, 0), (0, 1), (0, 2), (2, 0), (2, 1), (2, 2)],
}

# A tiny 3x5 font for the numbers on the other dice and for the dice names
# ("#" = lit). Two digits next to each other with one dark column between
# them are 7 pixels wide, so they fit on the 8x8 display.
FONT = {
    "0": ["###", "#.#", "#.#", "#.#", "###"],
    "1": [".#.", "##.", ".#.", ".#.", "###"],
    "2": ["###", "..#", "###", "#..", "###"],
    "3": ["###", "..#", ".##", "..#", "###"],
    "4": ["#.#", "#.#", "###", "..#", "..#"],
    "5": ["###", "#..", "###", "..#", "###"],
    "6": ["###", "#..", "###", "#.#", "###"],
    "7": ["###", "..#", ".#.", ".#.", ".#."],
    "8": ["###", "#.#", "###", "#.#", "###"],
    "9": ["###", "#.#", "###", "..#", "###"],
    "D": ["##.", "#.#", "#.#", "#.#", "##."],
    "x": ["...", "#.#", ".#.", "#.#", "..."],
}


def dim(color, amount):
    """The colour made darker: amount 1 keeps it, 0.5 is half as bright."""
    return tuple(int(c * amount) for c in color)


def transpose(frame):
    """Swap rows and columns of an 8x8 picture (mirror it over the diagonal)."""
    return [list(row) for row in zip(*frame)]


def text_columns(text):
    """The text as a list of pixel columns (each 5 pixels high), one dark column between letters."""
    columns = []
    for ch in text:
        glyph = FONT[ch]
        for x in range(len(glyph[0])):
            columns.append([glyph[y][x] == "#" for y in range(5)])
        columns.append([False] * 5)
    return columns[:-1]


def big_die(sides, value, body, pip=PIP):
    """An 8x8 picture of the big die with `sides` sides showing `value`,
    with a body of colour `body`.

    The six-sided die has pips; the other dice show their number.
    """
    frame = [[body] * 8 for _ in range(8)]
    for x, y in ((0, 0), (7, 0), (0, 7), (7, 7)):
        frame[y][x] = OFF                  # Rounded corners
    if sides == 6:
        for px, py in BIG_PIPS[value]:
            for dy in (0, 1):
                for dx in (0, 1):
                    frame[py + dy][px + dx] = pip
    else:
        columns = text_columns(str(value))
        x0 = (8 - len(columns)) // 2       # Centred (a 7-wide number leaves one column)
        for i, column in enumerate(columns):
            for y, lit in enumerate(column):
                if lit:
                    frame[y + 2 if value < 10 else y + 1][x0 + i] = pip
    return frame


def tumble_frame(old, new, direction, amount):
    """One in-between picture of the die tipping over onto its next side.

    The die is drawn like a box that turns: the side that was on top gets
    narrower and darker as it turns away, while the new side comes into view
    from the opposite edge, wider and brighter.

    Args:
        old: the 8x8 picture of the side that was showing.
        new: the 8x8 picture of the side that comes up.
        direction: where the die tips to: "left", "right", "up" or "down".
        amount: how far it has turned, from 0 (only old) to 1 (only new).
    """
    if direction in ("up", "down"):
        # Up and down are left and right with rows and columns swapped
        side = "left" if direction == "up" else "right"
        return transpose(tumble_frame(transpose(old), transpose(new), side, amount))
    width = 8 * amount                     # How many columns the new side takes
    frame = [[OFF] * 8 for _ in range(8)]
    for x in range(8):
        c = x + 0.5                        # The middle of this column
        # Tipping right means the new side appears on the left, and the other way around
        new_from = 0 if direction == "right" else 8 - width
        old_from = width if direction == "right" else 0
        if new_from <= c < new_from + width:
            src, col, light = new, int((c - new_from) / width * 8), 0.4 + 0.6 * amount
        else:
            src, col, light = old, int((c - old_from) / (8 - width) * 8), 1 - 0.6 * amount
        col = max(0, min(7, col))
        for y in range(8):
            frame[y][x] = dim(src[y][col], light)
    return frame


def small_dice(values, places, body, pip=PIP):
    """An 8x8 picture of the two small dice.

    Args:
        values: the two values showing, e.g. [3, 5].
        places: the top-left pixel (x, y) of each die.
        body: the colour of the dice.
        pip: the colour of the pips (gold for a double).
    """
    frame = [[OFF] * 8 for _ in range(8)]
    for value, (x0, y0) in zip(values, places):
        for y in range(3):
            for x in range(3):
                frame[y0 + y][x0 + x] = body
        for x, y in SMALL_PIPS[value]:
            frame[y0 + y][x0 + x] = pip
    return frame


def new_value(old, sides=6):
    """A random side for a die that tips over: never the same side again."""
    return random.choice([v for v in range(1, sides + 1) if v != old])


def wait(stick, seconds):
    """Wait `seconds` while listening to the joystick; returns what was pressed."""
    pressed = []
    for _ in range(max(1, round(seconds / TICK))):
        pressed += stick.read()
        time.sleep(TICK)
    return pressed


def show_name(sense, stick, name, color):
    """Scroll the name of the chosen die (like "D12") across the display, in its colour.

    Returns the joystick moves made meanwhile, so a quick second push still counts.
    """
    columns = [[False] * 5] * 8 + text_columns(name) + [[False] * 5] * 8
    pressed = []
    for start in range(len(columns) - 7):
        frame = [[OFF] * 8 for _ in range(8)]
        for x in range(8):
            for y, lit in enumerate(columns[start + x]):
                if lit:
                    frame[y + 1][x] = color
        sense.set_pixels(flatten(frame))
        pressed += wait(stick, SCROLL_STEP)
    return pressed


def roll_big(sense, stick, sides, value, body):
    """Let the big die tumble over and over, slower and slower, and land.

    Returns the value it lands on. The highest number flashes gold.
    """
    for step in range(ROLL_STEPS):
        new = new_value(value, sides)
        direction = random.choice(SIDES)
        slow = step / ROLL_STEPS             # 0 at the start, almost 1 at the end
        frames = 2 + step // 4               # More in-between pictures as it slows down
        for f in range(1, frames + 1):
            sense.set_pixels(flatten(tumble_frame(big_die(sides, value, body), big_die(sides, new, body),
                                                  direction, f / (frames + 1))))
            wait(stick, 0.02 + 0.03 * slow)
        value = new
        sense.set_pixels(flatten(big_die(sides, value, body)))
        wait(stick, 0.03 + 0.3 * slow * slow)
    # It landed: the pips or the number light up gold for a moment
    flashes = 3 if value == sides else 1
    for _ in range(flashes):
        sense.set_pixels(flatten(big_die(sides, value, body, GOLD)))
        wait(stick, 0.15)
        sense.set_pixels(flatten(big_die(sides, value, body)))
        wait(stick, 0.15 if flashes > 1 else 0)
    return value


def separated(a, b):
    """True when two small dice (3x3, top-left corners a and b) have at least one dark pixel between them."""
    return abs(a[0] - b[0]) >= 4 or abs(a[1] - b[1]) >= 4


def roll_small(sense, stick, values, places, body):
    """Shake the two small dice across the display: with every hop they jump
    to a new spot (they may fly over each other), slower and slower, then they
    roll to two separate places and come to rest.

    Returns (values, places) where they landed. Doubles flash gold.
    """
    places = [list(p) for p in places]
    # Where they will lie at the end: two spots with a dark gap between them
    while True:
        final = [[random.randint(0, 5), random.randint(0, 5)] for _ in range(2)]
        if separated(*final):
            break
    settle = 6                               # The last hops bring them to those spots
    for step in range(TUMBLE_STEPS):
        for i, place in enumerate(places):
            for axis in (0, 1):
                if step < TUMBLE_STEPS - settle:
                    # Shaking: jump 1 or 2 pixels in a random direction (a die is
                    # 3 pixels wide, so its top-left corner stays between 0 and 5)
                    place[axis] = max(0, min(5, place[axis] + random.choice((-2, -1, 1, 2))))
                else:
                    # Settling: one pixel at a time towards the final spot
                    gap = final[i][axis] - place[axis]
                    place[axis] += (gap > 0) - (gap < 0)
        if step >= TUMBLE_STEPS - 2:
            places = [p[:] for p in final]       # Make sure they really lie apart
        values = [new_value(v) for v in values]
        sense.set_pixels(flatten(small_dice(values, places, body)))
        slow = step / TUMBLE_STEPS
        wait(stick, 0.05 + 0.28 * slow * slow)
    if values[0] == values[1]:
        # A double: the pips flash gold
        for _ in range(3):
            sense.set_pixels(flatten(small_dice(values, places, body, GOLD)))
            wait(stick, 0.15)
            sense.set_pixels(flatten(small_dice(values, places, body)))
            wait(stick, 0.15)
    return values, places


def play(sense, stick):
    """Wait for the joystick and roll the dice, forever."""
    choice = START_DIE                       # Which die of DICE is picked
    color = random.randrange(len(COLORS))    # Which colour of COLORS it has
    last = {sides: random.randint(1, sides) for sides in DICE if sides}   # What each die shows
    pair = [random.randint(1, 6), random.randint(1, 6)]
    places = [(0, 1), (5, 4)]

    def show():
        """Draw the dice as they are now."""
        sides = DICE[choice]
        if sides:
            sense.set_pixels(flatten(big_die(sides, last[sides], COLORS[color])))
        else:
            sense.set_pixels(flatten(small_dice(pair, places, COLORS[color])))

    def pick(step):
        """Pick the next (step 1) or previous (step -1) die, in a new random colour,
        and show its name first. Returns the joystick moves made meanwhile."""
        nonlocal choice, color
        choice = (choice + step) % len(DICE)
        color = random.choice([c for c in range(len(COLORS)) if c != color])
        sides = DICE[choice]
        more = show_name(sense, stick, "D%d" % sides if sides else "2xD6", COLORS[color])
        show()
        return more

    show()
    while True:
        keys = wait(stick, TICK)
        while keys:
            key = keys.pop(0)
            if key in ("left", "right"):
                keys = pick(-1 if key == "left" else 1) + keys
            elif key in ("up", "down"):
                color = (color + (1 if key == "up" else -1)) % len(COLORS)
                show()
            elif key == "middle":
                sides = DICE[choice]
                if sides:
                    last[sides] = roll_big(sense, stick, sides, last[sides], COLORS[color])
                else:
                    pair, places = roll_small(sense, stick, pair, places, COLORS[color])
                stick.read()                 # Forget presses made while the dice rolled
                break


def main():
    """Show the die and roll it whenever the joystick is pressed."""
    sense = init(hat)
    stick = Joystick(sense)
    play(sense, stick)


if __name__ == "__main__":
    run(main)
