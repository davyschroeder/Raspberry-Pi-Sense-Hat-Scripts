#!/usr/bin/env python3
"""Sense HAT – Block Attack: swap blocks, make rows of three, set off chains.

A stack of coloured blocks keeps rising from the bottom. Your white-blinking
cursor covers two blocks side by side: swap them to line up 3 or more of the
same colour, across or up and down. Matched blocks flash and vanish, and the
blocks above fall down. If they form a new line of three, that's a chain,
worth a lot more points. The stack rises faster and faster: when it pushes
against the top, it's game over.

The computer plays a demo until you touch the joystick: then you play.
Move the cursor with the joystick, press to swap. After game over, or when you
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


OFF = (0, 0, 0)
COLORS = [(255, 0, 0), (255, 220, 0), (0, 220, 0), (0, 180, 255), (200, 0, 255)]
CURSOR = (255, 255, 255)   # The two cursor blocks blink bright white
FLASH = (255, 255, 255)
GAME_OVER = (80, 80, 80)

TICK = 0.02                 # Seconds per game step
RISE_DELAY = 4.5            # Seconds between new rows at the start ...
RISE_SPEEDUP = 0.98         # ... a little faster with every new row ...
MIN_RISE_DELAY = 1.0        # ... down to this
CURSOR_DELAY = 0.12         # Seconds per cursor step while a direction is held
AI_CURSOR_DELAY = 0.15      # Seconds per cursor step for the computer
FALL_STEP = 0.05            # Seconds per row for falling blocks
AI_SLOPPY = 0.1             # Chance the computer makes a random swap


def find_matches(grid):
    """All cells that are part of 3 or more of the same colour in a row or column."""
    found = set()
    for y in range(8):
        for x in range(8):
            color = grid[y][x]
            if color is None:
                continue
            if x <= 5 and grid[y][x + 1] == color == grid[y][x + 2]:
                found.update({(x, y), (x + 1, y), (x + 2, y)})
            if y <= 5 and grid[y + 1][x] == color == grid[y + 2][x]:
                found.update({(x, y), (x, y + 1), (x, y + 2)})
    return found


def fall_one_row(grid):
    """Let every floating block drop one row. Returns True if anything moved."""
    moved = False
    for y in range(6, -1, -1):
        for x in range(8):
            if grid[y][x] is not None and grid[y + 1][x] is None:
                grid[y + 1][x], grid[y][x] = grid[y][x], None
                moved = True
    return moved


def safe_color(grid, x, y, check_below=False):
    """A colour that doesn't make 3 in a row with its neighbours to the left
    and above (or below, while building the stack from the bottom up)."""
    banned = set()
    if x >= 2 and grid[y][x - 1] is not None and grid[y][x - 1] == grid[y][x - 2]:
        banned.add(grid[y][x - 1])
    if y >= 2 and grid[y - 1][x] is not None and grid[y - 1][x] == grid[y - 2][x]:
        banned.add(grid[y - 1][x])
    if check_below and y <= 5 and grid[y + 1][x] is not None and grid[y + 1][x] == grid[y + 2][x]:
        banned.add(grid[y + 1][x])
    return random.choice([c for c in range(len(COLORS)) if c not in banned])


def settle(grid):
    """Simulate falling and clearing until nothing happens. Returns (blocks, chains)."""
    cleared = chains = 0
    while True:
        while fall_one_row(grid):
            pass
        matches = find_matches(grid)
        if not matches:
            return cleared, chains
        chains += 1
        cleared += len(matches)
        for x, y in matches:
            grid[y][x] = None


class Game:
    def __init__(self, human):
        self.human = human
        self.grid = [[None] * 8 for _ in range(8)]
        for x in range(8):                      # Start with an uneven stack, 2-4 high
            for y in range(7, 7 - random.randint(2, 4), -1):
                self.grid[y][x] = safe_color(self.grid, x, y, check_below=True)
        self.cursor = [3, 5]                    # Left cell of the 2-wide cursor
        self.score = 0
        self.rises = 0
        self.rise_timer = 0.0
        self.cursor_timer = 0.0
        self.want_move = None
        self.ticks = 0
        self.plan = None

    def rise_delay(self):
        return max(MIN_RISE_DELAY, RISE_DELAY * RISE_SPEEDUP ** self.rises)

    def rise(self):
        """Push a new row in from the bottom. Returns False when the stack hits the top."""
        if any(self.grid[0][x] is not None for x in range(8)):
            return False
        self.grid = self.grid[1:] + [[None] * 8]
        for x in range(8):
            self.grid[7][x] = safe_color(self.grid, x, 7)
        self.cursor[1] = max(0, self.cursor[1] - 1)
        if self.plan:
            self.plan = (self.plan[0], self.plan[1] - 1)
            if self.plan[1] < 0:
                self.plan = None
        self.rises += 1
        return True

    def swap(self):
        x, y = self.cursor
        row = self.grid[y]
        row[x], row[x + 1] = row[x + 1], row[x]

    # ---------- the computer player ----------
    def choose_swap(self):
        best, best_value = None, None
        height = max((8 - y for y in range(8) for x in range(8) if self.grid[y][x] is not None),
                     default=0)
        for y in range(8):
            for x in range(7):
                if self.grid[y][x] is None and self.grid[y][x + 1] is None:
                    continue
                if self.grid[y][x] == self.grid[y][x + 1]:
                    continue
                grid = [row[:] for row in self.grid]
                grid[y][x], grid[y][x + 1] = grid[y][x + 1], grid[y][x]
                cleared, chains = settle(grid)
                new_height = max((8 - gy for gy in range(8) for gx in range(8)
                                  if grid[gy][gx] is not None), default=0)
                value = (cleared * 10 + chains * 25 - (new_height - height) * 6
                         - abs(x - self.cursor[0]) * 0.3 - abs(y - self.cursor[1]) * 0.3
                         + random.random())
                if best_value is None or value > best_value:
                    best, best_value = (x, y), value
        if best is None or random.random() < AI_SLOPPY:
            x, y = random.randint(0, 6), random.randint(3, 7)
            return x, y
        return best

    def ai_controls(self):
        """Returns (move, swap) like the joystick would."""
        if self.plan is None:
            self.plan = self.choose_swap()
        x, y = self.plan
        cx, cy = self.cursor
        if (cx, cy) == (x, y):
            self.plan = None
            return None, True
        if cx != x:
            return ((x > cx) - (x < cx), 0), False
        return (0, (y > cy) - (y < cy)), False

    # ---------- one game step ----------
    def step(self, move, swap, cursor_delay):
        """Advance one tick. Returns "swap", "over" or None."""
        self.ticks += 1
        if move:
            self.want_move = move
        self.cursor_timer += TICK
        if self.want_move and self.cursor_timer >= cursor_delay - 1e-9:
            self.cursor_timer = 0.0
            dx, dy = self.want_move
            self.cursor[0] = max(0, min(6, self.cursor[0] + dx))
            self.cursor[1] = max(0, min(7, self.cursor[1] + dy))
            self.want_move = None
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
        frame = [[COLORS[c] if c is not None else OFF for c in row] for row in self.grid]
        for x, y in flash:
            frame[y][x] = FLASH
        if cursor and (self.ticks // 12) % 2 == 0:       # The cursor blinks
            cx, cy = self.cursor
            for x in (cx, cx + 1):
                frame[cy][x] = CURSOR
        sense.set_pixels(flatten(frame))


def resolve(sense, game):
    """After a swap: let blocks fall, clear matches and keep going for chains."""
    chain = 0
    while True:
        while fall_one_row(game.grid):
            game.draw(sense)
            time.sleep(FALL_STEP)
        matches = find_matches(game.grid)
        if not matches:
            return
        chain += 1
        for _ in range(3):
            game.draw(sense, flash=matches, cursor=False)
            time.sleep(0.08)
            game.draw(sense, cursor=False)
            time.sleep(0.08)
        for x, y in matches:
            game.grid[y][x] = None
        game.score += len(matches) * 10 * chain + max(0, len(matches) - 3) * 20


def joystick_controls(stick, pressed):
    keys = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
    move = next((keys[k] for k in pressed if k in keys), None)
    if move is None:
        move = next((keys[k] for k in stick.held if k in keys), None)
    return move, "middle" in pressed


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
            game.plan = None

        if game.human:
            move, swap = joystick_controls(stick, pressed)
            if move and any(k in pressed for k in ("up", "down", "left", "right")):
                game.cursor_timer = CURSOR_DELAY      # A tap moves right away
            result = game.step(move, swap, CURSOR_DELAY)
        else:
            move, swap = game.ai_controls()
            result = game.step(move, swap, AI_CURSOR_DELAY)

        if result == "swap":
            resolve(sense, game)
        elif result == "over":
            for y in range(8):                 # Game over: the stack turns grey
                for x in range(8):
                    if game.grid[y][x] is not None:
                        sense.set_pixel(x, y, GAME_OVER)
                time.sleep(0.08)
            return "over", game.score

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
        sense.show_message(f"Score {score}", text_colour=COLORS[1], scroll_speed=0.06)
        human = False   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
