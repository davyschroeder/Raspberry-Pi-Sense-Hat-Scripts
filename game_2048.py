#!/usr/bin/env python3
"""Sense HAT – 2048: slide the tiles and merge them into bigger and bigger ones.

The 8x8 display shows a 4x4 board of tiles, each 2x2 pixels. Push the joystick
and all tiles slide that way as far as they can; two tiles with the same value
that bump into each other merge into one tile worth double, and you score its
value. After every move a new 2 (sometimes a 4) appears in an empty spot.

Every value has its own colour, from dim grey for 2 to blue for 2048:
    2 grey, 4 white, 8 orange, 16 red-orange, 32 red, 64 pink, 128 yellow,
    256 yellow-green, 512 green, 1024 cyan, 2048 blue, 4096 and up purple.
Reach 2048 and the board celebrates, but you can keep going. When the board is
full and nothing can merge any more, it's game over.

The computer plays a demo until you touch the joystick: then you play.
Push the joystick up, down, left or right to slide. After game over, or when
you leave the joystick alone for a while, the computer takes over again.
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


# Colours: one for every tile value (2, 4, 8, ... 2048, then the rest)
OFF = (0, 0, 0)
TILE_COLORS = [
    (70, 70, 70),       # 2
    (230, 230, 230),    # 4
    (255, 130, 0),      # 8
    (255, 70, 0),       # 16
    (255, 0, 0),        # 32
    (255, 0, 140),      # 64
    (255, 230, 0),      # 128
    (170, 255, 0),      # 256
    (0, 220, 0),        # 512
    (0, 220, 220),      # 1024
    (0, 80, 255),       # 2048
    (170, 0, 255),      # 4096 and up
]
NEW_TILE_GLOW = (255, 255, 255)
WIN_VALUE = 2048

# Gameplay
TICK = 0.02                      # Seconds per game step
AI_DELAY = 0.35                  # Seconds between two moves of the computer
AI_RANDOM = 0.03                 # Chance the computer makes a random move
DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


def tile_color(value):
    """2 -> TILE_COLORS[0], 4 -> TILE_COLORS[1], ..."""
    index = value.bit_length() - 2
    return TILE_COLORS[min(index, len(TILE_COLORS) - 1)]


def slide_line(line):
    """Slide one row of 4 towards index 0. Returns (new line, points scored)."""
    tiles = [v for v in line if v]
    out, points, i = [], 0, 0
    while i < len(tiles):
        if i + 1 < len(tiles) and tiles[i] == tiles[i + 1]:
            out.append(tiles[i] * 2)
            points += tiles[i] * 2
            i += 2
        else:
            out.append(tiles[i])
            i += 1
    return out + [0] * (4 - len(out)), points


def slide(board, direction):
    """Slide the whole board. Returns (new board, points, whether anything moved)."""
    dx, dy = DIRECTIONS[direction]
    new = [row[:] for row in board]
    points = 0
    for i in range(4):
        # Read each row or column starting from the side the tiles move towards
        if dx:
            cells = [(x, i) for x in (range(4) if dx < 0 else range(3, -1, -1))]
        else:
            cells = [(i, y) for y in (range(4) if dy < 0 else range(3, -1, -1))]
        line, p = slide_line([board[y][x] for x, y in cells])
        points += p
        for (x, y), v in zip(cells, line):
            new[y][x] = v
    return new, points, new != board


def empty_cells(board):
    return [(x, y) for y in range(4) for x in range(4) if not board[y][x]]


def can_move(board):
    return any(slide(board, d)[2] for d in DIRECTIONS)


def evaluate(board):
    """How good a board looks: empty space, big tiles in a corner, tidy rows."""
    empty = len(empty_cells(board))
    corner = board[3][0]                          # The computer keeps its biggest tile bottom left
    biggest = max(max(row) for row in board)
    smooth = 0
    for y in range(4):
        for x in range(4):
            v = board[y][x]
            if v and x < 3 and board[y][x + 1]:
                smooth -= abs(v.bit_length() - board[y][x + 1].bit_length())
            if v and y < 3 and board[y + 1][x]:
                smooth -= abs(v.bit_length() - board[y + 1][x].bit_length())
    mono = 0                                      # Tiles growing towards the bottom-left corner
    for y in range(4):
        for x in range(3):
            a, b = board[y][x].bit_length(), board[y][x + 1].bit_length()
            mono += min(0, a - b)
    for x in range(4):
        for y in range(3):
            a, b = board[y][x].bit_length(), board[y + 1][x].bit_length()
            mono += min(0, b - a)
    return empty * 2.7 + smooth * 0.1 + mono * 1.0 + (2.0 if corner == biggest else 0)


class Game:
    def __init__(self, human):
        self.human = human
        self.board = [[0] * 4 for _ in range(4)]
        self.score = 0
        self.won = False
        self.ai_timer = 0.0
        self.new_tile = None
        self.add_tile()
        self.add_tile()

    def add_tile(self):
        x, y = random.choice(empty_cells(self.board))
        self.board[y][x] = 4 if random.random() < 0.1 else 2
        self.new_tile = (x, y)

    def move(self, direction):
        """Returns True when the board changed."""
        board, points, moved = slide(self.board, direction)
        if moved:
            self.board = board
            self.score += points
            self.add_tile()
        return moved

    # ---------- the computer player ----------
    def ai_move(self):
        """Look two moves ahead (with the average new tile in between)."""
        if random.random() < AI_RANDOM:
            options = [d for d in DIRECTIONS if slide(self.board, d)[2]]
            return random.choice(options) if options else None
        best, best_value = None, None
        for d in DIRECTIONS:
            board, points, moved = slide(self.board, d)
            if not moved:
                continue
            empties = empty_cells(board)
            total = 0.0
            sample = empties if len(empties) <= 6 else random.sample(empties, 6)
            for x, y in sample:
                board[y][x] = 2
                follow = -50                      # No move left after this: very bad
                for d2 in DIRECTIONS:
                    after, points2, moved2 = slide(board, d2)
                    if moved2:
                        follow = max(follow, evaluate(after) + points2 / 64)
                board[y][x] = 0
                total += follow
            value = points / 64 + total / max(1, len(sample))
            if best_value is None or value > best_value:
                best, best_value = d, value
        return best

    def draw(self, sense, glow=True):
        frame = [[OFF] * 8 for _ in range(8)]
        for ty in range(4):
            for tx in range(4):
                v = self.board[ty][tx]
                if not v:
                    continue
                color = tile_color(v)
                if glow and (tx, ty) == self.new_tile:
                    color = tuple((c + w) // 2 for c, w in zip(color, NEW_TILE_GLOW))
                for dy in (0, 1):
                    for dx in (0, 1):
                        frame[ty * 2 + dy][tx * 2 + dx] = color
        sense.set_pixels(flatten(frame))


def celebrate(sense, game):
    """Reached 2048: the board flashes in all tile colours."""
    for color in TILE_COLORS:
        sense.clear(color)
        time.sleep(0.08)
    game.draw(sense)


def play_game(sense, stick, human):
    """Play one game. Returns (result, score): result is "over", or "start" when
    someone touches the joystick during the demo."""
    game = Game(human)
    glow_timer = 0.3
    while True:
        pressed = stick.read()
        if not game.human and pressed:
            return "start", 0
        if game.human and stick.idle():      # Player walked away: computer takes over
            game.human = False

        direction = None
        if game.human:
            direction = next((k for k in pressed if k in DIRECTIONS), None)
        else:
            game.ai_timer += TICK
            if game.ai_timer >= AI_DELAY:
                game.ai_timer = 0.0
                direction = game.ai_move()
        if direction and game.move(direction):
            glow_timer = 0.3
            if not game.won and any(v >= WIN_VALUE for row in game.board for v in row):
                game.won = True
                celebrate(sense, game)
            if not can_move(game.board):
                game.draw(sense)
                time.sleep(1.0)
                for _ in range(3):
                    sense.clear(60, 0, 0)
                    time.sleep(0.2)
                    game.draw(sense)
                    time.sleep(0.2)
                return "over", game.score

        glow_timer = max(0.0, glow_timer - TICK)
        game.draw(sense, glow=glow_timer > 0)
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
        sense.show_message(f"Score {score}", text_colour=TILE_COLORS[6], scroll_speed=0.06)
        human = False   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
