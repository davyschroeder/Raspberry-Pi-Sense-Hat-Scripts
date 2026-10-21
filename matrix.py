#!/usr/bin/env python3
"""Sense HAT – Matrix rain: green code trails falling down the display."""
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


OFF = (0, 0, 0)
BACKGROUND = (0, 40, 0)   # Faint scrolling background lines
# Trail shades, index 1 (dim tail) .. 4 (bright head)
GREENS = [OFF, (0, 120, 0), (0, 160, 0), (0, 200, 0), (0, 255, 0)]
GLITCH = (0, 255, 0)      # Colour of the rare single bright "glitch" pixel
# The head (lowest pixel) of every trail flickers through these greens, a new one each frame
HEAD_GREENS = [(0, 255, 0), (120, 255, 120), (180, 255, 180), (0, 220, 80), (80, 255, 40)]

DROP_CHANCE = 0.11         # Chance per column per frame that a new trail starts
SPARK_CHANCE = 0.10        # Chance per frame of a short, still flash somewhere
SPARK_FRAMES = 2           # How many frames a flash stays lit
GLITCH_CHANCE = 0.01       # Chance per frame of one bright random pixel
BACKGROUND_TICKS = 1       # Frames per step of the background dots (higher = slower)
BACKGROUND_SPACING = 16    # Pixels between background dots (higher = calmer)
FRAME_DELAY = 0.1          # Seconds per frame (lower = faster rain)


class Drop:
    """A falling trail with a bright head and a fading tail.

    A drop belongs to one column (the main loop keeps a list of drops per
    column), so it only needs to know its row. self.y is the row of the head;
    the tail is the self.length - 1 pixels above it. Rows count from 0 at the
    top to 7 at the bottom, and a drop can be partly off the screen.
    """

    def __init__(self):
        """Start a new drop just above the top of the screen, with random length and speed."""
        self.y = -1                                   # Head starts one row above the screen
        self.length = random.randint(5, 8)            # Head + tail, in pixels
        self.ticks_per_step = random.choice([1, 2])   # 1 = fast, 2 = slow
        self.ticks = 0                                # Frames since the last step down

    def pixels(self):
        """Yield (row, colour) for every pixel of the trail that is on the screen.

        i counts from the head (i = 0) up the tail. The head gets a random
        bright green from HEAD_GREENS each frame, so it flickers. The tail
        fades: GREENS[3], [2], then [1] for everything after that.
        """
        for i in range(self.length):
            y = self.y - i
            # Skip the parts that are above or below the screen
            if 0 <= y <= 7:
                color =random.choice(HEAD_GREENS) if i == 0 else GREENS[max(1, 4 - i)]
                yield y, color

    def advance(self):
        """Move down; returns False once the whole trail has left the screen.

        Called once per frame. A fast drop moves down every frame, a slow one
        every second frame. The trail is gone when even its last tail pixel
        (row self.y - self.length + 1) is below row 7.
        """
        self.ticks += 1
        if self.ticks >= self.ticks_per_step:
            self.y += 1
            self.ticks = 0
        return self.y - self.length < 7


class BackgroundLine:
    """Faint dots in a column, BACKGROUND_SPACING apart, scrolling down.

    Think of an endless column with a dot every BACKGROUND_SPACING pixels.
    self.offset says where the dots are: a row y has a dot when
    (y - offset) is a multiple of BACKGROUND_SPACING. Adding 1 to the offset
    moves every dot one row down. With a spacing of 16 and only 8 rows, there
    is at most one dot on the screen per column at a time.
    """

    def __init__(self):
        """Start at a random offset, so the columns don't all scroll in step."""
        self.offset = random.randrange(BACKGROUND_SPACING)
        self.ticks = 0     # Frames since the last scroll step

    def step(self):
        """Called once per frame: scroll one row down every BACKGROUND_TICKS frames.

        The offset wraps around at BACKGROUND_SPACING (the % does that), which
        looks the same as scrolling on forever.
        """
        self.ticks += 1
        if self.ticks >= BACKGROUND_TICKS:
            self.offset = (self.offset + 1) % BACKGROUND_SPACING
            self.ticks = 0

    def pixels(self):
        """Return the list of rows (0-7) in this column that have a background dot."""
        return [y for y in range(8) if (y - self.offset) % BACKGROUND_SPACING == 0]


class Spark:
    """A short flash that lights up in place for a few frames, without falling."""

    def __init__(self, color=None, frames=SPARK_FRAMES):
        """Put a spark on a random pixel.

        Args:
            color: its colour; None means the medium green GREENS[2].
            frames: how many frames it stays lit.
        """
        self.x = random.randint(0, 7)
        self.y = random.randint(0, 7)
        self.color = color or GREENS[2]
        self.frames = frames

    def draw(self, frame):
        """Light up only on dark or background pixels; returns False once done.

        Drawn after the trails, so a spark never covers a falling trail.

        Args:
            frame: the picture being built, 8 rows of 8 colours (frame[y][x]).
        """
        if frame[self.y][self.x] in (OFF, BACKGROUND):
            frame[self.y][self.x] = self.color
        self.frames -= 1
        return self.frames > 0


def main():
    """Draw the rain forever, one frame every FRAME_DELAY seconds.

    Each frame is built from scratch in layers: first the faint background
    dots, then the falling trails on top, then the sparks in the dark gaps.
    """
    sense = init(hat)
    columns = [[] for _ in range(8)]                    # The falling Drops in each column
    background = [BackgroundLine() for _ in range(8)]   # One background line per column
    sparks = []                                         # Flashes that are lit right now

    while True:
        # Start every frame with a black screen (frame[y][x])
        frame = [[OFF] * 8 for _ in range(8)]

        for x in range(8):
            # Layer 1: the background dots of this column
            background[x].step()
            for y in background[x].pixels():
                frame[y][x] = BACKGROUND

            # Now and then a new trail starts at the top of this column
            if random.random() < DROP_CHANCE:
                columns[x].append(Drop())

            # Layer 2: draw every trail, move it down, and keep only the
            # ones that are still (partly) on the screen
            alive = []
            for drop in columns[x]:
                for y, color in drop.pixels():
                    frame[y][x] = color
                if drop.advance():
                    alive.append(drop)
            columns[x] = alive

        # Layer 3: maybe add a spark or a rare glitch, then draw them all and
        # keep only the sparks that still have frames left
        if random.random() < SPARK_CHANCE:
            sparks.append(Spark())
        if random.random() < GLITCH_CHANCE:
            sparks.append(Spark(GLITCH, frames=1))
        sparks = [spark for spark in sparks if spark.draw(frame)]

        # Show the finished frame and wait for the next one
        sense.set_pixels(flatten(frame))
        time.sleep(FRAME_DELAY)


if __name__ == "__main__":
    run(main)
