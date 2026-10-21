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


SNAKE_COLOR = (0, 255, 0)
HEAD_COLOR = (150, 255, 150)   # A lighter green, so you can see where the snake is going
RIP_COLOR = (255, 0, 0)
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
STEP_DELAY = 0.3
DIRECTIONS = [(1, 0), (0, 1), (-1, 0), (0, -1)]
KEYS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
WAIT_STEP = 0.02   # Check the joystick this often while waiting for the next step


def place_food(snake):
    """Return a random free cell, or None if the snake fills the whole board."""
    free = [(x, y) for x in range(8) for y in range(8) if (x, y) not in snake]
    return random.choice(free) if free else None


def is_blocked(cell, snake):
    x, y = cell
    return not (0 <= x <= 7 and 0 <= y <= 7) or cell in snake


def choose_direction(snake, food, current):
    """Head towards the food, but never straight into a wall or itself."""
    head_x, head_y = snake[0]
    food_x, food_y = food

    preferred = []
    if food_x != head_x:
        preferred.append((1 if food_x > head_x else -1, 0))
    if food_y != head_y:
        preferred.append((0, 1 if food_y > head_y else -1))

    for dx, dy in preferred + DIRECTIONS:
        if not is_blocked((head_x + dx, head_y + dy), snake):
            return dx, dy
    return current   # Trapped: crash


def new_food_color(previous=None):
    """A random food colour, never the same as the previous one."""
    return random.choice([c for c in FOOD_COLORS if c != previous])


def draw(sense, snake, food, food_color):
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
    """
    for _ in range(int(STEP_DELAY / WAIT_STEP)):
        for key in stick.read():
            if not human:
                return "start"
            new = KEYS.get(key)
            if new and new != (-direction[0], -direction[1]):   # No turning back into yourself
                wanted = new
        time.sleep(WAIT_STEP)
    return wanted


def play_game(sense, stick, human):
    """Play one game. Returns (result, score); result is "won", "lost" or "start"
    (someone touched the joystick during the demo). The score is the food eaten."""
    snake = [(3, 4), (3, 3), (3, 2)]
    direction = (1, 0)
    wanted = direction
    food = place_food(snake)
    food_color = new_food_color()
    draw(sense, snake, food, food_color)

    while True:
        wanted = wait_for_next_step(stick, human, direction, wanted)
        if wanted == "start":
            return "start", 0
        direction = wanted if human else choose_direction(snake, food, direction)
        head = (snake[0][0] + direction[0], snake[0][1] + direction[1])
        if is_blocked(head, snake):
            return "lost", len(snake) - 3

        snake.insert(0, head)
        if head == food:
            food = place_food(snake)
            food_color = new_food_color(food_color)
            if food is None:
                return "won", len(snake) - 3
        else:
            snake.pop()

        draw(sense, snake, food, food_color)


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
