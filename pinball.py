#!/usr/bin/env python3
"""Sense HAT – Pinball: a self-playing pinball machine.

The playfield is 8 wide x 16 tall. The 8x8 LED matrix shows a window that
follows the ball (scrolling camera), just like a real, tall pinball table.

Every ball is shot up a launch lane on the right, like a real plunger, and
enters the playfield from the top.

On the playfield:
  - 3 pop bumpers close together (red, flash yellow)          100 points
  - a dark grey ramp: only reachable with a shot straight off a flipper;
    the ball rides over the top and drops onto the left flipper  1000 points
  - every ramp shot lights a third of the ramp orange; at 3 -> MULTIBALL:
    2 extra balls are launched and every ramp shot is a jackpot  3000 points
  - 2 slingshots above the flippers (orange) that kick the ball away
  - 2 top lanes (green)                                        50 / 500 bonus
  - long flippers with a 2-pixel gap in the middle; the AI sometimes catches
    the ball, lets it settle and then aims at the ramp, bumpers or lanes

The computer plays a demo until you touch the joystick: then you play a new
game of 3 balls yourself. After game over, or when you leave the joystick
alone for a while, the computer takes over again.

Controls:
  joystick left/right   -> left/right flipper (hold to keep it up)
  joystick up/down      -> "tilt": nudge the balls
  press joystick        -> toggle between follow camera and overview
  Ctrl+C                -> quit

Run:  python3 pinball.py
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


# ---------- settings ----------
W, H = 8, 16            # playfield
FPS = 30
DT = 1.0 / FPS
SUB = 4                 # physics substeps per frame (prevents tunnelling through walls)
GRAVITY = 9.0           # cells / s^2
R = 0.5                 # ball radius
MAX_SPEED = 22.0
BALLS_PER_GAME = 3
AI_SKILL = 0.97         # chance the AI hits an incoming ball correctly
AI_CATCH = 0.85        # chance the AI catches a ball and then takes an aimed shot
AI_CATCH_RAMP = 0.7     # same, for a ball coming off the ramp
RAMP_SPEED = 11.0       # cells / s along the ramp
RAMP_MIN_SPEED = 5.0    # how fast a flipper shot must travel upwards to make the ramp
RAMP_SHOTS_FOR_MULTIBALL = 3
MULTIBALL_EXTRA = 2     # extra balls during multiball
JACKPOT = 3000
BALL_SAVE = 8.0         # for this long after multiball starts, a drained ball is returned

# ---------- colours ----------
BLACK = (0, 0, 0)
BALL_COL = (255, 255, 255)
BUMPER_COL = (200, 0, 0)
BUMPER_HIT = (255, 220, 0)
FLIPPER_COL = (0, 120, 255)
FLIPPER_UP = (0, 220, 255)
WALL_COL = (70, 0, 90)
SLING_COL = (200, 70, 0)
SLING_HIT = (255, 160, 40)
RAMP_COL = (45, 45, 45)
RAMP_HIT = (130, 130, 130)
RAMP_LIT = (255, 120, 0)
TOP_LANE_COL = (0, 90, 0)
TOP_LANE_HIT = (0, 255, 80)
LAUNCH_COL = (60, 50, 0)

# ---------- playfield ----------
# pop bumpers in a tight triangle: (x, y) centre (pixel i spans i to i+1)
BUMPERS = [(3.0, 4.0), (5.0, 4.0), (4.0, 5.7)]
BUMPER_RADIUS = 0.5
BUMPER_BOOST = 4.5

# fixed angled walls (rounded top corners)
WALLS = [
    ((0.5, 2.5), (2.5, 0.5)),
    ((5.5, 0.5), (7.5, 2.5)),
]

# slingshots above the flippers (leaving an inlane along the outer wall)
SLINGS = [
    ((1.3, 8.2), (2.3, 9.8)),
    ((6.7, 8.2), (5.7, 9.8)),
]
SLING_KICK = 7.0

# ramp: entrance on the right (the ball must pass through it going up), runs over
# the top and drops the ball on the left, above the left flipper
RAMP_PATH = [(6.4, 7.2), (7.0, 5.0), (6.6, 1.4), (4.0, 0.6),
             (1.4, 1.4), (1.0, 5.0), (0.8, 9.0)]
RAMP_ENTRY_X = (5.0, 7.5)
RAMP_ENTRY_Y = 7.2

# launch lane: like a real plunger, every new ball first shoots up the right
# side, curves over the top and enters the playfield from above. The lane is
# only drawn while a ball is in it.
LAUNCH_PATH = [(7.5, 15.5), (7.5, 3.0), (7.3, 1.3), (6.5, 0.6), (5.2, 0.7)]
LAUNCH_SPEED = 15.0     # cells / s
LAUNCH_WAIT = 0.6       # seconds the ball waits at the bottom (pulling the plunger)

# top lanes: bonus points when the ball passes through
TOP_LANES = [3, 4]


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def lerp(a, b, t):
    return a + (b - a) * t


def closest_on_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else clamp(((px - ax) * dx + (py - ay) * dy) / l2, 0.0, 1.0)
    return ax + t * dx, ay + t * dy, t


class Ramp:
    def __init__(self, path):
        self.path = path
        self.cum = [0.0]
        for (x1, y1), (x2, y2) in zip(path, path[1:]):
            self.cum.append(self.cum[-1] + math.hypot(x2 - x1, y2 - y1))
        self.length = self.cum[-1]

    def point(self, s):
        s = clamp(s, 0.0, self.length)
        for i in range(len(self.path) - 1):
            if s <= self.cum[i + 1]:
                seg = self.cum[i + 1] - self.cum[i]
                t = 0.0 if seg == 0 else (s - self.cum[i]) / seg
                (x1, y1), (x2, y2) = self.path[i], self.path[i + 1]
                return lerp(x1, x2, t), lerp(y1, y2, t)
        return self.path[-1]

    def pixels(self):
        """{(x, y): part} with part 0, 1 or 2 (third of the ramp, counted from the entrance)."""
        pix = {}
        s = 0.0
        while s <= self.length:
            x, y = self.point(s)
            key = (int(clamp(x, 0, W - 1)), int(clamp(y, 0, H - 1)))
            pix.setdefault(key, min(2, int(3 * s / self.length)))
            s += 0.25
        return pix


class Ball:
    def __init__(self):
        # starts in the launch lane at the bottom right; it rides the lane like a ramp
        self.x, self.y = LAUNCH_PATH[0]
        self.vx = self.vy = 0.0
        self.slow_time = 0.0
        self.on_ramp = True
        self.ride = "launch"        # "launch" = in the launch lane, "ramp" = on the ramp
        self.ramp_s = -LAUNCH_SPEED * LAUNCH_WAIT
        self.flipper_shot = False   # just shot by a flipper (required to make the ramp)
        # per-ball AI memory
        self.armed = True
        self.will_hit = True
        self.catch = False

    def speed(self):
        return math.hypot(self.vx, self.vy)


class Ghost:
    """Lightweight ball used to work out aimed shots."""
    __slots__ = ("x", "y", "vx", "vy")

    def __init__(self, x, y, vx, vy):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy


def collide_segment(ball, a, b, restitution=0.55):
    """Ball <-> line segment collision. Returns (t, nx, ny) or None."""
    cx, cy, t = closest_on_segment(ball.x, ball.y, a[0], a[1], b[0], b[1])
    nx, ny = ball.x - cx, ball.y - cy
    d = math.hypot(nx, ny)
    if d >= R:
        return None
    if d < 1e-6:
        nx, ny, d = 0.0, -1.0, 1.0
    nx /= d
    ny /= d
    ball.x = cx + nx * R
    ball.y = cy + ny * R
    vn = ball.vx * nx + ball.vy * ny
    if vn < 0:
        ball.vx -= (1 + restitution) * vn * nx
        ball.vy -= (1 + restitution) * vn * ny
    return t, nx, ny


class Flipper:
    def __init__(self, pivot, rest_tip, up_tip, side, pixel_range):
        self.pivot = pivot
        self.rest_tip = rest_tip
        self.up_tip = up_tip
        self.side = side              # -1 = left, +1 = right
        self.pixel_range = pixel_range  # columns it is drawn in
        self.pos = 0.0                # 0 = at rest, 1 = up
        self.prev_pos = 0.0
        self.timer = 0.0
        self.cooldown = 0.0
        self.mode = "idle"            # idle / hold (cradling the ball) / aim (aimed shot)
        self.mode_time = 0.0
        self.ball = None              # the ball being held / aimed
        self.hold_for = 1.0
        self.aimed = False
        self.kicked = False           # already kicked during this stroke

    def tip(self):
        return (lerp(self.rest_tip[0], self.up_tip[0], self.pos),
                lerp(self.rest_tip[1], self.up_tip[1], self.pos))

    def flip(self, force=False):
        if self.cooldown <= 0 or force:
            self.timer = 0.22
            self.cooldown = 0.45

    def rising(self):
        return self.pos > self.prev_pos

    def release(self):
        self.mode = "idle"
        self.ball = None

    def update(self, dt):
        self.prev_pos = self.pos
        if self.mode == "hold":
            self.timer = max(self.timer, 0.1)   # keep the flipper up
        self.timer -= dt
        self.cooldown -= dt
        if self.timer > 0:
            self.pos = min(1.0, self.pos + 12.0 * dt)
        else:
            self.pos = max(0.0, self.pos - 6.0 * dt)
        if not self.rising():
            self.kicked = False


class Pinball:
    def __init__(self, sense):
        self.sense = sense
        try:
            self.sense.low_light = False
        except (AttributeError, OSError):
            pass    # Not every Sense HAT version (or the emulator) supports this
        # flipper tips at x=3.5 and x=4.5: the gap between them is exactly one ball wide.
        # They're drawn in columns 0-2 and 5-7, plus a half-lit "half pixel" for the tip
        # in columns 3 and 4.
        self.left = Flipper((0.0, 11.4), (3.5, 14.55), (3.7, 10.75), -1, (0, 2))
        self.right = Flipper((8.0, 11.4), (4.5, 14.55), (4.3, 10.75), +1, (5, 7))
        self.flippers = [self.left, self.right]
        self.ramp = Ramp(RAMP_PATH)
        self.ramp_pixels = self.ramp.pixels()
        self.launch_lane = Ramp(LAUNCH_PATH)
        self.launch_pixels = self.launch_lane.pixels()
        self.balls = [Ball()]
        self.launch_queue = []        # delays before extra multiball balls launch
        self.bumper_flash = [0.0] * len(BUMPERS)
        self.sling_flash = [0.0] * len(SLINGS)
        self.ramp_flash = 0.0
        self.ramp_count = 0
        self.multiball = False
        self.multiball_flash = 0.0
        self.ball_save = 0.0
        self.lane_lit = [False] * len(TOP_LANES)
        self.lane_flash = 0.0
        self.score = 0
        self.balls_left = BALLS_PER_GAME
        self.cam = 8.0
        self.overview = False
        self.stick = Joystick(sense)
        self.human = False            # True: you play the flippers

    # ---------- AI ----------
    def ai(self):
        single = len(self.balls) == 1 and not self.launch_queue
        for b in self.balls:
            if b.on_ramp:
                continue
            # sometimes raise the flipper early to catch the ball (single ball only)
            if single and b.catch and b.vy > 1.0 and 6.0 < b.y < 10.6:
                f = self.predict_landing(b)
                if f is not None and f.mode == "idle":
                    b.catch = False
                    f.mode, f.mode_time, f.ball = "hold", 0.0, b
                    f.hold_for = random.uniform(0.6, 1.4)
                    f.flip(force=True)
            # ball high on the playfield: get ready for its next approach
            if b.y < 9.5:
                if not b.armed:
                    b.armed = True
                    b.catch = random.random() < AI_CATCH
                continue
            # ball coming down: decide once whether the AI reacts correctly this time
            if b.armed:
                b.armed = False
                b.will_hit = random.random() < AI_SKILL

        for f in self.flippers:
            f.mode_time += DT
            if f.mode != "idle":
                self.ai_special(f)
                continue
            for b in self.balls:
                if b.on_ramp or b.y < 9.5 or not b.will_hit:
                    continue
                dist, t = self.flipper_distance(f, b)
                # reaction distance grows with speed (the flipper needs a moment to swing up)
                reach = 0.9 + b.speed() * random.uniform(0.05, 0.09)
                if dist < reach and b.vy > -2.0 and t > 0.2:
                    f.flip()
                    break

    def ai_special(self, f):
        b = f.ball
        if b is None or b not in self.balls or b.on_ramp:
            f.release()
            return
        dist, t = self.flipper_distance(f, b)
        if f.mode == "hold":
            # ball resting on the raised flipper? then release and aim
            if f.mode_time > f.hold_for and dist < 0.8 and b.speed() < 2.0:
                f.mode, f.mode_time = "aim", 0.0
            elif f.mode_time > 3.0 or dist > 2.5:
                f.release()
        elif f.mode == "aim":
            # flipper drops, ball rolls towards the tip: fire at the right moment
            if dist < 0.9 and t > random.uniform(0.75, 0.9) and f.pos < 0.1:
                f.flip(force=True)
                f.aimed = True
                f.release()
            elif f.mode_time > 2.0 or dist > 2.5:
                f.release()

    @staticmethod
    def flipper_distance(f, b):
        tip = f.tip()
        cx, cy, t = closest_on_segment(b.x, b.y, f.pivot[0], f.pivot[1], tip[0], tip[1])
        return math.hypot(b.x - cx, b.y - cy), t

    def predict_landing(self, b):
        """Will the ball land on a flipper past a slingshot? Which one?"""
        dy = 10.0 - b.y
        disc = b.vy * b.vy + 2 * GRAVITY * dy
        if dy <= 0 or disc < 0:
            return None
        t = (-b.vy + math.sqrt(disc)) / GRAVITY
        x = b.x + b.vx * t
        # outside (inlane) or inside of the slingshot
        if x < 1.1 or 2.4 < x < 3.3:
            return self.left
        if x > W - 1.1 or W - 3.3 < x < W - 2.4:
            return self.right
        return None

    # ---------- physics ----------
    def ride_ramp(self, b, dt):
        if b.ride == "launch":
            b.ramp_s += LAUNCH_SPEED * dt
            b.x, b.y = self.launch_lane.point(b.ramp_s)
            if b.ramp_s >= self.launch_lane.length:
                # out of the launch lane: into the top of the playfield, heading left
                b.on_ramp = False
                b.vx = -random.uniform(3.0, 6.5)
                b.vy = random.uniform(0.0, 2.0)
                b.armed = True
            return
        b.ramp_s += RAMP_SPEED * dt
        b.x, b.y = self.ramp.point(b.ramp_s)
        if b.ramp_s >= self.ramp.length:
            # off the ramp: gently onto the left flipper
            b.on_ramp = False
            b.vx = random.uniform(-0.3, 0.2)
            b.vy = 2.0
            b.armed = True
            b.catch = random.random() < AI_CATCH_RAMP
            if self.ramp_count >= RAMP_SHOTS_FOR_MULTIBALL and not self.multiball:
                self.start_multiball()

    def ramp_hit(self, b):
        b.on_ramp = True
        b.ride = "ramp"
        b.ramp_s = 0.0
        b.flipper_shot = False
        self.ramp_flash = 1.2
        if self.multiball:
            self.score += JACKPOT
        else:
            self.score += 1000
            self.ramp_count = min(RAMP_SHOTS_FOR_MULTIBALL, self.ramp_count + 1)

    def start_multiball(self):
        self.multiball = True
        self.multiball_flash = 2.0
        self.launch_queue = [0.4 + 0.6 * i for i in range(MULTIBALL_EXTRA)]
        self.ball_save = BALL_SAVE

    def step(self, b, dt):
        if b.on_ramp:
            self.ride_ramp(b, dt)
            return

        prev_y = b.y
        b.vy += GRAVITY * dt
        sp = b.speed()
        if sp > MAX_SPEED:
            b.vx *= MAX_SPEED / sp
            b.vy *= MAX_SPEED / sp
        b.x += b.vx * dt
        b.y += b.vy * dt
        if b.vy > 0:
            b.flipper_shot = False

        # ramp entrance: only a hard upward flipper shot makes the ramp;
        # balls coming from above or out of the bumpers simply pass underneath
        if (b.flipper_shot and b.vy < -RAMP_MIN_SPEED
                and prev_y >= RAMP_ENTRY_Y > b.y
                and RAMP_ENTRY_X[0] <= b.x <= RAMP_ENTRY_X[1]):
            self.ramp_hit(b)
            return

        # outer walls
        if b.x < 0.5:
            b.x, b.vx = 0.5, abs(b.vx) * 0.8
        elif b.x > W - 0.5:
            b.x, b.vx = W - 0.5, -abs(b.vx) * 0.8
        if b.y < 0.5:
            b.y, b.vy = 0.5, abs(b.vy) * 0.8

        # top-lanes
        if b.y < 1.2:
            col = int(b.x)
            if col in TOP_LANES:
                i = TOP_LANES.index(col)
                if not self.lane_lit[i]:
                    self.lane_lit[i] = True
                    self.score += 50
                    if all(self.lane_lit):
                        self.score += 500
                        self.lane_flash = 1.0
                        self.lane_lit = [False] * len(TOP_LANES)

        for a, c in WALLS:
            collide_segment(b, a, c, restitution=0.6)

        # slingshots: active kick when the ball hits them hard enough
        for i, (a, c) in enumerate(SLINGS):
            v_before = b.speed()
            hit = collide_segment(b, a, c, restitution=0.5)
            if hit is not None:
                b.flipper_shot = False
                if v_before > 2.0 and self.sling_flash[i] <= 0:
                    _, nx, ny = hit
                    b.vx += nx * SLING_KICK
                    b.vy += ny * SLING_KICK - 2.0
                    self.sling_flash[i] = 0.2
                    self.score += 10

        # pop bumpers
        for i, (bx, by) in enumerate(BUMPERS):
            dx, dy = b.x - bx, b.y - by
            d = math.hypot(dx, dy)
            min_d = R + BUMPER_RADIUS
            if d < min_d:
                if d < 1e-6:
                    dx, dy, d = 0.0, -1.0, 1.0
                nx, ny = dx / d, dy / d
                b.x, b.y = bx + nx * min_d, by + ny * min_d
                vn = b.vx * nx + b.vy * ny
                if vn < 0:
                    b.vx -= 2 * vn * nx
                    b.vy -= 2 * vn * ny
                b.vx += nx * BUMPER_BOOST
                b.vy += ny * BUMPER_BOOST
                b.flipper_shot = False
                self.bumper_flash[i] = 0.25
                self.score += 100

        # flippers
        for f in self.flippers:
            holding = f.mode == "hold" and f.ball is b
            hit = collide_segment(b, f.pivot, f.tip(), restitution=0.05 if holding else 0.35)
            if hit is None:
                continue
            if holding:
                # catching: the ball loses speed and rolls into the corner by the pivot
                b.vx *= 0.9
                b.vy *= 0.9
            elif f.rising() and not f.kicked:
                f.kicked = True
                self.snap_to_flipper(b, f, hit[0])
                if f.aimed:
                    self.aimed_shot(b, f)
                else:
                    t = hit[0]
                    power = 0.55 + 0.45 * t          # closer to the tip = harder
                    b.vy = -random.uniform(15.0, 19.5) * power
                    b.vx = -f.side * random.uniform(1.0, 7.0) + random.uniform(-1.5, 1.5)
                b.flipper_shot = True
                self.score += 10

    @staticmethod
    def collide_balls(a, b):
        if a.on_ramp or b.on_ramp:
            return
        dx, dy = b.x - a.x, b.y - a.y
        d = math.hypot(dx, dy)
        if d >= 2 * R or d < 1e-6:
            return
        nx, ny = dx / d, dy / d
        push = (2 * R - d) / 2
        a.x -= nx * push
        a.y -= ny * push
        b.x += nx * push
        b.y += ny * push
        rel = (a.vx - b.vx) * nx + (a.vy - b.vy) * ny
        if rel > 0:
            a.vx -= rel * nx * 0.95
            a.vy -= rel * ny * 0.95
            b.vx += rel * nx * 0.95
            b.vy += rel * ny * 0.95

    def snap_to_flipper(self, b, f, t):
        """The flipper snaps up in one go; place the ball on top of the raised flipper
        so the fast-moving flipper can't overtake it and push it underneath."""
        f.pos = 1.0
        f.timer = max(f.timer, 0.15)
        (ax, ay), (cx, cy) = f.pivot, f.tip()
        px, py = lerp(ax, cx, t), lerp(ay, cy, t)
        dx, dy = cx - ax, cy - ay
        length = math.hypot(dx, dy)
        nx, ny = dy / length, -dx / length
        if ny > 0:
            nx, ny = -nx, -ny          # normal pointing up
        b.x, b.y = px + nx * (R + 0.02), py + ny * (R + 0.02)
        b.x = clamp(b.x, 0.5, W - 0.5)

    def aimed_shot(self, b, f):
        """Aimed shot: try out a range of shots in simulation and pick one that
        hits the target (ramp, bumpers or top lanes)."""
        f.aimed = False
        if f.side < 0:
            goals = random.choice([["ramp", "bumper"], ["ramp", "lane"], ["bumper", "ramp"]])
        else:
            goals = random.choice([["bumper", "lane"], ["lane", "bumper"], ["ramp", "bumper"]])
        outcomes = {}
        for vy in (-16.5, -18.5):
            for i in range(-22, 23):
                vx = i * 0.5
                outcomes.setdefault(self.trace(b.x, b.y, vx, vy), []).append((vx, vy))
        hits = []
        for goal in goals:
            hits = outcomes.get(goal, [])
            if hits:
                break
        if hits:
            # middle of the hitting shots = the safest choice, plus a little human error
            hits.sort()
            vx, vy = hits[len(hits) // 2]
            b.vx = vx + random.uniform(-0.3, 0.3)
            b.vy = vy
        else:
            b.vy = -random.uniform(15.0, 19.5)
            b.vx = -f.side * random.uniform(1.0, 7.0)

    def trace(self, x, y, vx, vy, max_time=1.2):
        """Ghost ball: what does a shot hit first? ('ramp', 'bumper', 'lane', 'sling' or None)."""
        dt = DT / SUB
        ghost = Ghost(x, y, vx, vy)
        t = 0.0
        while t < max_time:
            t += dt
            prev_y = ghost.y
            ghost.vy += GRAVITY * dt
            ghost.x += ghost.vx * dt
            ghost.y += ghost.vy * dt
            if (ghost.vy < -RAMP_MIN_SPEED and prev_y >= RAMP_ENTRY_Y > ghost.y
                    and RAMP_ENTRY_X[0] <= ghost.x <= RAMP_ENTRY_X[1]):
                return "ramp"
            if ghost.x < 0.5:
                ghost.x, ghost.vx = 0.5, abs(ghost.vx) * 0.8
            elif ghost.x > W - 0.5:
                ghost.x, ghost.vx = W - 0.5, -abs(ghost.vx) * 0.8
            if ghost.y < 1.2 and int(ghost.x) in TOP_LANES:
                return "lane"
            if ghost.y < 0.5:
                ghost.y, ghost.vy = 0.5, abs(ghost.vy) * 0.8
            if ghost.y < 3.0:
                for a, c in WALLS:
                    collide_segment(ghost, a, c, restitution=0.6)
            for a, c in SLINGS:
                if collide_segment(ghost, a, c) is not None:
                    return "sling"
            for bx, by in BUMPERS:
                if math.hypot(ghost.x - bx, ghost.y - by) < R + BUMPER_RADIUS:
                    return "bumper"
            if ghost.vy > 0 and ghost.y > 10.0:
                return None   # falls back without hitting anything
        return None

    def update(self):
        """One frame. Returns True when the last ball has drained."""
        # multiball: launch extra balls
        if self.launch_queue:
            self.launch_queue = [t - DT for t in self.launch_queue]
            if self.launch_queue[0] <= 0:
                self.launch_queue.pop(0)
                self.balls.append(Ball())

        if not self.human:
            self.ai()
        for _ in range(SUB):
            # move the flippers in substeps too, otherwise the ball can pass through them
            for f in self.flippers:
                f.update(DT / SUB)
            for b in self.balls:
                self.step(b, DT / SUB)
            for i in range(len(self.balls)):
                for j in range(i + 1, len(self.balls)):
                    self.collide_balls(self.balls[i], self.balls[j])

        for b in self.balls:
            # stuck ball? automatic nudge
            held = any(f.ball is b for f in self.flippers)
            if not b.on_ramp and not held and b.speed() < 1.0:
                b.slow_time += DT
                if b.slow_time > 2.5:
                    self.nudge_ball(b)
            else:
                b.slow_time = 0.0

        # remove drained balls
        self.ball_save = max(0.0, self.ball_save - DT)
        for b in [b for b in self.balls if b.y > H + 0.5]:
            self.balls.remove(b)
            for f in self.flippers:
                if f.ball is b:
                    f.release()
            if self.ball_save > 0:
                self.launch_queue.append(0.3)   # ball save: the ball comes straight back
        if self.multiball and len(self.balls) + len(self.launch_queue) <= 1:
            self.multiball = False
            self.ramp_count = 0

        self.bumper_flash = [max(0.0, x - DT) for x in self.bumper_flash]
        self.sling_flash = [max(0.0, x - DT) for x in self.sling_flash]
        self.ramp_flash = max(0.0, self.ramp_flash - DT)
        self.lane_flash = max(0.0, self.lane_flash - DT)
        self.multiball_flash = max(0.0, self.multiball_flash - DT)

        # camera follows the lowest ball (the most dangerous one)
        if self.balls:
            low = max(self.balls, key=lambda b: (not b.on_ramp, b.y))
            target = clamp(low.y - 4.0, 0.0, H - 8.0)
            self.cam += (target - self.cam) * 0.25

        return not self.balls and not self.launch_queue

    def nudge_ball(self, b, direction=None):
        if b.on_ramp:
            return
        b.vx += (direction if direction else random.choice([-1, 1])) * random.uniform(3, 6)
        b.vy -= random.uniform(4, 8)
        b.slow_time = 0.0

    def nudge(self, direction):
        for b in self.balls:
            self.nudge_ball(b, direction)

    # ---------- drawing ----------
    def draw_segment(self, field, a, c, color, xr=(0, W - 1), mirrored=False):
        """Draw a line segment. mirrored=True: draw it as the mirror image of the left side,
        so left and right parts are symmetrical pixel for pixel."""
        if mirrored:
            a, c = (W - a[0], a[1]), (W - c[0], c[1])
            xr = (W - 1 - xr[1], W - 1 - xr[0])
        for i in range(17):
            t = i / 16
            x = int(clamp(lerp(a[0], c[0], t), xr[0], xr[1] + 0.999))
            y = int(clamp(lerp(a[1], c[1], t), 0, H - 1))
            if mirrored:
                x = W - 1 - x
            field[y][x] = color

    def render(self):
        field = [[BLACK] * W for _ in range(H)]

        for i, (a, c) in enumerate(WALLS):
            self.draw_segment(field, a, c, WALL_COL, mirrored=i == 1)

        # the launch lane is only there while a ball is in it
        if any(b.on_ramp and b.ride == "launch" for b in self.balls):
            for (x, y) in self.launch_pixels:
                field[y][x] = LAUNCH_COL

        # the ramp sits above the playfield; every part already "earned" lights orange
        blink = int(self.ramp_flash * 10) % 2 if self.ramp_flash > 0 else 0
        mb_blink = int(self.multiball_flash * 8) % 2 if self.multiball_flash > 0 else 0
        for (x, y), part in self.ramp_pixels.items():
            if self.multiball:
                col = RAMP_LIT if (mb_blink or self.multiball_flash == 0) else RAMP_HIT
            elif part < self.ramp_count:
                col = RAMP_LIT
            else:
                col = RAMP_HIT if blink else RAMP_COL
            field[y][x] = col

        for i, (a, c) in enumerate(SLINGS):
            self.draw_segment(field, a, c, SLING_HIT if self.sling_flash[i] > 0 else SLING_COL,
                              mirrored=i == 1)

        for i, col in enumerate(TOP_LANES):
            on = self.lane_lit[i] or (self.lane_flash > 0 and int(self.lane_flash * 10) % 2)
            field[0][col] = TOP_LANE_HIT if on else TOP_LANE_COL

        for i, (bx, by) in enumerate(BUMPERS):
            field[int(by)][int(bx)] = BUMPER_HIT if self.bumper_flash[i] > 0 else BUMPER_COL

        for f in self.flippers:
            color = FLIPPER_UP if f.pos > 0.3 else FLIPPER_COL
            self.draw_segment(field, f.pivot, f.tip(), color, f.pixel_range,
                              mirrored=f.side > 0)
            # the tip: half a pixel, drawn at half brightness next to the flipper
            tip_x = 3 if f.side < 0 else 4
            tip_y = int(clamp(f.tip()[1], 0, H - 1))
            if field[tip_y][tip_x] == BLACK:
                field[tip_y][tip_x] = tuple(c // 2 for c in color)

        # ball save active: the gap between the flippers blinks green
        if self.ball_save > 0 and int(self.ball_save * 4) % 2 == 0:
            field[H - 1][3] = field[H - 1][4] = TOP_LANE_HIT

        for b in self.balls:
            bx, by = int(b.x), int(b.y)
            if 0 <= bx < W and 0 <= by < H:
                field[by][bx] = BALL_COL

        if self.overview:
            rows = []
            for r in range(8):
                rows.append([max(field[2 * r][x], field[2 * r + 1][x], key=sum) for x in range(W)])
        else:
            top = int(round(self.cam))
            rows = field[top:top + 8]

        self.sense.set_pixels([p for row in rows for p in row])

    # ---------- joystick ----------
    def handle_input(self):
        pressed = self.stick.read()
        if not self.human:
            if pressed:
                self.start_player_game()
            return
        if self.stick.idle():                 # Player walked away: computer takes over
            self.human = False
            return
        for key in pressed:
            if key == "left":
                self.left.flip(force=True)
            elif key == "right":
                self.right.flip(force=True)
            elif key in ("up", "down"):
                self.nudge(random.choice([-1, 1]))
            elif key == "middle":
                self.overview = not self.overview
        # Keep a flipper up for as long as its direction is held
        for key, f in (("left", self.left), ("right", self.right)):
            if key in self.stick.held:
                f.timer = max(f.timer, 0.08)

    def new_game(self):
        self.score = 0
        self.balls_left = BALLS_PER_GAME
        self.lane_lit = [False] * len(TOP_LANES)
        self.ramp_count = 0
        self.multiball = False
        self.ball_save = 0.0
        self.launch_queue = []
        for f in self.flippers:
            f.release()
        self.balls = [Ball()]
        self.cam = 8.0

    def start_player_game(self):
        show_play_sign(self.sense)
        self.stick.flush()
        self.human = True
        self.overview = False
        self.new_game()

    # ---------- game flow ----------
    def ball_lost(self):
        self.sense.clear(255, 0, 0)
        time.sleep(0.15)
        self.sense.clear()
        self.balls_left -= 1
        if self.balls_left <= 0:
            self.sense.show_message(f"Score {self.score}", scroll_speed=0.06,
                                    text_colour=[255, 200, 0])
            self.new_game()
            self.human = False        # Back to the demo; touch the joystick to play again
            self.stick.flush()
            return
        self.sense.show_letter(str(self.balls_left), text_colour=[0, 150, 255])
        time.sleep(0.8)
        for f in self.flippers:
            f.release()
        self.multiball = False
        self.balls = [Ball()]
        self.cam = 8.0

    def play(self):
        next_frame = time.monotonic()
        while True:
            self.handle_input()
            if self.update():
                self.ball_lost()
                next_frame = time.monotonic()
                continue
            self.render()
            next_frame += DT
            delay = next_frame - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_frame = time.monotonic()


def main():
    Pinball(init(hat)).play()


if __name__ == "__main__":
    run(main)
