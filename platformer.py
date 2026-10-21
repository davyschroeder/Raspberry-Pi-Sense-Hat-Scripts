#!/usr/bin/env python3
"""Sense HAT – Platformer: a little red hero runs through random levels by itself.

Watch the hero jump over pipes and pits, grab coins, stomp walking enemies
and dodge birds on the way to the flag. The hero has 3 lives and now and
then reacts a moment too late, so you never know if it will make it.
Every level is a bit harder than the last. When all lives are gone, the
score scrolls by and a new game starts.

The computer plays a demo until you touch the joystick: then you play.
Left and right to run, up or press to jump. After game over, or when you
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


# Colours
SKY = (0, 0, 50)
GROUND = (101, 67, 33)
BLOCK = (150, 90, 40)          # Stairs and floating platforms
PIPE = (0, 200, 0)
COIN = (255, 200, 0)
COIN_DIM = (170, 120, 0)       # Coins twinkle between these two
HERO = (255, 0, 0)
ENEMY = (139, 69, 19)
BIRD = (180, 180, 255)
FLAG = (255, 255, 255)
CASTLE = (80, 80, 80)
LIFE = (255, 0, 0)
LIFE_LOST = (50, 0, 0)
FIREWORK_COLORS = [(255, 0, 0), (255, 255, 0), (0, 255, 0), (0, 150, 255)]

# Gameplay
WORLD_WIDTH = 128
FLAG_X = WORLD_WIDTH - 3       # Column of the flag pole; the castle is behind it
JUMP_HEIGHT = 3
LIVES = 3
TICK = 0.2                     # Seconds per game step
MISTAKE_CHANCE = 0.06          # Chance the hero reacts too late to a danger
HESITATE_CHANCE = 0.06         # Chance the hero takes a step back on safe ground
COIN_JUMP_CHANCE = 0.5         # Chance the hero jumps for coins it can reach
BIRD_CHANCE = 0.012            # Chance per step that a bird flies in (grows per level)
LEVEL_TIMEOUT = 120            # Seconds before a stuck level is started again


def is_solid(world, x, y):
    """True if (x, y) blocks movement. Below the screen is open (pits)."""
    if not 0 <= x < WORLD_WIDTH:
        return True
    if not 0 <= y < 8:
        return False
    return world[y][x] not in (SKY, COIN)


def is_pit(world, x):
    return 0 <= x < WORLD_WIDTH and world[7][x] == SKY


class Walker:
    """An enemy that walks along the ground and turns at walls and pits."""

    def __init__(self, x):
        self.x = x
        self.y = 6
        self.direction = random.choice([-1, 1])

    def update(self, world):
        if random.random() < 0.3:
            nx = self.x + self.direction
            if is_solid(world, nx, self.y) or is_pit(world, nx):
                self.direction = -self.direction
            else:
                self.x = nx


class Bird:
    """An enemy that flies in from the right at jumping height."""

    def __init__(self, x):
        self.x = x
        self.y = random.choice([3, 4])

    def update(self):
        self.x -= 1


class Hero:
    def __init__(self, x):
        self.x = x
        self.y = 6
        self.jump = 0       # Steps of upward movement left
        self.dead = False
        self.hop = 0        # Little hop up before falling off the screen

    def on_ground(self, world):
        return is_solid(world, self.x, self.y + 1)

    def die(self, hop=True):
        self.dead = True
        self.hop = 1 if hop else 0

    def grab_coin(self, world, game):
        if 0 <= self.y < 8 and world[self.y][self.x] == COIN:
            world[self.y][self.x] = SKY
            game["score"] += 1

    def danger_ahead(self, world, walkers):
        """Something in front that needs a jump: a wall, a pit or an enemy."""
        return (is_solid(world, self.x + 1, self.y)
                or (self.y == 6 and is_pit(world, self.x + 1))
                or any(w.y == self.y and 0 < w.x - self.x <= 2 for w in walkers))

    def coins_in_reach(self, world):
        return any(0 <= self.x + dx < WORLD_WIDTH and world[self.y - dy][self.x + dx] == COIN
                   for dx in (1, 2, 3) for dy in (2, 3) if self.y - dy >= 0)

    def update(self, world, walkers, birds, game, controls=None):
        """Advance one step. Returns "won", "dead" or None.

        controls is None when the computer plays, or (step, jump) when you do:
        step is -1, 0 or 1 and jump is True when the jump button is pressed.
        """
        if self.dead:
            if self.hop:
                self.y -= 1
                self.hop -= 1
            else:
                self.y += 1
            return "dead" if self.y > 8 else None

        # Vertical movement: rise while jumping, otherwise fall
        if self.jump > 0:
            self.jump -= 1
            if not is_solid(world, self.x, self.y - 1):
                self.y -= 1
            else:
                self.jump = 0   # Bumped our head
        elif not self.on_ground(world):
            self.y += 1
            if self.y > 7:      # Fell into a pit
                self.die(hop=False)
                return None
        self.grab_coin(world, game)

        # Landing on an enemy squashes it
        if self.jump == 0:
            for enemy in walkers + birds:
                if enemy.x == self.x and enemy.y == self.y + 1:
                    (walkers if enemy in walkers else birds).remove(enemy)
                    game["score"] += 1
                    break

        grounded = self.on_ground(world)
        if controls is not None:                # You play: do what the joystick says
            step, want_jump = controls
            if self.jump == 0 and grounded and want_jump:
                self.jump = JUMP_HEIGHT
                if not is_solid(world, self.x, self.y - 1):
                    self.y -= 1
                    self.jump -= 1
                    self.grab_coin(world, game)
            if step and not is_solid(world, self.x + step, self.y):
                self.x += step
                self.grab_coin(world, game)
            return self.result()

        # On the ground: look ahead first and decide whether to jump
        if self.jump == 0 and grounded:
            bird_near = any(0 <= b.x - self.x <= 4 for b in birds)
            if self.danger_ahead(world, walkers):
                if random.random() >= MISTAKE_CHANCE:
                    self.jump = JUMP_HEIGHT
                elif is_pit(world, self.x + 1):
                    pass    # Too late: about to run straight into the pit
                else:
                    return self.result()
            elif self.coins_in_reach(world) and not bird_near:
                if random.random() < COIN_JUMP_CHANCE:
                    self.jump = JUMP_HEIGHT
            if self.jump and not is_solid(world, self.x, self.y - 1):
                self.y -= 1             # Take off right away
                self.jump -= 1
                self.grab_coin(world, game)

        # Mostly run right; on safe ground, sometimes hesitate and step back
        safe_behind = not is_pit(world, self.x - 1)
        step = 1
        if grounded and not self.jump and safe_behind and random.random() < HESITATE_CHANCE:
            step = -1
        if not is_solid(world, self.x + step, self.y):
            self.x += step
            self.grab_coin(world, game)

        return self.result()

    def result(self):
        if self.x + 1 >= FLAG_X:
            return "won"
        return None


def generate_level(level):
    world = [[SKY] * WORLD_WIDTH for _ in range(8)]
    for x in range(WORLD_WIDTH):
        world[7][x] = GROUND
    walkers = []
    walker_chance = min(0.6, 0.25 + 0.07 * level)
    max_pipe = 2 if level == 1 else 3
    max_pit = 2 if level < 3 else 3

    x = 10
    while x < FLAG_X - 12:
        kind = random.choice(["flat", "pipe", "pit", "platform", "stairs", "coins"])
        if kind == "flat":
            x += random.randint(2, 4)
        elif kind == "pipe":
            for y in range(7 - random.randint(1, max_pipe), 7):
                world[y][x] = PIPE
            x += 4
        elif kind == "pit":
            width = random.randint(1, max_pit)
            for i in range(width):
                world[7][x + i] = SKY
            x += width + 4
        elif kind == "platform":
            for i in range(3):
                world[4][x + i] = BLOCK
                world[3][x + i] = COIN
            x += 6
        elif kind == "stairs":
            for i, height in enumerate((1, 2, 3)):
                for y in range(7 - height, 7):
                    world[y][x + i] = BLOCK
            x += 6
        elif kind == "coins":
            for i in range(3):
                world[4][x + i] = COIN
            x += 4

        if random.random() < walker_chance and world[7][x] == GROUND and world[6][x] == SKY:
            walkers.append(Walker(x))
            x += 2

    for y in range(3, 7):
        world[y][FLAG_X] = FLAG
    for y in range(4, 7):
        for x in range(FLAG_X + 1, WORLD_WIDTH):
            world[y][x] = CASTLE

    return world, walkers


def draw(sense, world, hero, walkers, birds, tick):
    camera = max(0, min(WORLD_WIDTH - 8, hero.x - 3))
    coin = COIN if tick % 4 < 2 else COIN_DIM
    frame = [[coin if c == COIN else c for c in row[camera:camera + 8]] for row in world]

    for enemy in walkers:
        if 0 <= enemy.x - camera < 8:
            frame[enemy.y][enemy.x - camera] = ENEMY
    for bird in birds:
        if 0 <= bird.x - camera < 8:
            frame[bird.y][bird.x - camera] = BIRD
    if 0 <= hero.y < 8:
        frame[hero.y][hero.x - camera] = HERO

    sense.set_pixels(flatten(frame))


def show_lives(sense, lives):
    """Three 2x2 hearts: bright for lives left, dim for lives lost."""
    frame = [[(0, 0, 0)] * 8 for _ in range(8)]
    for i in range(LIVES):
        color = LIFE if i < lives else LIFE_LOST
        for dx in (0, 1):
            for dy in (0, 1):
                frame[3 + dy][i * 3 + dx] = color
    sense.set_pixels(flatten(frame))
    time.sleep(1)


def is_safe_spot(world, hero, walkers):
    """A good place to come back after losing a life."""
    return (not hero.dead and hero.y == 6 and hero.on_ground(world)
            and not any(is_pit(world, hero.x + dx) for dx in (-1, 0, 1, 2))
            and not any(abs(w.x - hero.x) <= 3 for w in walkers))


def flag_finish(sense, world, hero, walkers, birds, game):
    """Slide down the pole, walk into the castle and celebrate."""
    while hero.y < 6:
        hero.y += 1
        draw(sense, world, hero, walkers, birds, 0)
        time.sleep(0.15)
    for _ in range(2):
        hero.x += 1 if not is_solid(world, hero.x + 1, hero.y) else 0
        draw(sense, world, hero, walkers, birds, 0)
        time.sleep(0.2)
    game["score"] += 5
    for _ in range(6):
        for _ in range(5):
            sense.set_pixel(random.randint(0, 7), random.randint(0, 7),
                            random.choice(FIREWORK_COLORS))
        time.sleep(0.2)
        sense.clear()
        time.sleep(0.1)


def read_controls(stick, game):
    """Read the joystick. Returns the hero's controls, None for the computer, or
    "start" when someone touches the joystick during the demo."""
    pressed = stick.read()
    if not game["human"]:
        return "start" if pressed else None
    if stick.idle():                     # Player walked away: computer takes over
        game["human"] = False
        return None
    step = (("right" in stick.held or "right" in pressed)
            - ("left" in stick.held or "left" in pressed))
    jump = any(k in pressed or k in stick.held for k in ("up", "middle"))
    return step, jump


def play_level(sense, level, game, stick):
    """Play one level. Returns True when the flag is reached, False on game over,
    or "start" when someone touches the joystick during the demo."""
    world, walkers = generate_level(level)
    birds = []
    hero = Hero(2)
    checkpoint = 2
    bird_chance = BIRD_CHANCE * level
    start = time.monotonic()
    tick = 0

    while time.monotonic() - start < LEVEL_TIMEOUT:
        tick += 1
        controls = read_controls(stick, game)
        if controls == "start":
            return "start"
        result = hero.update(world, walkers, birds, game, controls)

        for walker in walkers:
            walker.update(world)
        for bird in birds:
            bird.update()
        camera = max(0, min(WORLD_WIDTH - 8, hero.x - 3))
        birds = [b for b in birds if b.x >= camera - 2]
        if random.random() < bird_chance and hero.x < FLAG_X - 10:
            birds.append(Bird(camera + 9))

        if not hero.dead and any(e.x == hero.x and e.y == hero.y for e in walkers + birds):
            hero.die()
        if is_safe_spot(world, hero, walkers):
            checkpoint = hero.x

        draw(sense, world, hero, walkers, birds, tick)

        if result == "won":
            flag_finish(sense, world, hero, walkers, birds, game)
            return True
        if result == "dead":
            game["lives"] -= 1
            show_lives(sense, game["lives"])
            if game["lives"] == 0:
                return False
            hero = Hero(checkpoint)
            walkers = [w for w in walkers if abs(w.x - checkpoint) > 4]
            birds = []

        time.sleep(TICK)

    return play_level(sense, level, game, stick)   # Stuck: try this level again


def game_over(sense, game):
    for _ in range(2):
        sense.clear(120, 0, 0)
        time.sleep(0.2)
        sense.clear()
        time.sleep(0.2)
    sense.show_message(f"Score {game['score']}", text_colour=COIN, scroll_speed=0.06)


def main():
    sense = init(hat)
    stick = Joystick(sense)
    human = False
    while True:
        game = {"lives": LIVES, "score": 0, "human": human}
        level = 1
        show_lives(sense, game["lives"])
        while True:
            sense.show_message(f"L{level}", text_colour=COIN, scroll_speed=0.06)
            result = play_level(sense, level, game, stick)
            if result is not True:
                break
            level += 1

        if result == "start":           # Someone grabbed the joystick: new game, you play
            show_play_sign(sense)
            stick.flush()
            human = True
            continue

        game_over(sense, game)
        human = False                   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
