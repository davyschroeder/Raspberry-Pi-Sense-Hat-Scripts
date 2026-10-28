#!/usr/bin/env python3
"""Sense HAT – Flappy Bird: flap through the gaps between the pipes.

The yellow bird falls all the time; every push of the joystick makes it flap
up a little. Green pipes slide in from the right with a gap of three pixels
somewhere in them: fly through the gap for a point. Touch a pipe or the
ground and it's over. Every 5 points the pipes come a little faster and the
gaps jump further up and down.

The computer plays a demo until you touch the joystick: then you play.
Push the joystick in any direction (or press it) to flap. After game over, or
when you leave the joystick alone for a while, the computer takes over again.
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
SKY = (0, 0, 25)
BIRD = (255, 220, 0)
BIRD_FLAP = (255, 255, 160)      # The bird lights up for a moment when it flaps
PIPE = (0, 180, 0)
PIPE_EDGE = (120, 255, 120)      # The lip of a pipe, next to the gap
GROUND = (110, 60, 10)
CRASH = (255, 0, 0)

# Gameplay
TICK = 0.02                      # Seconds per game step
BIRD_X = 2                       # The bird stays in this column; the world moves
GRAVITY = 20.0                   # Cells per second per second
FLAP = -6.5                      # Upward speed right after a flap (cells per second)
MAX_FALL = 9.0
GAP = 3                          # Height of the gap in every pipe
PIPE_SPACING = 4                 # Columns from one pipe to the next
PIPE_DELAY = 0.22                # Seconds per column the pipes move at the start ...
PIPE_SPEEDUP = 0.93              # ... a little faster every 5 points ...
MIN_PIPE_DELAY = 0.13            # ... down to this
FLOOR = 7                        # The bottom row is the ground
AI_SLOPPY = 0.04                 # Chance per pipe that the computer misjudges it


def advance(y, vy, pipes, pipe_timer, pipe_delay, flap):
    """One tick of physics. Returns (y, vy, pipes, pipe_timer, pipes_moved)."""
    if flap:
        vy = FLAP
    vy = min(MAX_FALL, vy + GRAVITY * TICK)
    y = max(0.0, y + vy * TICK)
    if y == 0.0:
        vy = max(vy, 0.0)                 # Bumping into the top just stops you
    pipe_timer += TICK
    moved = pipe_timer >= pipe_delay - 1e-9
    if moved:
        pipe_timer = 0.0
        for p in pipes:
            p[0] -= 1
    return y, vy, pipes, pipe_timer, moved


def blocked(y, pipes):
    """Does the bird hit the ground or a pipe at height y?"""
    row = int(y)
    if row >= FLOOR:
        return True
    return any(x == BIRD_X and not gap <= row < gap + GAP for x, gap in pipes)


class Game:
    def __init__(self, human):
        self.human = human
        self.y = 3.0
        self.vy = 0.0
        self.score = 0
        self.pipes = []              # [x, top of gap]
        self.pipe_timer = 0.0
        self.next_pipe = 3           # Columns until the next pipe appears
        self.flap_glow = 0.0
        self.ai_offset = 0.0

    def pipe_delay(self):
        return max(MIN_PIPE_DELAY, PIPE_DELAY * PIPE_SPEEDUP ** (self.score // 5))

    def new_gap(self):
        """Where the gap of the next pipe goes; the jumps get bigger as you score."""
        reach = min(3, 1 + self.score // 5)
        last = self.pipes[-1][1] if self.pipes else 2
        return max(0, min(FLOOR - GAP, last + random.randint(-reach, reach)))

    # ---------- the computer player ----------
    def heuristic(self, y, vy, pipes):
        """Simple rule: flap when the bird would sink below the next gap."""
        ahead = [p for p in pipes if p[0] >= BIRD_X]
        target = (ahead[0][1] + GAP - 1.05 if ahead else 4.0) + self.ai_offset
        t = 0.12
        future = y + vy * t + 0.5 * GRAVITY * t * t
        return future > target and vy > -1.0

    def survival_time(self, flap):
        """Look ahead about a second and a half: how many ticks does the bird
        last if it flaps (or not) now and then follows the simple rule?"""
        y, vy = self.y, self.vy
        pipes = [p[:] for p in self.pipes]
        timer = self.pipe_timer
        for i in range(75):
            do_flap = flap if i == 0 else self.heuristic(y, vy, pipes)
            y, vy, pipes, timer, _ = advance(y, vy, pipes, timer, self.pipe_delay(), do_flap)
            if blocked(y, pipes):
                return i
        return 75

    def ai_controls(self):
        ahead = [p for p in self.pipes if p[0] >= BIRD_X]
        if ahead and ahead[0][0] == 7 and self.pipe_timer == 0.0:
            # A new pipe: now and then the computer misjudges it
            self.ai_offset = random.choice([-1.3, 1.3]) if random.random() < AI_SLOPPY else 0.0
        rule = self.heuristic(self.y, self.vy, self.pipes)
        if self.ai_offset:
            return rule
        keep, other = self.survival_time(rule), self.survival_time(not rule)
        return rule if keep >= other else not rule

    # ---------- one game step ----------
    def step(self, flap):
        """Advance one tick. Returns "crash" or None."""
        if flap:
            self.flap_glow = 0.12
        self.flap_glow = max(0.0, self.flap_glow - TICK)
        self.y, self.vy, self.pipes, self.pipe_timer, moved = advance(
            self.y, self.vy, self.pipes, self.pipe_timer, self.pipe_delay(), flap)
        if moved:
            self.score += sum(1 for p in self.pipes if p[0] == BIRD_X - 1)
            self.pipes = [p for p in self.pipes if p[0] >= 0]
            self.next_pipe -= 1
            if self.next_pipe <= 0:
                self.pipes.append([7, self.new_gap()])
                self.next_pipe = PIPE_SPACING
        if blocked(self.y, self.pipes):
            return "crash"
        return None

    def draw(self, sense, crashed=False):
        frame = [[SKY] * 8 for _ in range(8)]
        frame[FLOOR] = [GROUND] * 8
        for x, gap in self.pipes:
            for y in range(FLOOR):
                if not gap <= y < gap + GAP:
                    edge = y == gap - 1 or y == gap + GAP
                    frame[y][x] = PIPE_EDGE if edge else PIPE
        y = min(FLOOR, int(self.y))
        frame[y][BIRD_X] = CRASH if crashed else (BIRD_FLAP if self.flap_glow > 0 else BIRD)
        sense.set_pixels(flatten(frame))


def play_game(sense, stick, human):
    """Play one game. Returns (result, score): result is "over", or "start" when
    someone touches the joystick during the demo."""
    game = Game(human)
    game.draw(sense)
    time.sleep(0.5)
    while True:
        pressed = stick.read()
        if not game.human and pressed:
            return "start", 0
        if game.human and stick.idle():      # Player walked away: computer takes over
            game.human = False

        flap = bool(pressed) if game.human else game.ai_controls()
        if game.step(flap) == "crash":
            for _ in range(3):
                game.draw(sense, crashed=True)
                time.sleep(0.15)
                game.draw(sense)
                time.sleep(0.15)
            return "over", game.score

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
        sense.show_message(f"Score {score}", text_colour=BIRD, scroll_speed=0.06)
        human = False   # Back to the demo; touch the joystick to play again
        stick.flush()
        time.sleep(1)


if __name__ == "__main__":
    run(main)
