#!/usr/bin/env python3
"""Sense HAT – Breakout: smash all the bricks with the ball.

Bounce the ball off your paddle into the coloured bricks at the top. Where the
ball hits the paddle decides its direction: the left edge sends it left, the
right edge right, the middle keeps it going the same way. Clear all bricks for
the next level, where the ball is faster. You have 3 balls.

The computer plays a demo until you touch the joystick: then you play.
Left and right to move the paddle. After game over, or when you leave the
joystick alone for a while, the computer takes over again.
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
OFF = (0, 0, 0)
PADDLE = (0, 150, 255)
BALL = (255, 255, 255)
BRICK_COLORS = [(255, 0, 0), (255, 140, 0), (255, 255, 0), (0, 255, 0)]   # Per row
LOST_FLASH = (120, 0, 0)

# Gameplay
TICK = 0.01               # Seconds per game step
PADDLE_WIDTH = 3
PADDLE_DELAY = 0.06       # Seconds per paddle step
BALL_DELAY = 0.17         # Seconds per ball step on level 1 ...
BALL_SPEEDUP = 0.002      # ... a little faster with every paddle hit ...
MIN_BALL_DELAY = 0.07     # ... down to this
LIVES = 3
AI_SKILL = 0.93           # Chance the computer gets to the ball in time

# Brick layouts per level ("#" = brick); after the last one they repeat
LEVELS = [
    ["########",
     "########",
     "########"],
    ["########",
     "#.####.#",
     "########",
     ".#.##.#."],
    ["#.#.#.#.",
     ".#.#.#.#",
     "#.#.#.#.",
     ".#.#.#.#"],
    ["########",
     "##....##",
     "#.####.#",
     "########"],
]


class Game:
    def __init__(self, human):
        self.human = human
        self.lives = LIVES
        self.score = 0
        self.level = 0
        self.paddle = 2                    # Leftmost column of the paddle
        self.want_move = 0
        self.next_level()

    def next_level(self):
        self.level += 1
        layout = LEVELS[(self.level - 1) % len(LEVELS)]
        self.bricks = {(x, y): BRICK_COLORS[y % len(BRICK_COLORS)]
                       for y, row in enumerate(layout) for x, c in enumerate(row) if c == "#"}
        self.new_ball()

    def new_ball(self):
        self.x = self.paddle + 1
        self.y = 6
        self.dx = random.choice([-1, 1])
        self.dy = -1
        self.delay = max(MIN_BALL_DELAY, BALL_DELAY - 0.015 * (self.level - 1))
        self.ball_timer = -0.6             # Short pause before the ball moves
        self.paddle_timer = 0.0
        self.boring = 0                    # Paddle hits without hitting a brick
        self.ai_target = None

    # ---------- the computer player ----------
    def landing(self):
        """Predict the column where the ball reaches the paddle row."""
        x, y, dx = self.x, self.y, self.dx
        dy = self.dy
        for _ in range(40):
            if y == 6 and dy > 0:
                return x + dx if 0 <= x + dx <= 7 else x - dx
            nx, ny = x + dx, y + dy
            if not 0 <= nx <= 7:
                dx = -dx
                nx = x + dx
            if ny < 0:
                dy = 1
                ny = y + dy
            x, y = nx, ny
        return x

    def ai_controls(self):
        if self.dy > 0:
            if self.ai_target is None:
                land = self.landing()
                if random.random() > AI_SKILL:            # Misjudges this one
                    land += random.choice([-3, 3])
                # Aim for the remaining bricks with the edge of the paddle
                if self.bricks:
                    bx = sum(x for x, _ in self.bricks) / len(self.bricks)
                    spot = 0 if bx < land - 1 else (2 if bx > land + 1 else 1)
                else:
                    spot = 1
                self.ai_target = max(0, min(8 - PADDLE_WIDTH, land - spot))
            target = self.ai_target
        else:
            self.ai_target = None
            target = max(0, min(8 - PADDLE_WIDTH, self.x - 1))
        return (target > self.paddle) - (target < self.paddle)

    # ---------- one game step ----------
    def step(self, move):
        """Advance one tick. Returns "lost", "cleared" or None."""
        if move:
            self.want_move = move
        self.paddle_timer += TICK
        if self.paddle_timer >= PADDLE_DELAY - 1e-9:
            self.paddle_timer = 0.0
            if self.want_move:
                self.paddle = max(0, min(8 - PADDLE_WIDTH, self.paddle + self.want_move))
                self.want_move = 0

        self.ball_timer += TICK
        if self.ball_timer < self.delay - 1e-9:
            return None
        self.ball_timer = 0.0
        if self.y == 7:                           # Below the paddle: lost
            return "lost"

        nx, ny = self.x + self.dx, self.y + self.dy
        if not 0 <= nx <= 7:                      # Side walls
            self.dx = -self.dx
            nx = self.x + self.dx
        if ny < 0:                                # Top wall
            self.dy = 1
            ny = self.y + self.dy

        # Bricks: check straight up/down first, then sideways, then diagonal
        for cell, flip in (((self.x, ny), "y"), ((nx, self.y), "x"), ((nx, ny), "xy")):
            if cell in self.bricks:
                del self.bricks[cell]
                self.score += 10 * self.level
                self.boring = 0
                if "x" in flip:
                    self.dx = -self.dx
                if "y" in flip:
                    self.dy = -self.dy
                return "cleared" if not self.bricks else None

        if ny == 7 and self.dy > 0:               # Paddle
            spot = nx - self.paddle
            if 0 <= spot < PADDLE_WIDTH:
                self.dy = -1
                if spot == 0:
                    self.dx = -1
                elif spot == PADDLE_WIDTH - 1:
                    self.dx = 1
                self.boring += 1
                if self.boring > 6:               # Stuck in a loop: give it a nudge
                    self.dx = -self.dx
                    self.boring = 0
                self.delay = max(MIN_BALL_DELAY, self.delay - BALL_SPEEDUP)
                self.ai_target = None
                if not 0 <= self.x + self.dx <= 7:
                    self.dx = -self.dx
                return None
        self.x, self.y = nx, ny
        return None

    def draw(self, sense, ball=True):
        frame = [[OFF] * 8 for _ in range(8)]
        for (x, y), color in self.bricks.items():
            frame[y][x] = color
        for i in range(PADDLE_WIDTH):
            frame[7][self.paddle + i] = PADDLE
        if ball:
            frame[self.y][self.x] = BALL
        sense.set_pixels(flatten(frame))


def joystick_move(stick, pressed):
    return (("right" in stick.held or "right" in pressed)
            - ("left" in stick.held or "left" in pressed))


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

        move = joystick_move(stick, pressed) if game.human else game.ai_controls()
        result = game.step(move)

        if result == "lost":
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
        elif result == "cleared":
            game.draw(sense)
            for _ in range(3):
                sense.clear(0, 80, 0)
                time.sleep(0.15)
                sense.clear()
                time.sleep(0.15)
            game.score += 100
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
        sense.show_message(f"Score {score}", text_colour=BRICK_COLORS[2], scroll_speed=0.06)
        human = False   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
