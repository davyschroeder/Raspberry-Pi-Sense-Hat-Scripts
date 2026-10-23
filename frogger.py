#!/usr/bin/env python3
"""Sense HAT – Frogger: hop as far up as you can in a never-ending world.

The frog hops upward through an endless world of grass strips, busy roads and
rivers with drifting logs. Dodge the cars and trucks, and ride the logs across
the river: the water is deadly, and so is drifting off the edge. The world
keeps scrolling up by itself, so don't dawdle: if the frog drops off the bottom
of the screen, you lose a life. The further you get, the faster the traffic,
the shorter the logs and the quicker the scrolling. You have 3 lives.

Points: 10 for every new row you reach, 50 for every fly you catch on the
grass (the white dots).

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
FROG = (120, 255, 0)
GRASS = (0, 60, 0)
FLY = (255, 255, 255)
ROAD = (0, 0, 0)
WATER = (0, 0, 100)
LOG = (130, 70, 20)
CAR_COLORS = [(255, 0, 0), (255, 200, 0), (255, 0, 255), (0, 200, 255), (255, 120, 0)]
SPLAT = (255, 0, 0)        # Flashes where the frog died

# Gameplay
TICK = 0.02                # Seconds per game step
LIVES = 3
SCROLL_DELAY = 4.0         # Seconds per row the world scrolls up by itself at the start ...
MIN_SCROLL_DELAY = 1.2     # ... and the fastest it gets
FLY_CHANCE = 0.35          # Chance a grass strip has a fly on it
AI_DELAY = 0.3             # Seconds the computer waits between hops
AI_MISTAKE = 0.03          # Chance the computer hops without looking (on the road)


def difficulty(row):
    """0 at the start, growing slowly the further up you are.

    `row` is a row number in the world. Returns a number that goes up by 1 every
    25 rows; it makes traffic faster, gaps smaller and logs shorter.
    """
    return row / 25


class Row:
    """One row of the world: grass, road or river. Roads and rivers slide sideways.

    A road or river row is described by a repeating `pattern` text, for example
    "##....#...": "#" is a car (on a road) or a piece of log (on a river), "." is
    empty road or water. The pattern is longer than the screen and wraps around.
    `offset` says how far it has slid; changing it by 1 moves everything one LED.
    """

    def __init__(self, kind, level=0.0):
        """Create a row of `kind` "grass", "road" or "river".

        `level` comes from difficulty(): the higher it is, the faster the row
        slides, the closer the cars are together and the shorter the logs.
        """
        self.kind = kind
        self.fly = None             # Column of the fly on a grass strip, or None
        # Grass never moves; it may have a fly to catch
        if kind == "grass":
            self.pattern = "." * 8
            if random.random() < FLY_CHANCE:
                self.fly = random.randint(0, 7)
            return
        # Roads and rivers slide left or right; `delay` is the seconds per step
        self.direction = random.choice([-1, 1])
        self.delay = max(0.12, random.uniform(0.3, 0.55) * 0.93 ** level)
        # Build the pattern from pieces of "vehicle/log + gap" until it is at least
        # 14 long, so the same picture doesn't repeat on the 8-LED wide screen
        if kind == "road":
            self.color = random.choice(CAR_COLORS)
            length = random.choice([1, 1, 2])                      # Cars and trucks
            gap_min = max(2, 5 - int(level))
            parts = []
            while sum(len(p) for p in parts) < 14:
                parts.append("#" * length + "." * random.randint(gap_min, gap_min + 3))
        else:
            length_max = max(2, 4 - int(level / 2))                 # Logs get shorter
            parts = []
            while sum(len(p) for p in parts) < 14:
                parts.append("#" * random.randint(2, length_max) + "." * random.randint(2, 3))
        self.pattern = "".join(parts)
        # Start at a random point of the pattern and of the step timer, so rows
        # don't all look and move the same
        self.offset = random.randrange(len(self.pattern))
        self.timer = random.uniform(0, self.delay)

    def occupied(self, x, offset=None):
        """Is there a car or log at column `x`?

        `offset` lets you ask about a future position of the row (used by the
        computer player); by default the current one is used. The % (modulo)
        makes the pattern wrap around endlessly.
        """
        if self.kind == "grass":
            return False
        offset = self.offset if offset is None else offset
        return self.pattern[(x - offset) % len(self.pattern)] == "#"

    def deadly(self, x, offset=None):
        """Would the frog die at column `x` of this row?

        Off the screen is always deadly. On a road a car is deadly, on a river the
        water is (a log is safe). Grass is always safe.
        """
        if not 0 <= x <= 7:
            return True
        if self.kind == "road":
            return self.occupied(x, offset)
        if self.kind == "river":
            return not self.occupied(x, offset)
        return False

    def steps_within(self, seconds):
        """How many times this row will slide one step in the next `seconds`."""
        if self.kind == "grass":
            return 0
        return int((self.timer + seconds) / self.delay)


class Game:
    """One game of Frogger: the endless world, the frog, the score and the computer player.

    The world is a list of rows that grows upward as you go. World row numbers
    count up from the bottom (row 0 is where you start). `camera` is the world
    row shown at the bottom of the screen; the screen always shows 8 rows from
    there. The frog's position is [column, world row].
    """

    def __init__(self, human):
        """Start a new game; `human` is True when a person plays, False for the demo."""
        self.human = human
        self.lives = LIVES
        self.score = 0
        self.rows = [Row("grass"), Row("grass")]   # rows[0] is the bottom of the world
        self.camera = 0                            # World row shown at the bottom of the screen
        self.scroll_timer = 0.0                    # Seconds since the screen last scrolled
        self.frog = [3, 0]                         # x, world row
        self.best = 0                              # Highest row reached (for the score)
        self.ai_timer = 0.0                        # Seconds since the computer last hopped
        self.extend()

    def extend(self):
        """Make sure the world is generated a bit beyond the top of the screen.

        New rows come in groups: 1 to 4 rows of road or river (more as it gets
        harder), always followed by a grass strip to rest on. Roads are twice as
        likely as rivers.
        """
        while len(self.rows) < self.camera + 12:
            level = difficulty(len(self.rows))
            kind = random.choice(["road", "road", "river"])
            count = random.randint(1, min(4, 2 + int(level)))
            for _ in range(count):
                self.rows.append(Row(kind, level))
            self.rows.append(Row("grass"))

    def row(self, r):
        """The Row object for world row `r`."""
        return self.rows[r]

    def scroll_delay(self):
        """Seconds before the world scrolls up by itself one row.

        It gets 5% shorter for every 2 rows the screen has moved up, but never
        shorter than MIN_SCROLL_DELAY.
        """
        return max(MIN_SCROLL_DELAY, SCROLL_DELAY * 0.95 ** (self.camera / 2))

    # ---------- rules ----------
    def hop(self, dx, dy):
        """Hop the frog `dx` columns and `dy` rows (each -1, 0 or 1).

        Hops off the sides or below the bottom of the screen are ignored. Reaching
        a new highest row scores 10 points per row, and landing on a fly 50.
        Returns nothing. Dying is checked afterwards in step().
        """
        x, r = self.frog[0] + dx, self.frog[1] + dy
        if 0 <= x <= 7 and r >= self.camera:
            self.frog = [x, r]
            # Points only for rows you never reached before
            if r > self.best:
                self.score += 10 * (r - self.best)
                self.best = r
            # Eat the fly if there is one here
            row = self.row(r)
            if row.fly == x:
                row.fly = None
                self.score += 50
            # Keep the frog in the lower half: hopping up pulls the screen along
            if r - self.camera > 4:
                self.camera = r - 4
                self.scroll_timer = 0.0
            self.extend()

    def step(self, hop):
        """Advance one tick. Returns "dead" or None.

        `hop` is a (dx, dy) pair to hop, or None to stay. Then the visible rows
        slide, the world may scroll up, and finally it checks whether the frog is
        still alive.
        """
        if hop:
            self.hop(*hop)
        # Slide the road and river rows on the screen (and one just below it),
        # each at its own speed
        for r in range(max(0, self.camera - 1), self.camera + 10):
            row = self.row(r)
            if row.kind == "grass":
                continue
            row.timer += TICK
            if row.timer >= row.delay - 1e-9:
                row.timer = 0.0
                row.offset += row.direction
                if row.kind == "river" and self.frog[1] == r:
                    self.frog[0] += row.direction      # The frog drifts along on its log
        # Scroll the screen up one row now and then, so you can't stay forever
        self.scroll_timer += TICK
        if self.scroll_timer >= self.scroll_delay():
            self.scroll_timer = 0.0
            self.camera += 1                          # The world scrolls up by itself
            self.extend()
        # Check for death: off the bottom, hit by a car, in the water or off the side
        if self.frog[1] < self.camera:                # Dropped off the bottom
            return "dead"
        if self.row(self.frog[1]).deadly(self.frog[0]):
            return "dead"
        return None

    def respawn(self):
        """Put a new frog on the grass strip nearest the middle of the screen.

        If no grass is visible, it goes on the second row from the bottom. The
        scroll and computer timers start over, so you get a moment to look.
        """
        middle = self.camera + 3
        grass = [r for r in range(self.camera + 1, self.camera + 8) if self.row(r).kind == "grass"]
        r = min(grass, key=lambda g: abs(g - middle)) if grass else self.camera + 1
        self.frog = [3, r]
        self.scroll_timer = 0.0
        self.ai_timer = 0.0

    # ---------- the computer player ----------
    def safe_for(self, x, r, seconds):
        """Will a frog at (x, world row r) survive the next `seconds`?

        It looks ahead: for every step the row will slide in that time, it checks
        where the cars or logs will be. On a river the frog drifts along with the
        log, so its column is moved along too. Returns True or False.
        """
        if not 0 <= x <= 7 or r < self.camera:
            return False
        row = self.row(r)
        if row.kind == "grass":
            return True
        for k in range(row.steps_within(seconds) + 1):
            offset = row.offset + row.direction * k
            fx = x + row.direction * k if row.kind == "river" else x
            if row.deadly(fx, offset):
                return False
        return True

    def ai_controls(self):
        """Decide the computer's next hop. Returns (dx, dy), or None to stay put.

        The computer only thinks every AI_DELAY seconds. It makes a list of moves
        in order of preference (fly first, then up, stay, sideways towards the
        middle, sideways away from it, and down as a last resort) and takes the
        first one that is safe for a little longer than until its next decision.
        """
        # Wait until it's time to think again
        self.ai_timer += TICK
        if self.ai_timer < AI_DELAY:
            return None
        self.ai_timer = 0.0
        x, r = self.frog
        look = AI_DELAY + 0.15      # How many seconds ahead a spot must stay safe
        row = self.row(r)
        # Now and then on the road, hop without looking (so the demo can lose)
        if row.kind == "road" and random.random() < AI_MISTAKE:
            return random.choice([(0, 1), (-1, 0), (1, 0)])

        # Running out of screen: going up is the only real option
        hurry = r - self.camera <= 1 and self.scroll_timer > self.scroll_delay() - 1.0
        options = [(0, 1)]
        if row.fly is not None and not hurry:          # Grab the fly on this strip first
            options.insert(0, ((row.fly > x) - (row.fly < x), 0))
        if not hurry:
            options.append((0, 0))
        # Prefer moving sideways towards the middle (columns 3-4) of the screen
        side = 1 if x < 3 else -1 if x > 4 else random.choice([-1, 1])
        options += [(side, 0), (-side, 0)]
        if not hurry:
            options += [(0, -1)]
        # Take the first option that is safe; (0, 0) means "staying is fine"
        for dx, dy in options:
            if self.safe_for(x + dx, r + dy, look):
                return None if (dx, dy) == (0, 0) else (dx, dy)
        return None   # Nothing is safe: stay and hope

    # ---------- drawing ----------
    def draw(self, sense, frog=True):
        """Show the visible part of the world on the LEDs; `frog=False` hides the frog.

        Screen row 0 is the top of the LED grid, but world rows count upward, so
        the world row shown on screen row `sy` is camera + 7 - sy.
        """
        frame = []
        for sy in range(8):
            row = self.row(self.camera + 7 - sy)
            if row.kind == "grass":
                line = [FLY if x == row.fly else GRASS for x in range(8)]
            elif row.kind == "road":
                line = [row.color if row.occupied(x) else ROAD for x in range(8)]
            else:
                line = [LOG if row.occupied(x) else WATER for x in range(8)]
            frame.append(line)
        # Draw the frog on top, only when it is on the screen
        x, r = self.frog
        if frog and 0 <= x <= 7 and 0 <= r - self.camera <= 7:
            frame[7 - (r - self.camera)][x] = FROG
        sense.set_pixels(flatten(frame))


# Joystick direction -> hop (dx, dy); dy = 1 is one row up in the world
KEYS = {"up": (0, 1), "down": (0, -1), "left": (-1, 0), "right": (1, 0)}


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

        # One hop per joystick press (holding doesn't repeat), or the computer's hop
        if game.human:
            hop = next((KEYS[k] for k in pressed if k in KEYS), None)
        else:
            hop = game.ai_controls()

        # The frog died: flash a red splat where it was, lose a life, and either
        # end the game or show the lives left and put a new frog down
        if game.step(hop) == "dead":
            game.lives -= 1
            # The frog may be just off the screen; keep the splat on the LEDs
            x = max(0, min(7, game.frog[0]))
            sy = 7 - max(0, min(7, game.frog[1] - game.camera))
            for _ in range(3):
                game.draw(sense, frog=False)
                sense.set_pixel(x, sy, SPLAT)
                time.sleep(0.15)
                game.draw(sense, frog=False)
                time.sleep(0.15)
            if game.lives == 0:
                return "over", game.score
            sense.show_letter(str(game.lives), text_colour=FROG)
            time.sleep(0.6)
            game.respawn()

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
        sense.show_message(f"Score {score}", text_colour=FROG, scroll_speed=0.06)
        human = False   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
