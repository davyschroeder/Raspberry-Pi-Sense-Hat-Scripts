#!/usr/bin/env python3
"""Sense HAT – Fireflies: softly coloured fireflies drift, light up and fade out at night."""
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


NIGHT = (0, 0, 10)   # Background colour
FIREFLY_COUNT = 6
FRAME_DELAY = 0.05


class Firefly:
    def __init__(self):
        self.reset()

    def reset(self):
        """Start a new firefly at a random spot with a random colour."""
        self.x = random.uniform(0, 7)
        self.y = random.uniform(0, 7)
        self.dx = random.uniform(-0.2, 0.2)
        self.dy = random.uniform(-0.2, 0.2)
        self.brightness = 0
        self.peak = random.randint(100, 255)
        self.brightening = True
        self.fade_speed = random.choice([2, 3, 4])
        self.color = tuple(random.randint(80, 255) for _ in range(3))

    def update(self):
        # Drift, bouncing off the edges
        self.x += self.dx
        self.y += self.dy
        if not 0 <= self.x <= 7:
            self.dx = -self.dx
        if not 0 <= self.y <= 7:
            self.dy = -self.dy

        # Light up quickly, then fade out slowly
        if self.brightening:
            self.brightness = min(self.peak, self.brightness + 20)
            if self.brightness >= self.peak:
                self.brightening = False
        else:
            self.brightness -= self.fade_speed
            if self.brightness <= 0:
                self.reset()

    def draw(self, frame):
        if self.brightness <= 0:
            return
        x, y = round(self.x), round(self.y)
        if 0 <= x <= 7 and 0 <= y <= 7:
            scale = self.brightness / 255
            frame[y][x] = tuple(min(255, int(c * scale)) for c in self.color)


def main():
    sense = init(hat)
    fireflies = [Firefly() for _ in range(FIREFLY_COUNT)]
    while True:
        frame = [[NIGHT] * 8 for _ in range(8)]
        for firefly in fireflies:
            firefly.update()
            firefly.draw(frame)
        sense.set_pixels(flatten(frame))
        time.sleep(FRAME_DELAY)


if __name__ == "__main__":
    run(main)
