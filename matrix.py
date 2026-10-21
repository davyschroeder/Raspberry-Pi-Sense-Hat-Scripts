#!/usr/bin/env python3
"""Sense HAT – Matrix rain: green code trails falling down the display."""
import random
import time

try:
    from sense_hat import SenseHat
    hat = SenseHat()
except (ImportError, OSError):  # No Sense HAT library or board found: use the emulator
    from sense_emu import SenseHat
    hat = SenseHat()

from common import flatten, init, run

OFF = (0, 0, 0)
BACKGROUND = (0, 40, 0)   # Faint scrolling background lines
# Trail shades, index 1 (dim tail) .. 4 (bright head)
GREENS = [OFF, (0, 120, 0), (0, 160, 0), (0, 200, 0), (0, 255, 0)]
GLITCH = (0, 255, 0)
# The head (lowest pixel) of every trail flickers through these greens, a new one each frame
HEAD_GREENS = [(0, 255, 0), (120, 255, 120), (180, 255, 180), (0, 220, 80), (80, 255, 40)]

DROP_CHANCE = 0.11         # Chance per column per frame that a new trail starts
SPARK_CHANCE = 0.10        # Chance per frame of a short, still flash somewhere
SPARK_FRAMES = 2           # How many frames a flash stays lit
GLITCH_CHANCE = 0.01       # Chance per frame of one bright random pixel
BACKGROUND_TICKS = 1       # Frames per step of the background dots (higher = slower)
BACKGROUND_SPACING = 16    # Pixels between background dots (higher = calmer)
FRAME_DELAY = 0.1


class Drop:
    """A falling trail with a bright head and a fading tail."""

    def __init__(self):
        self.y = -1
        self.length = random.randint(5, 8)
        self.ticks_per_step = random.choice([1, 2])   # 1 = fast, 2 = slow
        self.ticks = 0

    def pixels(self):
        for i in range(self.length):
            y = self.y - i
            if 0 <= y <= 7:
                color = random.choice(HEAD_GREENS) if i == 0 else GREENS[max(1, 4 - i)]
                yield y, color

    def advance(self):
        """Move down; returns False once the whole trail has left the screen."""
        self.ticks += 1
        if self.ticks >= self.ticks_per_step:
            self.y += 1
            self.ticks = 0
        return self.y - self.length < 7


class BackgroundLine:
    """Faint dots in a column, BACKGROUND_SPACING apart, scrolling down."""

    def __init__(self):
        self.offset = random.randrange(BACKGROUND_SPACING)
        self.ticks = 0

    def step(self):
        self.ticks += 1
        if self.ticks >= BACKGROUND_TICKS:
            self.offset = (self.offset + 1) % BACKGROUND_SPACING
            self.ticks = 0

    def pixels(self):
        return [y for y in range(8) if (y - self.offset) % BACKGROUND_SPACING == 0]


class Spark:
    """A short flash that lights up in place for a few frames, without falling."""

    def __init__(self, color=None, frames=SPARK_FRAMES):
        self.x = random.randint(0, 7)
        self.y = random.randint(0, 7)
        self.color = color or GREENS[2]
        self.frames = frames

    def draw(self, frame):
        """Light up only on dark or background pixels; returns False once done."""
        if frame[self.y][self.x] in (OFF, BACKGROUND):
            frame[self.y][self.x] = self.color
        self.frames -= 1
        return self.frames > 0


def main():
    sense = init(hat)
    columns = [[] for _ in range(8)]
    background = [BackgroundLine() for _ in range(8)]
    sparks = []

    while True:
        frame = [[OFF] * 8 for _ in range(8)]

        for x in range(8):
            background[x].step()
            for y in background[x].pixels():
                frame[y][x] = BACKGROUND

            if random.random() < DROP_CHANCE:
                columns[x].append(Drop())

            alive = []
            for drop in columns[x]:
                for y, color in drop.pixels():
                    frame[y][x] = color
                if drop.advance():
                    alive.append(drop)
            columns[x] = alive

        if random.random() < SPARK_CHANCE:
            sparks.append(Spark())
        if random.random() < GLITCH_CHANCE:
            sparks.append(Spark(GLITCH, frames=1))
        sparks = [spark for spark in sparks if spark.draw(frame)]

        sense.set_pixels(flatten(frame))
        time.sleep(FRAME_DELAY)


if __name__ == "__main__":
    run(main)
