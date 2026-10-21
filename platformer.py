#!/usr/bin/env python3
"""Sense HAT – Platformer: a little red hero runs through random levels by itself.

Under a light blue sky with clouds drifting by in the background, watch the
hero jump over pipes and pits, grab coins, stomp walking enemies
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

# A "play" triangle (like on a music player), shown when a person takes over.
# "#" = green LED, "." = LED off.
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


# Colours, as (R, G, B) tuples: red, green and blue, each from 0 (off) to 255 (full).
# The level itself is stored as a grid of these colours, so a colour also says
# what a spot *is*: GROUND, PIPE and BLOCK are solid, SKY and COIN are not.
SKY = (0, 0, 50)              # Marks empty space in the level (drawn as SKY_COL)
SKY_COL = (30, 100, 230)       # A bright, light blue sky
CLOUD = (225, 235, 245)        # Little clouds drifting by in the background
GROUND = (101, 67, 33)         # Brown earth along the bottom row
BLOCK = (150, 90, 40)          # Stairs and floating platforms
PIPE = (0, 200, 0)             # Green pipes to jump over
COIN = (255, 200, 0)           # Gold coins: 1 point each
COIN_DIM = (170, 120, 0)       # Coins twinkle between these two
HERO = (255, 0, 0)             # The hero is a single red LED
ENEMY = (139, 69, 19)          # Walking enemies
BIRD = (30, 30, 90)            # Dark, so birds stand out against the light sky
FLAG = (255, 255, 255)         # The flag pole at the end of the level
CASTLE = (80, 80, 80)          # The grey castle behind the flag
LIFE = (255, 0, 0)             # A life you still have (on the lives screen)
LIFE_LOST = (50, 0, 0)         # A life you have lost
FIREWORK_COLORS = [(255, 0, 0), (255, 255, 0), (0, 255, 0), (0, 150, 255)]  # Level done!

# Gameplay
WORLD_WIDTH = 128              # Level length in columns; the display shows 8 at a time
FLAG_X = WORLD_WIDTH - 3       # Column of the flag pole; the castle is behind it
JUMP_HEIGHT = 3                # Rows the hero rises in one jump (higher = easier)
LIVES = 3                      # Lives per game
TICK = 0.2                     # Seconds per game step (lower = faster game)
MISTAKE_CHANCE = 0.06          # Chance the hero reacts too late to a danger
HESITATE_CHANCE = 0.06         # Chance the hero takes a step back on safe ground
COIN_JUMP_CHANCE = 0.5         # Chance the hero jumps for coins it can reach
BIRD_CHANCE = 0.012            # Chance per step that a bird flies in (grows per level)
LEVEL_TIMEOUT = 120            # Seconds before a stuck level is started again


def is_solid(world, x, y):
    """True if (x, y) blocks movement. Below the screen is open (pits).

    Args:
        world: the level, a list of 8 rows of WORLD_WIDTH colours.
            world[y][x] is column x (0 = start of the level) in row y
            (0 = top of the display, 7 = the ground row).
        x, y: the spot to check.

    Returns:
        True for walls, ground, pipes, blocks and so on. Sky and coins are not
        solid, so you can move through them. Left and right of the level count
        as solid (invisible walls); above and below the display count as open,
        so you can jump up past the top and fall down into pits.
    """
    if not 0 <= x < WORLD_WIDTH:
        return True
    if not 0 <= y < 8:
        return False
    return world[y][x] not in (SKY, COIN)


def is_pit(world, x):
    """True if column x has a hole in the ground (sky in the bottom row).

    Args:
        world: the level (see is_solid()).
        x: the column to check. Columns outside the level are never pits.
    """
    return 0 <= x < WORLD_WIDTH and world[7][x] == SKY


class Walker:
    """An enemy that walks along the ground and turns at walls and pits."""

    def __init__(self, x):
        """Put a walker in column x, standing on the ground (row 6), facing a random way."""
        self.x = x
        self.y = 6
        self.direction = random.choice([-1, 1])     # -1 = walking left, 1 = right

    def update(self, world):
        """Maybe take one step. Walkers are slow: they only move on about 30% of steps.

        Args:
            world: the level (see is_solid()), to see walls and pits.

        If the next spot is a wall or a pit, the walker turns around instead,
        so walkers never fall into pits.
        """
        if random.random() < 0.3:
            nx = self.x + self.direction
            if is_solid(world, nx, self.y) or is_pit(world, nx):
                self.direction = -self.direction
            else:
                self.x = nx


class Bird:
    """An enemy that flies in from the right at jumping height."""

    def __init__(self, x):
        """Start a bird in column x, in row 3 or 4: the height the hero reaches while jumping.

        So a bird is dangerous mid-jump, and you can also jump on it from above.
        """
        self.x = x
        self.y = random.choice([3, 4])

    def update(self):
        """Fly one column to the left. Birds ignore walls and fly straight through."""
        self.x -= 1


class Hero:
    """The player's character: one red LED that runs, jumps, collects coins and
    squashes enemies.

    The same class is used for the computer demo and for a person playing:
    update() either follows the joystick, or makes its own decisions.
    """

    def __init__(self, x):
        """Put the hero in column x, standing on the ground (row 6)."""
        self.x = x
        self.y = 6
        self.jump = 0       # Steps of upward movement left
        self.dead = False
        self.hop = 0        # Little hop up before falling off the screen

    def on_ground(self, world):
        """True when there is something solid right below the hero to stand on."""
        return is_solid(world, self.x, self.y + 1)

    def die(self, hop=True):
        """Mark the hero as dead. update() then plays the falling-off-the-screen animation.

        Args:
            hop: True for a little hop up first (when hit by an enemy, like in
                classic platform games), False to just keep falling (into a pit).
        """
        self.dead = True
        self.hop = 1 if hop else 0

    def grab_coin(self, world, game):
        """If the hero stands on a coin, take it away and add 1 point to game["score"].

        Args:
            world: the level; the coin is removed from it (replaced with sky).
            game: the dict that holds the score and lives (see main()).
        """
        if 0 <= self.y < 8 and world[self.y][self.x] == COIN:
            world[self.y][self.x] = SKY
            game["score"] += 1

    def danger_ahead(self, world, walkers):
        """Something in front that needs a jump: a wall, a pit or an enemy.

        Used by the computer player. Returns True when the very next column is
        solid at the hero's height, or is a pit (only checked when the hero walks
        at ground level, row 6), or when a walker is 1 or 2 columns ahead on the
        same row.
        """
        return (is_solid(world, self.x + 1, self.y)
                or (self.y == 6 and is_pit(world, self.x + 1))
                or any(w.y == self.y and 0 < w.x - self.x <= 2 for w in walkers))

    def coins_in_reach(self, world):
        """True when a coin is close enough to collect with a jump.

        Used by the computer player. It looks 1 to 3 columns ahead and 2 to 3
        rows up: the area a jump of JUMP_HEIGHT passes through while running right.
        """
        return any(0 <= self.x + dx < WORLD_WIDTH and world[self.y - dy][self.x + dx] == COIN
                   for dx in (1, 2, 3) for dy in (2, 3) if self.y - dy >= 0)

    def update(self, world, walkers, birds, game, controls=None):
        """Advance one step. Returns "won", "dead" or None.

        controls is None when the computer plays, or (step, jump) when you do:
        step is -1, 0 or 1 and jump is True when the jump button is pressed.

        Args:
            world: the level (see is_solid()); coins are removed from it when taken.
            walkers, birds: the lists of enemies; squashed ones are removed.
            game: the dict with the score (see main()).
            controls: see above.

        Returns:
            "won" when the hero reached the flag, "dead" once a dead hero has
            fallen off the bottom of the display, or None otherwise.

        The order each step: falling or rising, then coins, then squashing
        enemies, and then moving left or right (from the joystick, or decided by
        the computer).
        """
        # A dead hero no longer listens to anything: hop up (if needed), then fall
        # until it is below the display (row 8 or lower)
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
        # (only when not going up, and the enemy is right below the hero's feet;
        # it gives 1 point. `break`: squash at most one enemy per step)
        if self.jump == 0:
            for enemy in walkers + birds:
                if enemy.x == self.x and enemy.y == self.y + 1:
                    (walkers if enemy in walkers else birds).remove(enemy)
                    game["score"] += 1
                    break

        grounded = self.on_ground(world)
        if controls is not None:                # You play: do what the joystick says
            step, want_jump = controls
            # You can only start a jump while standing on something. The first row
            # of the jump happens right away, so the jump feels quick.
            if self.jump == 0 and grounded and want_jump:
                self.jump = JUMP_HEIGHT
                if not is_solid(world, self.x, self.y - 1):
                    self.y -= 1
                    self.jump -= 1
                    self.grab_coin(world, game)
            # Walk one column left or right, unless a wall is in the way
            if step and not is_solid(world, self.x + step, self.y):
                self.x += step
                self.grab_coin(world, game)
            return self.result()

        # From here on the computer plays (the demo).
        # On the ground: look ahead first and decide whether to jump
        if self.jump == 0 and grounded:
            # A bird 0-4 columns ahead could hit the hero mid-jump at rows 3-4
            bird_near = any(0 <= b.x - self.x <= 4 for b in birds)
            if self.danger_ahead(world, walkers):
                # Usually jump over the danger. But with a small chance (MISTAKE_CHANCE)
                # the hero reacts too late, so the demo isn't perfect and can lose lives
                if random.random() >= MISTAKE_CHANCE:
                    self.jump = JUMP_HEIGHT
                elif is_pit(world, self.x + 1):
                    pass    # Too late: about to run straight into the pit
                else:
                    # Too late for a wall or enemy: stand still this step and
                    # (most likely) jump next time
                    return self.result()
            # No danger: sometimes jump for coins, but not with a bird coming
            elif self.coins_in_reach(world) and not bird_near:
                if random.random() < COIN_JUMP_CHANCE:
                    self.jump = JUMP_HEIGHT
            if self.jump and not is_solid(world, self.x, self.y - 1):
                self.y -= 1             # Take off right away
                self.jump -= 1
                self.grab_coin(world, game)

        # Mostly run right; on safe ground, sometimes hesitate and step back
        # (never back into a pit, and not in mid-air), to look more like a person
        safe_behind = not is_pit(world, self.x - 1)
        step = 1
        if grounded and not self.jump and safe_behind and random.random() < HESITATE_CHANCE:
            step = -1
        if not is_solid(world, self.x + step, self.y):
            self.x += step
            self.grab_coin(world, game)

        return self.result()

    def result(self):
        """Return "won" when the hero is right next to the flag pole, otherwise None."""
        if self.x + 1 >= FLAG_X:
            return "won"
        return None


# Small cloud shapes ("#" = cloud) for the background
CLOUD_SHAPES = [
    [".##.", "####"],
    ["##"],
    [".#..", "####", ".##."],
    ["###"],
]
clouds = [[False] * WORLD_WIDTH for _ in range(3)]   # Background layer: rows 0-2


def generate_clouds():
    """Scatter a few clouds over the background layer, with gaps of clear sky.

    Takes no arguments and returns nothing: it fills the global `clouds` grid
    (3 rows of True = cloud / False = clear sky). The clouds are only decoration;
    they are kept apart from the level so they never get in the hero's way.
    """
    # `global` lets this function replace the clouds grid defined outside it
    global clouds
    clouds = [[False] * WORLD_WIDTH for _ in range(3)]
    # Walk from left to right, dropping a random cloud shape at a random height,
    # then skipping ahead past the cloud plus a gap of 6-16 columns
    x = random.randint(0, 6)
    while x < WORLD_WIDTH - 4:
        shape = random.choice(CLOUD_SHAPES)
        top = random.randint(0, 3 - len(shape))
        for dy, line in enumerate(shape):
            for dx, mark in enumerate(line):
                if mark == "#" and x + dx < WORLD_WIDTH:
                    clouds[top + dy][x + dx] = True
        x += len(shape[0]) + random.randint(6, 16)


def generate_level(level):
    """Build a new random level. Higher levels get more enemies, taller pipes and wider pits.

    Args:
        level: the level number, starting at 1.

    Returns:
        A tuple (world, walkers): world is the level as 8 rows of WORLD_WIDTH
        colours (see is_solid()), and walkers is a list of Walker enemies.

    How it works: it starts with flat ground and then walks from left to right,
    placing one random piece at a time (a pipe, a pit, stairs, ...), each followed
    by some room to land. The first 10 columns are kept clear for a calm start,
    and the last part is kept clear for the flag pole and the castle.
    """
    generate_clouds()
    # Start with an empty sky and a full row of ground at the bottom
    world = [[SKY] * WORLD_WIDTH for _ in range(8)]
    for x in range(WORLD_WIDTH):
        world[7][x] = GROUND
    walkers = []
    # Difficulty: the chance of a walker after each piece grows by 7% per level
    # (at most 60%); pipes are at most 2 high in level 1, pits at most 2 wide
    # until level 3
    walker_chance = min(0.6, 0.25 + 0.07 * level)
    max_pipe = 2 if level == 1 else 3
    max_pit = 2 if level < 3 else 3

    # x is the column where the next piece goes; every piece moves it further right
    x = 10
    while x < FLAG_X - 12:
        kind = random.choice(["flat", "pipe", "pit", "platform", "stairs", "coins"])
        if kind == "flat":                  # Just some plain ground
            x += random.randint(2, 4)
        elif kind == "pipe":                # A pipe standing on the ground, 1 to max_pipe high
            for y in range(7 - random.randint(1, max_pipe), 7):
                world[y][x] = PIPE
            x += 4
        elif kind == "pit":                 # A hole in the ground: fall in and you lose a life
            width = random.randint(1, max_pit)
            for i in range(width):
                world[7][x + i] = SKY
            x += width + 4
        elif kind == "platform":            # A floating 3-block platform with coins on top
            for i in range(3):
                world[4][x + i] = BLOCK
                world[3][x + i] = COIN
            x += 6
        elif kind == "stairs":              # Three steps going up: 1, 2 and 3 blocks high
            for i, height in enumerate((1, 2, 3)):
                for y in range(7 - height, 7):
                    world[y][x + i] = BLOCK
            x += 6
        elif kind == "coins":               # Three coins in the air, to collect with a jump
            for i in range(3):
                world[4][x + i] = COIN
            x += 4

        # Maybe put a walker here, but only on open ground (not in a pit or a pipe)
        if random.random() < walker_chance and world[7][x] == GROUND and world[6][x] == SKY:
            walkers.append(Walker(x))
            x += 2

    # The end of the level: a flag pole 4 rows high, with a castle behind it
    for y in range(3, 7):
        world[y][FLAG_X] = FLAG
    for y in range(4, 7):
        for x in range(FLAG_X + 1, WORLD_WIDTH):
            world[y][x] = CASTLE

    return world, walkers


def draw(sense, world, hero, walkers, birds, tick):
    """Show the part of the level around the hero on the 8x8 display.

    Args:
        sense: the SenseHat object to draw on.
        world: the level (see is_solid()).
        hero: the Hero.
        walkers, birds: the enemies.
        tick: the step number, used to make the coins twinkle.

    How it works: a "camera" picks which 8 columns of the long level to show.
    It follows the hero so that the hero stays in column 3 of the display,
    which leaves more room to see what is coming from the right. Returns nothing.
    """
    # The camera is the level column shown at the left edge of the display. It
    # stops at both ends of the level, so it never shows anything outside it.
    camera = max(0, min(WORLD_WIDTH - 8, hero.x - 3))
    # Coins are bright for 2 steps and dim for 2 steps: a twinkle
    coin = COIN if tick % 4 < 2 else COIN_DIM
    # Cut the 8 visible columns out of every row (row[camera:camera + 8]),
    # swapping in the twinkling coin colour
    frame = [[coin if c == COIN else c for c in row[camera:camera + 8]] for row in world]

    # Light blue sky; the clouds drift by at half speed (parallax), behind everything else
    for y in range(8):
        for x in range(8):
            if frame[y][x] == SKY:
                cx = x + camera // 2
                frame[y][x] = CLOUD if y < 3 and clouds[y][cx] else SKY_COL

    # Draw the enemies and then the hero on top, but only the ones that are on screen
    # (their level column minus the camera gives their column on the display)
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
    """Three 2x2 hearts: bright for lives left, dim for lives lost.

    Args:
        sense: the SenseHat object to draw on.
        lives: how many lives are left (0 to LIVES).

    The "hearts" are 2x2 red squares in the middle rows, 3 columns apart
    (columns 0-1, 3-4 and 6-7). They stay on screen for 1 second.
    """
    frame = [[(0, 0, 0)] * 8 for _ in range(8)]
    for i in range(LIVES):
        color = LIFE if i < lives else LIFE_LOST
        for dx in (0, 1):
            for dy in (0, 1):
                frame[3 + dy][i * 3 + dx] = color
    sense.set_pixels(flatten(frame))
    time.sleep(1)


def is_safe_spot(world, hero, walkers):
    """A good place to come back after losing a life.

    Args:
        world: the level (see is_solid()).
        hero: the Hero, whose current spot is checked.
        walkers: the walking enemies.

    Returns:
        True when the hero is alive and standing on the ground row, with no pit
        from one column behind to two columns ahead and no walker within 3
        columns. play_level() remembers such spots as the checkpoint, so you
        don't come back to life right next to a danger.
    """
    return (not hero.dead and hero.y == 6 and hero.on_ground(world)
            and not any(is_pit(world, hero.x + dx) for dx in (-1, 0, 1, 2))
            and not any(abs(w.x - hero.x) <= 3 for w in walkers))


def flag_finish(sense, world, hero, walkers, birds, game):
    """Slide down the pole, walk into the castle and celebrate.

    Args:
        sense: the SenseHat object to draw on.
        world, hero, walkers, birds: the level and everything in it, for drawing.
        game: the dict with the score; reaching the flag is worth 5 points.

    Returns nothing; it returns once the fireworks are over.
    """
    # Slide down the flag pole to the ground, one row at a time
    while hero.y < 6:
        hero.y += 1
        draw(sense, world, hero, walkers, birds, 0)
        time.sleep(0.15)
    # Walk two steps toward the castle (if nothing solid is in the way)
    for _ in range(2):
        hero.x += 1 if not is_solid(world, hero.x + 1, hero.y) else 0
        draw(sense, world, hero, walkers, birds, 0)
        time.sleep(0.2)
    game["score"] += 5
    # Fireworks: 6 bursts of 5 random coloured dots at random spots
    for _ in range(6):
        for _ in range(5):
            sense.set_pixel(random.randint(0, 7), random.randint(0, 7),
                            random.choice(FIREWORK_COLORS))
        time.sleep(0.2)
        sense.clear()
        time.sleep(0.1)


def read_controls(stick, game):
    """Read the joystick. Returns the hero's controls, None for the computer, or
    "start" when someone touches the joystick during the demo.

    Args:
        stick: the Joystick to read.
        game: the dict with game["human"], True while a person plays.

    Returns:
        (step, jump) while a person plays: step is 1 (right), -1 (left) or 0, and
        jump is True for up or the middle button. None means "the computer plays".
        "start" means the demo should stop so a person can play.

    Unlike Block Attack, holding the joystick counts here: hold right to keep
    running, hold up to jump again as soon as you land.
    """
    pressed = stick.read()
    # During the demo, any press at all means someone wants to play
    if not game["human"]:
        return "start" if pressed else None
    if stick.idle():                     # Player walked away: computer takes over
        game["human"] = False
        return None
    # True counts as 1 and False as 0, so "right minus left" gives 1, -1, or 0
    # (when both or neither are pushed)
    step = (("right" in stick.held or "right" in pressed)
            - ("left" in stick.held or "left" in pressed))
    jump = any(k in pressed or k in stick.held for k in ("up", "middle"))
    return step, jump


def play_level(sense, level, game, stick):
    """Play one level. Returns True when the flag is reached, False on game over,
    or "start" when someone touches the joystick during the demo.

    Args:
        sense: the SenseHat object to draw on.
        level: the level number (1, 2, 3, ...); higher is harder.
        game: the dict with lives, score and whether a person plays (see main()).
        stick: the Joystick to read.

    Each loop is one game step of TICK seconds: read the controls, move the hero,
    move the enemies, check for hits and checkpoints, draw, and handle winning
    or losing a life. If the level is not finished within LEVEL_TIMEOUT seconds
    (for example because the demo hero got stuck), a fresh level is started.
    """
    world, walkers = generate_level(level)
    birds = []
    hero = Hero(2)
    checkpoint = 2              # Where the hero comes back after losing a life
    # Birds show up more often in higher levels
    bird_chance = BIRD_CHANCE * level
    start = time.monotonic()
    tick = 0

    while time.monotonic() - start < LEVEL_TIMEOUT:
        tick += 1
        # Input: the joystick, or None for the computer; "start" ends the demo
        controls = read_controls(stick, game)
        if controls == "start":
            return "start"
        result = hero.update(world, walkers, birds, game, controls)

        # Move the enemies. Birds that flew off the left of the screen are removed,
        # and new birds sometimes appear just past the right edge (not near the flag)
        for walker in walkers:
            walker.update(world)
        for bird in birds:
            bird.update()
        camera = max(0, min(WORLD_WIDTH - 8, hero.x - 3))
        birds = [b for b in birds if b.x >= camera - 2]
        if random.random() < bird_chance and hero.x < FLAG_X - 10:
            birds.append(Bird(camera + 9))

        # Running into an enemy (in the same spot) costs a life. (Landing on top
        # of one squashes it instead, see Hero.update().) Also remember safe spots.
        if not hero.dead and any(e.x == hero.x and e.y == hero.y for e in walkers + birds):
            hero.die()
        if is_safe_spot(world, hero, walkers):
            checkpoint = hero.x

        draw(sense, world, hero, walkers, birds, tick)

        if result == "won":
            flag_finish(sense, world, hero, walkers, birds, game)
            return True
        # Lost a life: show the lives left, then come back at the checkpoint with
        # nearby walkers cleared away and no birds, so you get a fair restart
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
    """Flash the screen red twice, then scroll the final score across the display.

    Args:
        sense: the SenseHat object to draw on.
        game: the dict with the score.
    """
    for _ in range(2):
        sense.clear(120, 0, 0)
        time.sleep(0.2)
        sense.clear()
        time.sleep(0.2)
    sense.show_message(f"Score {game['score']}", text_colour=COIN, scroll_speed=0.06)


def main():
    """Run the platformer forever: a computer demo, real games when someone plays.

    Takes no arguments and never returns (stop it with Ctrl+C). Each game starts
    at level 1 with full lives and goes up a level every time the flag is reached,
    until all lives are lost. Then the score is shown and the demo starts again.
    """
    sense = init(hat)
    stick = Joystick(sense)
    human = False
    while True:
        # Everything about the current game lives in this dict, so the functions
        # above can change the score and lives: lives left, points, and whether
        # a person (True) or the computer (False) is playing
        game = {"lives": LIVES, "score": 0, "human": human}
        level = 1
        show_lives(sense, game["lives"])
        # Play level after level ("L1", "L2", ... scrolls by first) while the hero
        # keeps reaching the flag
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
