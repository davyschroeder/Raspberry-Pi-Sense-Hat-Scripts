# Sense HAT Scripts

Little animations and self-playing games for the 8×8 LED matrix of the
[Raspberry Pi Sense HAT](https://www.raspberrypi.com/products/sense-hat/).

| Script | What it shows |
| --- | --- |
| `beating_heart.py` | A red heart pulsing like a heartbeat |
| `cpu_temp.py` | The Pi's CPU temperature as two digits plus a bar, green to red |
| `firefly.py` | Softly coloured fireflies drifting and glowing in the dark |
| `matrix.py` | Green "Matrix" code rain with the odd flicker |
| `platformer.py` | A tiny hero with 3 lives runs through ever harder levels: pits, pipes, coins, enemies and birds |
| `pong.py` | Two computer players play Pong to 5 points, with angled shots and a ball that speeds up |
| `snake.py` | A computer-controlled snake hunting for food that changes colour every time |

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

Each script imports `SenseHat` from `sense_hat` itself (falling back to
`sense_emu`) and hands it to `init()` in `common.py`. That shared helper stops
other running scripts from this folder, sets the rotation and clears the
display on exit (Ctrl+C or `SIGTERM`). Keep `common.py` next to the scripts.

## License

[MIT](LICENSE) © Davy Schroëder
