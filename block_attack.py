#!/usr/bin/env python3
"""Sense HAT – Block Attack: swap blocks, make rows of three, set off chains.

A stack of coloured blocks keeps rising from the bottom. Your white-blinking
cursor covers two blocks side by side: swap them to line up 3 or more of the
same colour, across or up and down. Matched blocks flash and vanish, and the
blocks above fall down. If they form a new line of three, that's a chain,
worth a lot more points. The stack rises faster and faster: when it pushes
against the top, it's game over.

The computer plays a demo until you touch the joystick: then you play.
Move the cursor one block per push of the joystick, press to swap. After game over, or when you
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

# A "play" triangle (like on a music player), shown when a person takes over.
# "#" = green LED, "." = LED off.
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


# Colours are (R, G, B) tuples: red, green and blue, each from 0 (off) to 255 (full)
OFF = (0, 0, 0)             # An empty spot: LED off
# The block colours: red, yellow, green, light blue and purple. The grid stores a
# block as its number in this list (0-4), or None for an empty spot. Adding a colour
# makes the game harder, because matching three of the same gets less likely.
COLORS = [(255, 0, 0), (255, 220, 0), (0, 220, 0), (0, 180, 255), (200, 0, 255)]
CURSOR_ON = (255, 255, 255)   # An empty cursor cell blinks white ...
CURSOR_OFF = (90, 90, 90)     # ... and grey; a block under the cursor blinks brighter
FLASH = (255, 255, 255)     # Matched blocks flash white before they vanish
GAME_OVER = (80, 80, 80)    # The stack turns this grey when the game is lost

TICK = 0.02                 # Seconds per game step (50 steps per second)
RISE_DELAY = 7.0            # Seconds between new rows at the start (a calm start) ...
RISE_SPEEDUP = 0.98         # ... a little faster with every new row ...
MIN_RISE_DELAY = 1.0        # ... down to this
CURSOR_DELAY = 0.12         # Seconds between queued cursor steps
AI_CURSOR_DELAY = 0.28      # Seconds per cursor step for the computer at the start (calm) ...
AI_CURSOR_DELAY_FAST = 0.12 # ... speeding up to this as the stack rises faster
AI_THINK = 0.45             # Seconds the computer pauses on the spot before it swaps ...
AI_THINK_FAST = 0.1         # ... shrinking to this later in the game
AI_PANIC_HEIGHT = 7         # Stack this high (rows): the computer gets a short speed boost ...
AI_PANIC_SPEED = 0.5        # ... and plays twice as fast as its fastest pace until it's lower
FALL_STEP = 0.05            # Seconds per row for falling blocks
AI_SLOPPY = 0.1             # Chance the computer makes a random swap
AI_LOOKAHEAD = 12           # Set-up swaps the computer looks one move further into
AI_SETUP_WEIGHT = 0.7      # How much a set-up for the next swap is worth now
# (AI = "artificial intelligence", here simply: the computer player. A higher
# AI_SLOPPY makes it play worse and more human; a higher AI_LOOKAHEAD makes it
# smarter but slower to think.)


def find_matches(grid):
    """All cells that are part of 3 or more of the same colour in a row or column.

    Args:
        grid: the playing field, a list of 8 rows of 8 cells. grid[y][x] is the
            cell in row y (0 = top) and column x (0 = left); it holds a colour
            number (0-4) or None when empty.

    Returns:
        A set of (x, y) positions. A set never holds the same thing twice, so a
        block that is part of both a row and a column is only counted once.

    How it works: for every block it checks the two blocks to its right and the
    two blocks below it. A line of 4 or 5 is found as several overlapping lines
    of 3, which together cover the whole line.
    """
    found = set()
    for y in range(8):
        for x in range(8):
            color = grid[y][x]
            if color is None:
                continue
            # Same colour in this cell and the next two to the right? (x <= 5 makes
            # sure x + 2 is still on the 8-wide grid.) Then do the same downward.
            if x <= 5 and grid[y][x + 1] == color == grid[y][x + 2]:
                found.update({(x, y), (x + 1, y), (x + 2, y)})
            if y <= 5 and grid[y + 1][x] == color == grid[y + 2][x]:
                found.update({(x, y), (x, y + 1), (x, y + 2)})
    return found


def fall_one_row(grid):
    """Let every floating block drop one row. Returns True if anything moved.

    Args:
        grid: the playing field (see find_matches()); it is changed in place.

    Call it again and again until it returns False to let everything land.
    Doing one row at a time lets the game draw the blocks as they fall.
    """
    moved = False
    # Work from the second-lowest row (6) up to the top (0). Going bottom-up means
    # a block only moves into a gap that was already there at the start of this call,
    # so each block drops at most one row per call.
    for y in range(6, -1, -1):
        for x in range(8):
            if grid[y][x] is not None and grid[y + 1][x] is None:
                grid[y + 1][x], grid[y][x] = grid[y][x], None
                moved = True
    return moved


def safe_color(grid, x, y, check_below=False):
    """A colour that doesn't make 3 in a row with its neighbours to the left
    and above (or below, while building the stack from the bottom up).

    Args:
        grid: the playing field (see find_matches()); it is only read, not changed.
        x, y: the cell that is about to get a new block.
        check_below: also look at the two blocks below. Only needed for the
            starting stack, which is filled from the bottom row upward.

    Returns:
        A random colour number (0-4). New blocks should never match on their
        own, or the player would get free points without doing anything.

    How it works: if the two blocks to the left share a colour, that colour is
    banned, because a third one would make a line. The same goes for the two
    blocks above (and below). Then it picks at random from the colours left.
    """
    banned = set()
    if x >= 2 and grid[y][x - 1] is not None and grid[y][x - 1] == grid[y][x - 2]:
        banned.add(grid[y][x - 1])
    if y >= 2 and grid[y - 1][x] is not None and grid[y - 1][x] == grid[y - 2][x]:
        banned.add(grid[y - 1][x])
    if check_below and y <= 5 and grid[y + 1][x] is not None and grid[y + 1][x] == grid[y + 2][x]:
        banned.add(grid[y + 1][x])
    return random.choice([c for c in range(len(COLORS)) if c not in banned])


def settle_points(grid):
    """Like settle(), but scored like the real game: returns (points, chains, biggest match).

    The computer player uses this to try out a swap "in its head", without
    drawing or waiting: it lets the blocks fall, clears matches and repeats
    until nothing happens any more. It scores exactly like resolve() does.

    Args:
        grid: a playing field to settle; it is changed in place, so pass a copy.

    Returns:
        A tuple (points, chains, biggest):
        points: the points all the clears together would score.
        chains: how many rounds of clearing happened (0 = nothing cleared,
            1 = one match, 2 or more = a chain reaction).
        biggest: the most blocks cleared in one round.
    """
    points = chains = biggest = 0
    while True:
        # Let everything fall as far as it can (no drawing, so all at once)
        while fall_one_row(grid):
            pass
        matches = find_matches(grid)
        if not matches:
            return points, chains, biggest
        # Score this round: 10 points per block, times the chain number (so a
        # second round counts double, a third triple), plus 20 for each block
        # beyond 3 in one round
        chains += 1
        n = len(matches)
        points += n * 10 * chains + max(0, n - 3) * 20
        biggest = max(biggest, n)
        # Remove the matched blocks; the next loop lets the blocks above fall
        for x, y in matches:
            grid[y][x] = None


def stack_height(grid):
    """How high the stack is: the number of rows from the bottom up to the
    highest block (0 for an empty grid, 8 when a block touches the top).

    Args:
        grid: the playing field (see find_matches()).

    Returns:
        An int from 0 to 8. Row y counts as height 8 - y, so the bottom row
        (y = 7) is height 1 and the top row (y = 0) is height 8.
    """
    return max((8 - y for y in range(8) for x in range(8) if grid[y][x] is not None), default=0)


def swap_options(grid):
    """Every useful swap: yields (x, y, grid after the swap has settled, points, chains, biggest).

    Args:
        grid: the playing field (see find_matches()); it is not changed.

    Yields:
        One tuple per possible cursor position, where x, y is the left cell of
        the swap. "Yield" means this is a generator: it hands over one option at
        a time, so a loop can use each option as soon as it is worked out.

    Swapping two blocks of the same colour (or two empty cells) changes nothing,
    so those are skipped. Every other swap is tried on a copy of the grid.
    """
    for y in range(8):
        for x in range(7):
            if grid[y][x] == grid[y][x + 1]:     # also skips two empty cells
                continue
            # row[:] copies a row, so changes to `after` don't touch the real grid
            after = [row[:] for row in grid]
            after[y][x], after[y][x + 1] = after[y][x + 1], after[y][x]
            points, chains, biggest = settle_points(after)
            yield x, y, after, points, chains, biggest


def swap_value(points, chains, biggest):
    """What the computer likes: points, and extra for chains and matches of more than 3.

    Args:
        points, chains, biggest: the three numbers from settle_points().

    Returns:
        A score for how good the swap is. Every chain round after the first is
        worth 90 extra and every block beyond 3 in one match 45 extra, so the
        computer goes for the flashy moves instead of always the quick small one.
    """
    return points + max(0, chains - 1) * 90 + max(0, biggest - 3) * 45


class Game:
    """One game of Block Attack: the grid, the cursor, the score and the timers.

    It also contains the computer player (choose_swap() and ai_controls()),
    which only gets used when `human` is False.
    """

    def __init__(self, human):
        """Set up a fresh game.

        Args:
            human: True when a person plays with the joystick, False for the
                computer demo. It can change during the game (see play_game()).
        """
        self.human = human
        # Start with an empty 8x8 field: grid[y][x], with None for "no block"
        self.grid = [[None] * 8 for _ in range(8)]
        for x in range(8):                      # Start with an uneven stack, 2-4 high
            for y in range(7, 7 - random.randint(2, 4), -1):
                self.grid[y][x] = safe_color(self.grid, x, y, check_below=True)
        self.cursor = [3, 5]                    # Left cell of the 2-wide cursor
        self.score = 0
        self.rises = 0              # How many new rows have come up (makes it faster)
        self.rise_timer = 0.0       # Seconds since the last new row
        self.cursor_timer = 0.0     # Seconds since the cursor last moved
        self.want_move = None       # A cursor step (dx, dy) waiting to be made
        self.ticks = 0              # Game steps so far (used for the cursor blink)
        self.plan = None            # The computer's target swap (x, y), if it has one
        self.ai_wait = None         # Seconds the computer still waits before swapping

    def ai_pace(self, slow, fast):
        """A computer timing that keeps up with the game: `slow` at the start,
        moving towards `fast` as the stack rises faster (fast at full speed).
        When the stack nearly touches the top it gets a short speed boost."""
        progress = (RISE_DELAY - self.rise_delay()) / (RISE_DELAY - MIN_RISE_DELAY)  # 0 .. 1
        # Speed boost when the stack is about to reach the top: a burst of quick moves
        if stack_height(self.grid) >= AI_PANIC_HEIGHT:
            return fast * AI_PANIC_SPEED
        return slow + (fast - slow) * progress

    def rise_delay(self):
        """Seconds to wait before the next row comes up.

        It starts at RISE_DELAY and is multiplied by RISE_SPEEDUP (0.98) once for
        every row so far, so the wait gets 2% shorter each time, but never shorter
        than MIN_RISE_DELAY. After about 75 rows it reaches that fastest speed.
        """
        return max(MIN_RISE_DELAY, RISE_DELAY * RISE_SPEEDUP ** self.rises)

    def rise(self):
        """Push a new row in from the bottom. Returns False when the stack hits the top.

        Everything moves up one row, and the cursor and the computer's plan move
        along with the blocks, so they keep pointing at the same blocks.
        """
        # A block in the top row has nowhere to go: game over
        if any(self.grid[0][x] is not None for x in range(8)):
            return False
        # Drop the top row (it is empty) and add an empty row at the bottom ...
        self.grid = self.grid[1:] + [[None] * 8]
        # ... then fill it with new blocks that don't make a match by themselves
        for x in range(8):
            self.grid[7][x] = safe_color(self.grid, x, 7)
        # Move the cursor up with the blocks (but not off the top)
        self.cursor[1] = max(0, self.cursor[1] - 1)
        # The computer's target moved up too; if it went off the top, forget it
        if self.plan:
            self.plan = (self.plan[0], self.plan[1] - 1)
            if self.plan[1] < 0:
                self.plan = None
                self.ai_wait = None
        self.rises += 1
        return True

    def swap(self):
        """Swap the two blocks under the cursor (a block may also swap with an empty spot)."""
        x, y = self.cursor
        row = self.grid[y]
        row[x], row[x + 1] = row[x + 1], row[x]

    # ---------- the computer player ----------
    def choose_swap(self):
        """The computer's pick. It prefers matches of more than 3 and chains, and it
        looks one swap ahead: a swap that clears nothing now can set up a big clear
        or a chain for the next swap.

        Returns:
            (x, y): the left cell of the swap the computer wants to make.

        How it decides: it gives every possible swap a value and picks the
        highest. The value is:
        - swap_value(): the points the swap scores, with bonuses for chains and
          big matches;
        - minus 6 for every row the stack gets taller (a swap that drops a block
          into a hole can make the stack lower, which then counts as a plus);
        - minus a little for distance from the cursor, so between two equal
          swaps it takes the nearby one and wastes less time walking;
        - plus a random bit below 1, so equal choices aren't always the same
          and the demo looks more alive.
        """
        height = stack_height(self.grid)
        options = []
        # Step 1: judge every swap on what it does right now
        for x, y, after, points, chains, biggest in swap_options(self.grid):
            value = (swap_value(points, chains, biggest) - (stack_height(after) - height) * 6
                     - abs(x - self.cursor[0]) * 0.3 - abs(y - self.cursor[1]) * 0.3
                     + random.random())
            options.append([value, x, y, after, points])
        # Step 2: look one swap ahead, but only for swaps that score nothing now
        # ("set-ups"). For a random handful of them (AI_LOOKAHEAD, to keep the
        # thinking fast) it tries every swap that could follow, and adds part of
        # the best follow-up's value. A set-up for a big chain can then beat a
        # small match right now.
        setups = [o for o in options if o[4] == 0]
        random.shuffle(setups)
        for option in setups[:AI_LOOKAHEAD]:
            follow = max((swap_value(p, c, b) for _, _, _, p, c, b in swap_options(option[3])),
                         default=0)
            option[0] += follow * AI_SETUP_WEIGHT
        # Step 3: now and then (AI_SLOPPY), or when there is nothing to swap at all,
        # make a random swap somewhere in the lower part of the field instead
        if not options or random.random() < AI_SLOPPY:
            return random.randint(0, 6), random.randint(3, 7)
        # Otherwise pick the option with the highest value
        best = max(options, key=lambda o: o[0])
        return best[1], best[2]

    def ai_controls(self):
        """Returns (move, swap) like the joystick would.

        This makes the computer play with the same "buttons" as a person:
        it walks the cursor to its planned spot one step at a time, then swaps.

        Returns:
            A tuple (move, swap). move is a step (dx, dy) such as (1, 0) for
            "right", or None for no step; swap is True when it wants to swap now.
        """
        # No target yet? Think of one
        if self.plan is None:
            self.plan = self.choose_swap()
        x, y = self.plan
        cx, cy = self.cursor
        # Arrived: (after a short pause) swap, and think of a new target next time
        if (cx, cy) == (x, y):
            # Pause a moment on the spot first, like a person checking the move,
            # so the demo is easy to follow
            if self.ai_wait is None:
                self.ai_wait = self.ai_pace(AI_THINK, AI_THINK_FAST)
            self.ai_wait -= TICK
            if self.ai_wait > 0:
                return None, False
            self.plan = None
            self.ai_wait = None
            return None, True
        # Walk sideways first, then up or down. (x > cx) - (x < cx) is a short way
        # to get 1 (target is to the right), -1 (to the left) or 0 (same column).
        if cx != x:
            return ((x > cx) - (x < cx), 0), False
        return (0, (y > cy) - (y < cy)), False

    # ---------- one game step ----------
    def step(self, move, swap, cursor_delay):
        """Advance one tick. Returns "swap", "over" or None.

        Args:
            move: a cursor step (dx, dy) to make, or None.
            swap: True to swap the two blocks under the cursor.
            cursor_delay: the least number of seconds between two cursor steps.

        Returns:
            "swap" when blocks were swapped (the caller then clears matches),
            "over" when the stack hit the top, or None when nothing special happened.
        """
        self.ticks += 1
        # Remember the wanted step; it is made as soon as the cursor timer allows
        if move:
            self.want_move = move
        # Move the cursor, but no more often than once per cursor_delay seconds.
        # (The tiny "- 1e-9" stops rounding errors in adding up 0.02s from making
        # it wait one tick too long.) min/max keep the 2-wide cursor on the grid:
        # its left cell can be in columns 0-6.
        self.cursor_timer += TICK
        if self.want_move and self.cursor_timer >= cursor_delay - 1e-9:
            self.cursor_timer = 0.0
            dx, dy = self.want_move
            self.cursor[0] = max(0, min(6, self.cursor[0] + dx))
            self.cursor[1] = max(0, min(7, self.cursor[1] + dy))
            self.want_move = None
        # Time for a new row from the bottom? If there's no room left, the game is over
        self.rise_timer += TICK
        if self.rise_timer >= self.rise_delay():
            self.rise_timer = 0.0
            if not self.rise():
                return "over"
        if swap:
            self.swap()
            return "swap"
        return None

    def draw(self, sense, flash=(), cursor=True):
        """Show the grid on the LEDs.

        Args:
            sense: the SenseHat object to draw on.
            flash: (x, y) positions to light up white, for matched blocks.
            cursor: False hides the cursor (used while matches flash).
        """
        # Turn every cell into an LED colour: its block colour, or off when empty
        frame = [[COLORS[c] if c is not None else OFF for c in row] for row in self.grid]
        for x, y in flash:
            frame[y][x] = FLASH
        if cursor:                                       # The cursor blinks
            # ticks // 12 goes up by one every 12 steps (0.24 s); switching on even
            # and odd numbers makes the cursor blink about twice a second
            on = (self.ticks // 12) % 2 == 0
            cx, cy = self.cursor
            for x in (cx, cx + 1):
                c = self.grid[cy][x]
                if c is None:
                    frame[cy][x] = CURSOR_ON if on else CURSOR_OFF
                elif on:
                    # A block under the cursor is mixed halfway with white, which
                    # makes it lighter but still shows its colour
                    frame[cy][x] = tuple((v + 255) // 2 for v in COLORS[c])
        sense.set_pixels(flatten(frame))


def resolve(sense, game):
    """After a swap: let blocks fall, clear matches and keep going for chains.

    Args:
        sense: the SenseHat object to draw on.
        game: the Game whose grid and score are updated.

    This is the visible version of settle_points(): it shows the blocks falling
    and the matches flashing, and adds the points to game.score. While it runs,
    the game is paused (the stack does not rise). Returns nothing.
    """
    chain = 0
    while True:
        # Let the blocks fall one row at a time, drawing each step so you see it
        while fall_one_row(game.grid):
            game.draw(sense)
            time.sleep(FALL_STEP)
        matches = find_matches(game.grid)
        if not matches:
            return
        # Every round of matches in a row makes the chain one longer
        chain += 1
        # Flash the matched blocks white three times (about half a second)
        for _ in range(3):
            game.draw(sense, flash=matches, cursor=False)
            time.sleep(0.08)
            game.draw(sense, cursor=False)
            time.sleep(0.08)
        # Remove them and score: 10 per block times the chain number, plus 20 for
        # every block beyond 3. Then loop: blocks above the gap fall and may match again.
        for x, y in matches:
            game.grid[y][x] = None
        game.score += len(matches) * 10 * chain + max(0, len(matches) - 3) * 20


def joystick_controls(stick, pressed):
    """Turn joystick presses into game controls.

    Args:
        stick: the Joystick (not used here, but kept so it matches the other controls).
        pressed: the list of new presses from Joystick.read().

    Returns:
        A tuple (move, swap): move is a cursor step (dx, dy) or None, and swap is
        True when the joystick was pushed in ("middle"). Up is dy = -1, because
        row 0 is at the top of the display.
    """
    keys = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
    # One step per press: holding a direction does not keep the cursor moving
    move = next((keys[k] for k in pressed if k in keys), None)
    return move, "middle" in pressed


def play_game(sense, stick, human):
    """Play one game. Returns (result, score): result is "over", or "start" when
    someone touches the joystick during the demo.

    Args:
        sense: the SenseHat object to draw on.
        stick: the Joystick to read.
        human: True when a person plays, False for the computer demo.

    Returns:
        ("over", score) when the stack reached the top, or ("start", 0) when the
        demo was interrupted because someone wants to play.

    Each loop is one game step of TICK seconds: read the joystick, ask the person
    or the computer what to do, advance the game, handle swaps and game over,
    then draw.
    """
    game = Game(human)
    while True:
        # Input: during the demo, any press stops it so a real game can start
        pressed = stick.read()
        if not game.human and pressed:
            return "start", 0
        if game.human and stick.idle():      # Player walked away: computer takes over
            game.human = False
            game.plan = None

        # Decide this step's move and swap: from the joystick, or from the computer.
        # Both go through game.step(); only the cursor speed differs.
        if game.human:
            move, swap = joystick_controls(stick, pressed)
            if move and any(k in pressed for k in ("up", "down", "left", "right")):
                game.cursor_timer = CURSOR_DELAY      # A tap moves right away
            result = game.step(move, swap, CURSOR_DELAY)
        else:
            move, swap = game.ai_controls()
            result = game.step(move, swap, game.ai_pace(AI_CURSOR_DELAY, AI_CURSOR_DELAY_FAST))

        # After a swap, play out the falling and clearing; on game over, end the game
        if result == "swap":
            resolve(sense, game)
        elif result == "over":
            for y in range(8):                 # Game over: the stack turns grey
                for x in range(8):
                    if game.grid[y][x] is not None:
                        sense.set_pixel(x, y, GAME_OVER)
                time.sleep(0.08)
            return "over", game.score

        # Draw the new situation and wait for the next step
        game.draw(sense)
        time.sleep(TICK)


def main():
    """Run Block Attack forever: a computer demo, real games when someone plays.

    Takes no arguments and never returns (stop it with Ctrl+C). It starts with
    the demo. When the demo is interrupted it shows the play sign and starts a
    game for the person. After every game over it scrolls the score across the
    display and goes back to the demo.
    """
    sense = init(hat)
    stick = Joystick(sense)
    human = False
    while True:
        result, score = play_game(sense, stick, human)
        # Someone touched the joystick during the demo: start a real game for them
        if result == "start":
            show_play_sign(sense)
            stick.flush()
            human = True
            continue
        # Game over: scroll the score across the display in yellow
        sense.show_message(f"Score {score}", text_colour=COLORS[1], scroll_speed=0.06)
        human = False   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
