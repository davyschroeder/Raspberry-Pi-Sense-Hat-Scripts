#!/usr/bin/env python3
"""Sense HAT – Beating heart: a red heart that pulses like a heartbeat."""
import time

try:
    from sense_hat import SenseHat
    hat = SenseHat()
except (ImportError, OSError):  # No Sense HAT library or board found: use the emulator
    from sense_emu import SenseHat
    hat = SenseHat()

from common import init, run

# Shades of red, from off (0) to full brightness (4)
SHADES = [
    (0, 0, 0),
    (70, 0, 0),
    (120, 0, 0),
    (200, 0, 0),
    (255, 0, 0),
]

SMALL = [
    0, 0, 1, 0, 0, 1, 0, 0,
    0, 1, 2, 1, 1, 2, 1, 0,
    1, 2, 2, 2, 2, 2, 2, 1,
    1, 2, 2, 2, 2, 2, 2, 1,
    0, 1, 2, 2, 2, 2, 1, 0,
    0, 0, 1, 2, 2, 1, 0, 0,
    0, 0, 0, 1, 1, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
]

MEDIUM = [
    0, 1, 2, 0, 0, 2, 1, 0,
    1, 2, 3, 3, 3, 3, 2, 1,
    2, 3, 4, 4, 4, 4, 3, 2,
    2, 3, 4, 4, 4, 4, 3, 2,
    1, 2, 3, 4, 4, 3, 2, 1,
    0, 1, 2, 3, 3, 2, 1, 0,
    0, 0, 1, 2, 2, 1, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
]

LARGE = [
    0, 2, 3, 0, 0, 3, 2, 0,
    2, 3, 4, 4, 4, 4, 3, 2,
    3, 4, 4, 4, 4, 4, 4, 3,
    3, 4, 4, 4, 4, 4, 4, 3,
    1, 3, 4, 4, 4, 4, 3, 1,
    0, 1, 3, 4, 4, 3, 1, 0,
    0, 0, 1, 3, 3, 1, 0, 0,
    0, 0, 0, 1, 1, 0, 0, 0,
]

# One heartbeat: (frame, seconds to show it)
HEARTBEAT = [
    (SMALL, 0.25),   # Resting
    (MEDIUM, 0.2),   # Growing
    (LARGE, 0.35),   # Full beat
    (MEDIUM, 0.2),   # Shrinking
    (SMALL, 0.3),    # Rest before the next beat
]


def main():
    sense = init(hat)
    frames = [([SHADES[v] for v in frame], delay) for frame, delay in HEARTBEAT]
    while True:
        for pixels, delay in frames:
            sense.set_pixels(pixels)
            time.sleep(delay)


if __name__ == "__main__":
    run(main)
