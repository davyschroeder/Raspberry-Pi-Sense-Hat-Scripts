#!/usr/bin/env python3
"""Sense HAT – CPU temperature: shows the Pi's CPU temperature as two digits plus a bar.

The colour runs from green (40 °C or cooler) to red (75 °C or hotter),
and the bar along the bottom row fills up over the same range.

Now and then the display reacts to the temperature: when the Pi runs hot,
flames flare up for a moment; when it stays nice and cool, a happy
Japanese-style ^_^ face pops up and wiggles.
"""
import random
import subprocess
import time

try:
    from sense_hat import SenseHat
    hat = SenseHat()
except (ImportError, OSError):  # No Sense HAT library or board found: use the emulator
    from sense_emu import SenseHat
    hat = SenseHat()

from common import flatten, init, run

TEMP_MIN = 40   # °C: fully green, empty bar
TEMP_MAX = 75   # °C: fully red, full bar
HOT_TEMP = 70   # °C: at or above this, flames show up now and then
COOL_TEMP = 45  # °C: at or below this, a happy face shows up now and then
EFFECT_CHANCE = 0.08   # Chance per second of an effect (about once every 12 s)
OFF = (0, 0, 0)

# Happy face expressions. E = eyes and mouth, P = pink cheeks, B = bright blush
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
HEART = [
    "..P..P..",
    ".PBP.PBP",
    "PBBBPBBB",
    "PBBBBBBP",
    ".PBBBBP.",
    "..PBBP..",
    "...PP...",
    "........",
]
FACE_COLORS = {"E": (255, 255, 255), "P": (200, 40, 100), "B": (255, 80, 150)}
SPARKLE_COLORS = [(255, 255, 255), (255, 230, 100), (150, 220, 255)]

# One flame with a pointed tip and a round bottom.
# R = red edge, O = orange, Y = yellow, W = white-hot core
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
SPARK_COLORS = [(255, 200, 0), (255, 120, 0), (255, 255, 150)]
FLAME_COLORS = {"R": (200, 20, 0), "O": (255, 110, 0), "Y": (255, 210, 0), "W": (255, 255, 160)}

# 3x5 bitmap digits
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
    """Return the CPU temperature in whole degrees Celsius."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read()) / 1000)
    except (OSError, ValueError):
        output = subprocess.check_output(["vcgencmd", "measure_temp"], text=True)
        return round(float(output.split("=")[1].split("'")[0]))


def heat(temp):
    """Map a temperature to 0.0 (cool) .. 1.0 (hot)."""
    return max(0.0, min(1.0, (temp - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)))


def temp_to_color(temp):
    t = heat(temp)
    return (int(255 * t), int(255 * (1 - t)), 0)


def draw(temp):
    color = temp_to_color(temp)
    frame = [[OFF] * 8 for _ in range(8)]

    # Two digits on rows 1-5, in columns 0-2 and 4-6 (column 3 is the gap)
    shown = max(0, min(99, temp))
    for offset, digit in zip((0, 4), f"{shown:02d}"):
        for y, line in enumerate(DIGITS[digit]):
            for x, bit in enumerate(line):
                if bit == "1":
                    frame[y + 1][x + offset] = color

    # Bar on the bottom row
    for x in range(round(heat(temp) * 8)):
        frame[7][x] = color

    return flatten(frame)


def picture(lines, shift=0, colors=FACE_COLORS):
    """Turn a picture made of letters into an 8x8 frame, moved up by `shift` rows."""
    frame = [[OFF] * 8 for _ in range(8)]
    for y, line in enumerate(lines):
        for x, mark in enumerate(line):
            if mark in colors and 0 <= y - shift < 8:
                frame[y - shift][x] = colors[mark]
    return frame


def show(sense, frame, seconds):
    sense.set_pixels(flatten(frame))
    time.sleep(seconds)


def show_happy_face(sense):
    """About 7 seconds of joy: bounce, wink, grin, sparkles, then a beating heart."""
    for shift in (0, 1, 0, 1, 0):                      # Happy bounce
        show(sense, picture(FACE_HAPPY, shift), 0.25)
    show(sense, picture(FACE_WINK), 0.7)               # Wink
    show(sense, picture(FACE_HAPPY), 0.3)
    for _ in range(3):                                 # Big grin, cheeks glowing
        show(sense, picture(FACE_GRIN), 0.3)
        show(sense, picture(FACE_HAPPY), 0.2)
    for _ in range(12):                                # Sparkles all around
        frame = picture(FACE_HAPPY)
        for _ in range(3):
            x, y = random.randint(0, 7), random.randint(0, 7)
            if frame[y][x] == OFF:
                frame[y][x] = random.choice(SPARKLE_COLORS)
        show(sense, frame, 0.12)
    for shift in (0, 1, 0):
        show(sense, picture(FACE_HAPPY, shift), 0.25)
    for _ in range(2):                                 # Turns into a beating heart
        show(sense, picture(HEART), 0.35)
        show(sense, picture(HEART, colors={"P": (120, 20, 60), "B": (200, 40, 100)}), 0.25)
    show(sense, picture(HEART), 0.5)


def show_fire(sense, seconds=3.5):
    """A wild flame: it shakes, flares up, throws sparks and makes the air glow."""
    sparks = []
    for _ in range(int(seconds / 0.07)):
        # Hot air: a dim red glow flickers in the background
        frame = [[(random.randint(20, 60), 0, 0) if random.random() < 0.3 else OFF
                  for _ in range(8)] for _ in range(8)]

        # The flame itself, sometimes flaring up, shaking left and right
        shape = random.choice(FLAME_FRAMES + [FLAME_FLARE, FLAME_FLARE])
        shake = random.choice([-1, 0, 0, 1])
        for y, line in enumerate(shape):
            for x, mark in enumerate(line):
                if mark in FLAME_COLORS and 0 <= x + shake < 8:
                    glow = random.uniform(0.6, 1.0)
                    frame[y][x + shake] = tuple(int(c * glow) for c in FLAME_COLORS[mark])

        # Sparks shoot up from the flame and drift away
        for _ in range(random.randint(1, 3)):
            sparks.append([random.randint(2, 5) + shake, random.randint(1, 3),
                           random.choice(SPARK_COLORS)])
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
    sense = init(hat)
    while True:
        temp = read_cpu_temp()
        if temp >= HOT_TEMP and random.random() < EFFECT_CHANCE:
            show_fire(sense)
        elif temp <= COOL_TEMP and random.random() < EFFECT_CHANCE:
            show_happy_face(sense)
        sense.set_pixels(draw(temp))
        time.sleep(1)


if __name__ == "__main__":
    run(main)
