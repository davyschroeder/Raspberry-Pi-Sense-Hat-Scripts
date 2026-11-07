#!/usr/bin/env python3
"""Sense HAT – Fireworks: a full fireworks show with rockets, flashes, glowing
trails and ten kinds of bursts, ending every so often in a grand finale.

Push the joystick to set off your own fireworks. Every arrow instantly sets
off a big rainbow-coloured explosion on its side of the sky, and:
  left / right  - also launches a rocket from that side
  up            - also fires a salvo of three rockets
  down          - also starts the grand finale
  press         - launches a rocket from a random spot
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


FPS = 30                 # Frames per second
FRAME_DELAY = 1 / FPS
GRAVITY = 5.0            # How fast things fall, in LEDs per second per second
AFTERGLOW = 0.70         # How much of each LED's light is left one frame later (the trails)
MAX_PARTICLES = 320      # Upper limit, so a slow Pi keeps up during the finale
STAR_COUNT = 5           # Faint twinkling stars in the background

FINALE_EVERY = (40, 55)  # Seconds of normal show between two grand finales
FINALE_LENGTH = 7.0      # Seconds of non-stop rockets in a finale

# Firework colours (red, green, blue 0-255), bright and saturated so they
# still look like themselves when they fade out
COLOURS = [
    (255, 30, 20),    # red
    (255, 120, 0),    # orange
    (255, 210, 40),   # gold
    (60, 255, 40),    # green
    (0, 220, 255),    # cyan
    (40, 80, 255),    # blue
    (180, 40, 255),   # purple
    (255, 40, 170),   # pink
    (255, 255, 255),  # white
]
GOLD = (255, 170, 40)
WHITE = (255, 255, 255)


def mix(a, b, t):
    """Blend colour a into colour b: t = 0 gives a, t = 1 gives b."""
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


class Particle:
    """One glowing spark. Everything in the show is built from these.

    x and y are fractions (3.4 = between LED 3 and 4); the spark is drawn
    spread over the four nearest LEDs, so it glides smoothly instead of
    jumping from LED to LED.
    """

    def __init__(self, x, y, vx, vy, colour, life, drag=1.5, gravity=1.0,
                 colour2=None, twinkle=False, ember=None, split=None, hot=True,
                 bright=1.0):
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.colour = colour
        self.colour2 = colour2      # Colour it changes into halfway (or None)
        self.life = life            # Seconds until it has burned out
        self.age = 0.0
        self.drag = drag            # Air resistance: higher = slows down sooner
        self.gravity = gravity      # 1 = normal falling, 0 = floats
        self.twinkle = twinkle      # Crackles and strobes white at the end of its life
        self.ember = ember          # Colour of the embers it drips (willow), or None
        self.split = split          # Seconds after which it bursts into 4 (crossette)
        self.hot = hot              # Starts white-hot for a split second
        self.bright = bright
        self.snap = False

    def update(self, dt, show):
        """Move one frame. Returns False when the spark has burned out."""
        self.age += dt
        if self.age >= self.life:
            return False
        slow = math.exp(-self.drag * dt)
        self.vx *= slow
        self.vy = self.vy * slow + GRAVITY * self.gravity * dt
        self.x += self.vx * dt
        self.y += self.vy * dt

        # Willow sparks drip small embers that hang and fall behind them
        if self.ember and random.random() < 0.12:
            show.add(Particle(self.x, self.y, random.uniform(-0.2, 0.2), 0.4,
                              self.ember, random.uniform(0.3, 0.6), drag=3,
                              gravity=0.5, hot=False, bright=0.35))

        # Crossette sparks split into four smaller ones
        if self.split is not None and self.age >= self.split:
            turn = random.uniform(0, math.pi / 2)
            for k in range(4):
                angle = turn + k * math.pi / 2
                show.add(Particle(self.x, self.y, math.cos(angle) * 3.2,
                                  math.sin(angle) * 3.2, self.colour2 or self.colour,
                                  random.uniform(0.6, 0.9), drag=2.2, gravity=0.8))
            show.flash(self.colour, 0.08)
            return False
        return True

    def light(self):
        """The colour this spark gives off right now (already dimmed)."""
        t = self.age / self.life
        colour = self.colour
        if self.colour2 and self.split is None:
            colour = mix(self.colour, self.colour2, min(1, max(0, (t - 0.3) * 2.5)))
        if self.hot and self.age < 0.12:
            colour = mix(WHITE, colour, self.age / 0.12)  # White-hot at the start
        level = (1 - t) ** 1.3 * self.bright
        if self.twinkle and t > 0.45:
            # Crackle: flicker between dark and a bright white strobe
            if random.random() < 0.3:
                return tuple(c * 1.4 for c in mix(colour, WHITE, 0.7))
            return (0, 0, 0)
        return tuple(c * level for c in colour)


class Rocket:
    """A rocket climbing up with a spark trail, bursting at the top."""

    def __init__(self, x, burst, height=None):
        self.x = x
        self.y = 8.4
        top = height if height is not None else random.uniform(1.6, 3.0)
        # Start speed that makes it stop climbing (and burst) at y = top
        self.vy = -math.sqrt(2 * GRAVITY * (self.y - top))
        self.burst = burst
        self.snap = True  # Drawn as one sharp LED instead of smeared over four

    def update(self, dt, show):
        """Climb one frame. Returns False once it has burst."""
        self.vy += GRAVITY * dt
        self.y += self.vy * dt
        # Sparks spraying out of the bottom of the rocket
        for _ in range(2):
            show.add(Particle(round(self.x) + random.uniform(-0.25, 0.25), self.y + 0.6,
                              random.uniform(-0.6, 0.6), random.uniform(0.5, 2.0), GOLD,
                              random.uniform(0.15, 0.35), drag=2, gravity=0.5, hot=False,
                              bright=0.45))
        if self.vy >= -0.4:
            self.burst(show, round(self.x), self.y)
            return False
        return True

    def light(self):
        return (255, 230, 170)


# ---------------------------------------------------------------------------
# The bursts. Each one takes the show and the spot where the rocket burst.
# ---------------------------------------------------------------------------
def ring_of(count, speed, jitter=0.0):
    """Evenly spread directions round a circle, as (vx, vy) pairs."""
    turn = random.uniform(0, 2 * math.pi)
    for k in range(count):
        angle = turn + 2 * math.pi * k / count + random.uniform(-jitter, jitter)
        s = speed * random.uniform(1 - jitter, 1 + jitter) if jitter else speed
        yield math.cos(angle) * s, math.sin(angle) * s


def peony(show, x, y):
    """The classic round ball of sparks in one colour."""
    colour = random.choice(COLOURS)
    for vx, vy in ring_of(26, 5.0, 0.15):
        show.add(Particle(x, y, vx, vy, colour, random.uniform(1.1, 1.5)))
    show.flash(colour, 0.35)


def chrysanthemum(show, x, y):
    """A ball of sparks that changes colour halfway."""
    a, b = random.sample(COLOURS, 2)
    for vx, vy in ring_of(28, 5.3, 0.12):
        show.add(Particle(x, y, vx, vy, a, random.uniform(1.3, 1.7), colour2=b))
    show.flash(a, 0.35)


def ring(show, x, y):
    """A crisp, perfectly round ring."""
    colour = random.choice(COLOURS)
    for vx, vy in ring_of(22, 5.5):
        show.add(Particle(x, y, vx, vy, colour, 1.2, drag=1.8, gravity=0.4))
    show.flash(colour, 0.3)


def double_ring(show, x, y):
    """A small ring inside a big one, in two colours."""
    a, b = random.sample(COLOURS, 2)
    for vx, vy in ring_of(20, 6.0):
        show.add(Particle(x, y, vx, vy, a, 1.3, drag=1.8, gravity=0.4))
    for vx, vy in ring_of(12, 2.8):
        show.add(Particle(x, y, vx, vy, b, 1.3, drag=1.8, gravity=0.4))
    show.flash(a, 0.4)


def willow(show, x, y):
    """Golden sparks that droop down like the branches of a weeping willow."""
    for vx, vy in ring_of(14, 5.0, 0.15):
        show.add(Particle(x, y, vx, vy - 0.8, GOLD, random.uniform(2.4, 3.0), drag=2.2,
                          gravity=0.9, ember=(200, 110, 20), bright=0.85))
    show.flash(GOLD, 0.3)


def palm(show, x, y):
    """A few thick, bright fronds, like a palm tree."""
    colour = random.choice([GOLD, (255, 120, 0), (60, 255, 40), WHITE])
    for vx, vy in ring_of(7, 5.5, 0.08):
        show.add(Particle(x, y, vx, vy - 1.0, colour, 1.6, drag=1.7, bright=1.3,
                          ember=mix(colour, (0, 0, 0), 0.4)))
    show.flash(colour, 0.35)


def crossette(show, x, y):
    """Sparks that fly out and then each burst into a cross of four."""
    a, b = random.sample(COLOURS, 2)
    for vx, vy in ring_of(4, 6.0, 0.1):
        show.add(Particle(x, y, vx, vy, a, 2.0, drag=1.2, gravity=0.6,
                          colour2=b, split=random.uniform(0.45, 0.6)))
    show.flash(a, 0.25)


def crackle(show, x, y):
    """A ball of sparks that ends in a sizzle of crackling white strobes."""
    colour = random.choice(COLOURS)
    for vx, vy in ring_of(24, 4.8, 0.2):
        show.add(Particle(x, y, vx, vy, colour, random.uniform(1.6, 2.0), twinkle=True))
    show.flash(colour, 0.35)


def heart(show, x, y):
    """A heart-shaped burst."""
    colour = random.choice([(255, 30, 20), (255, 40, 170)])
    for k in range(30):
        t = 2 * math.pi * k / 30
        hx = 16 * math.sin(t) ** 3
        hy = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        show.add(Particle(x, y, hx * 0.36, hy * 0.36, colour, 1.4, drag=1.8, gravity=0.25))
    show.flash(colour, 0.35)


def strobe(show, x, y):
    """A cloud of white flashing glitter that hangs in the air."""
    for vx, vy in ring_of(16, 4.5, 0.4):
        show.add(Particle(x, y, vx, vy, WHITE, random.uniform(1.6, 2.2), drag=2.5,
                          gravity=0.3, twinkle=True, hot=False))
    show.flash(WHITE, 0.25)


def rainbow(show, x, y):
    """A big explosion in which every spark has its own colour (the joystick's burst)."""
    # Six bright colours, each filling its own slice of the ring, so the
    # colours stay pure instead of blending into pastel where sparks overlap
    hues = random.sample(COLOURS[:-1], 6)  # Every colour except white
    for k, (vx, vy) in enumerate(ring_of(30, 6.0, 0.08)):
        show.add(Particle(x, y, vx, vy, hues[k // 5], random.uniform(1.3, 1.7),
                          drag=1.6, bright=1.2))
    for vx, vy in ring_of(10, 2.5, 0.2):  # A white-hot core
        show.add(Particle(x, y, vx, vy, WHITE, 0.6, drag=2.5, gravity=0.3))
    show.flash((255, 220, 255), 0.6)


# Where the joystick's rainbow explosion goes off for each arrow (x, y)
ARROW_SPOTS = {"left": (1.8, 3.5), "right": (5.2, 3.5), "up": (3.5, 2.0), "down": (3.5, 4.8)}


# How often each burst is picked (bigger number = more often)
BURSTS = [(peony, 5), (chrysanthemum, 4), (ring, 3), (double_ring, 2), (willow, 3),
          (palm, 2), (crossette, 2), (crackle, 2), (heart, 1), (strobe, 1)]


def random_burst():
    return random.choices([b for b, _ in BURSTS], [w for _, w in BURSTS])[0]


class Show:
    """Runs the fireworks: launches rockets, moves every spark and draws it all."""

    def __init__(self):
        self.things = []                       # Rockets and sparks in the air
        self.glow = [[0.0] * 3 for _ in range(64)]  # Light left on each LED (trails)
        self.flash_colour, self.flash_level = (0, 0, 0), 0.0
        self.stars = [(random.randrange(8), random.randrange(5), random.uniform(0, 6))
                      for _ in range(STAR_COUNT)]
        self.next_launch = 0.5
        self.next_finale = random.uniform(*FINALE_EVERY)
        self.finale_left = 0.0
        self.quiet = 0.0                       # Pause after a finale
        self.no_stick = False                  # True once the joystick turned out unreadable

    def add(self, thing):
        if len(self.things) < MAX_PARTICLES or isinstance(thing, Rocket):
            self.things.append(thing)

    def flash(self, colour, level):
        """Light up the whole sky for a moment, as when a shell bursts."""
        if level > self.flash_level:
            self.flash_colour, self.flash_level = colour, level

    def launch(self, x=None, burst=None, height=None):
        if x is None:
            x = random.uniform(1.0, 6.0)
        self.add(Rocket(x, burst or random_burst(), height))

    def salvo(self):
        """Three rockets side by side with the same burst."""
        burst = random_burst()
        height = random.uniform(2.0, 2.8)
        for x in (1.2, 3.5, 5.8):
            self.launch(x + random.uniform(-0.3, 0.3), burst, height)

    def start_finale(self):
        self.finale_left = FINALE_LENGTH
        self.next_launch = 0

    def schedule(self, dt):
        """Decide when the next rocket goes up."""
        if self.quiet > 0:
            self.quiet -= dt
            return
        self.next_launch -= dt
        if self.finale_left > 0:
            self.finale_left -= dt
            if self.next_launch <= 0:
                self.launch()
                if random.random() < 0.3:
                    self.launch()
                self.next_launch = random.uniform(0.12, 0.3)
            if self.finale_left <= 0:
                # One last big salvo, then a moment of silence
                self.salvo()
                self.quiet = 3.5
                self.next_finale = random.uniform(*FINALE_EVERY)
                self.next_launch = 1.0
            return
        self.next_finale -= dt
        if self.next_finale <= 0:
            self.start_finale()
        elif self.next_launch <= 0:
            if random.random() < 0.12:
                self.salvo()
            else:
                self.launch()
            self.next_launch = random.uniform(0.7, 1.9)

    def joystick(self, sense):
        """Handle joystick pushes: launch your own rockets."""
        if self.no_stick:
            return
        try:
            events = sense.stick.get_events()
        except (AttributeError, OSError):
            # No joystick to read (e.g. the emulator, or no access to it):
            # just run the show without it
            self.no_stick = True
            return
        for event in events:
            if event.action != "pressed":
                continue
            if event.direction in ARROW_SPOTS:
                x, y = ARROW_SPOTS[event.direction]
                rainbow(self, x + random.uniform(-0.4, 0.4), y + random.uniform(-0.4, 0.4))
            if event.direction == "left":
                self.launch(random.uniform(0.8, 2.2))
            elif event.direction == "right":
                self.launch(random.uniform(4.8, 6.2))
            elif event.direction == "middle":
                self.launch()
            elif event.direction == "up":
                self.salvo()
            elif event.direction == "down":
                self.quiet = 0
                self.start_finale()

    def update(self, dt):
        self.schedule(dt)
        self.things = [t for t in self.things if t.update(dt, self)]
        self.flash_level = max(0.0, self.flash_level - dt * 2.2)

    def draw(self, now):
        """Turn everything into one 8x8 picture."""
        # Fresh light from every spark, smeared over the 4 LEDs around it
        fresh = [[0.0] * 3 for _ in range(64)]
        for thing in self.things:
            r, g, b = thing.light()
            if r + g + b < 1:
                continue
            x = round(thing.x) if thing.snap else thing.x
            x0, y0 = math.floor(x), math.floor(thing.y)
            fx, fy = x - x0, thing.y - y0
            for px, py, w in ((x0, y0, (1 - fx) * (1 - fy)), (x0 + 1, y0, fx * (1 - fy)),
                              (x0, y0 + 1, (1 - fx) * fy), (x0 + 1, y0 + 1, fx * fy)):
                if 0 <= px < 8 and 0 <= py < 8 and w > 0.02:
                    w = w ** 0.75  # A bit brighter between LEDs, so sparks stay visible
                    cell = fresh[py * 8 + px]
                    cell[0] += r * w
                    cell[1] += g * w
                    cell[2] += b * w

        pixels = []
        fr, fg, fb = (c * self.flash_level for c in self.flash_colour)
        for i in range(64):
            # Trails: an LED fades slowly, but new light shows straight away
            glow, new = self.glow[i], fresh[i]
            for c in range(3):
                glow[c] = max(glow[c] * AFTERGLOW, new[c])
            # The flash lights up the lower sky most, like smoke over the crowd
            sky = 0.5 + 0.5 * (i // 8) / 7
            pixels.append([glow[0] + fr * sky * 0.5, glow[1] + fg * sky * 0.5,
                           glow[2] + fb * sky * 0.5])

        # Faint twinkling stars, only where nothing else is shining
        for sx, sy, phase in self.stars:
            p = pixels[sy * 8 + sx]
            level = 14 + 10 * math.sin(now * 1.7 + phase)
            if sum(p) < 40:
                p[0], p[1], p[2] = max(p[0], level), max(p[1], level), max(p[2], level + 8)

        return [tuple(min(255, int(c)) for c in p) for p in pixels]


def main():
    """Run the fireworks show forever."""
    sense = init(hat)
    show = Show()
    last = time.monotonic()
    while True:
        now = time.monotonic()
        dt = min(now - last, 0.1)  # Don't let a hiccup make everything jump
        last = now
        show.joystick(sense)
        show.update(dt)
        sense.set_pixels(show.draw(now))
        time.sleep(max(0.0, FRAME_DELAY - (time.monotonic() - now)))


if __name__ == "__main__":
    run(main)
