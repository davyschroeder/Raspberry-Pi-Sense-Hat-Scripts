#!/usr/bin/env python3
"""Sense HAT – Beating heart: a red heart that pulses like a heartbeat."""
import atexit
import os
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


# Shades of red, from off (0) to full brightness (4).
# Each colour is an (R, G, B) tuple: red, green and blue, each from 0 (off) to 255 (full).
# The heart pictures below use the numbers 0-4 as an index into this list.
SHADES = [
    (0, 0, 0),
    (70, 0, 0),
    (120, 0, 0),
    (200, 0, 0),
    (255, 0, 0),
]

# The three heart sizes. Each is 64 numbers: 8 rows of 8 LEDs, read left to right,
# top to bottom. Every number picks a shade from SHADES (0 = off, 4 = brightest).
# The small heart, shown while the heart "rests" between beats
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

# The in-between heart, shown while growing and shrinking so the beat looks smooth
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

# The big, bright heart at the peak of the beat
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

# One heartbeat: (frame, seconds to show it).
# The whole beat takes 1.3 seconds (about 46 beats per minute). Make the times
# shorter for a faster, more excited heart, or longer for a calmer one.
HEARTBEAT = [
    (SMALL, 0.25),   # Resting
    (MEDIUM, 0.2),   # Growing
    (LARGE, 0.35),   # Full beat
    (MEDIUM, 0.2),   # Shrinking
    (SMALL, 0.3),    # Rest before the next beat
]


def main():
    """Show the beating heart on the LEDs, forever (until Ctrl+C or another script).

    Takes no arguments and never returns. It first turns every heart picture from
    shade numbers (0-4) into real colours, once, so the loop below only has to send
    ready-made pixel lists to the LEDs. Then it plays the HEARTBEAT frames over and
    over, waiting the given number of seconds after each one.
    """
    sense = init(hat)
    # Translate each number in each picture into its (R, G, B) colour from SHADES.
    # Doing this up front means no work is wasted on it while the heart beats.
    frames = [([SHADES[v] for v in frame], delay) for frame, delay in HEARTBEAT]
    # Play the heartbeat again and again: show a frame, hold it, show the next one
    while True:
        for pixels, delay in frames:
            sense.set_pixels(pixels)
            time.sleep(delay)


if __name__ == "__main__":
    run(main)
