#!/usr/bin/env python3
"""Sense HAT – Pong: two computer players play Pong, first to 5 points wins.

The paddles are quick, the ball starts slow and speeds up with every return.
Where the ball hits the paddle decides its angle: the top edge sends it up,
the middle straight back, the bottom edge down. The players predict where
the ball will arrive, but now and then they misjudge it. After every point
the score is shown; the winner's side lights up at the end of a game.

The computer plays both sides until you touch the joystick: then you play the
left paddle with up and down. After the match, or when you leave the joystick
alone for a while, the computer takes over again.
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
    """Keeps track of the Sense HAT joystick (or the emulator's: arrow keys + Enter).

    The Sense HAT library reports joystick "events": a direction ("up", "down",
    "left", "right" or "middle" for pushing it in) plus an action ("pressed",
    "held" while you keep it pushed, and "released"). This class turns those
    events into two handy things for the games:

    - read() gives the new presses since last time (good for single steps),
    - self.held is the set of directions that are pushed right now (good for
      moving as long as you hold the stick).

    It also remembers when the joystick was last touched, so the game can tell
    whether the player has walked away (see idle()).
    """

    def __init__(self, sense):
        """Remember the SenseHat object; nothing is held and no input seen yet."""
        self.sense = sense
        self.held = set()        # Directions held down right now
        self.last_input = 0.0    # time.monotonic() of the last joystick event (0 = never)

    def read(self):
        """Return the directions pressed since the last call ("up", "left", "middle", ...).

        Also updates self.held and the time of the last input. Returns an empty
        list when there is no joystick to read (for example a broken connection).
        """
        pressed = []
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


SIZE = 8                 # The LED matrix is 8x8 pixels
PADDLE_HEIGHT = 3        # Paddle size in pixels (taller = easier)
WIN_SCORE = 5            # Points needed to win a match (the digits below go up to 5)

TICK = 0.01              # Seconds per game step
PADDLE_DELAY = 0.05      # Seconds per paddle move
BALL_DELAY = 0.18        # Seconds per ball move at the start of each rally
BALL_SPEEDUP = 0.01      # Every return makes the ball this much faster ...
MIN_BALL_DELAY = 0.08    # ... down to this limit
LEFT_SKILL = 0.85        # Chance a player reads the ball right (1.0 = never misses)
RIGHT_SKILL = 0.85       # Same for the right player
EDGE_WEIGHTS = [2, 1, 2]  # How often players hit with the top, middle or bottom of the paddle

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
DIM = (60, 60, 60)       # Grey, for the dash between the two scores

# 3x5 digits for the score: 5 rows of 3 pixels each, "1" = lit
DIGITS = {
    "0": ["111", "101", "101", "101", "111"],
    "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"],
    "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"],
    "5": ["111", "100", "111", "001", "111"],
}


class Player:
    """One side of the table: a paddle, its score, and the computer brain that moves it.

    The paddle is a vertical bar of PADDLE_HEIGHT pixels in column 0 (left) or
    column 7 (right). Rows count from 0 at the top to 7 at the bottom. A
    computer-controlled paddle walks one pixel per step towards self.target;
    a human paddle simply follows the joystick.
    """

    def __init__(self, column, skill):
        """Create a player with its paddle near the middle and score 0.

        Args:
            column: 0 for the left player, 7 for the right player.
            skill: chance (0.0-1.0) that the computer reads the ball correctly.
        """
        self.column = column      # 0 = left, 7 = right
        self.skill = skill
        self.top = 2              # Top row of the paddle
        self.target = 2           # Top row the computer wants its paddle to move to
        self.score = 0
        self.human = False        # True: moved with the joystick

    def covers(self, y):
        """Return True when the paddle covers row y (so a ball there is hit)."""
        return self.top <= y < self.top + PADDLE_HEIGHT

    def aim(self, arrival_y):
        """Pick where to put the paddle for a ball arriving at arrival_y.

        Players like to hit the ball with an edge of the paddle to give it an
        angle, and only sometimes with the middle to send it straight back.

        This only sets self.target; move() then walks the paddle there step by
        step, so a ball that is too fast can still be missed.

        Args:
            arrival_y: the row where the ball will reach this paddle's column
                (from Ball.arrival()).
        """
        # Which paddle pixel should meet the ball: 0 = top edge, 1 = middle,
        # 2 = bottom edge. EDGE_WEIGHTS makes the edges twice as likely.
        spot = random.choices([0, 1, 2], weights=EDGE_WEIGHTS)[0]
        # Most of the time the guess is spot on; otherwise it is 2 or 3 rows off
        error = 0 if random.random() < self.skill else random.choice([-3, -2, 2, 3])
        # Paddle top so that pixel "spot" ends up on the ball's row
        self.target = arrival_y - spot + error

    def relax(self):
        """Ball is going away: drift back to the middle.

        Rows 2 and 3 are both "the middle" for a 3-high paddle; picking the
        nearer one avoids a needless step.
        """
        self.target = 2 if self.top <= 2 else 3

    def move(self, joystick_move=0):
        """Move the paddle one step (called every PADDLE_DELAY seconds).

        Args:
            joystick_move: for a human player -1 (up), +1 (down) or 0.
                Ignored for the computer, which steps towards self.target.
        """
        # Human: follow the joystick, but stay on the screen
        if self.human:
            self.top = max(0, min(SIZE - PADDLE_HEIGHT, self.top + joystick_move))
            return
        # Computer: keep the target on the screen, then take one step towards it
        self.target = max(0, min(SIZE - PADDLE_HEIGHT, self.target))
        if self.top < self.target:
            self.top += 1
        elif self.top > self.target:
            self.top -= 1


class Ball:
    """The ball: its position, direction and speed.

    x is the column (0 = left, 7 = right), y the row (0 = top). Every ball
    step it moves dx columns (-1 = left, +1 = right) and dy rows (-1 = up,
    0 = straight across, +1 = down). delay is the time between two steps.
    """

    def __init__(self, direction):
        """Serve a new ball from the middle of the table.

        Args:
            direction: -1 to serve towards the left player, +1 towards the right.
        """
        # Start in one of the two middle columns, on a random row away from
        # the walls, flying up or down at random
        self.x = 3 if direction < 0 else 4
        self.y = random.randint(2, 5)
        self.dx = direction
        self.dy = random.choice([-1, 1])
        self.delay = BALL_DELAY

    def arrival(self):
        """Predict the row where the ball reaches the paddle it is heading for.

        It replays the ball's flight on copies of its position, bouncing off
        the top and bottom walls, until it reaches the paddle column.

        Returns:
            The row (0-7) where the ball arrives at column 0 or 7.
        """
        x, y, dy = self.x, self.y, self.dy
        target = 0 if self.dx < 0 else SIZE - 1
        while x != target:
            # About to leave the screen at the top or bottom: bounce
            if not 0 <= y + dy < SIZE:
                dy = -dy
            x += self.dx
            y += dy
        return y


def draw(sense, left, right, ball):
    """Show both paddles and the ball on the LEDs.

    Args:
        sense: the SenseHat object to draw on.
        left, right: the two Players (their paddles are drawn white).
        ball: the Ball (drawn red), or None to draw only the paddles.
    """
    # Build the picture as 8 rows of 8 colours, then send it in one go
    frame = [[BLACK] * SIZE for _ in range(SIZE)]
    for i in range(PADDLE_HEIGHT):
        frame[left.top + i][0] = WHITE
        frame[right.top + i][SIZE - 1] = WHITE
    if ball:
        frame[ball.y][ball.x] = RED
    sense.set_pixels(flatten(frame))


def show_score(sense, left, right, winner=None):
    """Left score in columns 0-2, right score in columns 5-7, a dash between.

    Args:
        sense: the SenseHat object to draw on.
        left, right: the two Players.
        winner: the Player who won the match, whose score is shown in green;
            None during the match (both scores white).
    """
    frame = [[BLACK] * SIZE for _ in range(SIZE)]
    # offset = the column where that player's 3-pixel-wide digit starts
    for offset, player in ((0, left), (5, right)):
        color = GREEN if player is winner else WHITE
        # Copy the digit's lit pixels into the frame, one row lower (rows 1-5)
        for y, line in enumerate(DIGITS[str(player.score)]):
            for x, bit in enumerate(line):
                if bit == "1":
                    frame[y + 1][x + offset] = color
    # The dash in the middle: "3 - 2"
    frame[3][3] = frame[3][4] = DIM
    sense.set_pixels(flatten(frame))


def celebrate(sense, winner):
    """Light up the winner's half of the screen a few times.

    Args:
        sense: the SenseHat object.
        winner: the Player who won; their column (0 or 7) picks the half.
    """
    half = range(0, 4) if winner.column == 0 else range(4, 8)
    for _ in range(3):
        frame = [[GREEN if x in half else BLACK for x in range(SIZE)] for _ in range(SIZE)]
        sense.set_pixels(flatten(frame))
        time.sleep(0.25)
        sense.clear()
        time.sleep(0.25)


def play_point(sense, left, right, direction, stick):
    """Play one rally. Returns the player who scored, or "start" when someone
    touches the joystick during the demo.

    The loop runs every TICK seconds. The paddles and the ball each have their
    own timer, so they move at their own speed, and the screen is only redrawn
    when something actually moved.

    Args:
        sense: the SenseHat object.
        left, right: the two Players (a human always plays left).
        direction: -1 to serve towards the left player, +1 towards the right.
        stick: the Joystick.
    """
    ball = Ball(direction)
    # Look up a player by the ball's dx: dx -1 heads for left, +1 for right
    players = {-1: left, 1: right}
    # The player the ball is coming at plans the return, the other one relaxes
    players[ball.dx].aim(ball.arrival())
    players[-ball.dx].relax()
    draw(sense, left, right, ball)
    time.sleep(0.6)   # Short pause before the serve

    ball_timer = paddle_timer = 0.0
    joystick_move = 0   # Human's paddle move (-1 up, +1 down) for the next paddle step
    while True:
        # Input: during the demo any press means "I want to play"
        pressed = stick.read()
        if pressed and not left.human:
            return "start"
        if left.human:
            if stick.idle():                    # Player walked away: computer takes over
                left.human = False
                # Give the new computer player a plan straight away
                if ball.dx < 0:
                    left.aim(ball.arrival())
                else:
                    left.relax()
            for key in pressed:                 # Quick taps between paddle moves count too
                joystick_move = {"up": -1, "down": 1}.get(key, joystick_move)

        # Paddles: one step every PADDLE_DELAY seconds. (The 1e-9 avoids
        # rounding errors from adding up 0.01 many times.)
        changed = False
        paddle_timer += TICK
        if paddle_timer >= PADDLE_DELAY - 1e-9:
            paddle_timer = 0.0
            # No quick tap waiting: use the direction that is held down
            # (True/False count as 1/0, so this gives -1, 0 or +1)
            if not joystick_move:
                joystick_move = ("down" in stick.held) - ("up" in stick.held)
            left.move(joystick_move)
            right.move()
            joystick_move = 0
            changed = True

        # Ball: one step every ball.delay seconds
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
                    # Hit: the spot on the paddle decides the new angle:
                    # top pixel -> up (-1), middle -> straight (0), bottom -> down (+1)
                    new_dy = (next_y - player.top) - 1
                    if new_dy == 0 and ball.dy == 0:
                        new_dy = random.choice([-1, 1])   # No endless straight rallies
                    # Send it back the other way, a little faster
                    ball.dy = new_dy
                    ball.dx = -ball.dx
                    ball.delay = max(MIN_BALL_DELAY, ball.delay - BALL_SPEEDUP)
                    # Now the opponent plans their return and the hitter relaxes
                    players[ball.dx].aim(ball.arrival())
                    player.relax()
                else:
                    # Missed: show the ball in the paddle column for a moment
                    ball.x, ball.y = next_x, next_y
                    draw(sense, left, right, ball)
                    time.sleep(0.4)
                    return players[-ball.dx]    # The other player scores
            else:
                # Somewhere in the middle: just move on
                ball.x, ball.y = next_x, next_y

        # Only redraw when something moved, then wait for the next tick
        if changed:
            draw(sense, left, right, ball)
        time.sleep(TICK)


def main():
    """Play matches forever: computer against computer, until someone takes the joystick.

    A match is won by the first to WIN_SCORE points. When someone touches the
    joystick during a demo match, a new match starts with them on the left
    paddle. After a match the winner is shown and the demo starts again.
    """
    sense = init(hat)
    stick = Joystick(sense)
    human = False
    while True:
        # A new match: fresh players, served in a random direction
        left = Player(0, LEFT_SKILL)
        right = Player(SIZE - 1, RIGHT_SKILL)
        left.human = human
        direction = random.choice([-1, 1])

        # Play rallies until one player reaches WIN_SCORE
        started = False
        while max(left.score, right.score) < WIN_SCORE:
            scorer = play_point(sense, left, right, direction, stick)
            if scorer == "start":
                started = True
                break
            scorer.score += 1
            direction = -1 if scorer is right else 1   # Serve to whoever lost the point
            show_score(sense, left, right)
            time.sleep(1.2)

        if started:                     # Someone grabbed the joystick: new match, you play left
            show_play_sign(sense)
            stick.flush()
            human = True
            continue

        # Match over: show the final score with the winner in green, then celebrate
        winner = left if left.score > right.score else right
        show_score(sense, left, right, winner)
        time.sleep(1.5)
        celebrate(sense, winner)
        human = False                   # Back to the demo; touch the joystick to play again
        stick.flush()


if __name__ == "__main__":
    run(main)
