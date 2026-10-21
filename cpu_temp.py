#!/usr/bin/env python3
"""Sense HAT – CPU temperature: shows the Pi's CPU temperature as two digits plus a bar.

The colour runs from green (40 °C or cooler) to red (75 °C or hotter),
and the bar along the bottom row fills up over the same range.

Now and then the display reacts to the temperature: when the Pi runs hot,
a wild flame flares up, shaking and throwing sparks; when it stays nice and
cool, a happy Japanese-style ^_^ face bounces, winks, grins and turns into
a beating heart.
"""
import atexit
import os
import random
import signal
import subprocess
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


TEMP_MIN = 40   # °C: fully green, empty bar
TEMP_MAX = 75   # °C: fully red, full bar
HOT_TEMP = 70   # °C: at or above this, flames show up now and then
COOL_TEMP = 45  # °C: at or below this, a happy face shows up now and then
EFFECT_CHANCE = 0.08   # Chance per second of an effect (about once every 12 s)
OFF = (0, 0, 0)        # An LED that is switched off: (red, green, blue) all zero

# Happy face expressions. E = eyes and mouth, P = pink cheeks, B = bright blush.
# Each picture is 8 strings of 8 letters, one string per LED row; "." means "LED off".
# picture() below turns the letters into colours using FACE_COLORS.
FACE_HAPPY = [          # ^_^
    "........",
    ".E....E.",
    "E.E..E.E",
    "........",
    "PP....PP",
    "..E..E..",
    "...EE...",
    "........",
]
FACE_WINK = [           # ^_-
    "........",
    ".E......",
    "E.E..EEE",
    "........",
    "PP....PP",
    "..E..E..",
    "...EE...",
    "........",
]
FACE_GRIN = [           # ^o^ with a big open smile and glowing cheeks
    "........",
    ".E....E.",
    "E.E..E.E",
    "........",
    "BB....BB",
    "..EEEE..",
    "..EPPE..",
    "...EE...",
]
HEART = [               # The heart the happy face turns into at the end
    "..P..P..",
    ".PBP.PBP",
    "PBBBPBBB",
    "PBBBBBBP",
    ".PBBBBP.",
    "..PBBP..",
    "...PP...",
    "........",
]
# Which (R, G, B) colour each letter in the face pictures stands for
FACE_COLORS = {"E": (255, 255, 255), "P": (200, 40, 100), "B": (255, 80, 150)}
# Colours to pick from at random for the sparkles around the happy face
# (white, warm yellow and light blue)
SPARKLE_COLORS = [(255, 255, 255), (255, 230, 100), (150, 220, 255)]

# One flame with a pointed tip and a round bottom.
# R = red edge, O = orange, Y = yellow, W = white-hot core
# These are the normal flame poses; show_fire() picks one at random for every frame,
# which makes the flame look like it is flickering.
FLAME_FRAMES = [
    [   # Tip straight up
        "....R...",
        "...RR...",
        "...ROR..",
        "..ROOR..",
        ".ROYYOR.",
        ".ROWWOR.",
        ".ROOOOR.",
        "..RRRR..",
    ],
    [   # Tip leaning left
        "...R....",
        "...RR...",
        "..ROR...",
        "..ROOR..",
        ".ROYYOR.",
        ".ROWWOR.",
        ".ROOOOR.",
        "..RRRR..",
    ],
    [   # Tip leaning right
        ".....R..",
        "....RR..",
        "...ROR..",
        "..ROOR..",
        ".ROYYOR.",
        ".ROWWOR.",
        ".ROOOOR.",
        "..RRRR..",
    ],
    [   # Ducking down for a moment
        "........",
        "...RR...",
        "..RORR..",
        "..ROOR..",
        ".ROYYOR.",
        ".ROWWOR.",
        ".ROOOOR.",
        "..RRRR..",
    ],
]
FLAME_FLARE = [   # The flame flaring up big
    "..R.R...",
    "..RRRR..",
    ".RROOR..",
    ".ROOYOR.",
    "ROYYYYOR",
    "ROYWWYOR",
    ".ROWWOR.",
    "..RRRR..",
]
# Colours for the sparks that fly up from the flame (gold, orange and pale yellow)
SPARK_COLORS = [(255, 200, 0), (255, 120, 0), (255, 255, 150)]
# Which (R, G, B) colour each letter in the flame pictures stands for
FLAME_COLORS = {"R": (200, 20, 0), "O": (255, 110, 0), "Y": (255, 210, 0), "W": (255, 255, 160)}

# 3x5 bitmap digits: each digit is 5 rows of 3 characters, "1" = LED on, "0" = LED off.
# Two of them side by side (3 + 1 gap + 3 = 7 columns) fit on the 8x8 display.
DIGITS = {
    "0": ["111", "101", "101", "101", "111"],
    "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"],
    "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"],
    "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"],
    "7": ["111", "001", "010", "100", "100"],
    "8": ["111", "101", "111", "101", "111"],
    "9": ["111", "101", "111", "001", "111"],
}


def read_cpu_temp():
    """Return the CPU temperature in whole degrees Celsius.

    Takes no arguments and returns an int, for example 52.

    It tries two ways, in order:
    1. The Linux kernel keeps the temperature in a small text file,
       /sys/class/thermal/thermal_zone0/temp, in thousandths of a degree
       (52345 means 52.345 °C). Reading a file is quick, so this is tried first.
    2. If that file is missing or holds something odd, it runs the Raspberry Pi
       tool `vcgencmd measure_temp`, which prints text like "temp=52.3'C",
       and cuts the number out of that text.
    """
    # Way 1: read the kernel's temperature file and turn thousandths into degrees
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read()) / 1000)
    except (OSError, ValueError):
        # Way 2: ask vcgencmd. "temp=52.3'C" -> take the part after "=",
        # then the part before "'" -> "52.3" -> 52
        output = subprocess.check_output(["vcgencmd", "measure_temp"], text=True)
        return round(float(output.split("=")[1].split("'")[0]))


def heat(temp):
    """Map a temperature to 0.0 (cool) .. 1.0 (hot).

    Args:
        temp: temperature in °C.

    Returns:
        A float between 0.0 and 1.0 that says how far `temp` lies between
        TEMP_MIN and TEMP_MAX. For example with 40 and 75: 40 °C -> 0.0,
        57.5 °C -> 0.5, 75 °C -> 1.0. Anything colder or hotter is clamped
        (held) at 0.0 or 1.0, so the colour and bar never go out of range.
    """
    return max(0.0, min(1.0, (temp - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)))


def temp_to_color(temp):
    """Pick the display colour for a temperature, from green (cool) to red (hot).

    Args:
        temp: temperature in °C.

    Returns:
        An (R, G, B) tuple. As the heat goes up, red goes up and green goes down
        by the same amount: cool is (0, 255, 0) green, halfway is (127, 127, 0)
        a yellowish mix, and hot is (255, 0, 0) red. Blue is always 0.
    """
    t = heat(temp)
    return (int(255 * t), int(255 * (1 - t)), 0)


def draw(temp):
    """Build the normal temperature screen: two digits with a bar underneath.

    Args:
        temp: temperature in °C.

    Returns:
        A flat list of 64 (R, G, B) colours, ready for sense.set_pixels().
        The digits and the bar are both drawn in the colour from temp_to_color().
        The bar on the bottom row is 0 to 8 LEDs long: empty at TEMP_MIN,
        full at TEMP_MAX.
    """
    color = temp_to_color(temp)
    # Start with an empty 8x8 grid (a list of 8 rows, each a list of 8 colours).
    # frame[y][x] is the LED in row y (0 = top) and column x (0 = left).
    frame = [[OFF] * 8 for _ in range(8)]

    # Two digits on rows 1-5, in columns 0-2 and 4-6 (column 3 is the gap)
    # Only two digits fit, so keep the number between 0 and 99.
    # f"{shown:02d}" makes it a two-character text with a leading zero ("07", "52"),
    # and zip() pairs the first character with column 0 and the second with column 4.
    shown = max(0, min(99, temp))
    for offset, digit in zip((0, 4), f"{shown:02d}"):
        # Copy the digit's 3x5 bitmap onto the grid: every "1" becomes a lit LED,
        # moved down 1 row and right by `offset` columns
        for y, line in enumerate(DIGITS[digit]):
            for x, bit in enumerate(line):
                if bit == "1":
                    frame[y + 1][x + offset] = color

    # Bar on the bottom row: heat 0.0..1.0 times 8 LEDs, rounded, lit from the left
    for x in range(round(heat(temp) * 8)):
        frame[7][x] = color

    # set_pixels() wants one long list of 64 colours, not 8 rows of 8
    return flatten(frame)


def picture(lines, shift=0, colors=FACE_COLORS):
    """Turn a picture made of letters into an 8x8 frame, moved up by `shift` rows.

    Args:
        lines: the picture as 8 strings of 8 letters, like FACE_HAPPY or HEART.
        shift: how many rows to move the picture up (0 = not moved). The happy
            face uses 0 and 1 in turn to make a little bounce. Rows that move off
            the top are simply dropped, and the bottom fills with dark LEDs.
        colors: a dict saying which colour each letter stands for. Letters that
            are not in it (such as ".") stay dark. Passing a different dict is an
            easy way to draw the same picture in other colours, e.g. a dimmer heart.

    Returns:
        An 8x8 grid (list of 8 rows of 8 (R, G, B) colours). It is not flattened
        yet, so callers can still draw extra things on it, such as sparkles.
    """
    frame = [[OFF] * 8 for _ in range(8)]
    # Go through every letter; if it has a colour and its new row (y - shift) is
    # still on the display, light that LED
    for y, line in enumerate(lines):
        for x, mark in enumerate(line):
            if mark in colors and 0 <= y - shift < 8:
                frame[y - shift][x] = colors[mark]
    return frame


def show(sense, frame, seconds):
    """Show an 8x8 frame on the LEDs and keep it there for a while.

    Args:
        sense: the SenseHat object to draw on.
        frame: an 8x8 grid of (R, G, B) colours, as made by picture().
        seconds: how long to wait before returning (this sets the animation speed).

    Returns nothing. Used by the animations to play one frame after another.
    """
    sense.set_pixels(flatten(frame))
    time.sleep(seconds)


def show_happy_face(sense):
    """About 7 seconds of joy: bounce, wink, grin, sparkles, then a beating heart.

    Args:
        sense: the SenseHat object to draw on.

    Returns nothing; it returns once the animation is over. The animation is a
    simple script of pictures, each shown for a fixed time with show().
    """
    for shift in (0, 1, 0, 1, 0):                      # Happy bounce
        show(sense, picture(FACE_HAPPY, shift), 0.25)
    show(sense, picture(FACE_WINK), 0.7)               # Wink
    show(sense, picture(FACE_HAPPY), 0.3)
    for _ in range(3):                                 # Big grin, cheeks glowing
        show(sense, picture(FACE_GRIN), 0.3)
        show(sense, picture(FACE_HAPPY), 0.2)
    for _ in range(12):                                # Sparkles all around
        # Each frame: a fresh face plus up to 3 sparkles at random spots. A spot that
        # is already part of the face is skipped, so the face is never covered.
        frame = picture(FACE_HAPPY)
        for _ in range(3):
            x, y = random.randint(0, 7), random.randint(0, 7)
            if frame[y][x] == OFF:
                frame[y][x] = random.choice(SPARKLE_COLORS)
        show(sense, frame, 0.12)
    for shift in (0, 1, 0):                            # One more little bounce
        show(sense, picture(FACE_HAPPY, shift), 0.25)
    for _ in range(2):                                 # Turns into a beating heart
        # Bright heart, then the same heart in darker colours: that looks like a pulse
        show(sense, picture(HEART), 0.35)
        show(sense, picture(HEART, colors={"P": (120, 20, 60), "B": (200, 40, 100)}), 0.25)
    show(sense, picture(HEART), 0.5)


def show_fire(sense, seconds=3.5):
    """A wild flame: it shakes, flares up, throws sparks and makes the air glow.

    Args:
        sense: the SenseHat object to draw on.
        seconds: roughly how long the fire lasts.

    Returns nothing; it returns once the fire is over.

    How it works: every frame (0.07 s, about 14 frames per second) the whole
    picture is drawn again from scratch, with a lot of randomness each time:
    a random background glow, a random flame shape, a random sideways shake and
    random brightness per LED. Only the sparks are remembered between frames,
    so they can fly upward step by step.
    """
    # Each spark is a small list [x, y, colour] that is changed as the spark moves
    sparks = []
    # Number of frames = total time / time per frame (3.5 / 0.07 = 50 frames)
    for _ in range(int(seconds / 0.07)):
        # Hot air: a dim red glow flickers in the background
        # (each LED has a 30% chance to glow a random dark red, the rest stay off)
        frame = [[(random.randint(20, 60), 0, 0) if random.random() < 0.3 else OFF
                  for _ in range(8)] for _ in range(8)]

        # The flame itself, sometimes flaring up, shaking left and right
        # FLAME_FLARE is in the list twice, so it is picked a bit more often (2 in 6).
        # The shake moves the flame one column left or right, but half the time not at all.
        shape = random.choice(FLAME_FRAMES + [FLAME_FLARE, FLAME_FLARE])
        shake = random.choice([-1, 0, 0, 1])
        # Draw the flame; every LED gets a random brightness between 60% and 100%
        # of its colour, which makes the fire shimmer. LEDs shaken off the edge are skipped.
        for y, line in enumerate(shape):
            for x, mark in enumerate(line):
                if mark in FLAME_COLORS and 0 <= x + shake < 8:
                    glow = random.uniform(0.6, 1.0)
                    frame[y][x + shake] = tuple(int(c * glow) for c in FLAME_COLORS[mark])

        # Sparks shoot up from the flame and drift away
        # 1 to 3 new sparks start near the top of the flame (columns 2-5, rows 1-3),
        # following the flame's shake
        for _ in range(random.randint(1, 3)):
            sparks.append([random.randint(2, 5) + shake, random.randint(1, 3),
                           random.choice(SPARK_COLORS)])
        # Move every spark one row up and maybe one column sideways. Sparks still on
        # the display are drawn and kept; the ones that left it are forgotten.
        alive = []
        for spark in sparks:
            spark[1] -= 1
            spark[0] += random.choice([-1, 0, 1])
            if 0 <= spark[0] < 8 and spark[1] >= 0:
                frame[spark[1]][spark[0]] = spark[2]
                alive.append(spark)
        sparks = alive

        show(sense, frame, 0.07)


def main():
    """Show the CPU temperature, updated once per second, forever.

    Takes no arguments and never returns (stop it with Ctrl+C). Every second it
    reads the temperature and draws it. When the Pi is hot or nicely cool, it
    also rolls a dice: with a chance of EFFECT_CHANCE it first plays the fire or
    the happy face animation, and then goes back to showing the number.
    """
    sense = init(hat)
    while True:
        temp = read_cpu_temp()
        # random.random() gives a number from 0.0 to 1.0, so "< EFFECT_CHANCE" is true
        # 8% of the time. That keeps the animations a nice surprise, not constant.
        if temp >= HOT_TEMP and random.random() < EFFECT_CHANCE:
            show_fire(sense)
        elif temp <= COOL_TEMP and random.random() < EFFECT_CHANCE:
            show_happy_face(sense)
        # Show the temperature screen, then wait a second before measuring again
        sense.set_pixels(draw(temp))
        time.sleep(1)


if __name__ == "__main__":
    run(main)
