#!/usr/bin/env python3
"""Sense HAT – Space Invaders: defend the bottom row against the invasion.

Rows of invaders march left and right, stepping down every other time they
reach the edge, and drop bombs on your cannon. Hide behind the blue shields,
shoot every invader to start the next, faster wave, and keep an eye out for
the purple saucer for bonus points. If the invaders reach your row, or you
lose all 3 lives, it's game over.

The computer plays a demo until you touch the joystick: then you play.
Left and right to move, up or press to fire. After game over, or when you
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
OFF = (0, 0, 0)
CANNON = (0, 255, 0)
SHOT = (255, 255, 255)
BOMB = (255, 60, 0)
SHIELD = (0, 120, 255)
SHIELD_HURT = (0, 40, 130)
SAUCER = (255, 0, 255)
INVADER_COLORS = [(255, 255, 0), (0, 255, 255), (255, 120, 0)]   # Per row, from the top
HIT_FLASH = (255, 0, 0)

# Gameplay
TICK = 0.02                 # Seconds per game step
LIVES = 3
INVADER_COLUMNS = (0, 2, 4) # Starting columns of the invaders in each row
INVADER_DELAY = 0.75        # Seconds per invader step in wave 1, with the full formation
MIN_INVADER_DELAY = 0.12
SHOT_DELAY = 0.05           # Seconds per row for your shot
BOMB_DELAY = 0.16           # Seconds per row for a bomb
CANNON_DELAY = 0.1          # Seconds per step for the cannon
BOMB_CHANCE = 0.02          # Chance per step of a new bomb (grows each wave)
SAUCER_CHANCE = 0.002       # Chance per step that the saucer flies over
SAUCER_DELAY = 0.12
AI_MISS_CHANCE = 0.12       # Chance the computer doesn't notice a bomb in time


class Bomb:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        # The row at which the computer notices this bomb (8 = never)
        self.seen_at = 8 if random.random() < AI_MISS_CHANCE else random.choice([2, 3, 4])


class Game:
    def __init__(self, human):
        self.human = human
        self.lives = LIVES
        self.score = 0
        self.wave = 0
        self.cannon = 3
        self.want_move = 0
        self.next_wave()

    def next_wave(self):
        self.wave += 1
        rows = 2 if self.wave == 1 else 3
        self.invaders = {(x, y) for y in range(1, 1 + rows) for x in INVADER_COLUMNS}
        self.wave_size = len(self.invaders)
        self.direction = 1
        self.edge_hits = 0
        self.legs = False           # Toggles every step: the invaders "walk"
        self.shields = {(1, 5): 2, (2, 5): 2, (5, 5): 2, (6, 5): 2}
        self.shot = None
        self.bombs = []
        self.saucer = None          # [x, direction]
        self.timers = dict.fromkeys(("invaders", "shot", "bombs", "cannon", "saucer"), 0.0)

    # ---------- timing ----------
    def ready(self, name, delay):
        """Advance a timer by one tick; True (and reset) when `delay` has passed."""
        self.timers[name] += TICK
        if self.timers[name] >= delay - 1e-9:
            self.timers[name] = 0.0
            return True
        return False

    def invader_delay(self):
        base = INVADER_DELAY * 0.85 ** (self.wave - 1)
        return max(MIN_INVADER_DELAY, base * (0.35 + 0.65 * len(self.invaders) / self.wave_size))

    # ---------- the computer player ----------
    def danger(self, column):
        """Is a bomb (that the computer has noticed) about to land in this column?"""
        for bomb in self.bombs:
            if bomb.x == column and bomb.y >= bomb.seen_at:
                shielded = (column, 5) in self.shields and bomb.y < 5
                if not shielded:
                    return True
        return False

    def ai_controls(self):
        x = self.cannon
        if self.danger(x):                       # Dodge
            for move in (random.choice([-1, 1]), 0):
                for m in (move, -move):
                    if 0 <= x + m <= 7 and not self.danger(x + m):
                        return m, False
            return 0, False
        if self.saucer and abs(self.saucer[0] - x) <= 2 and self.shot is None:
            target = self.saucer[0] + self.saucer[1] * 2
        elif self.invaders:
            lowest = max(y for _, y in self.invaders)
            columns = [ix for ix, iy in self.invaders if iy == lowest]
            target = min(columns, key=lambda c: abs(c - x))
        else:
            target = x
        move = (target > x) - (target < x)
        if move and self.danger(x + move):
            move = 0
        fire = self.shot is None and any(ix == x for ix, _ in self.invaders)
        if self.saucer and self.saucer[0] == x:
            fire = self.shot is None
        return move, fire

    # ---------- one game step ----------
    def step(self, move, fire):
        """Advance one tick. Returns "hit", "invaded", "cleared" or None."""
        if move:
            self.want_move = move
        if self.ready("cannon", CANNON_DELAY) and self.want_move:
            self.cannon = max(0, min(7, self.cannon + self.want_move))
            self.want_move = 0
        if fire and self.shot is None:
            self.shot = [self.cannon, 6]
            self.check_shot()

        if self.shot and self.ready("shot", SHOT_DELAY):
            self.shot[1] -= 1
            if self.shot[1] < 0:
                self.shot = None
            else:
                self.check_shot()

        if self.ready("invaders", self.invader_delay()):
            if self.move_invaders():
                return "invaded"
            self.check_shot()

        bomb_chance = BOMB_CHANCE + 0.006 * (self.wave - 1)
        if (self.invaders and len(self.bombs) < 2 + self.wave // 3
                and random.random() < bomb_chance):
            x, y = random.choice(sorted(self.invaders))
            y = max(iy for ix, iy in self.invaders if ix == x)   # Lowest in that column
            self.bombs.append(Bomb(x, y + 1))

        if self.ready("bombs", max(0.09, BOMB_DELAY - 0.01 * (self.wave - 1))):
            for bomb in self.bombs:
                bomb.y += 1
        if self.check_bombs():
            return "hit"

        if self.saucer is None and random.random() < SAUCER_CHANCE:
            direction = random.choice([-1, 1])
            self.saucer = [0 if direction > 0 else 7, direction]
        elif self.saucer and self.ready("saucer", SAUCER_DELAY):
            self.saucer[0] += self.saucer[1]
            if not 0 <= self.saucer[0] <= 7:
                self.saucer = None
            else:
                self.check_shot()

        if not self.invaders:
            return "cleared"
        return None

    def move_invaders(self):
        """March the formation. Returns True when the invaders reached your row."""
        if not self.invaders:
            return False
        self.legs = not self.legs
        xs = [x for x, _ in self.invaders]
        if not 0 <= min(xs) + self.direction <= max(xs) + self.direction <= 7:
            self.direction = -self.direction
            self.edge_hits += 1
            if self.edge_hits % 2 == 0:          # Step down every other edge
                self.invaders = {(x, y + 1) for x, y in self.invaders}
                for cell in self.invaders:
                    self.shields.pop(cell, None)  # Invaders crush the shields
        else:
            self.invaders = {(x + self.direction, y) for x, y in self.invaders}
        return any(y >= 6 for _, y in self.invaders)

    def check_shot(self):
        if not self.shot:
            return
        cell = tuple(self.shot)
        if cell in self.invaders:
            self.invaders.discard(cell)
            self.score += 10 * (4 - cell[1]) if cell[1] < 4 else 10
            self.shot = None
        elif self.saucer and cell == (self.saucer[0], 0):
            self.score += 100
            self.saucer = None
            self.shot = None
        else:
            for bomb in self.bombs:              # Shots and bombs cancel each other out
                if (bomb.x, bomb.y) == cell or (bomb.x, bomb.y - 1) == cell:
                    self.bombs.remove(bomb)
                    self.shot = None
                    break

    def check_bombs(self):
        """Handle bombs hitting shields or the cannon. Returns True when you're hit."""
        alive = []
        hit = False
        for bomb in self.bombs:
            cell = (bomb.x, bomb.y)
            if cell in self.shields:
                self.shields[cell] -= 1
                if self.shields[cell] == 0:
                    del self.shields[cell]
            elif bomb.y == 7 and bomb.x == self.cannon:
                hit = True
            elif bomb.y <= 7:
                alive.append(bomb)
        self.bombs = alive
        return hit

    # ---------- drawing ----------
    def draw(self, sense, cannon=True):
        frame = [[OFF] * 8 for _ in range(8)]
        for (x, y), hp in self.shields.items():
            frame[y][x] = SHIELD if hp == 2 else SHIELD_HURT
        for x, y in self.invaders:
            color = INVADER_COLORS[(y - 1) % len(INVADER_COLORS)]
            if self.legs:
                color = tuple(int(c * 0.7) for c in color)
            frame[y][x] = color
        if self.saucer:
            frame[0][self.saucer[0]] = SAUCER
        for bomb in self.bombs:
            frame[bomb.y][bomb.x] = BOMB
        if self.shot:
            frame[self.shot[1]][self.shot[0]] = SHOT
        if cannon:
            frame[7][self.cannon] = CANNON
        sense.set_pixels(flatten(frame))


def joystick_controls(stick, pressed):
    move = (("right" in stick.held or "right" in pressed)
            - ("left" in stick.held or "left" in pressed))
    fire = any(k in pressed or k in stick.held for k in ("up", "middle"))
    return move, fire


def you_were_hit(sense, game):
    for _ in range(3):
        game.draw(sense, cannon=False)
        sense.set_pixel(game.cannon, 7, HIT_FLASH)
        time.sleep(0.15)
        game.draw(sense, cannon=False)
        time.sleep(0.15)
    game.bombs = []
    game.shot = None
    if game.lives > 0:
        sense.show_letter(str(game.lives), text_colour=CANNON)
        time.sleep(0.6)


def wave_cleared(sense, game):
    for _ in range(2):
        sense.clear(0, 80, 0)
        time.sleep(0.15)
        sense.clear()
        time.sleep(0.15)
    game.score += 50
    game.next_wave()


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

        move, fire = joystick_controls(stick, pressed) if game.human else game.ai_controls()
        result = game.step(move, fire)

        if result == "hit":
            game.lives -= 1
            you_were_hit(sense, game)
            if game.lives == 0:
                return "over", game.score
        elif result == "invaded":
            game.draw(sense)
            for _ in range(3):
                sense.clear(120, 0, 0)
                time.sleep(0.2)
                game.draw(sense)
                time.sleep(0.2)
            return "over", game.score
        elif result == "cleared":
            wave_cleared(sense, game)

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
        sense.show_message(f"Score {score}", text_colour=INVADER_COLORS[0], scroll_speed=0.06)
        human = False   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
