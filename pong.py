#!/usr/bin/env python3
"""Sense HAT – Pong: two computer players play Pong, first to 5 points wins.

The paddles are quick, the ball starts slow and speeds up with every return.
Where the ball hits the paddle decides its angle: the top edge sends it up,
the middle straight back, the bottom edge down. The players predict where
the ball will arrive, but now and then they misjudge it. After every point
the score is shown; the winner's side lights up at the end of a game.
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


SIZE = 8
PADDLE_HEIGHT = 3
WIN_SCORE = 5

TICK = 0.01              # Seconds per game step
PADDLE_DELAY = 0.05      # Seconds per paddle move
BALL_DELAY = 0.18        # Seconds per ball move at the start of each rally
BALL_SPEEDUP = 0.01      # Every return makes the ball this much faster ...
MIN_BALL_DELAY = 0.08    # ... down to this limit
LEFT_SKILL = 0.85        # Chance a player reads the ball right (1.0 = never misses)
RIGHT_SKILL = 0.85
EDGE_WEIGHTS = [2, 1, 2]  # How often players hit with the top, middle or bottom of the paddle

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
DIM = (60, 60, 60)

# 3x5 digits for the score
DIGITS = {
    "0": ["111", "101", "101", "101", "111"],
    "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"],
    "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"],
    "5": ["111", "100", "111", "001", "111"],
}


class Player:
    def __init__(self, column, skill):
        self.column = column      # 0 = left, 7 = right
        self.skill = skill
        self.top = 2              # Top row of the paddle
        self.target = 2
        self.score = 0

    def covers(self, y):
        return self.top <= y < self.top + PADDLE_HEIGHT

    def aim(self, arrival_y):
        """Pick where to put the paddle for a ball arriving at arrival_y.

        Players like to hit the ball with an edge of the paddle to give it an
        angle, and only sometimes with the middle to send it straight back.
        """
        spot = random.choices([0, 1, 2], weights=EDGE_WEIGHTS)[0]
        error = 0 if random.random() < self.skill else random.choice([-3, -2, 2, 3])
        self.target = arrival_y - spot + error

    def relax(self):
        """Ball is going away: drift back to the middle."""
        self.target = 2 if self.top <= 2 else 3

    def move(self):
        self.target = max(0, min(SIZE - PADDLE_HEIGHT, self.target))
        if self.top < self.target:
            self.top += 1
        elif self.top > self.target:
            self.top -= 1


class Ball:
    def __init__(self, direction):
        self.x = 3 if direction < 0 else 4
        self.y = random.randint(2, 5)
        self.dx = direction
        self.dy = random.choice([-1, 1])
        self.delay = BALL_DELAY

    def arrival(self):
        """Predict the row where the ball reaches the paddle it is heading for."""
        x, y, dy = self.x, self.y, self.dy
        target = 0 if self.dx < 0 else SIZE - 1
        while x != target:
            if not 0 <= y + dy < SIZE:
                dy = -dy
            x += self.dx
            y += dy
        return y


def draw(sense, left, right, ball):
    frame = [[BLACK] * SIZE for _ in range(SIZE)]
    for i in range(PADDLE_HEIGHT):
        frame[left.top + i][0] = WHITE
        frame[right.top + i][SIZE - 1] = WHITE
    if ball:
        frame[ball.y][ball.x] = RED
    sense.set_pixels(flatten(frame))


def show_score(sense, left, right, winner=None):
    """Left score in columns 0-2, right score in columns 5-7, a dash between."""
    frame = [[BLACK] * SIZE for _ in range(SIZE)]
    for offset, player in ((0, left), (5, right)):
        color = GREEN if player is winner else WHITE
        for y, line in enumerate(DIGITS[str(player.score)]):
            for x, bit in enumerate(line):
                if bit == "1":
                    frame[y + 1][x + offset] = color
    frame[3][3] = frame[3][4] = DIM
    sense.set_pixels(flatten(frame))


def celebrate(sense, winner):
    """Light up the winner's half of the screen a few times."""
    half = range(0, 4) if winner.column == 0 else range(4, 8)
    for _ in range(3):
        frame = [[GREEN if x in half else BLACK for x in range(SIZE)] for _ in range(SIZE)]
        sense.set_pixels(flatten(frame))
        time.sleep(0.25)
        sense.clear()
        time.sleep(0.25)


def play_point(sense, left, right, direction):
    """Play one rally. Returns the player who scored."""
    ball = Ball(direction)
    players = {-1: left, 1: right}
    players[ball.dx].aim(ball.arrival())
    players[-ball.dx].relax()
    draw(sense, left, right, ball)
    time.sleep(0.6)   # Short pause before the serve

    ball_timer = paddle_timer = 0.0
    while True:
        changed = False
        paddle_timer += TICK
        if paddle_timer >= PADDLE_DELAY - 1e-9:
            paddle_timer = 0.0
            left.move()
            right.move()
            changed = True

        ball_timer += TICK
        if ball_timer >= ball.delay - 1e-9:
            ball_timer = 0.0
            changed = True
            next_y = ball.y + ball.dy
            if not 0 <= next_y < SIZE:          # Bounce off top or bottom wall
                ball.dy = -ball.dy
                next_y = ball.y + ball.dy

            next_x = ball.x + ball.dx
            if next_x in (0, SIZE - 1):         # Ball reaches a paddle column
                player = players[ball.dx]
                if player.covers(next_y):
                    # Hit: the spot on the paddle decides the new angle
                    new_dy = (next_y - player.top) - 1
                    if new_dy == 0 and ball.dy == 0:
                        new_dy = random.choice([-1, 1])   # No endless straight rallies
                    ball.dy = new_dy
                    ball.dx = -ball.dx
                    ball.delay = max(MIN_BALL_DELAY, ball.delay - BALL_SPEEDUP)
                    players[ball.dx].aim(ball.arrival())
                    player.relax()
                else:
                    ball.x, ball.y = next_x, next_y
                    draw(sense, left, right, ball)
                    time.sleep(0.4)
                    return players[-ball.dx]    # The other player scores
            else:
                ball.x, ball.y = next_x, next_y

        if changed:
            draw(sense, left, right, ball)
        time.sleep(TICK)


def main():
    sense = init(hat)
    while True:
        left = Player(0, LEFT_SKILL)
        right = Player(SIZE - 1, RIGHT_SKILL)
        direction = random.choice([-1, 1])

        while max(left.score, right.score) < WIN_SCORE:
            scorer = play_point(sense, left, right, direction)
            scorer.score += 1
            direction = -1 if scorer is right else 1   # Serve to whoever lost the point
            show_score(sense, left, right)
            time.sleep(1.2)

        winner = left if left.score > right.score else right
        show_score(sense, left, right, winner)
        time.sleep(1.5)
        celebrate(sense, winner)


if __name__ == "__main__":
    run(main)
