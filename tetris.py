#!/usr/bin/env python3
"""Sense HAT – Tetris: stack the falling blocks and clear full rows.

The seven classic pieces fall into an 8x8 well. Move and rotate them so they
fill complete rows: full rows flash and disappear. The more rows you clear at
once, the more points; every 5 rows the pieces fall faster. When a new piece
has no room left at the top, it's game over.

The computer plays a demo until you touch the joystick: then you play.
Left and right to move, press (the A button on a gamepad) to rotate, down to
drop faster, up to drop the piece straight down. After game over, or when you leave the joystick alone for
a while, the computer takes over again.
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


OFF = (0, 0, 0)            # An empty cell of the well
FLASH = (255, 255, 255)    # Colour of full rows while they flash before disappearing

# The seven pieces: their cells in the spawn position, and their colour.
# Each cell is (x, y): x counts to the right, y counts downwards from the top.
PIECES = {
    "I": ([(0, 0), (1, 0), (2, 0), (3, 0)], (0, 255, 255)),
    "O": ([(0, 0), (1, 0), (0, 1), (1, 1)], (255, 255, 0)),
    "T": ([(1, 0), (0, 1), (1, 1), (2, 1)], (180, 0, 255)),
    "S": ([(1, 0), (2, 0), (0, 1), (1, 1)], (0, 255, 0)),
    "Z": ([(0, 0), (1, 0), (1, 1), (2, 1)], (255, 0, 0)),
    "J": ([(0, 0), (0, 1), (1, 1), (2, 1)], (0, 80, 255)),
    "L": ([(2, 0), (0, 1), (1, 1), (2, 1)], (255, 120, 0)),
}

TICK = 0.02                 # Seconds per game step
FALL_DELAY = 1.0            # Seconds per row at the start ...
FALL_SPEEDUP = 0.92         # ... a bit faster every 5 cleared rows ...
MIN_FALL_DELAY = 0.25       # ... down to this
SOFT_DROP_DELAY = 0.05      # Seconds per row while you hold the joystick down
AI_DROP_DELAY = 0.25        # How fast the computer lets a piece fall once it's in place
MOVE_DELAY = 0.1            # Seconds per sideways step or rotation
LINE_POINTS = [0, 10, 30, 60, 100]   # Points for clearing 0, 1, 2, 3 or 4 rows at once
AI_SLOPPY = 0.05            # Chance the computer picks a less good spot


def normalise(cells):
    """Shift a shape so it touches the top-left corner, and sort its cells.

    `cells` is a list of (x, y) pairs. Returns a sorted tuple of (x, y) pairs whose
    smallest x and smallest y are both 0. Two shapes that look the same then also
    compare as equal, which rotations() uses to skip duplicate rotations.
    """
    min_x = min(x for x, _ in cells)
    min_y = min(y for _, y in cells)
    return tuple(sorted((x - min_x, y - min_y) for x, y in cells))


def rotations(cells):
    """All different rotations of a piece, starting with the spawn position.

    Rotating a quarter turn turns every cell (x, y) into (-y, x). This is done
    four times; rotations that look the same as an earlier one are left out.
    So the "O" gets 1 rotation, "I", "S" and "Z" get 2, and "T", "J" and "L" get 4.
    Returns a list of normalised shapes.
    """
    result = []
    shape = normalise(cells)
    for _ in range(4):
        if shape not in result:
            result.append(shape)
        shape = normalise([(-y, x) for x, y in shape])
    return result


# Worked out once at the start: piece name -> list of its rotations
ROTATIONS = {name: rotations(cells) for name, (cells, _) in PIECES.items()}


class Game:
    """One game of Tetris: the well, the falling piece, the score and the computer player.

    The well is stored as a dictionary `board` that maps (x, y) to the colour of a
    block that has landed there; empty cells are simply not in it. The falling
    piece is described by its name (`piece`), which of its rotations it is in
    (`rotation`) and the position of its top-left corner (`x`, `y`).
    """

    def __init__(self, human):
        """Start a new game; `human` is True when a person plays, False for the demo."""
        self.human = human
        self.board = {}             # (x, y) -> colour of the blocks that landed
        self.score = 0
        self.lines = 0              # Total number of rows cleared (sets the speed)
        self.timers = {"fall": 0.0, "move": 0.0}   # Seconds since the last fall / move
        self.plan = None            # The computer's chosen (rotation, x) for this piece
        self.over = not self.new_piece()

    # ---------- pieces ----------
    def cells(self, rotation=None, x=None, y=None):
        """The board cells the current piece covers, as a list of (x, y) pairs.

        Without arguments it uses the piece's real rotation and position. Pass a
        different rotation, x or y to ask "where would it be if ...?", which is
        handy for testing moves before making them.
        """
        rotation = self.rotation if rotation is None else rotation
        x = self.x if x is None else x
        y = self.y if y is None else y
        return [(x + cx, y + cy) for cx, cy in ROTATIONS[self.piece][rotation]]

    def fits(self, rotation, x, y, board=None):
        """True when the current piece in this rotation and position fits.

        Fitting means: inside the left and right walls, not below the floor, and
        not overlapping a block that already landed. Cells above the top (y < 0)
        are allowed. `board` lets the computer player test on a pretend board.
        """
        board = self.board if board is None else board
        return all(0 <= cx <= 7 and cy <= 7 and (cx, cy) not in board
                   for cx, cy in self.cells(rotation, x, y))

    def new_piece(self):
        """Bring in the next piece. Returns False when there's no room: game over."""
        # Pick a random piece in its spawn rotation ...
        self.piece = random.choice(list(PIECES))
        self.rotation = 0
        # ... and put it at the top, in the middle of the 8 columns
        width = max(x for x, _ in ROTATIONS[self.piece][0]) + 1
        self.x = (8 - width) // 2
        self.y = 0
        # The computer has to think again about where this new piece should go
        self.plan = None
        return self.fits(self.rotation, self.x, self.y)

    def fall_delay(self):
        """Seconds the piece waits before dropping one row.

        Starts at FALL_DELAY and is multiplied by FALL_SPEEDUP for every 5 rows
        cleared, so the game gets faster, but never faster than MIN_FALL_DELAY.
        """
        return max(MIN_FALL_DELAY, FALL_DELAY * FALL_SPEEDUP ** (self.lines // 5))

    def try_move(self, dx=0, rotate=False):
        """Move the piece `dx` columns sideways, or rotate it a quarter turn.

        Returns True when it worked, False when the piece doesn't fit there (then
        nothing changes). When rotating against a wall or another block, it also
        tries shifting the piece one column right or left (a "wall kick"), so a
        rotation next to the edge doesn't just fail.
        """
        rotation = (self.rotation + 1) % len(ROTATIONS[self.piece]) if rotate else self.rotation
        for kick in ((0, 1, -1) if rotate else (0,)):      # Rotating next to a wall: shift a bit
            if self.fits(rotation, self.x + dx + kick, self.y):
                self.rotation, self.x = rotation, self.x + dx + kick
                return True
        return False

    def drop_y(self, rotation, x, board=None):
        """The lowest row the piece can fall to in this rotation and column.

        Starts at the top and keeps moving down one row as long as it still fits.
        Returns that y; used for a hard drop and by the computer player.
        """
        y = 0
        while self.fits(rotation, x, y + 1, board):
            y += 1
        return y

    def lock(self):
        """The piece lands. Returns the full rows (not yet removed)."""
        # Copy the piece's cells into the board in the piece's colour
        color = PIECES[self.piece][1]
        for cell in self.cells():
            self.board[cell] = color
        # A row is full when all 8 of its cells are in the board
        return [y for y in range(8) if all((x, y) in self.board for x in range(8))]

    def clear_rows(self, rows):
        """Remove the full rows, let everything above them drop, and add points.

        `rows` is a list of row numbers. For each full row (top one first), the
        blocks in that row are thrown away and every block above it moves one row
        down. The score goes up by LINE_POINTS for the number of rows cleared.
        """
        for row in sorted(rows):
            self.board = {(x, y + 1 if y < row else y): c
                          for (x, y), c in self.board.items() if y != row}
        self.lines += len(rows)
        self.score += LINE_POINTS[len(rows)]

    # ---------- the computer player ----------
    def evaluate(self, board):
        """Give a board a score: higher means a nicer board to keep playing on.

        Returns a number (usually negative) that counts against:
        - the total height of all columns (a tall stack is dangerous),
        - holes: empty cells with a block above them (very hard to fill later),
        - bumpiness: big height differences between neighbouring columns,
        - the tallest column, extra hard as it gets close to the top.
        The numbers in front of each part say how much the computer cares about it.
        """
        # Height of each column: 0 when empty, 8 when it reaches the top
        heights = [8 - min((y for (x2, y) in board if x2 == x), default=8) for x in range(8)]
        holes = 0                   # Empty cells with a block somewhere above them
        for x in range(8):
            top = 8 - heights[x]
            holes += sum(1 for y in range(top + 1, 8) if (x, y) not in board)
        bumps = sum(abs(heights[i] - heights[i + 1]) for i in range(7))
        return -0.3 * sum(heights) - 2.5 * holes - 0.5 * bumps - 1.5 * max(heights) ** 2 / 8

    def make_plan(self):
        """Try every rotation and column; remember where the piece fits best.

        For each possible spot, the computer drops the piece on a copy of the
        board, removes any full rows, and scores the result with evaluate() plus
        a bonus for each cleared row. The best spot is stored in `self.plan` as
        (rotation, x). Now and then (AI_SLOPPY) it takes the second best instead,
        so the demo doesn't play perfectly.
        """
        choices = []
        for rotation in range(len(ROTATIONS[self.piece])):
            # Try every column; spots where the piece doesn't fit at the top are skipped
            for x in range(-1, 8):
                if not self.fits(rotation, x, 0):
                    continue
                # Drop the piece there on a pretend copy of the board
                y = self.drop_y(rotation, x)
                board = dict(self.board)
                for cell in self.cells(rotation, x, y):
                    board[cell] = 1
                # Remove full rows on the copy, just like clear_rows() would
                full = [r for r in range(8) if all((c, r) in board for c in range(8))]
                for row in sorted(full):
                    board = {(cx, cy + 1 if cy < row else cy): v
                             for (cx, cy), v in board.items() if cy != row}
                choices.append((self.evaluate(board) + 3.5 * len(full), rotation, x))
        # Best score first; occasionally pick the runner-up on purpose
        choices.sort(reverse=True)
        pick = 1 if len(choices) > 1 and random.random() < AI_SLOPPY else 0
        _, rotation, x = choices[pick]
        self.plan = (rotation, x)

    def ai_controls(self):
        """Returns (dx, rotate, soft_drop, hard_drop) like the joystick would.

        The computer first makes a plan for the new piece, then "pushes" the
        joystick step by step: rotate until the rotation is right, then move
        sideways towards the planned column, then let the piece fall faster.
        """
        if self.plan is None:
            self.make_plan()
        rotation, x = self.plan
        if self.rotation != rotation:
            return 0, True, False, False
        if self.x != x:
            # (x > self.x) - (x < self.x) is +1 (go right), -1 (go left) or 0
            return (x > self.x) - (x < self.x), False, False, False
        return 0, False, True, False

    # ---------- one game step ----------
    def step(self, controls):
        """Advance one tick. Returns the full rows when a piece landed, else None.

        `controls` is (dx, rotate, soft_drop, hard_drop) from the joystick or the
        computer. The returned list can be empty: the piece landed without filling
        a row. Moving and falling each have their own timer, so they happen at
        their own speed no matter how often step() is called.
        """
        dx, rotate, soft_drop, hard_drop = controls
        # Hard drop: put the piece straight at the bottom and land it right away
        if hard_drop:
            self.y = self.drop_y(self.rotation, self.x)
            return self.land()
        # Sideways moves and rotations: at most one every MOVE_DELAY seconds
        # (the tiny 1e-9 avoids rounding errors when adding up the ticks)
        self.timers["move"] += TICK
        if (dx or rotate) and self.timers["move"] >= MOVE_DELAY - 1e-9:
            self.timers["move"] = 0.0
            if rotate:
                self.try_move(rotate=True)
            elif dx:
                self.try_move(dx)
        # Falling: one row every fall_delay() seconds, faster while soft dropping
        self.timers["fall"] += TICK
        delay = self.fall_delay()
        if soft_drop:
            delay = min(delay, SOFT_DROP_DELAY if self.human else AI_DROP_DELAY)
        if self.timers["fall"] >= delay - 1e-9:
            self.timers["fall"] = 0.0
            # Move down when there is room, otherwise the piece has landed
            if self.fits(self.rotation, self.x, self.y + 1):
                self.y += 1
            else:
                return self.land()
        return None

    def land(self):
        """Reset the fall timer and lock the piece; returns the full rows."""
        self.timers["fall"] = 0.0
        return self.lock()

    def draw(self, sense, piece=True, flash_rows=()):
        """Show the well on the LEDs.

        `piece`: also draw the falling piece. `flash_rows`: rows to show in white
        instead of their own colours (used for the flashing of full rows).
        """
        # Start with an empty well, then paint the landed blocks
        frame = [[OFF] * 8 for _ in range(8)]
        for (x, y), color in self.board.items():
            frame[y][x] = FLASH if y in flash_rows else color
        # Paint the falling piece on top; parts above the top edge are skipped
        if piece:
            for x, y in self.cells():
                if 0 <= y <= 7:
                    frame[y][x] = PIECES[self.piece][1]
        sense.set_pixels(flatten(frame))


def joystick_controls(stick, pressed):
    """Turn the joystick state into (dx, rotate, soft_drop, hard_drop).

    `pressed` is the list from stick.read(). Sideways and down also work while
    held, so you can keep the joystick pushed; rotate and hard drop only react to
    a new press, so one push does exactly one rotation or drop.
    """
    # True/False count as 1/0, so right gives +1, left gives -1, both or none 0
    dx = (("right" in stick.held or "right" in pressed)
          - ("left" in stick.held or "left" in pressed))
    rotate = "middle" in pressed            # Press (A on a gamepad) rotates
    soft_drop = "down" in stick.held or "down" in pressed
    hard_drop = "up" in pressed
    return dx, rotate, soft_drop, hard_drop


def play_game(sense, stick, human):
    """Play one game. Returns (result, score): result is "over", or "start" when
    someone touches the joystick during the demo."""
    game = Game(human)
    while not game.over:
        # Input: in the demo any press means someone wants to play
        pressed = stick.read()
        if not game.human and pressed:
            return "start", 0
        if game.human and stick.idle():      # Player walked away: computer takes over
            game.human = False

        # Get this tick's controls from the player or the computer, then run a step
        controls = joystick_controls(stick, pressed) if game.human else game.ai_controls()
        if game.human and "middle" in pressed:
            game.timers["move"] = MOVE_DELAY   # Rotate right away on every press
        full_rows = game.step(controls)

        # A piece landed: flash any full rows three times, remove them, and bring
        # in the next piece (no room for it means game over)
        if full_rows is not None:
            if full_rows:
                for _ in range(3):
                    game.draw(sense, piece=False, flash_rows=full_rows)
                    time.sleep(0.1)
                    game.draw(sense, piece=False)
                    time.sleep(0.1)
                game.clear_rows(full_rows)
            game.over = not game.new_piece()

        # Show the result and wait for the next tick
        game.draw(sense, piece=not game.over)
        time.sleep(TICK)

    for y in range(7, -1, -1):                 # Game over: fill the well from the bottom
        for x in range(8):
            game.board[(x, y)] = (80, 80, 80)
        game.draw(sense, piece=False)
        time.sleep(0.06)
    return "over", game.score


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
        sense.show_message(f"Score {score}", text_colour=PIECES["I"][1], scroll_speed=0.06)
        human = False   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
