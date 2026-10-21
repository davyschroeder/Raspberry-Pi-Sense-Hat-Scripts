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


# Colours
OFF = (0, 0, 0)
PADDLE = (0, 150, 255)
BALL = (255, 255, 255)
BRICK_COLORS = [(255, 0, 0), (255, 140, 0), (255, 255, 0), (0, 255, 0)]   # Per row
LOST_FLASH = (120, 0, 0)  # Dark red flash when the ball is lost

# Gameplay
TICK = 0.01               # Seconds per game step
PADDLE_WIDTH = 3          # Paddle size in pixels (wider = easier)
PADDLE_DELAY = 0.06       # Seconds per paddle step
BALL_DELAY = 0.17         # Seconds per ball step on level 1 ...
BALL_SPEEDUP = 0.002      # ... a little faster with every paddle hit ...
MIN_BALL_DELAY = 0.07     # ... down to this
LIVES = 3                 # Balls per game
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
    """Everything about one game of Breakout: bricks, ball, paddle, lives and score.

    The screen is 8x8 pixels. x is the column (0 = left, 7 = right) and y the
    row (0 = top, 7 = bottom). The bricks sit at the top, the paddle lives on
    the bottom row (y = 7) and the ball flies around in between.

    The ball moves diagonally one pixel at a time: dx and dy are each -1 or +1
    and say which way it goes (dx = -1 is left, dy = -1 is up).
    """

    def __init__(self, human):
        """Start a new game at level 1.

        Args:
            human: True when a person plays with the joystick, False when the
                computer plays the demo.
        """
        self.human = human
        self.lives = LIVES
        self.score = 0
        self.level = 0                     # next_level() below turns this into level 1
        self.paddle = 2                    # Leftmost column of the paddle
        self.want_move = 0                 # Paddle move (-1, 0, +1) waiting for its turn
        self.next_level()

    def next_level(self):
        """Go up one level: build that level's bricks and serve a new ball.

        The brick layout comes from LEVELS (wrapping around after the last one),
        and every brick row gets its own colour from BRICK_COLORS.
        """
        self.level += 1
        layout = LEVELS[(self.level - 1) % len(LEVELS)]
        # The bricks are a dictionary {(x, y): colour}, so looking up
        # "is there a brick here?" and removing a hit brick are both quick
        self.bricks = {(x, y): BRICK_COLORS[y % len(BRICK_COLORS)]
                       for y, row in enumerate(layout) for x, c in enumerate(row) if c == "#"}
        self.new_ball()

    def new_ball(self):
        """Put a fresh ball just above the middle of the paddle, heading up.

        It starts going left or right at random. Its speed depends on the level:
        every level takes 0.015 s off the step time, but never below
        MIN_BALL_DELAY. Also resets the timers and the computer player's plan.
        """
        self.x = self.paddle + 1
        self.y = 6
        self.dx = random.choice([-1, 1])
        self.dy = -1
        self.delay = max(MIN_BALL_DELAY, BALL_DELAY - 0.015 * (self.level - 1))
        self.ball_timer = -0.6             # Short pause before the ball moves
        self.paddle_timer = 0.0
        self.boring = 0                    # Paddle hits without hitting a brick
        self.ai_target = None              # Column the computer wants its paddle at

    # ---------- the computer player ----------
    def landing(self):
        """Predict the column where the ball reaches the paddle row.

        It plays the ball's flight forward in its head (on copies of x, y, dx,
        dy, so the real ball is not touched), bouncing off the walls and the
        ceiling, until the ball is on row 6 heading down: its next step lands
        on the paddle row. Bricks are ignored, so the guess can be wrong when
        the ball hits one on the way. Gives up after 40 steps and returns the
        column it got to.

        Returns:
            The predicted column (0-7).
        """
        x, y, dx = self.x, self.y, self.dx
        dy = self.dy
        for _ in range(40):
            # One row above the paddle and falling: the next step lands here
            # (if that next step would hit a side wall, the ball bounces back)
            if y == 6 and dy > 0:
                return x + dx if 0 <= x + dx <= 7 else x - dx
            nx, ny = x + dx, y + dy
            # Bounce off the side walls ...
            if not 0 <= nx <= 7:
                dx = -dx
                nx = x + dx
            # ... and off the ceiling
            if ny < 0:
                dy = 1
                ny = y + dy
            x, y = nx, ny
        return x

    def ai_controls(self):
        """Decide how the computer moves its paddle this step.

        While the ball comes down, the computer works out once where it will
        land (see landing()) and picks a paddle position for it. It does not
        just catch the ball in the middle: it hits it with the left or right
        edge of the paddle to steer it towards where most bricks are left.
        Now and then (1 - AI_SKILL of the time) it misjudges by 3 columns, so
        the demo can lose a ball too. While the ball goes up, the paddle simply
        follows the ball.

        Returns:
            -1 to move left, +1 to move right, 0 to stay.
        """
        if self.dy > 0:
            # Ball falling: make a plan once, then stick to it
            if self.ai_target is None:
                land = self.landing()
                if random.random() > AI_SKILL:            # Misjudges this one
                    land += random.choice([-3, 3])
                # Aim for the remaining bricks with the edge of the paddle
                if self.bricks:
                    # bx = average column of the bricks that are left
                    bx = sum(x for x, _ in self.bricks) / len(self.bricks)
                    # spot = which paddle pixel should meet the ball:
                    # 0 (left edge, sends it left), 2 (right edge) or 1 (middle)
                    spot = 0 if bx < land - 1 else (2 if bx > land + 1 else 1)
                else:
                    spot = 1
                # Paddle's left column so that pixel "spot" is under the ball,
                # kept inside the screen
                self.ai_target = max(0, min(8 - PADDLE_WIDTH, land - spot))
            target = self.ai_target
        else:
            # Ball rising: forget the old plan and stay centred under the ball
            self.ai_target = None
            target = max(0, min(8 - PADDLE_WIDTH, self.x - 1))
        # True/False count as 1/0, so this gives +1, -1 or 0
        return (target > self.paddle) - (target < self.paddle)

    # ---------- one game step ----------
    def step(self, move):
        """Advance one tick. Returns "lost", "cleared" or None.

        The paddle and the ball each have their own timer, so they move at
        their own speed even though this is called every TICK seconds.

        Args:
            move: -1 (left), +1 (right) or 0, from the joystick or the computer.

        Returns:
            "lost" when the ball fell past the paddle, "cleared" when the last
            brick was hit, otherwise None.
        """
        # Paddle: remember the wish to move, and carry it out when the paddle
        # timer says it is time for a paddle step. (The 1e-9 avoids rounding
        # errors from adding up 0.01 many times.)
        if move:
            self.want_move = move
        self.paddle_timer += TICK
        if self.paddle_timer >= PADDLE_DELAY - 1e-9:
            self.paddle_timer = 0.0
            if self.want_move:
                self.paddle = max(0, min(8 - PADDLE_WIDTH, self.paddle + self.want_move))
                self.want_move = 0

        # Ball: only move once its own timer has run out
        self.ball_timer += TICK
        if self.ball_timer < self.delay - 1e-9:
            return None
        self.ball_timer = 0.0
        if self.y == 7:                           # Below the paddle: lost
            return "lost"

        # Where the ball wants to go next, after bouncing off the walls
        nx, ny = self.x + self.dx, self.y + self.dy
        if not 0 <= nx <= 7:                      # Side walls
            self.dx = -self.dx
            nx = self.x + self.dx
        if ny < 0:                                # Top wall
            self.dy = 1
            ny = self.y + self.dy

        # Bricks: check straight up/down first, then sideways, then diagonal
        # A brick above/below flips the up-down direction, one at the side
        # flips left-right, and one only on the diagonal (a corner) flips both.
        # Only one brick breaks per step, and the ball stays where it is.
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
            # spot = which pixel of the paddle the ball lands on (0 = left edge)
            spot = nx - self.paddle
            if 0 <= spot < PADDLE_WIDTH:
                # Bounce up; the edges choose the sideways direction, the
                # middle keeps it as it was
                self.dy = -1
                if spot == 0:
                    self.dx = -1
                elif spot == PADDLE_WIDTH - 1:
                    self.dx = 1
                # The ball can end up bouncing along the same path forever
                # without reaching the last bricks: after 6 such hits, flip it
                self.boring += 1
                if self.boring > 6:               # Stuck in a loop: give it a nudge
                    self.dx = -self.dx
                    self.boring = 0
                # Every paddle hit makes the ball a little faster
                self.delay = max(MIN_BALL_DELAY, self.delay - BALL_SPEEDUP)
                self.ai_target = None
                # Don't send the ball straight into a side wall next step
                if not 0 <= self.x + self.dx <= 7:
                    self.dx = -self.dx
                return None
        # Nothing hit: the ball moves to its new spot (on row 7 it is lost
        # at the next ball step)
        self.x, self.y = nx, ny
        return None

    def draw(self, sense, ball=True):
        """Show the bricks, paddle and (optionally) the ball on the LEDs.

        Args:
            sense: the SenseHat object to draw on.
            ball: False to leave the ball out, used when flashing after a lost ball.
        """
        # Build the picture as 8 rows of 8 colours, then send it in one go
        frame = [[OFF] * 8 for _ in range(8)]
        for (x, y), color in self.bricks.items():
            frame[y][x] = color
        for i in range(PADDLE_WIDTH):
            frame[7][self.paddle + i] = PADDLE
        if ball:
            frame[self.y][self.x] = BALL
        sense.set_pixels(flatten(frame))


def joystick_move(stick, pressed):
    """Turn the joystick into a paddle move: -1 (left), +1 (right) or 0.

    A direction counts when it is held down or was just pressed (so even a
    very short tap moves the paddle). Holding both left and right gives 0.

    Args:
        stick: the Joystick.
        pressed: the list of new presses from stick.read().
    """
    return (("right" in stick.held or "right" in pressed)
            - ("left" in stick.held or "left" in pressed))


def play_game(sense, stick, human):
    """Play one game. Returns (result, score): result is "over", or "start" when
    someone touches the joystick during the demo.

    Args:
        sense: the SenseHat object.
        stick: the Joystick.
        human: True for a game played by a person, False for the computer demo.
    """
    game = Game(human)
    while True:
        # Input: during the demo any press means "I want to play"
        pressed = stick.read()
        if not game.human and pressed:
            return "start", 0
        if game.human and stick.idle():      # Player walked away: computer takes over
            game.human = False

        # The paddle is moved by the joystick or by the computer player
        move = joystick_move(stick, pressed) if game.human else game.ai_controls()
        result = game.step(move)

        if result == "lost":
            # Ball lost: flash red twice, then show the balls left (or end the game)
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
            # All bricks gone: flash green three times, 100 bonus points, next level
            game.draw(sense)
            for _ in range(3):
                sense.clear(0, 80, 0)
                time.sleep(0.15)
                sense.clear()
                time.sleep(0.15)
            game.score += 100
            game.next_level()

        # Draw the new situation and wait for the next tick
        game.draw(sense)
        time.sleep(TICK)


def main():
    """Keep playing forever: demo games until someone takes the joystick.

    After a human's game the score scrolls by and the computer starts a new
    demo; touching the joystick during a demo starts a game for you.
    """
    sense = init(hat)
    stick = Joystick(sense)
    human = False
    while True:
        result, score = play_game(sense, stick, human)
        if result == "start":
            # Someone touched the joystick during the demo: start their game
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
