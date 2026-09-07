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
./tools/pico stubs           # MicroPython stubs for IntelliSense -> typings/
./tools/pico doctor          # check the toolchain
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
firmware/manifest.py   what's frozen into the custom-built firmware
firmware/Dockerfile    pinned ARM cross-compile toolchain
firmware/vendor/       git submodule: MicroPython + pico-sdk (+ all its own submodules)
tools/pico             mpremote/picotool wrapper (CLI + backs the VS Code tasks)
tools/build_uf2.py     assembles a UF2 on the host, no board required
pyrightconfig.json     IntelliSense / type-checking config
docs/VSCODE.md         editor setup and workflows
docs/RELEASE.md        the frozen-firmware release process
```

Everything under `src/` is uploaded to the Pico's root, so `src/main.py` becomes
`/main.py` and `src/lib/picozero` becomes `/lib/picozero`.

## Building the release UF2

**main.py, roboxlib.py, communication.py, framed.py, protocol.py, colors.py,
calibration.py, matrix.py and lib/picozero are frozen into the firmware
itself** (compiled from source, not the stock micropython.org build), so a
release UF2 carries **zero filesystem blocks** — it can't touch
`config.json` or `program.py` on any board it's flashed onto, no matter what
they contain. A frozen `main.py` autoruns on its own (confirmed on real
hardware — MicroPython's boot sequence checks the frozen module table
before the filesystem), so the same artifact works for a brand-new blank
board and an already-set-up one; there's no separate "factory image" and no
loose stub file to maintain. See [`docs/RELEASE.md`](docs/RELEASE.md) for
the full story, including why a real compile is needed rather than reusing
a stock UF2.

```bash
git submodule update --init --recursive firmware/vendor/micropython
./tools/pico fw-doctor    # checks Docker (or a local ARM toolchain)
./tools/pico release      # -> build/robox-<version>.uf2, verified filesystem-safe
```

The version is named at the top of [`src/main.py`](src/main.py)
(`CURRENT_FIRMWARE_VERSION`) — bump it there deliberately, not by accident.
`release` compiles via Docker by default (`firmware/Dockerfile`, so a build
doesn't depend on whatever's on a dev's PATH); pass `--local` to use
`arm-none-eabi-gcc`/`cmake` off PATH instead.

```bash
./tools/pico inspect build/robox-<version>.uf2   # list what a UF2 actually contains
./tools/build_uf2.py verify-release build/robox-<version>.uf2  # zero-FS-blocks check (release already runs this)
```

Flashing is unchanged — hold BOOTSEL while plugging the board in, then either
drag the UF2 onto the `RPI-RP2` volume or:

```bash
./tools/pico flash build/robox-<version>.uf2
```

### Provisioning a brand-new board

The same `release` build works here too — a blank Pico's filesystem region
is unformatted, and MicroPython's own boot code formats it fresh on first
mount, same as it always has. `./tools/pico build` and `./tools/pico
factory` are aliases for `release`, kept for discoverability:

```bash
./tools/pico build        # alias for release -- build/robox-<version>.uf2
```

### Capturing a UF2 off a board

Still supported, and still the way to snapshot a board that is already set up
(it also captures `program.py` and any calibration data, which a clean build
deliberately leaves out). Needs
[`picotool`](https://github.com/raspberrypi/picotool) and the board in BOOTSEL
mode — the task is **`UF2: Capture from board (sync -> BOOTSEL -> save)`**, or:

```bash
./tools/pico sync        # upload src/ to flash
./tools/pico bootsel     # reboot into BOOTSEL, no button press needed
./tools/pico uf2         # -> build/robox-<timestamp>.uf2
```

To do it by hand: transfer `src/` with [Thonny](https://thonny.org) or
`./tools/pico sync`, unplug the Pico and plug it back in while holding BOOTSEL
(a removable volume named `RPI-RP2` or `NO NAME` appears), then

```bash
picotool save -a <DESTINATION_PATH> -t uf2
```

which dumps the entire flash. Such a dump also works as a `--base` for
`tools/build_uf2.py build` directly (it keeps the firmware half and replaces
the filesystem half with a freshly built one) — `pico release`/`build`/
`factory` don't use this at all (they ship the compiled firmware as-is, no
filesystem image involved), so use `build_uf2.py` directly if you need this.

### Other boards

The flash map above is the 2 MB Pico's. `build_uf2.py build` reads the
filesystem window out of the base firmware's own binary-info block (via
`picotool`, when installed), so a firmware for a differently laid out board
comes out right by itself. If `picotool` is missing it falls back to the
RPI_PICO numbers, which `--fs-base`/`--fs-size` override; `./tools/pico
fs-layout` prints the real numbers from a connected board.
