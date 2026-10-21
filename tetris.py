#!/usr/bin/env python3
"""Sense HAT – Tetris: stack the falling blocks and clear full rows.

The seven classic pieces fall into an 8x8 well. Move and rotate them so they
fill complete rows: full rows flash and disappear. The more rows you clear at
once, the more points; every 5 rows the pieces fall faster. When a new piece
has no room left at the top, it's game over.

The computer plays a demo until you touch the joystick: then you play.
Left and right to move, up to rotate, down to drop faster, press to drop the
piece straight down. After game over, or when you leave the joystick alone for
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


OFF = (0, 0, 0)
FLASH = (255, 255, 255)

# The seven pieces: their cells in the spawn position, and their colour
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
FALL_DELAY = 0.7            # Seconds per row at the start ...
FALL_SPEEDUP = 0.88         # ... a bit faster every 5 cleared rows ...
MIN_FALL_DELAY = 0.12       # ... down to this
SOFT_DROP_DELAY = 0.05      # Holding down
AI_DROP_DELAY = 0.25        # How fast the computer lets a piece fall once it's in place
MOVE_DELAY = 0.1            # Seconds per sideways step or rotation
LINE_POINTS = [0, 10, 30, 60, 100]
AI_SLOPPY = 0.05            # Chance the computer picks a less good spot


def normalise(cells):
    min_x = min(x for x, _ in cells)
    min_y = min(y for _, y in cells)
    return tuple(sorted((x - min_x, y - min_y) for x, y in cells))


def rotations(cells):
    """All different rotations of a piece, starting with the spawn position."""
    result = []
    shape = normalise(cells)
    for _ in range(4):
        if shape not in result:
            result.append(shape)
        shape = normalise([(-y, x) for x, y in shape])
    return result


ROTATIONS = {name: rotations(cells) for name, (cells, _) in PIECES.items()}


class Game:
    def __init__(self, human):
        self.human = human
        self.board = {}             # (x, y) -> colour of the blocks that landed
        self.score = 0
        self.lines = 0
        self.timers = {"fall": 0.0, "move": 0.0}
        self.plan = None
        self.over = not self.new_piece()

    # ---------- pieces ----------
    def cells(self, rotation=None, x=None, y=None):
        rotation = self.rotation if rotation is None else rotation
        x = self.x if x is None else x
        y = self.y if y is None else y
        return [(x + cx, y + cy) for cx, cy in ROTATIONS[self.piece][rotation]]

    def fits(self, rotation, x, y, board=None):
        board = self.board if board is None else board
        return all(0 <= cx <= 7 and cy <= 7 and (cx, cy) not in board
                   for cx, cy in self.cells(rotation, x, y))

    def new_piece(self):
        """Bring in the next piece. Returns False when there's no room: game over."""
        self.piece = random.choice(list(PIECES))
        self.rotation = 0
        width = max(x for x, _ in ROTATIONS[self.piece][0]) + 1
        self.x = (8 - width) // 2
        self.y = 0
        self.plan = None
        return self.fits(self.rotation, self.x, self.y)

    def fall_delay(self):
        return max(MIN_FALL_DELAY, FALL_DELAY * FALL_SPEEDUP ** (self.lines // 5))

    def try_move(self, dx=0, rotate=False):
        rotation = (self.rotation + 1) % len(ROTATIONS[self.piece]) if rotate else self.rotation
        for kick in ((0, 1, -1) if rotate else (0,)):      # Rotating next to a wall: shift a bit
            if self.fits(rotation, self.x + dx + kick, self.y):
                self.rotation, self.x = rotation, self.x + dx + kick
                return True
        return False

    def drop_y(self, rotation, x, board=None):
        y = 0
        while self.fits(rotation, x, y + 1, board):
            y += 1
        return y

    def lock(self):
        """The piece lands. Returns the full rows (not yet removed)."""
        color = PIECES[self.piece][1]
        for cell in self.cells():
            self.board[cell] = color
        return [y for y in range(8) if all((x, y) in self.board for x in range(8))]

    def clear_rows(self, rows):
        for row in sorted(rows):
            self.board = {(x, y + 1 if y < row else y): c
                          for (x, y), c in self.board.items() if y != row}
        self.lines += len(rows)
        self.score += LINE_POINTS[len(rows)]

    # ---------- the computer player ----------
    def evaluate(self, board):
        heights = [8 - min((y for (x2, y) in board if x2 == x), default=8) for x in range(8)]
        holes = 0                   # Empty cells with a block somewhere above them
        for x in range(8):
            top = 8 - heights[x]
            holes += sum(1 for y in range(top + 1, 8) if (x, y) not in board)
        bumps = sum(abs(heights[i] - heights[i + 1]) for i in range(7))
        return -0.3 * sum(heights) - 2.5 * holes - 0.5 * bumps - 1.5 * max(heights) ** 2 / 8

    def make_plan(self):
        """Try every rotation and column; remember where the piece fits best."""
        choices = []
        for rotation in range(len(ROTATIONS[self.piece])):
            for x in range(-1, 8):
                if not self.fits(rotation, x, 0):
                    continue
                y = self.drop_y(rotation, x)
                board = dict(self.board)
                for cell in self.cells(rotation, x, y):
                    board[cell] = 1
                full = [r for r in range(8) if all((c, r) in board for c in range(8))]
                for row in sorted(full):
                    board = {(cx, cy + 1 if cy < row else cy): v
                             for (cx, cy), v in board.items() if cy != row}
                choices.append((self.evaluate(board) + 3.5 * len(full), rotation, x))
        choices.sort(reverse=True)
        pick = 1 if len(choices) > 1 and random.random() < AI_SLOPPY else 0
        _, rotation, x = choices[pick]
        self.plan = (rotation, x)

    def ai_controls(self):
        """Returns (dx, rotate, soft_drop, hard_drop) like the joystick would."""
        if self.plan is None:
            self.make_plan()
        rotation, x = self.plan
        if self.rotation != rotation:
            return 0, True, False, False
        if self.x != x:
            return (x > self.x) - (x < self.x), False, False, False
        return 0, False, True, False

    # ---------- one game step ----------
    def step(self, controls):
        """Advance one tick. Returns the full rows when a piece landed, else None."""
        dx, rotate, soft_drop, hard_drop = controls
        if hard_drop:
            self.y = self.drop_y(self.rotation, self.x)
            return self.land()
        self.timers["move"] += TICK
        if (dx or rotate) and self.timers["move"] >= MOVE_DELAY - 1e-9:
            self.timers["move"] = 0.0
            if rotate:
                self.try_move(rotate=True)
            elif dx:
                self.try_move(dx)
        self.timers["fall"] += TICK
        delay = self.fall_delay()
        if soft_drop:
            delay = min(delay, SOFT_DROP_DELAY if self.human else AI_DROP_DELAY)
        if self.timers["fall"] >= delay - 1e-9:
            self.timers["fall"] = 0.0
            if self.fits(self.rotation, self.x, self.y + 1):
                self.y += 1
            else:
                return self.land()
        return None

    def land(self):
        self.timers["fall"] = 0.0
        return self.lock()

    def draw(self, sense, piece=True, flash_rows=()):
        frame = [[OFF] * 8 for _ in range(8)]
        for (x, y), color in self.board.items():
            frame[y][x] = FLASH if y in flash_rows else color
        if piece:
            for x, y in self.cells():
                if 0 <= y <= 7:
                    frame[y][x] = PIECES[self.piece][1]
        sense.set_pixels(flatten(frame))


def joystick_controls(stick, pressed):
    dx = (("right" in stick.held or "right" in pressed)
          - ("left" in stick.held or "left" in pressed))
    rotate = "up" in pressed
    soft_drop = "down" in stick.held or "down" in pressed
    hard_drop = "middle" in pressed
    return dx, rotate, soft_drop, hard_drop


def play_game(sense, stick, human):
    """Play one game. Returns (result, score): result is "over", or "start" when
    someone touches the joystick during the demo."""
    game = Game(human)
    while not game.over:
        pressed = stick.read()
        if not game.human and pressed:
            return "start", 0
        if game.human and stick.idle():      # Player walked away: computer takes over
            game.human = False

        controls = joystick_controls(stick, pressed) if game.human else game.ai_controls()
        if game.human and "up" in pressed:
            game.timers["move"] = MOVE_DELAY   # Rotate right away on every press
        full_rows = game.step(controls)

        if full_rows is not None:
            if full_rows:
                for _ in range(3):
                    game.draw(sense, piece=False, flash_rows=full_rows)
                    time.sleep(0.1)
                    game.draw(sense, piece=False)
                    time.sleep(0.1)
                game.clear_rows(full_rows)
            game.over = not game.new_piece()

        game.draw(sense, piece=not game.over)
        time.sleep(TICK)

    for y in range(7, -1, -1):                 # Game over: fill the well from the bottom
        for x in range(8):
            game.board[(x, y)] = (80, 80, 80)
        game.draw(sense, piece=False)
        time.sleep(0.06)
    return "over", game.score


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
        sense.show_message(f"Score {score}", text_colour=PIECES["I"][1], scroll_speed=0.06)
        human = False   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
