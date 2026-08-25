# Robox-pythonLibs

Robox UF2 source and Python Library.

## Development environment

The board is a Raspberry Pi Pico running MicroPython with the libraries in
`src/` on top. Either editor works:

- **VS Code** (recommended) — fully configured in this repo. See
  [`docs/VSCODE.md`](docs/VSCODE.md) for setup, a Thonny→VS Code cheat sheet,
  and troubleshooting.
- **[Thonny](https://thonny.org)** — still fine; nothing here breaks it. Just
  don't have both connected to the board at once.

Quick start with VS Code:

```bash
python3 -m pip install --user -r requirements-dev.txt
./tools/pico doctor          # check mpremote / picotool / MicroPico
./tools/pico sync            # upload src/ to the Pico
./tools/pico repl            # open the MicroPython prompt
```

Run `./tools/pico help` for the full command list. The same commands are
available in VS Code under **Tasks: Run Task**.

## Layout

```
src/main.py            firmware entry point; command loop over USB + Bluetooth
src/roboxlib.py        motors, ultrasonic, servo, line and colour sensors
src/communication.py   USB / BLE transports and the outgoing message queue
src/lib/picozero       vendored dependency
template_program.py    starting point for a user robot program
tools/pico             mpremote/picotool wrapper (CLI + backs the VS Code tasks)
docs/VSCODE.md         editor setup and workflows
```

Everything under `src/` is uploaded to the Pico's root, so `src/main.py` becomes
`/main.py` and `src/lib/picozero` becomes `/lib/picozero`.

## Compiling UF2 from source

The `picotool` utility is used, which can be found [here](https://github.com/raspberrypi/picotool).

All source files must first be transferred to the Pico's flash memory, then the
board booted into BOOTSEL mode so its flash can be read back.

### Automated

Run the VS Code task **`UF2: Build release (sync -> BOOTSEL -> save)`**, or from
a terminal:

```bash
./tools/pico sync        # upload src/ to flash
./tools/pico bootsel     # reboot into BOOTSEL, no button press needed
./tools/pico uf2         # -> build/robox-<timestamp>.uf2
```

### Manual

Transfer all source files to the Pico's flash memory via
[Thonny](https://thonny.org) or `./tools/pico sync`.

Once transferred, unplug the Pico and plug it in while holding the BOOTSEL
button to boot it in BOOTSEL mode. A removable volume will be mounted to your
device, typically under the name `RPI-RP2` or `NO NAME`.

In a command line program, use the following command to extract the UF2 from the
Pico:

```bash
picotool save -a <DESTINATION_PATH> -t uf2
```

This will save the *entire* Pico's flash memory onto your device. This UF2 can
then be used to flash other Pico devices:

```bash
./tools/pico flash <PATH_TO_UF2>     # board must be in BOOTSEL mode
```
