#!/usr/bin/env python3
"""Sense HAT – Space Invaders: defend the bottom row against the invasion.

Rows of invaders march left and right, stepping down every other time they
reach the edge, and drop bombs on your cannon. Hide behind the blue shields,
shoot every invader to start the next, faster wave, and keep an eye out for
the purple saucer for bonus points. If the invaders reach your row, or you
lose all 3 lives, it's game over.

The computer plays a demo until you touch the joystick: then you play.
Left and right to move, up or press to fire. After game over, or when you
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

# The green "play" triangle shown when you take over: "#" = lit LED, "." = off
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


# Colours (red, green, blue, each 0-255)
OFF = (0, 0, 0)
CANNON = (0, 255, 0)
SHOT = (255, 255, 255)
BOMB = (255, 60, 0)
SHIELD = (0, 120, 255)
SHIELD_HURT = (0, 40, 130)      # A shield block that was hit once (one more hit breaks it)
SAUCER = (255, 0, 255)
INVADER_COLORS = [(255, 255, 0), (0, 255, 255), (255, 120, 0)]   # Per row, from the top
HIT_FLASH = (255, 0, 0)         # Flashes where your cannon was when a bomb hits it

# Gameplay
TICK = 0.02                 # Seconds per game step
LIVES = 3
INVADER_COLUMNS = (0, 2, 4) # Starting columns of the invaders in each row
INVADER_DELAY = 0.75        # Seconds per invader step in wave 1, with the full formation
MIN_INVADER_DELAY = 0.12    # The fastest the invaders can ever march
SHOT_DELAY = 0.05           # Seconds per row for your shot
BOMB_DELAY = 0.16           # Seconds per row for a bomb
CANNON_DELAY = 0.1          # Seconds per step for the cannon
BOMB_CHANCE = 0.02          # Chance per step of a new bomb (grows each wave)
SAUCER_CHANCE = 0.002       # Chance per step that the saucer flies over
SAUCER_DELAY = 0.12         # Seconds per column for the saucer
AI_MISS_CHANCE = 0.12       # Chance the computer doesn't notice a bomb in time


class Bomb:
    """A bomb dropped by an invader. It falls one row at a time towards your cannon."""

    def __init__(self, x, y):
        """Create a bomb at column `x`, row `y` (row 0 is the top, 7 is your row)."""
        self.x = x
        self.y = y
        # The row at which the computer notices this bomb (8 = never). A bit like a
        # human who sometimes sees a bomb late, or not at all.
        self.seen_at = 8 if random.random() < AI_MISS_CHANCE else random.choice([2, 3, 4])


class Game:
    """One game of Space Invaders: everything on the screen plus the computer player.

    Positions are (x, y) with x = column 0-7 (left to right) and y = row 0-7 (top to
    bottom). Row 0 is where the saucer flies, row 5 holds the shields and row 7 is
    your cannon's row. Each moving thing (invaders, shot, bombs, cannon, saucer)
    has its own timer, so they all move at their own speed.
    """

    def __init__(self, human):
        """Start a new game; `human` is True when a person plays, False for the demo."""
        self.human = human
        self.lives = LIVES
        self.score = 0
        self.wave = 0
        self.cannon = 3             # Column of your cannon in the bottom row
        self.want_move = 0          # Remembered move (-1, 0 or 1) until the cannon may step
        self.next_wave()

    def next_wave(self):
        """Set up the next wave: a fresh formation of invaders and repaired shields.

        Wave 1 has 2 rows of invaders, later waves have 3. Shots, bombs, the saucer
        and all timers are reset too.
        """
        self.wave += 1
        rows = 2 if self.wave == 1 else 3
        self.invaders = {(x, y) for y in range(1, 1 + rows) for x in INVADER_COLUMNS}
        self.wave_size = len(self.invaders)    # Used to speed up as invaders are shot
        self.direction = 1                     # 1 = marching right, -1 = left
        self.edge_hits = 0                     # How often the formation reached an edge
        self.legs = False           # Toggles every step: the invaders "walk"
        # Four shield blocks in row 5; each one can take 2 hits
        self.shields = {(1, 5): 2, (2, 5): 2, (5, 5): 2, (6, 5): 2}
        self.shot = None            # Your shot as [x, y], or None when there is none
        self.bombs = []
        self.saucer = None          # [x, direction]
        self.timers = dict.fromkeys(("invaders", "shot", "bombs", "cannon", "saucer"), 0.0)

    # ---------- timing ----------
    def ready(self, name, delay):
        """Advance a timer by one tick; True (and reset) when `delay` has passed.

        `name` is one of the keys in `self.timers`. The tiny 1e-9 avoids rounding
        errors when many TICKs are added together.
        """
        self.timers[name] += TICK
        if self.timers[name] >= delay - 1e-9:
            self.timers[name] = 0.0
            return True
        return False

    def invader_delay(self):
        """Seconds between two invader steps.

        Every wave is 15% faster than the one before. Within a wave they also
        speed up as you shoot them: with only a few left they march almost three
        times as fast (35% of the delay with a full formation). Never faster than
        MIN_INVADER_DELAY.
        """
        base = INVADER_DELAY * 0.85 ** (self.wave - 1)
        return max(MIN_INVADER_DELAY, base * (0.35 + 0.65 * len(self.invaders) / self.wave_size))

    # ---------- the computer player ----------
    def danger(self, column):
        """Is a bomb (that the computer has noticed) about to land in this column?

        A bomb doesn't count when a shield in that column is still between it and
        the cannon. Returns True or False.
        """
        for bomb in self.bombs:
            if bomb.x == column and bomb.y >= bomb.seen_at:
                shielded = (column, 5) in self.shields and bomb.y < 5
                if not shielded:
                    return True
        return False

    def ai_controls(self):
        """Decide what the computer does this tick. Returns (move, fire).

        `move` is -1 (left), 0 or 1 (right) and `fire` is True or False, the same
        as joystick_controls() gives for a person. In order of importance:
        1. Dodge: if a bomb is coming, step to a safe neighbouring column.
        2. Aim: at the saucer when it's close by, otherwise at the nearest invader
           in the lowest row (those are the most dangerous ones).
        3. Fire when an invader (or the saucer) is right above the cannon.
        """
        x = self.cannon
        # Dodge: try one random side first, then the other side; stay put if
        # neither neighbour is safe
        if self.danger(x):                       # Dodge
            for move in (random.choice([-1, 1]), 0):
                for m in (move, -move):
                    if 0 <= x + m <= 7 and not self.danger(x + m):
                        return m, False
            return 0, False
        # Choose a target column. For the saucer, aim two columns ahead of it,
        # because it keeps flying while the shot travels up.
        if self.saucer and abs(self.saucer[0] - x) <= 2 and self.shot is None:
            target = self.saucer[0] + self.saucer[1] * 2
        elif self.invaders:
            lowest = max(y for _, y in self.invaders)
            columns = [ix for ix, iy in self.invaders if iy == lowest]
            target = min(columns, key=lambda c: abs(c - x))
        else:
            target = x
        # Step towards the target, but never into a column where a bomb is falling
        move = (target > x) - (target < x)
        if move and self.danger(x + move):
            move = 0
        # Fire when there is no shot in the air yet and something is above us
        fire = self.shot is None and any(ix == x for ix, _ in self.invaders)
        if self.saucer and self.saucer[0] == x:
            fire = self.shot is None
        return move, fire

    # ---------- one game step ----------
    def step(self, move, fire):
        """Advance one tick. Returns "hit", "invaded", "cleared" or None.

        `move` is -1, 0 or 1 and `fire` is True or False. "hit" means a bomb hit
        your cannon, "invaded" that the invaders reached your row, "cleared" that
        the last invader of the wave was shot, and None that the game just goes on.
        """
        # Cannon: remember the wanted move and do it when the cannon timer allows
        if move:
            self.want_move = move
        if self.ready("cannon", CANNON_DELAY) and self.want_move:
            self.cannon = max(0, min(7, self.cannon + self.want_move))
            self.want_move = 0
        # Fire: only one shot can be in the air at a time; it starts just above
        # the cannon (row 6)
        if fire and self.shot is None:
            self.shot = [self.cannon, 6]
            self.check_shot()

        # Your shot flies up one row at a time and disappears at the top
        if self.shot and self.ready("shot", SHOT_DELAY):
            self.shot[1] -= 1
            if self.shot[1] < 0:
                self.shot = None
            else:
                self.check_shot()

        # The invaders march; they may walk into the shot, so check it again
        if self.ready("invaders", self.invader_delay()):
            if self.move_invaders():
                return "invaded"
            self.check_shot()

        # Maybe drop a new bomb from the lowest invader of a random column. The
        # chance and the number of bombs allowed at once both grow with the wave.
        bomb_chance = BOMB_CHANCE + 0.006 * (self.wave - 1)
        if (self.invaders and len(self.bombs) < 2 + self.wave // 3
                and random.random() < bomb_chance):
            x, y = random.choice(sorted(self.invaders))
            y = max(iy for ix, iy in self.invaders if ix == x)   # Lowest in that column
            self.bombs.append(Bomb(x, y + 1))

        # Bombs fall one row (a little faster each wave), then see what they hit
        if self.ready("bombs", max(0.09, BOMB_DELAY - 0.01 * (self.wave - 1))):
            for bomb in self.bombs:
                bomb.y += 1
        if self.check_bombs():
            return "hit"

        # The saucer: now and then it appears at one side and flies across row 0
        if self.saucer is None and random.random() < SAUCER_CHANCE:
            direction = random.choice([-1, 1])
            self.saucer = [0 if direction > 0 else 7, direction]
        elif self.saucer and self.ready("saucer", SAUCER_DELAY):
            self.saucer[0] += self.saucer[1]
            if not 0 <= self.saucer[0] <= 7:
                self.saucer = None
            else:
                self.check_shot()

        # All invaders shot: this wave is done
        if not self.invaders:
            return "cleared"
        return None

    def move_invaders(self):
        """March the formation. Returns True when the invaders reached your row.

        The whole formation moves one column sideways. When that would take it
        past an edge, it turns around instead, and every second time that happens
        it also steps one row down. "Your row" here means row 6 or lower.
        """
        if not self.invaders:
            return False
        self.legs = not self.legs
        xs = [x for x, _ in self.invaders]
        # Would the leftmost or rightmost invader leave the screen with this step?
        if not 0 <= min(xs) + self.direction <= max(xs) + self.direction <= 7:
            self.direction = -self.direction
            self.edge_hits += 1
            if self.edge_hits % 2 == 0:          # Step down every other edge
                self.invaders = {(x, y + 1) for x, y in self.invaders}
                for cell in self.invaders:
                    self.shields.pop(cell, None)  # Invaders crush the shields
        else:
            self.invaders = {(x + self.direction, y) for x, y in self.invaders}
        return any(y >= 6 for _, y in self.invaders)

    def check_shot(self):
        """See whether your shot hit something, and handle it.

        An invader hit scores more the higher up it is (30, 20 or 10 points), the
        saucer is worth 100, and a shot that meets a bomb destroys both. Called
        whenever the shot, the invaders or the saucer moved. Returns nothing.
        """
        if not self.shot:
            return
        cell = tuple(self.shot)
        if cell in self.invaders:
            self.invaders.discard(cell)
            self.score += 10 * (4 - cell[1]) if cell[1] < 4 else 10
            self.shot = None
        elif self.saucer and cell == (self.saucer[0], 0):
            self.score += 100
            self.saucer = None
            self.shot = None
        else:
            # Also check the row just above the bomb, so a shot and a bomb that
            # pass each other between two rows still collide
            for bomb in self.bombs:              # Shots and bombs cancel each other out
                if (bomb.x, bomb.y) == cell or (bomb.x, bomb.y - 1) == cell:
                    self.bombs.remove(bomb)
                    self.shot = None
                    break

    def check_bombs(self):
        """Handle bombs hitting shields or the cannon. Returns True when you're hit.

        A bomb that hits a shield takes away one of its 2 hit points and is gone.
        Bombs that fell below the screen disappear; the rest keep falling.
        """
        alive = []
        hit = False
        for bomb in self.bombs:
            cell = (bomb.x, bomb.y)
            if cell in self.shields:
                self.shields[cell] -= 1
                if self.shields[cell] == 0:
                    del self.shields[cell]
            elif bomb.y == 7 and bomb.x == self.cannon:
                hit = True
            elif bomb.y <= 7:
                alive.append(bomb)
        self.bombs = alive
        return hit

    # ---------- drawing ----------
    def draw(self, sense, cannon=True):
        """Show everything on the LEDs; `cannon=False` leaves the cannon out.

        Things drawn later cover things drawn earlier, so the order is: shields,
        invaders, saucer, bombs, your shot and finally the cannon.
        """
        frame = [[OFF] * 8 for _ in range(8)]
        for (x, y), hp in self.shields.items():
            frame[y][x] = SHIELD if hp == 2 else SHIELD_HURT
        for x, y in self.invaders:
            color = INVADER_COLORS[(y - 1) % len(INVADER_COLORS)]
            # Every other step the invaders are a bit dimmer: it looks like walking
            if self.legs:
                color = tuple(int(c * 0.7) for c in color)
            frame[y][x] = color
        if self.saucer:
            frame[0][self.saucer[0]] = SAUCER
        for bomb in self.bombs:
            frame[bomb.y][bomb.x] = BOMB
        if self.shot:
            frame[self.shot[1]][self.shot[0]] = SHOT
        if cannon:
            frame[7][self.cannon] = CANNON
        sense.set_pixels(flatten(frame))


def joystick_controls(stick, pressed):
    """Turn the joystick state into (move, fire), the same as ai_controls() gives.

    Left and right work while held, so you can keep pushing to keep moving. Up or
    pressing the joystick in fires (holding it keeps firing whenever you can).
    """
    # True/False count as 1/0, so right gives +1, left gives -1, both or none 0
    move = (("right" in stick.held or "right" in pressed)
            - ("left" in stick.held or "left" in pressed))
    fire = any(k in pressed or k in stick.held for k in ("up", "middle"))
    return move, fire


def you_were_hit(sense, game):
    """Show that a bomb hit you: flash the cannon red, then show the lives left.

    Also removes all bombs and your shot, so you get a fresh start.
    """
    for _ in range(3):
        game.draw(sense, cannon=False)
        sense.set_pixel(game.cannon, 7, HIT_FLASH)
        time.sleep(0.15)
        game.draw(sense, cannon=False)
        time.sleep(0.15)
    game.bombs = []
    game.shot = None
    if game.lives > 0:
        sense.show_letter(str(game.lives), text_colour=CANNON)
        time.sleep(0.6)


def wave_cleared(sense, game):
    """Celebrate a cleared wave: flash green twice, add 50 bonus points, next wave."""
    for _ in range(2):
        sense.clear(0, 80, 0)
        time.sleep(0.15)
        sense.clear()
        time.sleep(0.15)
    game.score += 50
    game.next_wave()


def play_game(sense, stick, human):
    """Play one game. Returns (result, score): result is "over", or "start" when
    someone touches the joystick during the demo."""
    game = Game(human)
    while True:
        # Input: in the demo any press means someone wants to play
        pressed = stick.read()
        if not game.human and pressed:
            return "start", 0
        if game.human and stick.idle():      # Player walked away: computer takes over
            game.human = False

        # Get this tick's controls from the player or the computer, then run a step
        move, fire = joystick_controls(stick, pressed) if game.human else game.ai_controls()
        result = game.step(move, fire)

        # React to what happened in this step
        if result == "hit":
            game.lives -= 1
            you_were_hit(sense, game)
            if game.lives == 0:
                return "over", game.score
        elif result == "invaded":
            # The invaders landed: flash the screen red, and the game is over
            game.draw(sense)
            for _ in range(3):
                sense.clear(120, 0, 0)
                time.sleep(0.2)
                game.draw(sense)
                time.sleep(0.2)
            return "over", game.score
        elif result == "cleared":
            wave_cleared(sense, game)

        # Show the result and wait for the next tick
        game.draw(sense)
        time.sleep(TICK)


def main():
    """Switch between the computer demo and real games, forever.

    The demo runs until someone touches the joystick: then the play sign shows and
    a fresh game starts for the player. After a game the score scrolls by and the
    demo starts again.
    """
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
        sense.show_message(f"Score {score}", text_colour=INVADER_COLORS[0], scroll_speed=0.06)
        human = False   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
