#!/usr/bin/env python3
"""Sense HAT – Missile Command: shoot down the missiles before they hit your cities.

Enemy missiles streak down from the top of the screen towards your four cities
(white) and your launcher (grey, in the middle). Move the blinking crosshair and
fire: an interceptor flies from the launcher to the crosshair and explodes in a
fireball. Every missile that flies into a fireball is destroyed, and blows up
in a small fireball of its own, so you can set off chain reactions. From wave 3
some missiles split in two halfway down.

Each wave gives you 10 interceptors. A hit on the launcher shuts it down for the
rest of the wave. When a wave is over you get bonus points for every city left
and every interceptor you didn't need, and every 1500 points a lost city is
rebuilt. When all four cities are gone, it's game over.

The computer plays a demo until you touch the joystick: then you play.
Move the crosshair with the joystick, press to fire. After game over, or when
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
OFF = (0, 0, 0)
CITY = (255, 255, 255)
LAUNCHER = (110, 110, 110)
LAUNCHER_DOWN = (60, 0, 0)       # The launcher after a hit: no more firing this wave
MISSILE = (255, 0, 0)            # Head of an enemy missile ...
TRAIL = (90, 0, 0)               # ... and the trail it leaves
INTERCEPTOR = (0, 255, 255)
CROSSHAIR = (0, 255, 0)
BLAST = [(255, 255, 255), (255, 230, 60), (255, 120, 0), (180, 30, 0)]   # Hot centre to cool edge

# The bottom row: four cities and the launcher in the middle
CITIES = (1, 2, 5, 6)
LAUNCH_X = 3.5                   # Interceptors start between the two launcher pixels
LAUNCH_PIXELS = (3, 4)

# Gameplay
TICK = 0.02                      # Seconds per game step
AMMO = 10                        # Interceptors per wave
INTERCEPTOR_SPEED = 12.0         # Cells per second
BLAST_RADIUS = 1.6               # How big a fireball grows (in cells)
BLAST_TIME = 0.9                 # Seconds a fireball lasts
CHAIN_RADIUS = 1.0               # Fireball of a missile that was hit
MISSILE_SPEED = 0.55             # Cells per second in wave 1 ...
MISSILE_SPEEDUP = 0.1            # ... this much faster each wave
CURSOR_DELAY = 0.08              # Seconds per crosshair step while a direction is held
REBUILD_POINTS = 1500            # A lost city comes back every this many points
AI_CURSOR_DELAY = 0.06
AI_MISTAKE = 0.1                 # Chance the computer misjudges where to aim


class Missile:
    def __init__(self, x, y, target_x, speed, can_split=False):
        self.x, self.y = x, y
        self.target_x = target_x
        dx, dy = target_x - x, 7.5 - y
        length = (dx * dx + dy * dy) ** 0.5
        self.vx, self.vy = dx / length * speed, dy / length * speed
        self.trail = [(x, y)]
        self.split_at = random.uniform(2.5, 3.5) if can_split else None

    def cell(self):
        return int(self.x), int(self.y)


class Blast:
    def __init__(self, x, y, radius):
        self.x, self.y = x, y
        self.max_radius = radius
        self.age = 0.0

    def radius(self):
        """Grows for the first half of its life, then shrinks again."""
        t = self.age / BLAST_TIME
        return self.max_radius * (1 - abs(2 * t - 1))

    def covers(self, x, y):
        return (x - self.x) ** 2 + (y - self.y) ** 2 <= self.radius() ** 2


class Game:
    def __init__(self, human):
        self.human = human
        self.score = 0
        self.wave = 0
        self.cities = set(CITIES)
        self.next_rebuild = REBUILD_POINTS
        self.cursor = [3, 3]
        self.want_move = None
        self.cursor_timer = 0.0
        self.ticks = 0
        self.next_wave()

    def next_wave(self):
        self.wave += 1
        self.ammo = AMMO
        self.launcher_ok = True
        self.missiles = []
        self.interceptors = []        # [x, y, target_x, target_y]
        self.blasts = []
        self.to_launch = 6 + 2 * self.wave
        self.launch_timer = 1.0
        self.speed = MISSILE_SPEED + MISSILE_SPEEDUP * (self.wave - 1)

    # ---------- enemies ----------
    def launch_missile(self):
        targets = list(self.cities) + [3, 4]
        target = random.choice(targets) + 0.5
        can_split = self.wave >= 3 and random.random() < 0.3
        self.missiles.append(Missile(random.uniform(0, 8), 0.0, target, self.speed, can_split))
        self.to_launch -= 1

    def move_missiles(self):
        """Returns True when a missile hit the launcher or a city."""
        landed = []
        for m in self.missiles:
            m.x += m.vx * TICK
            m.y += m.vy * TICK
            last_x, last_y = m.trail[-1]
            if (int(last_x), int(last_y)) != m.cell():     # Entered a new cell: extend the trail
                m.trail.append((m.x, m.y))
                m.trail = m.trail[-3:]
            if m.split_at is not None and m.y >= m.split_at:
                m.split_at = None
                targets = [c for c in self.cities if abs(c + 0.5 - m.target_x) > 0.6] or [m.target_x - 0.5]
                child = Missile(m.x, m.y, random.choice(targets) + 0.5, self.speed)
                self.missiles.append(child)
            if m.y >= 7:
                landed.append(m)
        for m in landed:
            self.missiles.remove(m)
            x = int(m.target_x)
            self.blasts.append(Blast(m.target_x, 7.3, CHAIN_RADIUS))
            if x in self.cities:
                self.cities.discard(x)
            elif x in LAUNCH_PIXELS:
                self.launcher_ok = False
        return bool(landed)

    # ---------- your side ----------
    def fire(self):
        if not (self.launcher_ok and self.ammo > 0):
            return
        tx, ty = self.cursor[0] + 0.5, self.cursor[1] + 0.5
        if any(i[2] == tx and i[3] == ty for i in self.interceptors):
            return                    # Already one on its way there
        self.ammo -= 1
        self.interceptors.append([LAUNCH_X + 0.5, 7.0, tx, ty])

    def move_interceptors(self):
        arrived = []
        for i in self.interceptors:
            dx, dy = i[2] - i[0], i[3] - i[1]
            dist = (dx * dx + dy * dy) ** 0.5
            step = INTERCEPTOR_SPEED * TICK
            if dist <= step:
                arrived.append(i)
            else:
                i[0] += dx / dist * step
                i[1] += dy / dist * step
        for i in arrived:
            self.interceptors.remove(i)
            self.blasts.append(Blast(i[2], i[3], BLAST_RADIUS))

    def update_blasts(self):
        for b in self.blasts:
            b.age += TICK
        self.blasts = [b for b in self.blasts if b.age < BLAST_TIME]
        for m in list(self.missiles):
            if any(b.covers(m.x, m.y) for b in self.blasts):
                self.missiles.remove(m)
                self.blasts.append(Blast(m.x, m.y, CHAIN_RADIUS))
                self.score += 25
        if self.score >= self.next_rebuild:
            self.next_rebuild += REBUILD_POINTS
            lost = [c for c in CITIES if c not in self.cities]
            if lost:
                self.cities.add(random.choice(lost))

    # ---------- the computer player ----------
    def ai_target(self):
        """Where to aim: the missile that will land first, a little ahead of it."""
        threats = []
        for m in self.missiles:
            if any(abs(i[2] - m.x) < 2 and abs(i[3] - m.y) < 3 for i in self.interceptors):
                continue                  # Already being dealt with
            if any(b.covers(m.x, m.y + 0.5) for b in self.blasts):
                continue
            if m.y < 0.8:
                continue                  # Let it come down a bit first
            threats.append(m)
        if not threats:
            return None
        m = max(threats, key=lambda q: q.y)
        tx, ty = LAUNCH_X + 0.5, 7.0
        flight = ((m.x - tx) ** 2 + (m.y - ty) ** 2) ** 0.5 / INTERCEPTOR_SPEED
        lead = flight + 0.25 + 0.3        # Fly there, then let the fireball grow
        x, y = m.x + m.vx * lead, m.y + m.vy * lead
        if random.random() < AI_MISTAKE:
            x += random.choice([-2, 2])
        return max(0, min(7, int(x))), max(0, min(6, int(y)))

    def ai_controls(self):
        target = self.ai_target()
        if target is None:
            return None, False
        cx, cy = self.cursor
        if abs(target[0] - cx) <= 0 and abs(target[1] - cy) <= 1:
            return None, True
        move = ((target[0] > cx) - (target[0] < cx), (target[1] > cy) - (target[1] < cy))
        return move, False

    # ---------- one game step ----------
    def step(self, move, fire, cursor_delay, sticky=True):
        """Advance one tick. Returns "over", "cleared" or None.

        sticky: a short tap of the joystick still counts at the next step. The
        computer decides afresh every tick, so for it the latest choice counts.
        """
        self.ticks += 1
        if move or not sticky:
            self.want_move = move
        self.cursor_timer += TICK
        if self.want_move and self.cursor_timer >= cursor_delay - 1e-9:
            self.cursor_timer = 0.0
            self.cursor[0] = max(0, min(7, self.cursor[0] + self.want_move[0]))
            self.cursor[1] = max(0, min(6, self.cursor[1] + self.want_move[1]))
            self.want_move = None
        if fire:
            self.fire()

        if self.to_launch > 0:
            self.launch_timer -= TICK
            if self.launch_timer <= 0:
                self.launch_missile()
                self.launch_timer = random.uniform(0.6, 2.2) * 0.93 ** (self.wave - 1)
        self.move_missiles()
        self.move_interceptors()
        self.update_blasts()

        if not self.cities:
            return "over"
        if self.to_launch == 0 and not self.missiles and not self.blasts and not self.interceptors:
            return "cleared"
        return None

    # ---------- drawing ----------
    def draw(self, sense):
        frame = [[OFF] * 8 for _ in range(8)]
        for x in CITIES:
            if x in self.cities:
                frame[7][x] = CITY
        for x in LAUNCH_PIXELS:
            frame[7][x] = LAUNCHER if self.launcher_ok else LAUNCHER_DOWN
        for m in self.missiles:
            for tx, ty in m.trail[:-1]:
                if 0 <= int(ty) <= 7 and 0 <= int(tx) <= 7:
                    frame[int(ty)][int(tx)] = TRAIL
        for b in self.blasts:
            r = b.radius()
            for y in range(8):
                for x in range(8):
                    d = ((x + 0.5 - b.x) ** 2 + (y + 0.5 - b.y) ** 2) ** 0.5
                    if d <= r + 0.3:
                        shade = BLAST[min(3, int(d / max(r, 0.3) * 3))]
                        frame[y][x] = shade
        for m in self.missiles:
            x, y = m.cell()
            if 0 <= x <= 7 and 0 <= y <= 7:
                frame[y][x] = MISSILE
        for i in self.interceptors:
            frame[min(7, int(i[1]))][min(7, int(i[0]))] = INTERCEPTOR
        if (self.ticks // 10) % 2 == 0:          # The crosshair blinks
            frame[self.cursor[1]][self.cursor[0]] = CROSSHAIR
        sense.set_pixels(flatten(frame))


def joystick_controls(stick, pressed):
    keys = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
    dx = dy = 0
    for k, (kx, ky) in keys.items():
        if k in pressed or k in stick.held:
            dx, dy = dx + kx, dy + ky
    move = (dx, dy) if (dx or dy) else None
    return move, "middle" in pressed


def wave_bonus(sense, game):
    """Count the bonus: 100 per city left, 10 per interceptor not used."""
    game.draw(sense)
    time.sleep(0.4)
    game.score += 100 * len(game.cities) + 10 * game.ammo
    for _ in range(2):
        sense.clear(0, 60, 0)
        time.sleep(0.15)
        game.draw(sense)
        time.sleep(0.15)


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
            if any(k in pressed for k in ("up", "down", "left", "right")):
                game.cursor_timer = CURSOR_DELAY      # A tap moves right away
            move, fire = joystick_controls(stick, pressed)
            result = game.step(move, fire, CURSOR_DELAY)
        else:
            move, fire = game.ai_controls()
            result = game.step(move, fire, AI_CURSOR_DELAY, sticky=False)

        if result == "over":
            game.draw(sense)
            for _ in range(3):
                sense.clear(255, 255, 255)
                time.sleep(0.1)
                sense.clear(120, 0, 0)
                time.sleep(0.2)
            return "over", game.score
        if result == "cleared":
            wave_bonus(sense, game)
            game.next_wave()

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
        sense.show_message(f"Score {score}", text_colour=INTERCEPTOR, scroll_speed=0.06)
        human = False   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
