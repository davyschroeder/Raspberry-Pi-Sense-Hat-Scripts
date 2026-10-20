#!/usr/bin/env python3
"""Sense HAT – Snake: a computer-controlled snake hunts for food until it crashes."""
import random
import time

try:
    from sense_hat import SenseHat
    hat = SenseHat()
except (ImportError, OSError):  # No Sense HAT library or board found: use the emulator
    from sense_emu import SenseHat
    hat = SenseHat()

from common import flatten, init, run

SNAKE_COLOR = (0, 255, 0)
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
    for x, y in snake:
        frame[y][x] = SNAKE_COLOR
    if food:
        frame[food[1]][food[0]] = food_color
    sense.set_pixels(flatten(frame))


def play_game(sense):
    """Play one game. Returns True if the snake filled the board."""
    snake = [(3, 4), (3, 3), (3, 2)]
    direction = (1, 0)
    food = place_food(snake)
    food_color = new_food_color()

    while True:
        direction = choose_direction(snake, food, direction)
        head = (snake[0][0] + direction[0], snake[0][1] + direction[1])
        if is_blocked(head, snake):
            return False

        snake.insert(0, head)
        if head == food:
            food = place_food(snake)
            food_color = new_food_color(food_color)
            if food is None:
                return True
        else:
            snake.pop()

        draw(sense, snake, food, food_color)
        time.sleep(STEP_DELAY)


def main():
    sense = init(hat)
    while True:
        if play_game(sense):
            sense.show_message("WIN", text_colour=SNAKE_COLOR, scroll_speed=0.07)
        else:
            sense.show_message("RIP", text_colour=RIP_COLOR, scroll_speed=0.07)
        time.sleep(1)


if __name__ == "__main__":
    run(main)
