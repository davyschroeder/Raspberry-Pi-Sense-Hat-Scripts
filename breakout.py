#!/usr/bin/env python3
"""Sense HAT – Breakout: smash all the bricks with the ball, helped by power-ups.

Controls: left and right move the paddle, pressing the joystick in launches
the ball (or fires the laser when you have it).

Bounce the ball off your paddle into the bricks at the top. Where the ball
hits the paddle decides its direction: the left edge sends it left, the right
edge right, the middle keeps it going the same way. Every new ball waits on
the paddle until you launch it (or 1.5 seconds have passed).

Bricks:
- coloured bricks break with one hit,
- silver (grey) bricks need two hits and go dim after the first,
- gold bricks can't be broken at all.
A level is cleared when all breakable bricks are gone. There are 8 levels;
after the last one they come back, faster each time. You have 3 lives.

A broken brick sometimes drops a capsule: a blinking pixel that falls down.
Catch it with the paddle for its power-up (only one works at a time, a new
one replaces the old one):
- blue    Enlarge:    a wider paddle,
- green   Catch:      the ball sticks to the paddle; press to launch it,
- red     Laser:      press to shoot two laser beams up from the paddle,
- orange  Slow:       the ball slows down again,
- cyan    Disruption: the ball splits into three,
- magenta Break:      the bottom-right corner opens for a few seconds; move
                      the paddle out through it to skip to the next level,
- pink    Player:     an extra life.

The computer plays a demo until you touch the joystick: then you play.
After game over, or when you leave the joystick alone for a while, the
computer takes over again.
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

# 8x8 picture of a "play" triangle, shown when a human takes over ("#" = lit LED)
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
    """Keeps track of the Sense HAT joystick (or the emulator's: arrow keys + Enter),
    and of a USB or Bluetooth gamepad if one is connected.

    The Sense HAT library reports joystick "events": a direction ("up", "down",
    "left", "right" or "middle" for pushing it in) plus an action ("pressed",
    "held" while you keep it pushed, and "released"). This class turns those
    events into two handy things for the games:

    - read() gives the new presses since last time (good for single steps),
    - self.held is the set of directions that are pushed right now (good for
      moving as long as you hold the stick).

    A gamepad works the same way: the d-pad or the left stick steers, and A, B
    or Start count as pushing the joystick in. Set GAMEPAD_SHOULDERS = True in a
    game to also use the shoulder buttons and triggers as left and right (the
    pinball flippers do). Gamepads need the evdev library
    (sudo apt install python3-evdev); without it only the Sense HAT joystick works.

    It also remembers when the joystick was last touched, so the game can tell
    whether the player has walked away (see idle()).
    """

    PAD_RESCAN = 3.0             # Seconds between looks for a newly plugged-in gamepad

    def __init__(self, sense):
        """Remember the SenseHat object; nothing is held and no input seen yet."""
        self.sense = sense
        self.held = set()        # Directions held down right now
        self.last_input = 0.0    # time.monotonic() of the last joystick event (0 = never)
        self.pads = []           # Connected gamepads (evdev devices)
        self.pad_state = {}      # Per gamepad: which directions it holds right now
        self.pad_axes = {}       # Per gamepad: latest position of the stick, d-pad and triggers
        self.next_scan = 0.0     # When to look for gamepads again

    def find_pads(self):
        """Look for gamepads (once every PAD_RESCAN seconds while none is found)."""
        if self.pads or time.monotonic() < self.next_scan:
            return
        self.next_scan = time.monotonic() + self.PAD_RESCAN
        try:
            import evdev
        except ImportError:
            self.next_scan = float("inf")          # No evdev library: never look again
            return
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                keys = dev.capabilities().get(evdev.ecodes.EV_KEY, [])
                if evdev.ecodes.BTN_SOUTH in keys or evdev.ecodes.BTN_GAMEPAD in keys:
                    self.pads.append(dev)          # Has an A button: it's a gamepad
                else:
                    dev.close()
            except OSError:
                pass                               # Not allowed to read it, or it went away

    def pad_directions(self, dev):
        """Which directions this gamepad holds right now, from its buttons and axes."""
        import evdev
        e, ax = evdev.ecodes, self.pad_axes.setdefault(dev.path, {})
        keys = set(dev.active_keys())
        state = set()

        def stick(code):
            """Stick position from -1 (left/up) to 1 (right/down)."""
            if code not in ax:
                return 0.0
            info = dev.absinfo(code)
            mid = (info.max + info.min) / 2
            return (ax[code] - mid) / max(1, (info.max - info.min) / 2)

        def trigger(code):
            """How far a trigger is pulled, from 0 to 1."""
            if code not in ax:
                return 0.0
            info = dev.absinfo(code)
            return (ax[code] - info.min) / max(1, info.max - info.min)

        x = stick(e.ABS_X) + ax.get(e.ABS_HAT0X, 0)   # Left stick plus d-pad (hat: -1, 0 or 1)
        y = stick(e.ABS_Y) + ax.get(e.ABS_HAT0Y, 0)
        if x < -0.5 or e.BTN_DPAD_LEFT in keys:
            state.add("left")
        if x > 0.5 or e.BTN_DPAD_RIGHT in keys:
            state.add("right")
        if y < -0.5 or e.BTN_DPAD_UP in keys:
            state.add("up")
        if y > 0.5 or e.BTN_DPAD_DOWN in keys:
            state.add("down")
        if keys & {e.BTN_SOUTH, e.BTN_EAST, e.BTN_START}:
            state.add("middle")
        if globals().get("GAMEPAD_SHOULDERS"):         # e.g. the pinball flippers
            if e.BTN_TL in keys or e.BTN_TL2 in keys or trigger(e.ABS_Z) > 0.3:
                state.add("left")
            if e.BTN_TR in keys or e.BTN_TR2 in keys or trigger(e.ABS_RZ) > 0.3:
                state.add("right")
        return state

    def read_pads(self, pressed):
        """Turn gamepad changes into joystick presses and releases."""
        self.find_pads()
        for dev in list(self.pads):
            try:
                import evdev
                event = dev.read_one()
                while event is not None:              # Remember the newest axis positions
                    if event.type == evdev.ecodes.EV_ABS:
                        self.pad_axes.setdefault(dev.path, {})[event.code] = event.value
                    event = dev.read_one()
                now = self.pad_directions(dev)
            except OSError:                           # Unplugged: forget it
                self.pads.remove(dev)
                for d in self.pad_state.pop(dev.path, set()):
                    self.held.discard(d)
                continue
            before = self.pad_state.get(dev.path, set())
            for d in now - before:                    # Newly pushed
                pressed.append(d)
                self.held.add(d)
                self.last_input = time.monotonic()
            for d in before - now:                    # Let go
                self.held.discard(d)
                self.last_input = time.monotonic()
            if now:
                self.last_input = time.monotonic()    # Still holding: still playing
            self.pad_state[dev.path] = now

    def read(self):
        """Return the directions pressed since the last call ("up", "left", "middle", ...).

        Also updates self.held and the time of the last input. Returns an empty
        list when there is no joystick to read (for example a broken connection).
        """
        pressed = []
        self.read_pads(pressed)
        # Fetch all events that queued up since the last call
        try:
            events = self.sense.stick.get_events()
        except (AttributeError, OSError):
            return pressed
        for event in events:
            # Any event at all counts as "someone is playing"
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
    """A green play symbol: you're in control now.

    Each "#" in PLAY_SIGN becomes a green pixel, every other character a dark
    one. The sign stays up for 0.8 seconds so the player can get ready.
    """
    sense.set_pixels([(0, 255, 0) if c == "#" else (0, 0, 0) for row in PLAY_SIGN for c in row])
    time.sleep(0.8)
# ---------------------------------------------------------------------------


# Colours
OFF = (0, 0, 0)
PADDLE = (0, 150, 255)
BALL = (255, 255, 255)
BRICK_COLORS = [(255, 0, 0), (255, 255, 0), (0, 255, 0), (150, 0, 255)]   # Per row
SILVER = (150, 150, 150)  # Silver brick, not hit yet ...
CRACKED = (50, 50, 50)    # ... and after its first hit
GOLD = (200, 110, 0)      # Gold brick (can't be broken)
LASER_SHOT = (255, 60, 60)
LOST_FLASH = (120, 0, 0)  # Dark red flash when the last ball is lost
CLEAR_FLASH = (0, 80, 0)  # Dark green flash when a level is cleared
SCORE_COLOR = (255, 255, 0)

# Capsules: (power-up, colour, how often it drops compared to the others)
CAPSULES = [
    ("enlarge", (0, 0, 255), 3),      # Wider paddle
    ("catch", (0, 255, 0), 3),        # Ball sticks to the paddle
    ("laser", (255, 0, 0), 3),        # Press to shoot
    ("slow", (255, 100, 0), 3),       # Ball back to a slow speed
    ("disrupt", (0, 255, 255), 3),    # Ball splits into three
    ("break", (255, 0, 255), 1),      # Exit in the bottom-right corner
    ("player", (255, 150, 150), 1),   # Extra life
]
CAPSULE_COLORS = {name: color for name, color, _ in CAPSULES}

# Gameplay
TICK = 0.01               # Seconds per game step
PADDLE_WIDTH = 3          # Normal paddle size in pixels ...
BIG_PADDLE_WIDTH = 4      # ... and with the Enlarge power-up
PADDLE_DELAY = 0.06       # Seconds per paddle step
BALL_DELAY = 0.17         # Seconds per ball step on level 1 (and with Slow) ...
LEVEL_SPEEDUP = 0.01      # ... this much faster every level ...
BALL_SPEEDUP = 0.002      # ... a little faster with every paddle hit ...
MIN_BALL_DELAY = 0.07     # ... down to this
SERVE_TIME = 1.5          # Seconds a new ball waits on the paddle before it goes by itself
CATCH_TIME = 2.0          # Same for a ball held by the Catch power-up
CAPSULE_CHANCE = 0.2      # Chance a broken brick drops a capsule
CAPSULE_DELAY = 0.3       # Seconds per capsule step down
LASER_COOLDOWN = 0.4      # Seconds between two laser shots
SHOT_DELAY = 0.04         # Seconds per laser shot step up
BREAK_TIME = 5.0          # Seconds the Break exit stays open
FLASH_TIME = 0.3          # Seconds the paddle lights up in the colour of a caught capsule
BLINK = 0.12              # Seconds per blink of capsules and the Break exit
STUCK_STEPS = 80          # Ball steps without paddle or brick before the ball gets a nudge
LIVES = 3                 # Lives per game
MAX_LIVES = 9             # Most lives you can collect (shown as one digit)
BRICK_POINTS = 10         # Points for a coloured brick, times the level number ...
SILVER_POINTS = 25        # ... and for a silver brick
CAPSULE_POINTS = 20       # Points for catching a capsule
CLEAR_BONUS = 100         # Bonus for clearing a level ...
BREAK_BONUS = 250         # ... plus this when leaving through the Break exit
AI_SKILL = 0.95           # Chance the computer gets to the ball in time
AI_LAUNCH_WAIT = 0.5      # Seconds the computer holds the ball before launching it
AI_BREAK_CHANCE = 0.5     # Chance the computer uses the Break exit

# Brick layouts per level: "#" = coloured brick (colour per row), "S" = silver,
# "G" = gold, "." = empty. After the last level they repeat.
LEVELS = [
    ["########",
     "########",
     "########"],
    ["SSSSSSSS",
     "########",
     "########"],
    ["#.#.#.#.",
     ".#.#.#.#",
     "#.#.#.#.",
     ".#.#.#.#"],
    ["########",
     "#.G..G.#",
     "########"],
    ["...SS...",
     "..####..",
     ".######.",
     "########"],
    ["SSSSSSSS",
     "S######S",
     "S######S"],
    ["S#S##S#S",
     "########",
     "##GGGG##"],
    ["G######G",
     "#SSSSSS#",
     "###GG###"],
]


def clamp(value, low, high):
    """Return value, but kept between low and high."""
    return max(low, min(high, value))


class Ball:
    """One ball: where it is, which way it goes, and a few counters.

    The ball moves diagonally one pixel at a time: dx and dy are each -1 or +1
    and say which way it goes (dx = -1 is left, dy = -1 is up).
    """

    def __init__(self, x, y, dx, dy):
        """Put a ball at (x, y), heading in direction (dx, dy)."""
        self.x, self.y, self.dx, self.dy = x, y, dx, dy
        self.miss = 0             # Columns the computer player misjudges this ball by
        self.stuck = 0            # Ball steps since it touched the paddle or a breakable brick
        self.still = 0            # Steps in a row it bounced without moving
        self.aim = 0              # Side the computer wants to send it to next (0 = to the bricks)


class Game:
    """Everything about one game of Breakout: bricks, balls, paddle, power-ups,
    lives and score.

    The screen is 8x8 pixels. x is the column (0 = left, 7 = right) and y the
    row (0 = top, 7 = bottom). The bricks sit at the top, the paddle lives on
    the bottom row (y = 7) and the balls fly around in between.
    """

    def __init__(self, human):
        """Start a new game at level 1.

        Args:
            human: True when a person plays with the joystick, False when the
                computer plays the demo.
        """
        self.human = human
        self.lives = LIVES
        self.score = 0
        self.level = 0                     # next_level() below turns this into level 1
        self.paddle = 2                    # Leftmost column of the paddle
        self.want_move = 0                 # Paddle move (-1, 0, +1) waiting for its turn
        self.clock = 0.0                   # Game time in seconds, for blinking
        self.next_level()

    def next_level(self):
        """Go up one level: build that level's bricks and serve a new ball.

        The brick layout comes from LEVELS (wrapping around after the last one).
        """
        self.level += 1
        layout = LEVELS[(self.level - 1) % len(LEVELS)]
        # The bricks are a dictionary {(x, y): [kind, hits left]}, so looking
        # up "is there a brick here?" and removing a broken brick are quick
        self.bricks = {}
        for y, row in enumerate(layout):
            for x, kind in enumerate(row):
                if kind != ".":
                    self.bricks[(x, y)] = [kind, 2 if kind == "S" else 1]
        # Breakable bricks left: the level is cleared when this reaches 0
        self.left = sum(1 for kind, _ in self.bricks.values() if kind != "G")
        self.new_ball()

    def new_ball(self):
        """Start a new life or level: one ball waiting on the paddle, no power-ups.

        Its speed depends on the level: every level takes LEVEL_SPEEDUP off
        the step time, but never below MIN_BALL_DELAY.
        """
        self.delay = max(MIN_BALL_DELAY, BALL_DELAY - LEVEL_SPEEDUP * (self.level - 1))
        self.width = PADDLE_WIDTH
        self.paddle = min(self.paddle, 8 - self.width)
        self.effect = None                 # Power-up working right now (a name from CAPSULES)
        self.capsule = None                # Falling capsule: [x, y, name], or None
        self.capsule_timer = 0.0
        self.shots = []                    # Laser shots flying up: [x, y] each
        self.shot_timer = 0.0
        self.laser_cooldown = 0.0
        self.break_timer = 0.0             # Seconds the Break exit stays open
        self.flash_timer = 0.0             # Seconds the paddle stays lit after a catch
        self.flash_color = PADDLE
        self.ai_break = False              # Will the computer use the Break exit?
        self.boring = 0                    # Paddle hits without hitting a brick
        self.ball_timer = 0.0
        self.paddle_timer = 0.0
        self.want_move = 0
        # The serve: the ball sits on the middle of the paddle
        ball = Ball(self.paddle + 1, 6, random.choice([-1, 1]), -1)
        self.balls = [ball]
        self.hold(ball, 1, SERVE_TIME)

    # ---------- holding and launching the ball ----------
    def hold(self, ball, spot, seconds):
        """Make the ball stick to the paddle at pixel "spot" (0 = left edge).

        It goes by itself after the given number of seconds, or earlier when
        the player presses the joystick (see launch()).
        """
        self.held = ball
        self.hold_spot = spot
        self.hold_timer = seconds
        self.held_for = 0.0                # Seconds held so far (the computer uses this)
        ball.x, ball.y = self.paddle + spot, 6
        ball.still = 0

    def launch(self):
        """Let go of the held ball: it flies up like after a paddle hit."""
        ball = self.held
        self.held = None
        self.bounce(ball, self.hold_spot)
        self.ball_timer = 0.0

    def bounce(self, ball, spot):
        """Send a ball up after it met paddle pixel "spot" (0 = left edge).

        The edges choose the sideways direction, the middle keeps it as it was.
        """
        ball.dy = -1
        if spot == 0:
            ball.dx = -1
        elif spot == self.width - 1:
            ball.dx = 1
        # The ball can end up bouncing along the same path forever without
        # reaching the last bricks: after 6 such hits, flip it and shift it
        # one pixel sideways. (A ball moving diagonally only ever visits
        # every other pixel, so some bricks can only be hit after a shift.)
        self.boring += 1
        if self.boring > 6:
            ball.dx = -ball.dx
            if not 0 <= ball.x + ball.dx <= 7:
                ball.dx = -ball.dx
            ball.x += ball.dx
            self.boring = 0
        # Don't send the ball straight into a side wall next step
        if not 0 <= ball.x + ball.dx <= 7:
            ball.dx = -ball.dx
        ball.stuck = 0
        ball.still = 0
        # Now and then (1 - AI_SKILL of the time) the computer misjudges this
        # ball by 3 columns, so the demo can lose a ball too
        ball.miss = 0 if random.random() < AI_SKILL else random.choice([-3, 3])
        # After a few hits without a brick the computer sends the ball to a
        # random side next time, to find a new path
        ball.aim = random.choice([-1, 1]) if self.boring >= 3 else 0

    # ---------- bricks and capsules ----------
    def hit_brick(self, cell):
        """A ball or laser shot hits the brick at cell (x, y).

        Gold bricks don't care, silver ones crack first, the others break.
        A broken brick scores points and can drop a capsule.

        Returns:
            True when the brick was breakable (so it took damage), else False.
        """
        kind, hits = self.bricks[cell]
        if kind == "G":
            return False
        if hits > 1:                               # Silver brick: crack it
            self.bricks[cell][1] = hits - 1
            return True
        del self.bricks[cell]
        self.left -= 1
        self.score += (SILVER_POINTS if kind == "S" else BRICK_POINTS) * self.level
        # Maybe drop a capsule: only one at a time, and not while there are
        # several balls (just like a power-up can't stack)
        if (self.left and self.capsule is None and len(self.balls) == 1
                and random.random() < CAPSULE_CHANCE):
            # Pick a power-up; some come more often than others (their weight)
            pick = random.random() * sum(weight for _, _, weight in CAPSULES)
            for name, _, weight in CAPSULES:
                pick -= weight
                if pick < 0:
                    break
            self.capsule = [cell[0], cell[1], name]
            self.capsule_timer = 0.0
        return True

    def power_up(self, name):
        """The paddle caught a capsule: switch to its power-up.

        Only one power-up works at a time, so first the old one is undone.
        """
        self.score += CAPSULE_POINTS
        self.flash_timer = FLASH_TIME
        self.flash_color = CAPSULE_COLORS[name]
        # Undo the old power-up
        self.width = PADDLE_WIDTH
        self.hold_spot = min(self.hold_spot, self.width - 1)
        self.break_timer = 0.0
        self.effect = name
        # Switch on the new one (Catch and Laser work in step())
        if name == "enlarge":
            self.width = BIG_PADDLE_WIDTH
            self.paddle = min(self.paddle, 8 - self.width)
        elif name == "slow":
            self.delay = BALL_DELAY
        elif name == "disrupt":
            if self.held is not None:              # Let go of a held ball first
                self.launch()
            # Two more balls from the same spot, each going another way
            ball = self.balls[0]
            for dx, dy in ((-ball.dx, ball.dy), (ball.dx, -ball.dy)):
                extra = Ball(ball.x, ball.y, dx, dy)
                extra.miss = ball.miss
                self.balls.append(extra)
        elif name == "break":
            self.break_timer = BREAK_TIME
            self.ai_break = random.random() < AI_BREAK_CHANCE
        elif name == "player":
            self.lives = min(MAX_LIVES, self.lives + 1)

    def fire(self):
        """Shoot two laser beams up from the edges of the paddle."""
        self.laser_cooldown = LASER_COOLDOWN
        for x in (self.paddle, self.paddle + self.width - 1):
            self.shots.append([x, 7])

    # ---------- the computer player ----------
    def landing(self, ball):
        """Predict where and when a ball reaches the paddle row.

        It plays the ball's flight forward in its head (on copies of x, y, dx,
        dy, so the real ball is not touched), bouncing off the walls and the
        ceiling, until the ball is on row 6 heading down: its next step lands
        on the paddle row. Bricks are ignored, so the guess can be wrong when
        the ball hits one on the way (that's why it is redone every step).

        Returns:
            (column 0-7, number of ball steps until it gets there).
        """
        x, y, dx, dy = ball.x, ball.y, ball.dx, ball.dy
        for steps in range(40):
            # One row above the paddle and falling: the next step lands here
            # (if that next step would hit a side wall, the ball bounces back)
            if y == 6 and dy > 0:
                return (x + dx if 0 <= x + dx <= 7 else x - dx), steps + 1
            nx, ny = x + dx, y + dy
            # Bounce off the side walls ...
            if not 0 <= nx <= 7:
                dx = -dx
                nx = x + dx
            # ... and off the ceiling
            if ny < 0:
                dy = 1
                ny = y + dy
            x, y = nx, ny
        return x, 40

    def laser_has_target(self):
        """True when a laser shot from either paddle edge would hit a breakable brick."""
        for x in (self.paddle, self.paddle + self.width - 1):
            # The first brick above this edge, from the bottom up
            for y in range(6, -1, -1):
                if (x, y) in self.bricks:
                    if self.bricks[(x, y)][0] != "G":
                        return True
                    break
        return False

    def ai_target(self):
        """The column where the computer wants the left end of its paddle.

        First the most dangerous ball: the falling one that lands soonest.
        The paddle doesn't just catch it in the middle: it hits it with an
        edge to steer it towards where most bricks are left. A capsule is
        worth a detour when there's time to catch it and still get back to
        that ball. With no ball falling, the paddle follows the lowest ball.
        """
        w = self.width
        # Find the falling ball that reaches the paddle row first
        danger = None
        for ball in self.balls:
            if ball is self.held or ball.dy < 0 or ball.y >= 7:
                continue
            land, steps = self.landing(ball)
            if danger is None or steps < danger[2]:
                danger = (ball, land, steps)
        if danger:
            ball, land, steps = danger
            ball_time = steps * self.delay - self.ball_timer
            land += ball.miss                      # Misjudged (usually 0)
            # spot = which paddle pixel should meet the ball: the left edge
            # (sends it left), the right edge or the middle
            spot = w // 2
            if ball.aim:
                # Bouncing around without hitting bricks: try a random side
                spot = 0 if ball.aim < 0 else w - 1
            elif self.left:
                # bx = average column of the breakable bricks that are left
                cols = [x for (x, _), (kind, _) in self.bricks.items() if kind != "G"]
                bx = sum(cols) / len(cols)
                spot = 0 if bx < land - 1 else (w - 1 if bx > land + 1 else w // 2)
            ball_target = clamp(land - spot, 0, 8 - w)

        # A capsule: go for it if there's time
        if self.capsule:
            cx, cy, _ = self.capsule
            cap_time = (7 - cy) * CAPSULE_DELAY - self.capsule_timer
            cap_target = clamp(cx - w // 2, 0, 8 - w)
            if abs(cap_target - self.paddle) * PADDLE_DELAY <= cap_time + PADDLE_DELAY:
                if danger is None or (cap_time + (abs(ball_target - cap_target) + 1)
                                      * PADDLE_DELAY < ball_time):
                    return cap_target
        if danger:
            return ball_target
        if self.held is not None:
            return self.paddle
        # No ball falling: stay under the lowest ball
        low = max(self.balls, key=lambda b: b.y)
        return clamp(low.x - w // 2, 0, 8 - w)

    def ai_controls(self):
        """Decide what the computer does this step.

        It launches a held ball after a short wait, fires the laser when a
        brick is above the paddle, runs for the Break exit when it decided to
        use it, and otherwise moves towards ai_target().

        Returns:
            (move, press): move is -1 (left), +1 (right) or 0, press is True
            to push the joystick in.
        """
        if self.break_timer > 0 and self.ai_break:
            return 1, False                        # Off to the exit!
        if self.held is not None:
            press = self.held_for >= AI_LAUNCH_WAIT
        else:
            press = (self.effect == "laser" and self.laser_cooldown <= 0
                     and self.laser_has_target())
        target = self.ai_target()
        # True/False count as 1/0, so this gives +1, -1 or 0
        return (target > self.paddle) - (target < self.paddle), press

    # ---------- one game step ----------
    def move_ball(self, ball):
        """Move one ball one step: bounce off walls, bricks and the paddle.

        Returns:
            False when the ball fell past the paddle (it is lost), else True.
        """
        if ball.y == 7:                           # Below the paddle: lost
            return False
        ball.stuck += 1

        # Where the ball wants to go next, after bouncing off the walls
        nx, ny = ball.x + ball.dx, ball.y + ball.dy
        if not 0 <= nx <= 7:                      # Side walls
            ball.dx = -ball.dx
            nx = ball.x + ball.dx
        if ny < 0:                                # Top wall
            ball.dy = 1
            ny = ball.y + ball.dy

        # A ball that bounced in place twice in a row (for example trapped
        # between the ceiling and a gold brick) slides one pixel sideways
        # instead, or hits the brick next to it
        if ball.still >= 2:
            ball.still = 0
            if (nx, ball.y) in self.bricks:
                if self.hit_brick((nx, ball.y)):
                    self.boring = 0
                    ball.stuck = 0
                ball.dx = -ball.dx
            else:
                ball.x = nx
            return True

        # Bricks: check straight up/down first, then sideways, then diagonal
        # A brick above/below flips the up-down direction, one at the side
        # flips left-right, and one only on the diagonal (a corner) flips both.
        # Only one brick is hit per step, and the ball stays where it is.
        for cell, flip in (((ball.x, ny), "y"), ((nx, ball.y), "x"), ((nx, ny), "xy")):
            if cell in self.bricks:
                if self.hit_brick(cell):
                    self.boring = 0
                    ball.stuck = 0
                if "x" in flip:
                    ball.dx = -ball.dx
                if "y" in flip:
                    ball.dy = -ball.dy
                ball.still += 1
                return True

        if ny == 7 and ball.dy > 0:               # Paddle
            # spot = which pixel of the paddle the ball lands on (0 = left edge)
            spot = nx - self.paddle
            if 0 <= spot < self.width:
                if self.effect == "catch" and self.held is None:
                    self.hold(ball, spot, CATCH_TIME)
                else:
                    self.bounce(ball, spot)
                    # Every paddle hit makes the ball a little faster
                    self.delay = max(MIN_BALL_DELAY, self.delay - BALL_SPEEDUP)
                return True

        # Nothing hit: the ball moves to its new spot (on row 7 it is lost
        # at its next step)
        ball.x, ball.y = nx, ny
        ball.still = 0
        # A ball that only bounces between walls and gold bricks for a long
        # time gets a nudge: next step it slides sideways (see above)
        if ball.stuck > STUCK_STEPS:
            ball.stuck = 0
            ball.still = 2
        return True

    def step(self, move, press):
        """Advance one tick. Returns "lost", "cleared", "break" or None.

        The paddle, balls, capsule and laser shots each have their own timer,
        so they move at their own speed even though this is called every
        TICK seconds.

        Args:
            move: -1 (left), +1 (right) or 0, from the joystick or the computer.
            press: True when the joystick was pushed in (launch / fire).

        Returns:
            "lost" when the last ball fell past the paddle, "cleared" when the
            last breakable brick was hit, "break" when the paddle left through
            the Break exit, otherwise None.
        """
        self.clock += TICK
        # Count down the timers
        self.flash_timer = max(0.0, self.flash_timer - TICK)
        self.laser_cooldown = max(0.0, self.laser_cooldown - TICK)
        if self.break_timer > 0:
            self.break_timer = max(0.0, self.break_timer - TICK)
            if self.break_timer == 0 and self.effect == "break":
                self.effect = None

        # Paddle: remember the wish to move, and carry it out when the paddle
        # timer says it is time for a paddle step. (The 1e-9 avoids rounding
        # errors from adding up 0.01 many times.)
        if move:
            self.want_move = move
        self.paddle_timer += TICK
        if self.paddle_timer >= PADDLE_DELAY - 1e-9:
            self.paddle_timer = 0.0
            if self.want_move:
                # Pushing right against the open Break exit: through it you go
                if (self.want_move > 0 and self.break_timer > 0
                        and self.paddle + self.width == 8):
                    return "break"
                self.paddle = clamp(self.paddle + self.want_move, 0, 8 - self.width)
                self.want_move = 0

        # Joystick pushed in: launch a held ball, or else fire the laser
        if self.held is not None:
            self.held_for += TICK
            self.hold_timer -= TICK
            self.held.x = self.paddle + self.hold_spot
            if press or self.hold_timer <= 0:
                self.launch()
        elif press and self.effect == "laser" and self.laser_cooldown <= 0:
            self.fire()

        # Laser shots fly up and hit the first brick in their way
        self.shot_timer += TICK
        if self.shots and self.shot_timer >= SHOT_DELAY - 1e-9:
            self.shot_timer = 0.0
            for shot in self.shots[:]:
                shot[1] -= 1
                if shot[1] < 0:
                    self.shots.remove(shot)
                elif tuple(shot) in self.bricks:
                    self.hit_brick(tuple(shot))
                    self.shots.remove(shot)

        # The capsule falls; on the bottom row the paddle catches it, or it's gone
        if self.capsule:
            self.capsule_timer += TICK
            if self.capsule_timer >= CAPSULE_DELAY - 1e-9:
                self.capsule_timer = 0.0
                self.capsule[1] += 1
                if self.capsule[1] > 7:
                    self.capsule = None
            if self.capsule and self.capsule[1] == 7:
                if 0 <= self.capsule[0] - self.paddle < self.width:
                    name = self.capsule[2]
                    self.capsule = None
                    self.power_up(name)

        # Balls: only move once their timer has run out (a held ball waits)
        self.ball_timer += TICK
        if self.ball_timer >= self.delay - 1e-9:
            self.ball_timer = 0.0
            for ball in self.balls[:]:
                if ball is not self.held and not self.move_ball(ball):
                    self.balls.remove(ball)
            if not self.balls:
                return "lost"
            if len(self.balls) == 1 and self.effect == "disrupt":
                self.effect = None                 # Back to one ball: Disruption is over
        return "cleared" if self.left == 0 else None

    def draw(self, sense, ball=True):
        """Show bricks, capsule, laser shots, paddle and (optionally) balls.

        Args:
            sense: the SenseHat object to draw on.
            ball: False to leave the balls out, used when flashing after a lost ball.
        """
        # Build the picture as 8 rows of 8 colours, then send it in one go
        frame = [[OFF] * 8 for _ in range(8)]
        for (x, y), (kind, hits) in self.bricks.items():
            if kind == "G":
                frame[y][x] = GOLD
            elif kind == "S":
                frame[y][x] = SILVER if hits > 1 else CRACKED
            else:
                frame[y][x] = BRICK_COLORS[y % len(BRICK_COLORS)]
        # Blinking things are bright for one BLINK, dim for the next
        blink_on = int(self.clock / BLINK) % 2 == 0
        if self.break_timer > 0:                  # The Break exit
            color = CAPSULE_COLORS["break"]
            frame[7][7] = color if blink_on else tuple(c // 4 for c in color)
        if self.capsule:
            x, y, name = self.capsule
            color = CAPSULE_COLORS[name]
            frame[y][x] = color if blink_on else tuple(c // 4 for c in color)
        for x, y in self.shots:
            if y <= 6:
                frame[y][x] = LASER_SHOT
        # The paddle; Catch and Laser colour its edges, a caught capsule
        # lights it up for a moment
        for i in range(self.width):
            if self.paddle + i > 7:               # Sliding out through the exit
                continue
            color = PADDLE
            if self.flash_timer > 0:
                color = self.flash_color
            elif self.effect in ("catch", "laser") and i in (0, self.width - 1):
                color = CAPSULE_COLORS[self.effect]
            frame[7][self.paddle + i] = color
        if ball:
            for b in self.balls:
                if 0 <= b.x <= 7:
                    frame[b.y][b.x] = BALL
        sense.set_pixels(flatten(frame))


def joystick_move(stick, pressed):
    """Turn the joystick into a paddle move: -1 (left), +1 (right) or 0.

    A direction counts when it is held down or was just pressed (so even a
    very short tap moves the paddle). Holding both left and right gives 0.

    Args:
        stick: the Joystick.
        pressed: the list of new presses from stick.read().
    """
    return (("right" in stick.held or "right" in pressed)
            - ("left" in stick.held or "left" in pressed))


def play_game(sense, stick, human):
    """Play one game. Returns (result, score): result is "over", or "start" when
    someone touches the joystick during the demo.

    Args:
        sense: the SenseHat object.
        stick: the Joystick.
        human: True for a game played by a person, False for the computer demo.
    """
    game = Game(human)
    while True:
        # Input: during the demo any press means "I want to play"
        pressed = stick.read()
        if not game.human and pressed:
            return "start", 0
        if game.human and stick.idle():      # Player walked away: computer takes over
            game.human = False

        # The paddle is moved by the joystick or by the computer player
        if game.human:
            move, press = joystick_move(stick, pressed), "middle" in pressed
        else:
            move, press = game.ai_controls()
        result = game.step(move, press)

        if result == "lost":
            # Last ball lost: flash red twice, then show the lives left (or end the game)
            game.lives -= 1
            for _ in range(2):
                sense.clear(LOST_FLASH)
                time.sleep(0.15)
                game.draw(sense, ball=False)
                time.sleep(0.15)
            if game.lives == 0:
                return "over", game.score
            sense.show_letter(str(game.lives), text_colour=PADDLE)
            time.sleep(0.6)
            game.new_ball()
        elif result in ("cleared", "break"):
            if result == "break":
                # Slide the paddle out through the exit, for bonus points
                for _ in range(game.width):
                    game.paddle += 1
                    game.draw(sense)
                    time.sleep(0.06)
                game.paddle = 8 - PADDLE_WIDTH
                game.score += BREAK_BONUS
            # Level done: flash green three times, bonus points, next level
            game.draw(sense)
            for _ in range(3):
                sense.clear(CLEAR_FLASH)
                time.sleep(0.15)
                sense.clear()
                time.sleep(0.15)
            game.score += CLEAR_BONUS
            game.next_level()

        # Draw the new situation and wait for the next tick
        game.draw(sense)
        time.sleep(TICK)


def main():
    """Keep playing forever: demo games until someone takes the joystick.

    After a human's game the score scrolls by and the computer starts a new
    demo; touching the joystick during a demo starts a game for you.
    """
    sense = init(hat)
    stick = Joystick(sense)
    human = False
    while True:
        result, score = play_game(sense, stick, human)
        if result == "start":
            # Someone touched the joystick during the demo: start their game
            show_play_sign(sense)
            stick.flush()
            human = True
            continue
        sense.show_message(f"Score {score}", text_colour=SCORE_COLOR, scroll_speed=0.06)
        human = False   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
