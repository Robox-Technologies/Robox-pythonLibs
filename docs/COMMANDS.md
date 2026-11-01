# Robox command guide

This is the short guide to the repository's command-line tools. The public
entry point is [`tools/robox`](tools/robox). It groups commands by what they
do:

```text
board      interact with a connected Pico
firmware   build, inspect, and flash firmware
dev        development utilities
test       run offline tests
```

Run `./tools/robox help` at any time to see the command groups.

## First-time setup

Install the host dependencies:

```bash
python3 -m pip install --user -r requirements-dev.txt
```

Install the MicroPython submodule before building firmware:

```bash
git submodule update --init --recursive firmware/vendor/micropython
```

Check the host tools and firmware toolchain:

```bash
./tools/robox dev doctor
./tools/robox firmware doctor
```

## Board commands

Board commands communicate with a Pico over its serial connection. Connect
only one serial client at a time; close the VS Code vREPL before running a
command that needs the board.

Set `PICO_PORT` when automatic port detection is not suitable:

```bash
PICO_PORT=/dev/cu.usbmodem1101 ./tools/robox board ports
```

### `board ports`

List detected serial ports.

```bash
./tools/robox board ports
```

### `board repl`

Open the MicroPython REPL. Press `Ctrl-]` to exit.

```bash
./tools/robox board repl
```

### `board stop`

Send `Ctrl-C` to interrupt the running program without resetting the board.

```bash
./tools/robox board stop
```

The website's stop action uses `reset_device`, which restarts the board and
therefore stops any running user program. `stop_motors` only stops the motors.

### `board reset`

Perform a hard MicroPython reset using `machine.reset()`.

```bash
./tools/robox board reset
```

### `board soft-reset`

Restart the interpreter using the soft-reset/EOF behavior. This is useful
when the board should restart without a hardware reset.

```bash
./tools/robox board soft-reset
```

### `board upload`

Copy a local file to the board. If the destination is omitted, the source
filename is used.

```bash
./tools/robox board upload template_program.py
./tools/robox board upload template_program.py program.py
```

### `board download`

Copy a file from the board to the local machine. If the destination is
omitted, the remote filename is used.

```bash
./tools/robox board download program.py
./tools/robox board download program.py backups/program.py
```

### `board files`

Show a recursive listing of files on the board. Pass a remote path to limit
the listing.

```bash
./tools/robox board files
./tools/robox board files /lib
```

### `board cat`

Print a remote file without downloading it.

```bash
./tools/robox board cat program.py
```

### `board run`

Run a local file on the board without saving it to the board filesystem.

```bash
./tools/robox board run template_program.py
```

### `board wipe`

Delete user files from the board filesystem. Use this carefully: it removes
the files that hold user programs and configuration.

```bash
./tools/robox board wipe
```

### `board command`

Send one named firmware command over the framed USB protocol and print reply
frames. The command is sent exactly as provided; this command does not open
the REPL or reset the board.

```bash
./tools/robox board command firmware_check
./tools/robox board command --port /dev/cu.usbmodem1101 firmware_check
./tools/robox board command --listen 5 firmware_check
```

### `board rename`

Change the board's advertised name. Names may contain letters, numbers,
underscores, and hyphens, and must be 1–16 characters long.

```bash
./tools/robox board rename MyRobot
```

### `board scan`

Scan for a BLE advertisement with an exact name. This requires the optional
BLE dependency from `requirements-dev.txt`.

```bash
./tools/robox board scan
./tools/robox board scan --name MyRobot --timeout 10
./tools/robox board scan --name MyRobot --show-all
```

### `board monitor`

Watch compact, important debug events from a debug firmware over USB. It does
not print every protocol frame.

During an upload, the monitor shows received upload frames, periodic stored
line counts, acknowledgements/negative acknowledgements, and the final upload
result. The detailed event stream is also written to `debug.log`.

```bash
./tools/robox board monitor
./tools/robox board monitor --port /dev/cu.usbmodem1101
```

Use `board monitor` when the website is connected over Bluetooth. When the
website owns the USB port, retrieve the detailed communication history with
`board debug-log` after disconnecting it.

The monitor stays running across a Pico reset. If the USB serial device
temporarily disappears, it waits for the same port to reappear and resumes
automatically. Press `Ctrl-C` to stop the monitor.

### `board debug-log`

Download the detailed log from the most recent boot-to-reset run.

```bash
./tools/robox board debug-log
./tools/robox board debug-log --output last-run.log
```

## Firmware commands

Firmware commands build or manipulate UF2 files. Building a release does not
require a connected board.

### `firmware doctor`

Check the firmware cross-compilation prerequisites. Docker is the default
build path; a local ARM toolchain can be used with `--local` on build-related
commands.

```bash
./tools/robox firmware doctor
```

### `firmware build`

Build the custom MicroPython firmware with the Robox source frozen into it.
Docker is used by default.

```bash
./tools/robox firmware build
./tools/robox firmware build --local
./tools/robox firmware build --debug
```

### `firmware release`

Build a distributable UF2 and verify that it is safe to use on a blank or
existing board. The optional argument selects the output path.

```bash
./tools/robox firmware release
./tools/robox firmware release build/robox-custom.uf2
./tools/robox firmware release --local
./tools/robox firmware release --debug
```

### `firmware deploy`

Build a release UF2 and flash it to a board in BOOTSEL mode in one workflow.

```bash
./tools/robox firmware deploy
./tools/robox firmware deploy build/robox-custom.uf2
./tools/robox firmware deploy --debug
```

### `firmware flash`

Flash an existing UF2 to a Pico that is already in BOOTSEL mode.

```bash
./tools/robox firmware flash build/robox-2.0.0.uf2
```

### `firmware reflash`

Reboot the board into BOOTSEL mode, wait for it to appear, and flash a UF2.
When no file is provided, the legacy default UF2 path is used.

```bash
./tools/robox firmware reflash
./tools/robox firmware reflash build/robox-2.0.0.uf2
```

### `firmware inspect`

List the contents and metadata of a UF2 without flashing it.

```bash
./tools/robox firmware inspect build/robox-2.0.0.uf2
```

### `firmware bootsel`

Reboot a connected board into Raspberry Pi BOOTSEL mass-storage mode.

```bash
./tools/robox firmware bootsel
```

### `firmware save`

Capture the board's flash into a UF2 file. The board must already be in
BOOTSEL mode and `picotool` must be installed.

```bash
./tools/robox firmware save
./tools/robox firmware save build/board-backup.uf2
```

### `firmware fs-layout`

Print the detected flash filesystem geometry used by the firmware tools.
This is mainly useful when diagnosing a board or validating a custom layout.

```bash
./tools/robox firmware fs-layout
```

## Development commands

### `dev doctor`

Check host-side development dependencies such as `mpremote`, `picotool`,
type-checking configuration, and installed stubs.

```bash
./tools/robox dev doctor
```

### `dev stubs`

Install MicroPython type stubs into `typings/` for editor completion and
type checking.

```bash
./tools/robox dev stubs
```

### `dev vectors`

Regenerate the protocol conformance vectors in
`tests/vectors/frames.json`. These vectors are shared with the website's
protocol tests.

```bash
./tools/robox dev vectors
```

### `dev benchmark`

Run the communication benchmark against a board. It uploads seeded,
non-moving programs, reads them back, and compares the contents. It does not
send a command that starts a program.

```bash
./tools/robox dev benchmark --transport usb
./tools/robox dev benchmark --transport ble
./tools/robox dev benchmark --corpus tiny
```

Useful options:

```bash
./tools/robox dev benchmark --port /dev/cu.usbmodem1101
./tools/robox dev benchmark --ble-address AA:BB:CC:DD:EE:FF
./tools/robox dev benchmark --repeat 3
./tools/robox dev benchmark --out docs/comm-baseline.json
./tools/robox dev benchmark --no-reset
./tools/robox dev benchmark --restore template_program.py
```

Use `./tools/robox dev benchmark --help` for the complete option list.

## Tests

Run the offline Python test suite:

```bash
./tools/robox test
```

Pass standard `unittest` options to narrow the run:

```bash
./tools/robox test -k crc
```

The current test suite is being reorganized alongside the source packages.
Hardware commands and the communication benchmark are separate from this
offline test command.

## Safe command preview

Set `DRY_RUN=1` to print the underlying board command without touching the
hardware:

```bash
DRY_RUN=1 ./tools/robox board upload template_program.py
DRY_RUN=1 ./tools/robox board reset
```

`DRY_RUN` applies to commands delegated through the board wrapper. Firmware
build and inspection commands still perform their normal local work.

## Common problems

### No serial port found

Check the port list and optionally set `PICO_PORT`:

```bash
./tools/robox board ports
PICO_PORT=/dev/cu.usbmodem1101 ./tools/robox board repl
```

Close any other serial client, especially the VS Code vREPL.

### Firmware build prerequisites are missing

Run both diagnostics:

```bash
./tools/robox dev doctor
./tools/robox firmware doctor
```

Use `./tools/robox firmware build --local` only when the local ARM
cross-compiler and CMake are installed and intentionally selected.

### UF2 flashing does not find the board

Put the Pico into BOOTSEL mode first:

```bash
./tools/robox firmware bootsel
./tools/robox firmware flash build/robox-2.0.0.uf2
```

If the board is not connected over serial, hold the physical BOOTSEL button
while reconnecting it, then run `firmware flash`.
