# Releasing custom firmware

## Why this exists

A release used to be a stock, unmodified MicroPython UF2 plus a **freshly
formatted littlefs filesystem** built from `src/`. Every file in `src/` --
`main.py`, `roboxlib.py`, `communication.py`, ... -- was a loose file in that
filesystem, not frozen into anything. Rebuilding that filesystem from scratch
on every release meant `config.json` (motor/colour calibration) and
`program.py` (the user's uploaded program) were silently wiped on every
real-world update.

The fix is the flash-region split MicroPython already has: a firmware region
(the interpreter plus anything frozen into it) and a filesystem region,
physically separate. A UF2 that only carries firmware-region blocks can't
touch `config.json`/`program.py` no matter what's on the board, because it
never writes to where they live. That requires actually compiling
MicroPython from source with our code frozen in -- see `firmware/manifest.py`.

## What's frozen, and what autorun actually does

**Frozen**: `main.py`, `roboxlib.py`, `communication.py`, `framed.py`,
`protocol.py`, `colors.py`, `calibration.py`, `matrix.py`, and `lib/picozero`.
All vendor code -- nothing here is ever hand-edited on a device; users write
`program.py` on the companion website instead.

Two things had to be verified against real hardware, not just documentation,
because the generic guidance out there is contradictory or version-specific:

1. **A frozen `main.py` autoruns on its own -- no loose stub needed.**
   MicroPython's boot sequence (`pyexec_file_if_exists` in
   `shared/runtime/pyexec.c`) checks the frozen module table for a module
   named after the file it's looking for *before* ever checking the
   filesystem. Confirmed on a real Pico with the LED test: flash the custom
   firmware, leave an unrelated no-op loose `/main.py` in place, power-cycle
   -- the frozen `main.py`'s `LED.on()` still runs. A loose `/main.py` is
   irrelevant once `main.py` is frozen; there is no "transition" problem for
   `main.py` itself and no stub file to maintain.
2. **Everything main.py imports needed one explicit fix.** MicroPython's
   default `sys.path` is `['', '.frozen', '/lib']` -- the filesystem root
   comes *before* the frozen search path, so a loose `roboxlib.py` (etc.)
   left over from before this scheme existed would otherwise keep shadowing
   the frozen version forever, no matter how many releases land on that
   board. Also confirmed directly on hardware: reordering `sys.path` so `''`
   comes last is what makes `import roboxlib` resolve to the frozen module
   instead of a stale loose file. `src/main.py` does this itself, once, as
   its first action, before importing anything else.

An earlier version of this migration added a "self-heal" mechanism (a loose
`/main.py` stub plus code in `roboxlib.py`/`communication.py` to rewrite it)
on the theory that a frozen `main.py` would *not* autorun and something else
was needed to force it. That theory was wrong (see point 1), and the
mechanism was actively harmful: on any firmware *without* frozen modules --
which describes every board during normal development via `./tools/pico
sync` -- it would detect the real `main.py` as "not yet migrated" and
overwrite it with a stub pointing at a frozen module that doesn't exist,
bricking the board. It has been removed. There is no `src/selfheal.py` and
no `firmware/main_stub.py`.

**Loose and preserved**: `config.json`, `program.py`. Preserved because a
release UF2 contains zero blocks in the filesystem region at all -- there's
nothing to preserve logic for, the bytes are simply never touched.

## The one build output

**`./tools/pico release [out.uf2]`** is the only artifact needed, for both a
brand-new/blank board and an already-set-up one:

- Compiles custom firmware (`./tools/pico fw-build`) and ships it as-is:
  firmware only, verified by `tools/build_uf2.py verify-release` to carry
  zero filesystem blocks.
- On a blank board, MicroPython's own `_boot.py` formats the unmounted
  filesystem region on first boot, same as it always has -- no factory-image
  filesystem content is needed to make that happen.
- On an existing board, the filesystem region is untouched, so
  `config.json`/`program.py` survive, and the `sys.path` fix means the newly
  frozen libraries are what actually run, not any stale loose files left
  over from before.

There's no separate command for provisioning a blank board -- `release` is
the only build this project ships. (An earlier version of this doc kept
`build`/`factory` as aliases for discoverability; they were removed since
they were just extra names for the exact same function, with no distinct
factory-image code path behind them.)

`./tools/pico uf2` is unchanged: dumps a real board's flash (needs
`picotool` + BOOTSEL). Still the way to snapshot a board that's already set
up, including whatever `program.py`/`config.json` it has.

## Getting a build onto a board

- **`./tools/pico deploy [out.uf2]`** -- `release`, then flash it onto
  whatever's connected, in one command. The everyday path: change something
  in `src/`, run this, test on the board.
- **`./tools/pico reflash [file.uf2]`** -- just the flashing half: nudges a
  connected board into BOOTSEL, polls for the RPI-RP2 volume to actually
  appear (not a guessed `sleep`), then loads. Defaults to `latest.uf2` at
  the repo root if no file is given -- deliberately *not* "most recently
  modified file matching `build/robox-*.uf2`", which picked up a stale
  one-off test build during development and flashed it onto a real board,
  wiping `config.json`/`program.py`. `build/` is a scratch directory; recency
  there means nothing.
- **`./tools/pico flash <file.uf2>`**/**`bootsel`** still exist separately
  for anyone who wants the steps apart (e.g. holding BOOTSEL by hand).

## Build toolchain

`firmware/vendor/micropython` is a git submodule pinned to the same version
`tools/build_uf2.py` used to download (`v1.24.1`), with `lib/pico-sdk` and
its own submodules checked out. **All of them** -- `tinyusb`, `mbedtls`,
`lwip`, `btstack`, `cyw43-driver` -- are required to build even the plain
`RPI_PICO` board; the rp2 CMake build references them structurally
regardless of whether wireless features end up compiled in. (An earlier
version of this doc claimed the wireless-only ones could be skipped for a
non-W board; that was wrong -- the build fails at the CMake step without
`mbedtls` specifically, `Cannot find source file .../lib/mbedtls/library/aes.c`.)
Also note MicroPython vendors **two separate copies** of both `tinyusb` and
`mbedtls` -- one under `lib/pico-sdk/lib/...` and one directly under
`lib/...` at the top level of the MicroPython repo -- and the rp2 build
needs the top-level ones initialized too, not just pico-sdk's.

```
git submodule update --init --recursive firmware/vendor/micropython
./tools/pico fw-doctor    # checks Docker (or a local ARM toolchain)
./tools/pico release      # -> build/robox-<version>.uf2
```

Docker (`firmware/Dockerfile`) is the default build path so a release
doesn't depend on whatever happens to be on a dev's PATH -- build the image
once with `docker build -t robox-fw-build firmware/`, or pass `--local` to
`fw-build`/`release` to use `arm-none-eabi-gcc`/`cmake` straight off PATH
instead. **Docker and a local build share the same `mpy-cross` output path**
(`firmware/vendor/micropython/mpy-cross/build/`) since both mount/use the
same checkout -- running one after the other leaves behind a binary for the
wrong OS (a Linux ELF where a local macOS build expects a Mach-O, or vice
versa), which fails with "Exec format error" on the next build. Rebuild
`mpy-cross` for whichever toolchain you're using next if you switch between
them: `make -C firmware/vendor/micropython/mpy-cross`. This wasn't caught
until it broke a real build; fixing it properly (e.g. separate output dirs
per toolchain) is still open.

On this dev machine specifically, `mpy-cross` also needed
`CFLAGS_EXTRA="-Wno-error=gnu-folding-constant"` to build with the installed
Clang -- a known MicroPython v1.24.1 issue with newer Clang treating a GNU
extension warning as an error. `./tools/pico fw-build --local` does not add
this automatically yet; if a local build fails on `emitnx64.c` with a
`-Wgnu-folding-constant` error, that's why.

There used to be a `./tools/pico sync`/`mount` fast-iteration path that
pushed loose files from `src/` straight to a board's filesystem in seconds,
for testing without a full rebuild. They're gone now: every one of those
files is frozen into the release build, so once a board is running
custom-compiled firmware (which describes every board in normal use now,
not just distributed ones), a loose copy is dead weight the frozen version
always wins over -- `sync` had no effect at all against such a board, and
that confusion is exactly what led to removing it rather than leaving a
command around that quietly does nothing. The real cost: testing any
change now needs a full firmware recompile + reflash (`./tools/pico
deploy`, a couple of minutes) instead of an instant file push. There is no
faster loop for this project's own source files; `program.py` (the
user's uploaded program, never frozen) is unaffected and still updates
instantly through the normal app protocol.

## Verifying a release

```
./tools/pico release
./tools/build_uf2.py verify-release build/robox-<version>.uf2   # run automatically by `release`
```

On real hardware (all of the following were actually run, not just
described, during this migration):

1. Flash a `release` build onto a board with an existing `config.json`/
   `program.py` and old loose `roboxlib.py` etc. still present. Confirm both
   data files are untouched (`./tools/pico ls /`).
2. Confirm the frozen code is what's actually running, not the stale loose
   copies -- the reliable way is a physical check (e.g. the onboard LED,
   which `main.py` turns on early) or a behavioural difference, **not**
   `mpremote exec`/`ls`/`cat`: connecting with mpremote at all forces the
   board into raw-REPL mode, and a soft-reset issued from raw-REPL mode
   skips the main.py autorun step entirely by design (see
   `pyexec_mode_kind` in `shared/runtime/pyexec.c`) -- this looks exactly
   like "main.py never runs" from the host side and cost significant time to
   tell apart from a real firmware bug. A genuine power-cycle, or a reboot
   triggered by `picotool load`/`picotool reboot` (not `mpremote reset`),
   avoids this.
3. `./tools/run-tests` green.
