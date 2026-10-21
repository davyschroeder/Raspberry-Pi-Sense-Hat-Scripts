#!/usr/bin/env python3
"""Sense HAT – Frogger: help the frog cross the road and the river.

Hop the frog from the bottom row to one of the 4 homes at the top. First dodge
the cars and trucks on the road, rest on the grey strip in the middle, then
hop across the river on the drifting logs: the water is deadly, and so is
drifting off the edge. Each frog has 30 seconds to get home. Fill all 4 homes
for the next, faster level. You have 3 lives.

The computer plays a demo until you touch the joystick: then you play.
Push the joystick to hop up, down, left or right. After game over, or when you
leave the joystick alone for a while, the computer takes over again.
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
    """Keeps track of the Sense HAT joystick (or the emulator's: arrow keys + Enter)."""

    def __init__(self, sense):
        self.sense = sense
        self.held = set()        # Directions held down right now
        self.last_input = 0.0

    def read(self):
        """Return the directions pressed since the last call ("up", "left", "middle", ...)."""
        pressed = []
        try:
            events = self.sense.stick.get_events()
        except (AttributeError, OSError):
            return pressed
        for event in events:
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
    """A green play symbol: you're in control now."""
    sense.set_pixels([(0, 255, 0) if c == "#" else (0, 0, 0) for row in PLAY_SIGN for c in row])
    time.sleep(0.8)
# ---------------------------------------------------------------------------


# Colours
FROG = (120, 255, 0)
HEDGE = (0, 70, 0)
HOME_EMPTY = (0, 0, 0)
HOME_FULL = (0, 255, 120)
WATER = (0, 0, 90)
LOG = (130, 70, 20)
ROAD = (0, 0, 0)
STRIP = (40, 40, 40)          # The safe strip in the middle and the start row
SPLAT = (255, 0, 0)

# The playfield, row by row:
#   0      hedge with 4 homes
#   1, 2   river with logs
#   3      safe strip
#   4-6    road with cars and trucks
#   7      start
HOMES = (1, 3, 4, 6)
START = (3, 7)


class Lane:
    """A row of objects ("#" in the pattern) that slides left or right."""

    def __init__(self, y, pattern, direction, delay, color, river=False):
        self.y = y
        self.pattern = pattern
        self.direction = direction
        self.base_delay = delay
        self.delay = delay
        self.color = color
        self.river = river
        self.offset = random.randrange(len(pattern))
        self.timer = 0.0

    def occupied(self, x, offset=None):
        offset = self.offset if offset is None else offset
        return self.pattern[(x - offset) % len(self.pattern)] == "#"

    def steps_within(self, seconds):
        """How many times this lane will move in the next `seconds`."""
        return int((self.timer + seconds) / self.delay)


LANES = [
    # y  pattern            direction delay  colour          river
    (1, "##...##....##...", -1, 0.60, LOG, True),
    (2, "###....###.....",   1, 0.50, LOG, True),
    (4, "#...#....#...",    -1, 0.30, (255, 0, 255), False),
    (5, "##.....##......",   1, 0.38, (255, 200, 0), False),
    (6, "#....#.....#....", -1, 0.45, (255, 60, 0), False),
]

TICK = 0.02                # Seconds per game step
LIVES = 3
LEVEL_SPEEDUP = 0.87       # Lanes move this much faster each level
AI_DELAY = 0.3             # Seconds the computer waits between hops
AI_MISTAKE = 0.03          # Chance the computer hops without looking
FROG_TIME = 30             # Seconds each frog has to reach a home


class Game:
    def __init__(self, human):
        self.human = human
        self.lives = LIVES
        self.score = 0
        self.level = 1
        self.lanes = [Lane(*spec) for spec in LANES]
        self.homes = set()            # Filled homes
        self.new_frog()

    def new_frog(self):
        self.frog = list(START)
        self.best_row = 7
        self.ai_timer = 0.0
        self.time_left = FROG_TIME

    def lane(self, y):
        return next((lane for lane in self.lanes if lane.y == y), None)

    def next_level(self):
        self.level += 1
        self.homes = set()
        for lane in self.lanes:
            lane.delay = lane.base_delay * LEVEL_SPEEDUP ** (self.level - 1)
        self.new_frog()

    # ---------- rules ----------
    def deadly(self, x, y):
        """Is the frog dead at (x, y) right now?"""
        if not 0 <= x <= 7:
            return True
        if y == 0:
            return x not in HOMES or x in self.homes
        lane = self.lane(y)
        if lane is None:
            return False
        return lane.occupied(x) != lane.river      # Car on the road, or no log in the river

    def hop(self, dx, dy):
        x, y = self.frog[0] + dx, self.frog[1] + dy
        if 0 <= x <= 7 and 0 <= y <= 7:
            self.frog = [x, y]
            if y < self.best_row:
                self.best_row = y
                self.score += 10

    def step(self, hop):
        """Advance one tick. Returns "dead", "home", "cleared" or None."""
        if hop:
            self.hop(*hop)
            if self.frog[1] == 0 and not self.deadly(*self.frog):
                self.homes.add(self.frog[0])
                self.score += 50
                if len(self.homes) == len(HOMES):
                    self.score += 200
                    return "cleared"
                return "home"
        for lane in self.lanes:
            lane.timer += TICK
            if lane.timer >= lane.delay - 1e-9:
                lane.timer = 0.0
                lane.offset += lane.direction
                if lane.river and self.frog[1] == lane.y:
                    self.frog[0] += lane.direction     # The frog drifts along on its log
        self.time_left -= TICK
        if self.deadly(*self.frog) or self.time_left <= 0:
            return "dead"
        return None

    # ---------- the computer player ----------
    def safe_for(self, x, y, seconds):
        """Will a frog at (x, y) survive the next `seconds`?"""
        if not 0 <= x <= 7:
            return False
        if y == 0:
            return x in HOMES and x not in self.homes
        lane = self.lane(y)
        if lane is None:
            return True
        for k in range(lane.steps_within(seconds) + 1):
            offset = lane.offset + lane.direction * k
            fx = x + lane.direction * k if lane.river else x
            if not 0 <= fx <= 7 or lane.occupied(fx, offset) != lane.river:
                return False
        return True

    def home_ahead(self, x):
        """Will the log in the top river row carry a frog at x past an empty home?"""
        direction = self.lane(1).direction
        return any((h - x) * direction >= 0 for h in HOMES if h not in self.homes)

    def ai_controls(self):
        self.ai_timer += TICK
        if self.ai_timer < AI_DELAY:
            return None
        self.ai_timer = 0.0
        x, y = self.frog
        look = AI_DELAY + 0.15
        if y >= 4 and random.random() < AI_MISTAKE:     # A careless hop on the road
            return random.choice([(0, -1), (-1, 0), (1, 0)])

        if y == 1:
            # On the top log: hop home when we pass an empty one, otherwise ride along
            options = [(0, -1)] if x in HOMES and x not in self.homes else []
            options += [(0, 0) if self.home_ahead(x) else None,
                        (1, 0) if self.home_ahead(x + 1) else None,
                        (-1, 0) if self.home_ahead(x - 1) else None,
                        (0, 1)]
        elif y == 2:
            # Only hop onto the top log if it will carry us past an empty home
            options = [(0, -1) if self.home_ahead(x) else None, (0, 0)]
            options += [(-self.lane(1).direction, 0), (self.lane(1).direction, 0), (0, 1)]
        else:
            options = [(0, -1), (0, 0)]
            # Drift towards the middle columns, then try sideways, then back down
            side = 1 if x < 3 else -1 if x > 4 else random.choice([-1, 1])
            options += [(side, 0), (-side, 0), (0, 1)]

        for option in options:
            if option and self.safe_for(x + option[0], y + option[1], look):
                return None if option == (0, 0) else option
        return None   # Nothing is safe: stay and hope

    def draw(self, sense, frog=True):
        frame = [[ROAD] * 8 for _ in range(8)]
        frame[0] = [HOME_FULL if x in self.homes else HOME_EMPTY if x in HOMES else HEDGE
                    for x in range(8)]
        frame[3] = [STRIP] * 8
        frame[7] = [STRIP] * 8
        for lane in self.lanes:
            for x in range(8):
                if lane.occupied(x):
                    frame[lane.y][x] = lane.color
                elif lane.river:
                    frame[lane.y][x] = WATER
        if frog and 0 <= self.frog[0] <= 7:
            frame[self.frog[1]][self.frog[0]] = FROG
        sense.set_pixels(flatten(frame))


KEYS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


def play_game(sense, stick, human):
    """Play one game. Returns (result, score): result is "over", or "start" when
    someone touches the joystick during the demo."""
    game = Game(human)
    while True:
        pressed = stick.read()
        if not game.human and pressed:
            return "start", 0
        if game.human and stick.idle():      # Player walked away: computer takes over
            game.human = False

        if game.human:
            hop = next((KEYS[k] for k in pressed if k in KEYS), None)
        else:
            hop = game.ai_controls()
        result = game.step(hop)

        if result == "dead":
            game.lives -= 1
            x = max(0, min(7, game.frog[0]))
            for _ in range(3):
                game.draw(sense, frog=False)
                sense.set_pixel(x, game.frog[1], SPLAT)
                time.sleep(0.15)
                game.draw(sense, frog=False)
                time.sleep(0.15)
            if game.lives == 0:
                return "over", game.score
            sense.show_letter(str(game.lives), text_colour=FROG)
            time.sleep(0.6)
            game.new_frog()
        elif result == "home":
            game.draw(sense, frog=False)
            time.sleep(0.4)
            game.new_frog()
        elif result == "cleared":
            for _ in range(3):
                sense.clear(HOME_FULL)
                time.sleep(0.15)
                sense.clear()
                time.sleep(0.15)
            game.next_level()

        game.draw(sense)
        time.sleep(TICK)


def main():
    sense = init(hat)
    stick = Joystick(sense)
    human = False
    while True:
        result, score = play_game(sense, stick, human)
        if result == "start":
            show_play_sign(sense)
            stick.flush()
            human = True
            continue
        sense.show_message(f"Score {score}", text_colour=FROG, scroll_speed=0.06)
        human = False   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
