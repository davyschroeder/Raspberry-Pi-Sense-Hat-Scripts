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


NIGHT = (0, 0, 10)   # Background colour: a very dark blue night sky (red, green, blue 0-255)
FIREFLY_COUNT = 6    # How many fireflies fly around at the same time
FRAME_DELAY = 0.05   # Seconds between two pictures (0.05 = 20 frames per second)


class Firefly:
    """One firefly: it drifts around, lights up quickly and then slowly fades out.

    The position is stored as a fraction (for example x = 3.4), so the firefly can
    move less than one LED per frame. It is only rounded to a whole LED when drawn.
    When the firefly has faded out completely it starts over somewhere else.
    """

    def __init__(self):
        """Create a firefly; reset() gives it its random starting values."""
        self.reset()

    def reset(self):
        """Start a new firefly at a random spot with a random colour.

        Sets the position (x, y), the speed per frame (dx, dy), how bright it gets
        at its brightest (peak), how fast it fades (fade_speed) and its colour.
        """
        # Random position anywhere on the 8x8 grid (0 to 7 in both directions)
        self.x = random.uniform(0, 7)
        self.y = random.uniform(0, 7)
        # Random slow speed: at most a fifth of an LED per frame, in any direction
        self.dx = random.uniform(-0.2, 0.2)
        self.dy = random.uniform(-0.2, 0.2)
        # It starts dark and will brighten up to its own random peak brightness
        self.brightness = 0
        self.peak = random.randint(100, 255)
        self.brightening = True
        # How much darker it gets each frame while fading (bigger = shorter glow)
        self.fade_speed = random.choice([2, 3, 4])
        # A random light colour: each of red, green and blue between 80 and 255
        self.color = tuple(random.randint(80, 255) for _ in range(3))

    def update(self):
        """Move the firefly one frame further and change its brightness.

        Called once per frame. Returns nothing; it only changes the firefly itself.
        """
        # Drift, bouncing off the edges: when it leaves the 0-7 area, turn that
        # speed around so the next frames bring it back inside
        self.x += self.dx
        self.y += self.dy
        if not 0 <= self.x <= 7:
            self.dx = -self.dx
        if not 0 <= self.y <= 7:
            self.dy = -self.dy

        # Light up quickly (20 brightness steps per frame), then fade out slowly
        # (fade_speed steps per frame). Once fully dark, start as a new firefly.
        if self.brightening:
            self.brightness = min(self.peak, self.brightness + 20)
            if self.brightness >= self.peak:
                self.brightening = False
        else:
            self.brightness -= self.fade_speed
            if self.brightness <= 0:
                self.reset()

    def draw(self, frame):
        """Paint the firefly into `frame` (an 8x8 list of rows of colours).

        The firefly's colour is dimmed according to its current brightness.
        Returns nothing; the frame is changed in place.
        """
        # A dark firefly is invisible: nothing to draw
        if self.brightness <= 0:
            return
        # Round the fractional position to the nearest LED, and only draw it when
        # that LED is really on the grid (it can be just outside while bouncing)
        x, y = round(self.x), round(self.y)
        if 0 <= x <= 7 and 0 <= y <= 7:
            # brightness 255 = full colour, 0 = black; scale every colour part
            scale = self.brightness / 255
            frame[y][x] = tuple(min(255, int(c * scale)) for c in self.color)


def main():
    """Show the fireflies forever: update, draw and display one frame at a time."""
    sense = init(hat)
    fireflies = [Firefly() for _ in range(FIREFLY_COUNT)]
    while True:
        # Start every frame with an empty night sky ...
        frame = [[NIGHT] * 8 for _ in range(8)]
        # ... let each firefly move and paint itself on top of it ...
        for firefly in fireflies:
            firefly.update()
            firefly.draw(frame)
        # ... then send the finished picture to the LEDs and wait for the next frame
        sense.set_pixels(flatten(frame))
        time.sleep(FRAME_DELAY)


if __name__ == "__main__":
    run(main)
