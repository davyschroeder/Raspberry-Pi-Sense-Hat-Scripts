#!/usr/bin/env python3
"""Sense HAT – Bomberman: blow up the bricks, the monsters and find the exit.

You are the white pixel in an arena with grey pillars and brown bricks. Drop a
bomb (blue, it blinks faster just before it goes off): two seconds later it
explodes in a cross of fire that destroys bricks and monsters, and you too if
you're standing in it. Bombs set each other off. Under some bricks you find a
power-up: green gives an extra bomb, red makes the fire reach further. One brick
hides the exit (cyan): it opens and blinks once every monster is gone, and
walking into it takes you to the next level, with more and faster monsters.
Touching a monster costs a life. You have 3 lives.

Points: 10 per brick, 100 per monster, 500 per level.

The computer plays a demo until you touch the joystick: then you play.
Push the joystick to walk, press it to drop a bomb. After game over, or when
you leave the joystick alone for a while, the computer takes over again.
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
    """Keeps track of the Sense HAT joystick (or the emulator's: arrow keys + Enter),
    and of a USB or Bluetooth gamepad if one is connected.

    The Sense HAT library reports joystick "events": a direction ("up", "down",
    "left", "right" or "middle" for pushing it in) plus an action ("pressed",
    "held" while you keep it pushed, and "released"). This class turns those
    events into two handy things for the games:

    - read() gives the new presses since last time (good for single steps),
    - self.held is the set of directions that are pushed right now (good for
      moving as long as you hold the stick).

    A gamepad works the same way: the d-pad or the left stick steers, and A, B
    or Start count as pushing the joystick in. Set GAMEPAD_SHOULDERS = True in a
    game to also use the shoulder buttons and triggers as left and right (the
    pinball flippers do). Gamepads need the evdev library
    (sudo apt install python3-evdev); without it only the Sense HAT joystick works.

    It also remembers when the joystick was last touched, so the game can tell
    whether the player has walked away (see idle()).
    """

    PAD_RESCAN = 3.0             # Seconds between looks for a newly plugged-in gamepad

    def __init__(self, sense):
        """Remember the SenseHat object; nothing is held and no input seen yet."""
        self.sense = sense
        self.held = set()        # Directions held down right now
        self.last_input = 0.0    # time.monotonic() of the last joystick event (0 = never)
        self.pads = []           # Connected gamepads (evdev devices)
        self.pad_state = {}      # Per gamepad: which directions it holds right now
        self.pad_axes = {}       # Per gamepad: latest position of the stick, d-pad and triggers
        self.next_scan = 0.0     # When to look for gamepads again

    def find_pads(self):
        """Look for gamepads (once every PAD_RESCAN seconds while none is found)."""
        if self.pads or time.monotonic() < self.next_scan:
            return
        self.next_scan = time.monotonic() + self.PAD_RESCAN
        try:
            import evdev
        except ImportError:
            self.next_scan = float("inf")          # No evdev library: never look again
            return
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                keys = dev.capabilities().get(evdev.ecodes.EV_KEY, [])
                if evdev.ecodes.BTN_SOUTH in keys or evdev.ecodes.BTN_GAMEPAD in keys:
                    self.pads.append(dev)          # Has an A button: it's a gamepad
                else:
                    dev.close()
            except OSError:
                pass                               # Not allowed to read it, or it went away

    def pad_directions(self, dev):
        """Which directions this gamepad holds right now, from its buttons and axes."""
        import evdev
        e, ax = evdev.ecodes, self.pad_axes.setdefault(dev.path, {})
        keys = set(dev.active_keys())
        state = set()

        def stick(code):
            """Stick position from -1 (left/up) to 1 (right/down)."""
            if code not in ax:
                return 0.0
            info = dev.absinfo(code)
            mid = (info.max + info.min) / 2
            return (ax[code] - mid) / max(1, (info.max - info.min) / 2)

        def trigger(code):
            """How far a trigger is pulled, from 0 to 1."""
            if code not in ax:
                return 0.0
            info = dev.absinfo(code)
            return (ax[code] - info.min) / max(1, info.max - info.min)

        x = stick(e.ABS_X) + ax.get(e.ABS_HAT0X, 0)   # Left stick plus d-pad (hat: -1, 0 or 1)
        y = stick(e.ABS_Y) + ax.get(e.ABS_HAT0Y, 0)
        if x < -0.5 or e.BTN_DPAD_LEFT in keys:
            state.add("left")
        if x > 0.5 or e.BTN_DPAD_RIGHT in keys:
            state.add("right")
        if y < -0.5 or e.BTN_DPAD_UP in keys:
            state.add("up")
        if y > 0.5 or e.BTN_DPAD_DOWN in keys:
            state.add("down")
        if keys & {e.BTN_SOUTH, e.BTN_EAST, e.BTN_START}:
            state.add("middle")
        if globals().get("GAMEPAD_SHOULDERS"):         # e.g. the pinball flippers
            if e.BTN_TL in keys or e.BTN_TL2 in keys or trigger(e.ABS_Z) > 0.3:
                state.add("left")
            if e.BTN_TR in keys or e.BTN_TR2 in keys or trigger(e.ABS_RZ) > 0.3:
                state.add("right")
        return state

    def read_pads(self, pressed):
        """Turn gamepad changes into joystick presses and releases."""
        self.find_pads()
        for dev in list(self.pads):
            try:
                import evdev
                event = dev.read_one()
                while event is not None:              # Remember the newest axis positions
                    if event.type == evdev.ecodes.EV_ABS:
                        self.pad_axes.setdefault(dev.path, {})[event.code] = event.value
                    event = dev.read_one()
                now = self.pad_directions(dev)
            except OSError:                           # Unplugged: forget it
                self.pads.remove(dev)
                for d in self.pad_state.pop(dev.path, set()):
                    self.held.discard(d)
                continue
            before = self.pad_state.get(dev.path, set())
            for d in now - before:                    # Newly pushed
                pressed.append(d)
                self.held.add(d)
                self.last_input = time.monotonic()
            for d in before - now:                    # Let go
                self.held.discard(d)
                self.last_input = time.monotonic()
            if now:
                self.last_input = time.monotonic()    # Still holding: still playing
            self.pad_state[dev.path] = now

    def read(self):
        """Return the directions pressed since the last call ("up", "left", "middle", ...).

        Also updates self.held and the time of the last input. Returns an empty
        list when there is no joystick to read (for example a broken connection).
        """
        pressed = []
        self.read_pads(pressed)
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
FLOOR = (0, 0, 0)
PILLAR = (55, 55, 70)
BRICK = (140, 70, 20)
PLAYER = (255, 255, 255)
MONSTER = (255, 0, 140)
BOMB = (0, 90, 255)
BOMB_HOT = (200, 220, 255)       # A bomb about to go off blinks between BOMB and this
FIRE = (255, 130, 0)
FIRE_CORE = (255, 240, 60)       # Where the bomb was
ITEM_COLORS = {"bomb": (0, 255, 80), "fire": (255, 0, 0)}
EXIT_CLOSED = (0, 70, 70)
EXIT_OPEN = (0, 255, 255)

# Gameplay
TICK = 0.02                      # Seconds per game step
LIVES = 3
FUSE = 2.0                       # Seconds before a bomb goes off
FIRE_TIME = 0.5                  # Seconds the fire stays
MOVE_DELAY = 0.18                # Seconds per step while you hold a direction
AI_MOVE_DELAY = 0.22             # Seconds per step for the computer
MONSTER_DELAY = 0.65             # Seconds per monster step on level 1 ...
MIN_MONSTER_DELAY = 0.3          # ... and the fastest they get
BRICK_CHANCE = 0.5               # Share of the free squares that start as a brick
SAFE_TIME = 1.5                  # Seconds you can't be hurt after coming back
MAX_BOMBS, MAX_RANGE = 3, 4
AI_MISTAKE = 0.003               # Chance per tick (50 a second) the computer wanders off

DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]
KEYS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
START = (0, 0)


def is_pillar(x, y):
    return x % 2 == 1 and y % 2 == 1


def inside(x, y):
    return 0 <= x <= 7 and 0 <= y <= 7


class Bomb:
    def __init__(self, x, y, reach):
        self.x, self.y = x, y
        self.reach = reach
        self.fuse = FUSE


class Monster:
    def __init__(self, x, y):
        self.x, self.y = x, y
        self.direction = random.choice(DIRS)


class Game:
    def __init__(self, human):
        self.human = human
        self.lives = LIVES
        self.score = 0
        self.level = 0
        self.max_bombs = 1
        self.reach = 1
        self.ticks = 0
        self.next_level()

    # ---------- level set-up ----------
    def next_level(self):
        self.level += 1
        free = [(x, y) for y in range(8) for x in range(8) if not is_pillar(x, y)]
        near_start = {(0, 0), (1, 0), (0, 1), (2, 0), (0, 2)}
        spots = [c for c in free if c not in near_start]
        self.bricks = {c for c in spots if random.random() < BRICK_CHANCE}
        hidden = random.sample(sorted(self.bricks), min(3, len(self.bricks)))
        self.exit = hidden[0]
        self.items = {hidden[1]: "bomb", hidden[2]: "fire"} if len(hidden) == 3 else {}
        open_far = [c for c in spots if c not in self.bricks and abs(c[0]) + abs(c[1]) >= 6]
        count = min(len(open_far), 1 + self.level, 6)
        self.monsters = [Monster(*c) for c in random.sample(open_far, count)]
        self.monster_delay = max(MIN_MONSTER_DELAY, MONSTER_DELAY * 0.9 ** (self.level - 1))
        self.monster_timer = 0.0
        self.respawn()

    def respawn(self):
        self.player = list(START)
        self.bombs = []
        self.fire = {}                # (x, y) -> seconds left
        self.move_timer = 0.0
        self.want_move = None
        self.safe = SAFE_TIME
        self.idle_ticks = 0

    # ---------- the map ----------
    def blocked(self, x, y):
        if not inside(x, y) or is_pillar(x, y) or (x, y) in self.bricks:
            return True
        return any((b.x, b.y) == (x, y) for b in self.bombs)

    def blast_cells(self, bomb, bricks=None):
        """The squares a bomb's fire reaches: a cross that stops at pillars and bricks."""
        bricks = self.bricks if bricks is None else bricks
        cells = [(bomb.x, bomb.y)]
        for dx, dy in DIRS:
            for r in range(1, bomb.reach + 1):
                x, y = bomb.x + dx * r, bomb.y + dy * r
                if not inside(x, y) or is_pillar(x, y):
                    break
                cells.append((x, y))
                if (x, y) in bricks:
                    break
        return cells

    # ---------- your actions ----------
    def drop_bomb(self):
        x, y = self.player
        if len(self.bombs) < self.max_bombs and not any((b.x, b.y) == (x, y) for b in self.bombs):
            self.bombs.append(Bomb(x, y, self.reach))

    def walk(self, dx, dy):
        x, y = self.player[0] + dx, self.player[1] + dy
        if not self.blocked(x, y):
            self.player = [x, y]
            cell = (x, y)
            if cell in self.items:
                kind = self.items.pop(cell)
                if kind == "bomb":
                    self.max_bombs = min(MAX_BOMBS, self.max_bombs + 1)
                else:
                    self.reach = min(MAX_RANGE, self.reach + 1)
                self.score += 50

    # ---------- one game step ----------
    def explode(self, bomb):
        self.bombs.remove(bomb)
        for cell in self.blast_cells(bomb):
            self.fire[cell] = FIRE_TIME
            if cell in self.bricks:
                self.bricks.discard(cell)
                self.score += 10
            for other in list(self.bombs):            # Chain reaction
                if (other.x, other.y) == cell:
                    other.fuse = min(other.fuse, 0.05)

    def step(self, move, bomb, move_delay, sticky=True):
        """Advance one tick. Returns "dead", "cleared" or None.

        sticky: a short tap of the joystick still counts at the next step. The
        computer decides afresh every tick, so for it the latest choice counts.
        """
        self.ticks += 1
        self.safe = max(0.0, self.safe - TICK)
        if move or not sticky:
            self.want_move = move
        self.move_timer += TICK
        if self.want_move and self.move_timer >= move_delay - 1e-9:
            self.move_timer = 0.0
            self.walk(*self.want_move)
            self.want_move = None
        if bomb:
            self.drop_bomb()

        for b in list(self.bombs):
            b.fuse -= TICK
            if b.fuse <= 0 and b in self.bombs:
                self.explode(b)
        self.fire = {c: t - TICK for c, t in self.fire.items() if t - TICK > 0}

        self.monster_timer += TICK
        if self.monster_timer >= self.monster_delay:
            self.monster_timer = 0.0
            for m in self.monsters:
                self.move_monster(m)
        before = len(self.monsters)
        self.monsters = [m for m in self.monsters if (m.x, m.y) not in self.fire]
        self.score += 100 * (before - len(self.monsters))

        p = tuple(self.player)
        if self.safe == 0 and (p in self.fire or any((m.x, m.y) == p for m in self.monsters)):
            return "dead"
        if not self.monsters and p == self.exit and self.exit not in self.bricks:
            return "cleared"
        return None

    def move_monster(self, m):
        """Walk on; at a crossing or a wall, now and then pick a new direction."""
        options = [d for d in DIRS if not self.blocked(m.x + d[0], m.y + d[1])
                   and (m.x + d[0], m.y + d[1]) not in self.fire]
        if not options:
            return
        ahead_open = m.direction in options
        if not ahead_open or (len(options) > 2 and random.random() < 0.3):
            back = (-m.direction[0], -m.direction[1])
            choices = [d for d in options if d != back] or options
            m.direction = random.choice(choices)
        m.x += m.direction[0]
        m.y += m.direction[1]

    # ---------- the computer player ----------
    def danger_cells(self, bombs, bricks):
        cells = set(self.fire)
        for b in bombs:
            cells.update(self.blast_cells(b, bricks))
        return cells

    def bfs(self, start, avoid):
        """Shortest walks from start, not through blocked squares or `avoid`.
        Returns {cell: (first step to take, number of steps)}, nearest first."""
        found = {start: (None, 0)}
        queue = [start]
        while queue:
            cx, cy = queue.pop(0)
            first, dist = found[(cx, cy)]
            for dx, dy in DIRS:
                n = (cx + dx, cy + dy)
                if n in found or self.blocked(*n) or n in avoid:
                    continue
                found[n] = (first or (dx, dy), dist + 1)
                queue.append(n)
        return found

    def near_monsters(self):
        cells = set()
        for m in self.monsters:
            cells.add((m.x, m.y))
            for dx, dy in DIRS:
                cells.add((m.x + dx, m.y + dy))
        return cells

    def worth_bombing(self, cell):
        """Would a bomb here hit a brick or a monster?"""
        hit = self.blast_cells(Bomb(cell[0], cell[1], self.reach))
        if any(c in self.bricks for c in hit[1:]):
            return True
        return any((m.x, m.y) in hit for m in self.monsters)

    def escape_exists(self, cell):
        """After dropping a bomb at `cell`, can we reach a safe square in time?"""
        bombs = self.bombs + [Bomb(cell[0], cell[1], self.reach)]
        danger = self.danger_cells(bombs, self.bricks)
        monsters = {(m.x, m.y) for m in self.monsters}
        steps = self.bfs(tuple(cell), monsters | set(self.fire))
        limit = int((min(b.fuse for b in bombs) - 0.3) / AI_MOVE_DELAY)
        threat = self.near_monsters()
        return any(dist <= limit for target, (_, dist) in steps.items()
                   if target not in danger and target not in threat)

    def step_towards(self, goals, avoid):
        steps = self.bfs(tuple(self.player), avoid)
        here = tuple(self.player)
        reachable = [g for g in goals if g in steps and g != here]
        if not reachable:
            return None
        goal = min(reachable, key=lambda g: steps[g][1])     # The nearest one
        return steps[goal][0]

    def ai_controls(self):
        """Returns (move, bomb) like the joystick would."""
        here = tuple(self.player)
        danger = self.danger_cells(self.bombs, self.bricks)
        threat = self.near_monsters()
        if random.random() < AI_MISTAKE:
            return random.choice(DIRS), False

        # 1. Standing in a blast or next to a monster: get out
        if here in danger or here in threat:
            safe = [c for c in self.bfs(here, set(self.fire) | {(m.x, m.y) for m in self.monsters})
                    if c not in danger and c not in threat]
            return self.step_towards(safe, set(self.fire) | {(m.x, m.y) for m in self.monsters}), False

        # 2. A good spot for a bomb, with a way out: drop it
        if len(self.bombs) < self.max_bombs and self.worth_bombing(here) and self.escape_exists(here):
            return None, True

        avoid = danger | threat
        # 3. All monsters gone and the exit is open: go there
        if not self.monsters and self.exit not in self.bricks:
            return self.step_towards([self.exit], avoid), False

        # 4. Items lying around: pick them up
        visible = [c for c in self.items if c not in self.bricks]
        if visible:
            move = self.step_towards(visible, avoid)
            if move:
                return move, False

        # 5. Walk to the nearest square where a bomb would be useful
        steps = self.bfs(here, avoid)
        targets = [c for c in steps if self.worth_bombing(c)]
        if not self.monsters and self.exit not in self.bricks:
            targets = [self.exit]
        move = self.step_towards(targets, avoid)
        return move, False

    # ---------- drawing ----------
    def draw(self, sense, player=True):
        frame = [[FLOOR] * 8 for _ in range(8)]
        for y in range(8):
            for x in range(8):
                if is_pillar(x, y):
                    frame[y][x] = PILLAR
        if self.exit not in self.bricks:
            open_ = not self.monsters
            frame[self.exit[1]][self.exit[0]] = (EXIT_OPEN if (self.ticks // 10) % 2 else EXIT_CLOSED) if open_ else EXIT_CLOSED
        for (x, y), kind in self.items.items():
            if (x, y) not in self.bricks and (self.ticks // 8) % 2 == 0:
                frame[y][x] = ITEM_COLORS[kind]
        for x, y in self.bricks:
            frame[y][x] = BRICK
        for b in self.bombs:
            fast = b.fuse < 0.6
            blink = (self.ticks // (3 if fast else 10)) % 2
            frame[b.y][b.x] = BOMB_HOT if blink else BOMB
        for (x, y) in self.fire:
            frame[y][x] = FIRE
        for m in self.monsters:
            frame[m.y][m.x] = MONSTER
        if player and (self.safe == 0 or (self.ticks // 5) % 2 == 0):
            frame[self.player[1]][self.player[0]] = PLAYER
        sense.set_pixels(flatten(frame))


def joystick_controls(stick, pressed):
    move = next((KEYS[k] for k in pressed if k in KEYS), None)
    if move is None:
        move = next((KEYS[k] for k in KEYS if k in stick.held), None)
    return move, "middle" in pressed


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

        if game.human:
            if any(k in pressed for k in KEYS):
                game.move_timer = MOVE_DELAY          # A tap moves right away
            move, bomb = joystick_controls(stick, pressed)
            result = game.step(move, bomb, MOVE_DELAY)
        else:
            move, bomb = game.ai_controls()
            result = game.step(move, bomb, AI_MOVE_DELAY, sticky=False)

        if result == "dead":
            game.lives -= 1
            for _ in range(3):
                game.draw(sense, player=False)
                sense.set_pixel(game.player[0], game.player[1], (255, 0, 0))
                time.sleep(0.15)
                game.draw(sense, player=False)
                time.sleep(0.15)
            if game.lives == 0:
                return "over", game.score
            sense.show_letter(str(game.lives), text_colour=PLAYER)
            time.sleep(0.6)
            game.respawn()
        elif result == "cleared":
            game.score += 500
            for _ in range(3):
                sense.clear(EXIT_OPEN)
                time.sleep(0.12)
                sense.clear()
                time.sleep(0.12)
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
        sense.show_message(f"Score {score}", text_colour=FIRE, scroll_speed=0.06)
        human = False   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
