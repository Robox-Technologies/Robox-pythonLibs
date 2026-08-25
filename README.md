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
tools/pico             mpremote/picotool wrapper (CLI + backs the VS Code tasks)
tools/build_uf2.py     builds a release UF2 on the host, no board required
pyrightconfig.json     IntelliSense / type-checking config
docs/VSCODE.md         editor setup and workflows
```

Everything under `src/` is uploaded to the Pico's root, so `src/main.py` becomes
`/main.py` and `src/lib/picozero` becomes `/lib/picozero`.

## Building the release UF2

A release UF2 is just two things stacked in flash: a stock MicroPython build,
and a littlefs filesystem holding everything in `src/`. Both are reproducible on
a laptop, so **no Pico is needed to build one** — `picotool save` only ever
needed a board because it read the result back off real flash.

```bash
python3 -m pip install --user -r requirements-dev.txt   # littlefs-python
./tools/pico build                                      # -> build/robox-<version>.uf2
```

The first build downloads the pinned MicroPython firmware into
`build/firmware/` (about 650 KB, cached); after that builds work offline. The
version is named at the top of [`tools/build_uf2.py`](tools/build_uf2.py) — bump
it there deliberately, not by accident. In VS Code the same thing is
**Tasks: Run Task → `UF2: Build release (no board needed)`**.

`build` prints the flash map it used, packs the same files `pico sync` uploads,
and then re-reads its own artifact — parsing the UF2 back and mounting the
filesystem inside it — before declaring success:

```
==> Base firmware build/firmware/RPI_PICO-20241129-v1.24.1.uf2
   map   filesystem 0x100a0000..0x10200000 (1408 KiB), read from the firmware
   add   main.py                                    6791 B
   fw    0x10000000..0x10051600  (325 KiB, 1302 block(s))
  ok wrote build/robox-2.0.1.uf2 (3.4 MiB, 6934 blocks)
  ok verified: 7 file(s) mount cleanly from the UF2
```

Two builds of the same tree are byte-identical, so a release can be diffed and
rebuilt in CI.

Useful variants:

```bash
./tools/pico build --offline                  # never download; fail if not cached
./tools/pico firmware 1.26.1                  # cache a different MicroPython
ROBOX_BASE_UF2=old-dump.uf2 ./tools/pico build # base it on a board dump
./tools/pico build --no-base --sparse libs.uf2 # just src/, keep the board's firmware
./tools/pico inspect build/robox-2.0.1.uf2     # list what a UF2 actually contains
```

Flashing is unchanged — hold BOOTSEL while plugging the board in, then either
drag the UF2 onto the `RPI-RP2` volume or:

```bash
./tools/pico flash build/robox-2.0.1.uf2
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

which dumps the entire flash. Such a dump also works as a base for
`./tools/pico build`: the builder keeps its firmware half and replaces the
filesystem half with a freshly built one.

### Other boards

The flash map above is the 2 MB Pico's. `build` reads the filesystem window out
of the base firmware's own binary-info block (via `picotool`, when installed),
so a firmware for a differently laid out board comes out right by itself. If
`picotool` is missing it falls back to the RPI_PICO numbers, which
`--fs-base`/`--fs-size` override; `./tools/pico fs-layout` prints the real
numbers from a connected board.
