# Sense HAT Scripts

Little animations and games for the 8×8 LED matrix of the
[Raspberry Pi Sense HAT](https://www.raspberrypi.com/products/sense-hat/).

### Animations

| Script | What it shows |
| --- | --- |
| `beating_heart.py` | A red heart pulsing like a heartbeat |
| `cpu_temp.py` | The Pi's CPU temperature as two digits plus a bar, green to red. A wild flame flares up when it runs hot; a happy ^_^ face pops up when it stays cool |
| `firefly.py` | Softly coloured fireflies drifting and glowing in the dark |
| `matrix.py` | Green "Matrix" code rain with the odd flicker |

### Games

| Script | What it is |
| --- | --- |
| `block_attack.py` | Swap blocks in a rising stack to make rows of three and set off chains |
| `bomberman.py` | Drop bombs to blow up bricks and monsters, collect power-ups and find the hidden exit, level after level |
| `breakout.py` | Smash the bricks with ball and paddle through 8 levels, with silver and gold bricks and power-up capsules (wider paddle, catch, laser, slow, three balls, level exit, extra life) |
| `coin_flip.py` | Heads or tails: a spinning coin flies up, turns from gold to silver and back, and wobbles to a stop on a smiling head or a 1, with a tally of every flip |
| `dice.py` | Roll a D4, D6, D8, D10, D12 or D20 that tumbles over and over, or two six-sided dice that bounce across the display; every die gets a random colour, the highest number and doubles flash gold |
| `flappy_bird.py` | Flap through the gaps in the pipes; every 5 points they come faster |
| `frogger.py` | Hop as far up as you can through an endless, ever faster world of roads and rivers that keeps scrolling up. 10 points per row, 50 per fly |
| `missile_command.py` | Shoot down the missiles before they hit your four cities, with chain-reaction fireballs and splitting missiles |
| `peg_drop.py` | Drop a ball through a board of pegs twice as tall as the display into slots worth 1 to 10 points, 5 balls per game |
| `pinball.py` | A pinball table twice as tall as the display, with a camera following the ball: a launch lane, pop bumpers, slingshots, top lanes, a ramp and multiball |
| `platformer.py` | A tiny hero with 3 lives runs through ever harder levels: pits, pipes, coins, enemies and birds |
| `pong.py` | Pong to 5 points, with angled shots and a ball that speeds up |
| `rubiks_cube.py` | A real, working Rubik's Cube: turn rows and columns, turn the whole cube to see every face. The demo scrambles it and solves it again; solve it yourself and the next scramble is longer |
| `slot_machine.py` | Three reels that stop one by one: three of a kind wins coins, three sevens hit the jackpot |
| `snake.py` | A snake hunting for food that changes colour every time |
| `space_invaders.py` | Shoot the marching invaders before they land, hide behind the shields and catch the saucer |
| `tetris.py` | Classic falling blocks in an 8×8 well |

Every game starts as a demo in which the computer plays, until you grab the
joystick: see [Playing the games yourself](#playing-the-games-yourself). Dice and
Heads or tails have no demo: they wait for you.

## Requirements

- A Raspberry Pi with a Sense HAT and Python 3
- The Sense HAT library:

  ```bash
  sudo apt install sense-hat
  ```

No Sense HAT? The scripts automatically fall back to the
[Sense HAT emulator](https://sense-emu.readthedocs.io/) when the library isn't
installed or no board is found. Install it and open the emulator window
before starting a script:

```bash
sudo apt install sense-emu-tools
sense_emu_gui &
```

## Usage

```bash
git clone https://github.com/davyschroeder/sensehat-scripts.git
cd sensehat-scripts
python3 firefly.py
```

Stop a script with **Ctrl+C**; the LEDs switch off on exit.

Starting a script automatically stops any other script from this folder that
is still running, so you can simply switch from one animation to the next.

### Playing the games yourself

Every game starts as a demo in which the computer plays. Touch the Sense HAT
joystick and a green play sign appears: now you're in control. After game over,
or when you leave the joystick alone for 30 seconds, the computer takes over
again. Dice (`dice.py`) and Heads or tails (`coin_flip.py`) have no demo: they
simply wait until you press the joystick.

| Game | Controls |
| --- | --- |
| `block_attack.py` | Move the 2-block cursor with the joystick, press to swap the two blocks |
| `bomberman.py` | Push the joystick to walk, press to drop a bomb |
| `breakout.py` | Left / right to move the paddle, press to launch the ball or fire the laser |
| `coin_flip.py` | Press (or left / right) to flip, up or down to see the tally (gold = heads on the left, silver = tails on the right) |
| `dice.py` | Left / right picks a die (its name scrolls by first), up / down changes its colour, press to roll |
| `flappy_bird.py` | Push the joystick any way (or press it) to flap |
| `frogger.py` | Push the joystick to hop up, down, left or right |
| `missile_command.py` | Move the crosshair with the joystick, press to fire |
| `peg_drop.py` | Left / right to aim, press or down to drop the ball |
| `pinball.py` | Left / right for the flippers (hold to keep them up), up / down to tilt, press to switch between the follow camera and an overview of the whole table |
| `platformer.py` | Left / right to run, up or press to jump |
| `pong.py` | Up / down to move the left paddle; the computer plays right |
| `rubiks_cube.py` | See [Turning the Rubik's Cube](#turning-the-rubiks-cube) |
| `slot_machine.py` | Press (or push any way) to spin, press again to stop the reels one by one. A spin costs 1 of your 10 coins |
| `snake.py` | Joystick in any direction to steer |
| `space_invaders.py` | Left / right to move, up or press to fire |
| `tetris.py` | Left / right to move, press (A) to rotate, down to drop faster, up to drop straight down |

In the emulator, use the joystick in the emulator window or the arrow keys and
Enter while that window is focused.

#### Turning the Rubik's Cube

The display shows the front face of the cube: 3×3 stickers of 2×2 pixels.

- **Move the cursor** (a blinking frame around one sticker) with the joystick.
  Push past the edge of the face and the whole cube turns to show the face
  behind that edge, so you can walk all the way round it.
- **Press** to grab the row and the column under the cursor: grey lines
  appear along them. Now push **left / right** to turn that row, or
  **up / down** to turn that column. Press again to let go and move the
  cursor.
- To turn the front face itself, look at the cube from the side first: the
  front face is then a column you can turn.
- **Hold the press for 2 seconds** and the computer shows you how to solve
  it, then gives you a new scramble.

Your first scramble is only 2 turns; every time you solve the cube the next
one is a bit longer. The demo uses exactly the same moves, so watching it is
a good way to learn the controls.

#### With a gamepad

A USB or Bluetooth gamepad works too: the d-pad or the left stick steers, and A, B
or Start count as pushing the joystick in. In `pinball.py` the shoulder buttons and
triggers work the flippers. Install the evdev library once:

```bash
sudo apt install python3-evdev
```

Plug in (or pair) the gamepad before or while a game runs; it is picked up
automatically. Without evdev the games simply use the Sense HAT joystick.

### Display rotation

All scripts use rotation 0 by default. If your Sense HAT is mounted differently,
override it for any script with `SENSE_HAT_ROTATION` (0, 90, 180 or 270):

```bash
SENSE_HAT_ROTATION=180 python3 matrix.py
```

## Run on boot (optional)

Create `/etc/systemd/system/sensehat.service` (adjust the user and path):

```ini
[Unit]
Description=Sense HAT animation

[Service]
ExecStart=/usr/bin/python3 /home/pi/sensehat-scripts/firefly.py
Restart=on-failure
User=pi

[Install]
WantedBy=multi-user.target
```

Then enable it:

```bash
sudo systemctl enable --now sensehat.service
```

## How it's organised

Every script is self-contained: grab a single file and run it, nothing else
needed. At the top of each script is the same small setup block, which picks
the real Sense HAT or the emulator, stops other running scripts from the same
folder, sets the rotation and clears the display on exit (Ctrl+C or `SIGTERM`).

## License

[MIT](LICENSE) © Davy Schroëder
