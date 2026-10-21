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
| `block_attack.py` | Swap blocks in a rising stack to make rows of three and set off chains, in the style of Tetris Attack |
| `breakout.py` | Smash all the bricks with a ball and paddle, level after level |
| `frogger.py` | Get the frog across the road and over the river on the logs into its 4 homes |
| `pinball.py` | A pinball table twice as tall as the display, with a camera following the ball: pop bumpers, slingshots, top lanes, a ramp and multiball |
| `platformer.py` | A tiny hero with 3 lives runs through ever harder levels: pits, pipes, coins, enemies and birds |
| `pong.py` | Pong to 5 points, with angled shots and a ball that speeds up |
| `snake.py` | A snake hunting for food that changes colour every time |
| `space_invaders.py` | Shoot the marching invaders before they land, hide behind the shields and catch the saucer |
| `tetris.py` | Classic falling blocks in an 8×8 well |

Every game starts as a demo in which the computer plays, until you grab the
joystick: see [Playing the games yourself](#playing-the-games-yourself).

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
again.

| Game | Controls |
| --- | --- |
| `block_attack.py` | Move the 2-block cursor with the joystick, press to swap the two blocks |
| `breakout.py` | Left / right to move the paddle |
| `frogger.py` | Push the joystick to hop up, down, left or right |
| `pinball.py` | Left / right for the flippers (hold to keep them up), up / down to tilt, press to switch between the follow camera and an overview of the whole table |
| `platformer.py` | Left / right to run, up or press to jump |
| `pong.py` | Up / down to move the left paddle; the computer plays right |
| `snake.py` | Joystick in any direction to steer |
| `space_invaders.py` | Left / right to move, up or press to fire |
| `tetris.py` | Left / right to move, up to rotate, down to drop faster, press to drop straight down |

In the emulator, use the joystick in the emulator window or the arrow keys and
Enter while that window is focused.

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
