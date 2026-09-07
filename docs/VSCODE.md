# VS Code as a Thonny replacement

Everything Thonny does for this repo — connect over USB serial, browse and edit
the Pico's filesystem, upload files, open a REPL, and dump the flash to a UF2 —
now works from VS Code.

Two layers are set up, deliberately overlapping:

| Layer | What it gives you | When you want it |
| --- | --- | --- |
| **MicroPico extension** | Status-bar buttons, a live vREPL terminal, right-click Upload/Download on files, remote filesystem browsing | Interactive work. This is the direct Thonny analogue. |
| **`tools/pico` + VS Code tasks** | Scriptable `mpremote`/`picotool` commands, identical from the terminal or CI | Repeatable steps, the UF2 release build (which needs no board at all), anything you want in version control |

---

## One-time setup

### 1. Install the host tooling

```bash
python3 -m pip install --user -r requirements-dev.txt   # mpremote + littlefs-python
brew install picotool                                    # only for dumping/flashing a board
```

On Linux, add yourself to the serial group and log out/in:

```bash
sudo usermod -aG dialout "$USER"   # or 'uucp' on Arch
```

### 2. Install the VS Code extensions

Open the repo in VS Code. It will prompt to install the workspace
recommendations from `.vscode/extensions.json`. Accept, or run:

```bash
code --install-extension paulober.pico-w-go
code --install-extension ms-python.python
```

> **On the "pico-w" in that ID:** MicroPico ships under the old Pico-W-Go
> marketplace ID. It is not Pico W specific and works fine with a plain Pico.
> This repo doesn't use its stub folder at all (see step 3).

`.vscode/extensions.json` also marks **Pymakr** as unwanted. Don't run both —
they fight over the serial port.

### 3. Install the MicroPython stubs

```bash
./tools/pico stubs        # or the "Pico: Install type stubs" task
```

Then reload the window (`Cmd/Ctrl+Shift+P` → *Developer: Reload Window*).

This installs `micropython-rp2-rpi_pico-stubs` — the **plain RP2040 Pico**
package, not the Pico W one — into `typings/`, where `pyrightconfig.json` points
`stubPath`. Without it, Pylance can't see `machine`, `utime`, `ustruct`,
`micropython` or `_thread`, and you get an error on nearly every import line.

`typings/` is gitignored (~1.3 MB of regenerable `.pyi` files), so re-run this
after a fresh clone.

> Deliberately **not** using MicroPico's *Configure project* command for this.
> It creates a machine-specific symlink (needing Developer Mode on Windows) and
> rewrites the `python.analysis.*` keys in `.vscode/settings.json`. Keeping the
> stubs local and the config in `pyrightconfig.json` means nothing can clobber
> it — and `npx pyright` reproduces exactly what the editor sees.

### 4. Confirm the toolchain

```bash
./tools/pico doctor
```

You should see `ok` for mpremote, littlefs-python, the stubs and pyrightconfig —
and, if you plan to dump or flash a board, picotool. Plug in the Pico and run
`./tools/pico devs`; it should list one port.

Confirm type checking is healthy too:

```bash
npx pyright        # or the "Pico: Type-check project" task
```

Expect **3 errors, 0 warnings** — see [Remaining diagnostics](#remaining-diagnostics).
Anything more than that (especially unresolved imports) means step 3 didn't
take.

You can also check what any board-touching command *would* do without
actually doing it:

```bash
DRY_RUN=1 ./tools/pico push template_program.py
```

---

## Thonny → VS Code cheat sheet

| Thonny | MicroPico (interactive) | Task / CLI |
| --- | --- | --- |
| Shell pane | The **Pico (W) vREPL** terminal | `Pico: REPL` · `./tools/pico repl` |
| Green **Run** button | `MicroPico > Run current file on Pico` | `Pico: Run current file (not saved to flash)` · `./tools/pico run <file>` |
| **Stop/Restart** (Ctrl-C) | `Ctrl-C` in the vREPL | `Pico: Stop running program` · `./tools/pico stop` |
| Files pane → *Upload to /* | — (no MicroPico equivalent now — see below) | `UF2: Build + flash (deploy)` · `./tools/pico deploy` |
| Upload one file | Right-click → **Upload file to Pico** | `Pico: Upload current file` · `./tools/pico push <file>` |
| Files pane → *Download to …* | Right-click a remote file → **Download** | `Pico: Download a file from board` · `./tools/pico pull <path>` |
| Browsing the Pico's files | MicroPico's remote filesystem view | `Pico: List files on board` · `./tools/pico tree` |
| **Run → Send EOF/Soft reboot** | `Ctrl-D` in the vREPL | `Pico: Soft reset board` · `./tools/pico soft-reset` |
| Unplug/replug (hard reset) | `MicroPico > Hard reset` | `Pico: Reset board` · `./tools/pico reset` |
| *Tools → Manage packages* | `MicroPico > Install package` | `mpremote mip install <pkg>` |
| Deleting everything before reflashing | — | `Pico: Wipe board filesystem` · `./tools/pico wipe` |

Run tasks with `Cmd/Ctrl+Shift+P` → **Tasks: Run Task**.
`UF2: Build + flash (deploy)` is the default build task, so `Cmd/Ctrl+Shift+B`
runs it directly.

MicroPico's own Upload/Download/right-click-a-file features still work for
poking at loose files (e.g. `program.py`, `config.json`), since those are
genuinely on the board's filesystem. They just don't do anything useful for
`src/`'s own files any more — those are frozen into the firmware, not
uploaded (see `docs/RELEASE.md`), so **MicroPico's "Upload project to Pico"
button and `syncFolder` setting should not be used for this repo's `src/`.**

### Suggested keybindings

VS Code has no workspace-scoped keybindings, so add these to your user
`keybindings.json` (`Cmd/Ctrl+Shift+P` → *Preferences: Open Keyboard Shortcuts (JSON)*)
to get Thonny's muscle memory back:

```jsonc
[
  {
    // Thonny's F5 = Run
    "key": "f5",
    "command": "workbench.action.tasks.runTask",
    "args": "Pico: Run current file (not saved to flash)",
    "when": "editorLangId == python"
  },
  {
    // Build a fresh firmware and flash it
    "key": "shift+f5",
    "command": "workbench.action.tasks.runTask",
    "args": "UF2: Build + flash (deploy)"
  },
  {
    "key": "ctrl+shift+r",
    "command": "workbench.action.tasks.runTask",
    "args": "Pico: REPL"
  }
]
```

The `args` strings must match the task labels exactly.

---

## Everyday workflows

### Editing the firmware (`src/main.py`, `roboxlib.py`, `communication.py`)

Every file in `src/` (except `lib/picozero`, which is frozen too, and
`template_program.py`, which isn't part of the firmware at all) is frozen
into the firmware — see `docs/RELEASE.md`. There's no loose-file upload
step any more: `main.py`, `roboxlib.py`, etc. only exist as compiled-in
frozen modules on a board running this project's firmware, and a same-named
loose file would be dead weight the frozen version always wins over. So the
loop is build-and-flash, not upload-and-reset:

```bash
./tools/pico deploy       # release (compile) + flash, one command
```

or the task **`UF2: Build + flash (deploy)`**. This takes a couple of
minutes (a real firmware compile), not seconds — there is no faster
loop for these files. `./tools/pico fw-doctor` checks the toolchain first if
something's wrong (needs Docker, or `arm-none-eabi-gcc`/`cmake` for
`./tools/pico deploy --local`).

`config.json`/`program.py` are the only things that stay loose on the
board's filesystem, and `deploy`/`release` never touch them (verified: the
release UF2 carries zero filesystem-region blocks). `push`/`pull` still work
for poking at those directly.

### Testing a robot program

`src/main.py` expects the user program at `/program.py` and runs it on a
`start_program` command frame. To iterate on a program without the
Bluetooth/USB command dance, run it directly:

```bash
./tools/pico run template_program.py
```

This executes the file from RAM with the real `roboxlib` imports resolved from
the frozen firmware, and streams `print()` output back to your terminal.
`Ctrl-C` stops it.

### Building a release UF2

Needs the MicroPython submodule and either Docker or a local ARM toolchain
— see `docs/RELEASE.md` for the full story (why this needs a real compile,
what's frozen, the toolchain setup). Quick version:

```bash
git submodule update --init --recursive firmware/vendor/micropython
./tools/pico fw-doctor    # checks Docker (or a local ARM toolchain)
./tools/pico release      # -> build/robox-<version>.uf2, verified filesystem-safe
```

or the tasks **`UF2: Check firmware toolchain`** then
**`UF2: Build release (the file end users update with)`**. The version comes
from `CURRENT_FIRMWARE_VERSION` in `src/main.py`. `release` compiles via
Docker by default; the **`UF2: Build release (local toolchain, no Docker)`**
task (or `./tools/pico release --local`) uses `arm-none-eabi-gcc`/`cmake` off
PATH instead.

To put the result on a board, hold BOOTSEL while plugging it in and either
drag the UF2 onto the `RPI-RP2` volume, or run `./tools/pico flash
build/robox-<version>.uf2`, or just `./tools/pico deploy` to build and flash
in one step (task **`UF2: Build + flash (deploy)`**).

#### Capturing a UF2 off a board instead

Still useful for snapshotting a board that is already configured — a dump
carries `program.py` and calibration data exactly as they are on that board.
Needs picotool.

**Tasks: Run Task → `UF2: Capture from board`**, or:

```bash
./tools/pico bootsel     # reboot into BOOTSEL, no button press needed
./tools/pico uf2         # -> build/robox-<timestamp>.uf2
```

A dump can be fed back in as the base for `tools/build_uf2.py build
--base dump.uf2` directly (not `./tools/pico release`/`deploy`, which always
compile their own firmware and never touch a littlefs image at all).

---

## Type checking

`pyrightconfig.json` at the repo root is the single source of truth for
IntelliSense and type checking — Pylance reads it and ignores
`python.analysis.*` in `.vscode/settings.json` while it exists. Reproduce
exactly what the editor sees with:

```bash
npx pyright
```

Three things it sets up that matter:

- **`stubPath: "typings"`** — MicroPython stubs for `machine`, `utime`,
  `ustruct`, `micropython`, `_thread`.
- **`extraPaths: ["src", "src/lib"]`** — everything in `src/` is uploaded to the
  Pico's *root*, so `import roboxlib` has to resolve as a top-level module on
  the host too. `src/lib` mirrors the board's `/lib`.
- **`ignore: ["src/lib"]`** — picozero is a vendored dependency. Still
  importable, but its type errors aren't reported as ours.

The `reportOptional*` family is switched off. MicroPython drivers routinely
use one method for both read and write —

```python
def _register8(self, register, value=None):
    if value is None:
        return self.i2c.readfrom_mem(self.address, register, 1)[0]   # returns int
    self.i2c.writeto_mem(self.address, register, ustruct.pack('<B', value))
    # implicit `return None` on the write path
```

so pyright types *every* read as `int | None` and flags every subsequent
`enable | _ENABLE_PON`. The firmware only ever calls the read form there. Those
are typing artifacts, not defects.

Rules that catch real mistakes — `reportAttributeAccessIssue`,
`reportUndefinedVariable`, `reportOperatorIssue`, `reportCallIssue`,
`reportArgumentType`, `reportIndexIssue` — are left **on**.

---

## Troubleshooting

**"no device found" / "could not open port"**
Only one program can hold the serial port. Close Thonny. Close the MicroPico
vREPL terminal (trash-can icon, not just hide) before running a `tools/pico`
task, and vice versa. This is the cause of roughly every problem here, which is
why `"micropico.openOnStart"` is `false` in `.vscode/settings.json`. Flip it to
`true` if you work mostly in the vREPL.

**MicroPico connects but the REPL shows nothing / garbage**
`src/main.py` runs an infinite loop at boot that reads from `sys.stdin`, so it
competes with you for the serial link. Press `Ctrl-C` once in the vREPL to raise
`KeyboardInterrupt` and get a `>>>` prompt. `./tools/pico repl` uses
`mpremote resume`, which attaches without a soft reset so you can see the loop's
output before interrupting.

**`Ctrl-C` doesn't interrupt**
`./tools/pico stop` sends a single Ctrl-C, which is enough for `main.py`'s loop
(it has no `KeyboardInterrupt` handler and never calls `micropython.kbd_intr`).
If it doesn't take, the board is probably wedged inside a `_thread` started by
`start_program` — that thread doesn't receive the interrupt. Reset instead:
`./tools/pico reset`. If even that fails, unplug and replug.

**Wrong port picked automatically**
Set `"micropico.autoConnect": false` and `"micropico.manualComDevice"` in
`.vscode/settings.json`. For the CLI, export the port:

```bash
export PICO_PORT=/dev/cu.usbmodem1101   # find it with ./tools/pico devs
```

**Pylance flags `machine`, `_thread`, `ustruct`, `utime`, `micropython`**
The stubs aren't installed. Run `./tools/pico stubs`, then reload the window.
`./tools/pico doctor` tells you whether `typings/` is populated.

**Suddenly *everything* is unresolved, including `json`, `sys` and `os`**
Something has pointed `typeshedPath`/`python.analysis.typeshedPaths` at a
directory that doesn't exist, which takes the stdlib down with it. Don't use
`typeshedPath` for MicroPython stubs — `stubPath` is the right setting, and
that's what `pyrightconfig.json` uses.

**Pylance ignores my `python.analysis.*` edits in `.vscode/settings.json`**
Working as intended: `pyrightconfig.json` exists, so it wins. Edit that file
instead. This is also why running MicroPico's *Configure project* can't break
the setup.

**`picotool save`/`reflash`/`deploy` say "no accessible RP-series devices"**
The board isn't in BOOTSEL mode. `reflash`/`deploy` already poll and wait for
it (nudging the board into BOOTSEL first if mpremote can reach it), so this
usually means neither mpremote nor picotool can see the board at all — check
`./tools/pico devs`, or unplug and replug while holding BOOTSEL by hand.

**`./tools/pico release`/`fw-build` fails with a toolchain error**
Run `./tools/pico fw-doctor` first — it checks Docker (or a local
`arm-none-eabi-gcc`/`cmake`) and whether the MicroPython submodule is
checked out. See `docs/RELEASE.md` for the known Docker/local build-cache
collision and the Clang `-Wgnu-folding-constant` issue on some machines.

**The board doesn't seem to run the code I just changed**
If it's already running a custom-compiled firmware from a previous
`release`/`deploy`, that's expected for anything in `src/` — those files
are frozen in, so nothing short of a fresh `deploy` changes what runs. There
is no loose-file upload path for this any more.

---

## What's in the repo

```
.micropico                 marker file; activates the MicroPico extension
.vscode/settings.json      MicroPico config + editor conventions
.vscode/extensions.json    recommended (and unwanted) extensions
.vscode/tasks.json         every Pico/UF2 command as a runnable task
pyrightconfig.json         IntelliSense + type-checking config (authoritative)
typings/                   MicroPython stubs (gitignored; ./tools/pico stubs)
tools/pico                 mpremote/picotool wrapper backing all the tasks
tools/build_uf2.py         host-side UF2 builder (no board needed)
tests/test_build_uf2.py    offline tests for that builder
requirements-dev.txt       host-side Python deps (mpremote, littlefs-python)
firmware/                  frozen-firmware manifest, Dockerfile, MicroPython submodule
build/                     UF2 output + cached firmware build dirs (gitignored)
```

`typings/` and `build/*` are gitignored (regenerable); everything else above is
committed, so a fresh clone needs exactly two commands:

```bash
python3 -m pip install --user -r requirements-dev.txt
./tools/pico stubs
```

`micropico.syncFolder`/`syncFileTypes`/`pyIgnore` are still set in
`.vscode/settings.json` from before `src/` was frozen into the firmware.
They're harmless (MicroPico's "Upload project to Pico" button still runs,
it just uploads files nothing imports any more) but don't do anything
useful for this repo now — use `./tools/pico deploy` instead.
