#!/usr/bin/env python3
"""Sense HAT – Snake: a snake hunts for food until it crashes.

The computer plays a demo until you touch the joystick: then it's your turn.
Steer with the joystick; after game over the demo starts again.
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


SNAKE_COLOR = (0, 255, 0)
HEAD_COLOR = (150, 255, 150)   # A lighter green, so you can see where the snake is going
RIP_COLOR = (255, 0, 0)        # Colour of the "RIP" message when the snake crashes
# Every new piece of food gets a different colour from this list
FOOD_COLORS = [
    (255, 0, 0),      # red
    (255, 140, 0),    # orange
    (255, 255, 0),    # yellow
    (0, 150, 255),    # blue
    (180, 0, 255),    # purple
    (255, 0, 150),    # pink
    (0, 255, 255),    # cyan
    (255, 255, 255),  # white
]
OFF = (0, 0, 0)
STEP_DELAY = 0.3   # Seconds between two snake moves (lower = faster, harder game)
# All four moves as (dx, dy): right, down, left, up. x grows to the right,
# y grows downwards (row 0 is the top of the screen).
DIRECTIONS = [(1, 0), (0, 1), (-1, 0), (0, -1)]
# Joystick direction -> move (dx, dy)
KEYS ={"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
WAIT_STEP = 0.02   # Check the joystick this often while waiting for the next step


def place_food(snake):
    """Return a random free cell, or None if the snake fills the whole board.

    Args:
        snake: list of (x, y) cells taken by the snake.

    Returns:
        An (x, y) cell that is not part of the snake, or None.
    """
    # Make a list of every cell the snake is not on, and pick one of them
    free =[(x, y) for x in range(8) for y in range(8) if (x, y) not in snake]
    return random.choice(free) if free else None


def is_blocked(cell, snake):
    """Return True when moving into cell would crash the snake.

    That is when the cell is off the 8x8 screen (a wall) or part of the snake.

    Args:
        cell: the (x, y) cell to check.
        snake: list of (x, y) cells taken by the snake.
    """
    x, y = cell
    return not (0 <= x <= 7 and 0 <= y <= 7) or cell in snake


def choose_direction(snake, food, current):
    """Head towards the food, but never straight into a wall or itself.

    This is the computer player for the demo. It is "greedy": it only looks
    one step ahead, so it can still trap itself in a dead end, which is what
    ends a demo game.

    Args:
        snake: list of (x, y) cells, the head first.
        food: the (x, y) cell of the food.
        current: the direction the snake is moving now, as (dx, dy).

    Returns:
        The direction to move next, as (dx, dy).
    """
    head_x, head_y = snake[0]
    food_x, food_y = food

    # First choice: the moves that bring the head closer to the food
    # (sideways if the food is in another column, up/down if in another row)
    preferred = []
    if food_x != head_x:
        preferred.append((1 if food_x > head_x else -1, 0))
    if food_y != head_y:
        preferred.append((0, 1 if food_y > head_y else -1))

    # Try those first, then any other direction, and take the first one that
    # does not crash. (Going back into the neck counts as blocked, because the
    # neck is part of the snake.)
    for dx, dy in preferred + DIRECTIONS:
        if not is_blocked((head_x + dx, head_y + dy), snake):
            return dx, dy
    return current   # Trapped: crash


def new_food_color(previous=None):
    """A random food colour, never the same as the previous one.

    Args:
        previous: the colour of the last food, or None for the first one.

    Returns:
        An (r, g, b) colour from FOOD_COLORS.
    """
    return random.choice([c for c in FOOD_COLORS if c != previous])


def draw(sense, snake, food, food_color):
    """Show the snake and the food on the LEDs.

    Args:
        sense: the SenseHat object to draw on.
        snake: list of (x, y) cells, the head first (drawn in a lighter green).
        food: the (x, y) cell of the food, or None when there is none.
        food_color: the colour to draw the food in.
    """
    # Build the picture as 8 rows of 8 colours (frame[y][x]), then send it in one go
    frame = [[OFF] * 8 for _ in range(8)]
    for x, y in snake[1:]:
        frame[y][x] = SNAKE_COLOR
    head_x, head_y = snake[0]
    frame[head_y][head_x] = HEAD_COLOR
    if food:
        frame[food[1]][food[0]] = food_color
    sense.set_pixels(flatten(frame))


def wait_for_next_step(stick, human, direction, wanted):
    """Wait STEP_DELAY seconds while listening to the joystick.

    Returns the direction the player wants next, or "start" if someone touched
    the joystick during the demo.

    Instead of one long sleep, it sleeps in small WAIT_STEP slices and reads
    the joystick in between, so a quick tap is never missed. Only the last
    valid press before the step counts.

    Args:
        stick: the Joystick.
        human: True when a person is playing, False during the demo.
        direction: the direction the snake is moving now, as (dx, dy).
        wanted: the direction the player had already asked for.

    Returns:
        The (dx, dy) direction to take next, or the text "start".
    """
    # E.g. 0.3 / 0.02 = 15 short waits
    for _ in range(int(STEP_DELAY / WAIT_STEP)):
        for key in stick.read():
            # During the demo any press means "I want to play"
            if not human:
                return "start"
            # Ignore "middle" (KEYS has no entry for it, so new is None)
            new = KEYS.get(key)
            if new and new != (-direction[0], -direction[1]):   # No turning back into yourself
                wanted = new
        time.sleep(WAIT_STEP)
    return wanted


def play_game(sense, stick, human):
    """Play one game. Returns (result, score); result is "won", "lost" or "start"
    (someone touched the joystick during the demo). The score is the food eaten.

    Args:
        sense: the SenseHat object.
        stick: the Joystick.
        human: True for a game played by a person, False for the computer demo.
    """
    # The snake is a list of cells with the head first: 3 pixels long to begin
    # with, moving right. The score is how much it grew: its length minus 3.
    snake = [(3, 4), (3, 3), (3, 2)]
    direction = (1, 0)
    wanted = direction
    food = place_food(snake)
    food_color = new_food_color()
    draw(sense, snake, food, food_color)

    while True:
        # Wait for the next step, listening to the joystick meanwhile
        wanted = wait_for_next_step(stick, human, direction, wanted)
        if wanted == "start":
            return "start", 0
        # The player steers with the joystick; in the demo the computer decides
        direction = wanted if human else choose_direction(snake, food, direction)
        # Where the head goes next; hitting a wall or the snake ends the game
        head = (snake[0][0] + direction[0], snake[0][1] + direction[1])
        if is_blocked(head, snake):
            return "lost", len(snake) - 3

        # Moving = add a new head at the front ...
        snake.insert(0, head)
        if head == food:
            # ... and when it ate the food, keep the tail too, so the snake
            # grows by one. New food appears; no room left means you won.
            food = place_food(snake)
            food_color = new_food_color(food_color)
            if food is None:
                return "won", len(snake) - 3
        else:
            # ... otherwise remove the tail, so the length stays the same
            snake.pop()

        draw(sense, snake, food, food_color)


def main():
    """Keep playing forever: demo games until someone takes the joystick.

    After each game "WIN" or "RIP" scrolls by (plus the score after a human's
    game), and the computer starts a new demo.
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

        # Game over: say how it ended (the demo's score is not shown)
        if result == "won":
            sense.show_message("WIN", text_colour=SNAKE_COLOR, scroll_speed=0.07)
        else:
            sense.show_message("RIP", text_colour=RIP_COLOR, scroll_speed=0.07)
        if human:
            sense.show_message(f"Score {score}", text_colour=HEAD_COLOR,
                               scroll_speed=0.07)
        human = False   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
