# VS Code as a Thonny replacement

Everything Thonny does for this repo — connect over USB serial, browse and edit
the Pico's filesystem, upload files, open a REPL, and dump the flash to a UF2 —
now works from VS Code.

Two layers are set up, deliberately overlapping:

| Layer | What it gives you | When you want it |
| --- | --- | --- |
| **MicroPico extension** | Status-bar buttons, a live vREPL terminal, right-click Upload/Download on files, remote filesystem browsing | Interactive work. This is the direct Thonny analogue. |
| **`tools/pico` + VS Code tasks** | Scriptable `mpremote`/`picotool` commands, identical from the terminal or CI | Repeatable steps, the UF2 release build, anything you want in version control |

---

## One-time setup

### 1. Install the host tooling

```bash
python3 -m pip install --user -r requirements-dev.txt   # mpremote (+ optional stubs)
brew install picotool                                    # only needed for UF2 builds
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

`.vscode/extensions.json` also marks **Pymakr** as unwanted. Don't run both —
they fight over the serial port.

### 3. Generate the MicroPython stubs

Command Palette (`Cmd/Ctrl+Shift+P`) → **MicroPico > Configure project**.

This creates `.vscode/Pico-W-Stub` — a link to the RP2040 stubs package the
extension installs — which `.vscode/settings.json` already points Pylance at.
Without it, `from machine import Pin` shows as an unresolved import (harmless,
but noisy). The link is gitignored since it's machine-specific.

Two caveats:

- The command **rewrites the `python.analysis.*` keys** in
  `.vscode/settings.json` and will strip the comments there. It writes the same
  values that are already committed, so this is cosmetic — but don't be
  surprised by the diff.
- On Windows it creates a symlink, which needs Developer Mode enabled.

If you'd rather not use the extension for stubs, install them from PyPI instead
and point `python.analysis.extraPaths` at the package:

```bash
python3 -m pip install --user micropython-rp2-rpi_pico-stubs
```

### 4. Confirm the toolchain

```bash
./tools/pico doctor
```

You should see `ok` for mpremote and, if you plan to build UF2s, picotool.
Plug in the Pico and run `./tools/pico devs` — it should list one port.

You can also check what an upload *would* do without any board attached:

```bash
DRY_RUN=1 ./tools/pico sync
```

---

## Thonny → VS Code cheat sheet

| Thonny | MicroPico (interactive) | Task / CLI |
| --- | --- | --- |
| Shell pane | The **Pico (W) vREPL** terminal | `Pico: REPL` · `./tools/pico repl` |
| Green **Run** button | `MicroPico > Run current file on Pico` | `Pico: Run current file (not saved to flash)` · `./tools/pico run <file>` |
| **Stop/Restart** (Ctrl-C) | `Ctrl-C` in the vREPL | `Pico: Stop running program` · `./tools/pico stop` |
| Files pane → *Upload to /* | Right-click a file → **Upload project to Pico** | `Pico: Upload src/ to board` · `./tools/pico sync` |
| Upload one file | Right-click → **Upload file to Pico** | `Pico: Upload current file` · `./tools/pico push <file>` |
| Files pane → *Download to …* | Right-click a remote file → **Download** | `Pico: Download a file from board` · `./tools/pico pull <path>` |
| Browsing the Pico's files | MicroPico's remote filesystem view | `Pico: List files on board` · `./tools/pico tree` |
| **Run → Send EOF/Soft reboot** | `Ctrl-D` in the vREPL | `Pico: Soft reset board` · `./tools/pico soft-reset` |
| Unplug/replug (hard reset) | `MicroPico > Hard reset` | `Pico: Reset board` · `./tools/pico reset` |
| *Tools → Manage packages* | `MicroPico > Install package` | `mpremote mip install <pkg>` |
| Deleting everything before reflashing | — | `Pico: Wipe board filesystem` · `./tools/pico wipe` |
| — (no Thonny equivalent) | — | `Pico: Mount src/ as board filesystem` · `./tools/pico mount` |

Run tasks with `Cmd/Ctrl+Shift+P` → **Tasks: Run Task**.
`Pico: Upload src/ to board` is the default build task, so `Cmd/Ctrl+Shift+B`
runs it directly.

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
    // Upload the whole project
    "key": "shift+f5",
    "command": "workbench.action.tasks.runTask",
    "args": "Pico: Upload src/ to board"
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

The straightforward loop is upload-and-reset:

```bash
./tools/pico sync         # copy src/ -> Pico flash (one connection, main.py last)
./tools/pico reset        # main.py reruns
```

Or run both plus a REPL in one step with the task
**`Pico: Upload src/ then open REPL`** — the closest thing to Thonny's green Run
button.

There's also a no-flash-writes loop for rapid iteration:

```bash
./tools/pico mount        # serves src/ from your machine over the serial link
```

Understand what this actually does before relying on it:

- Your local `src/` is mounted at **`/remote`** on the board, and the working
  directory is changed to it. The board's own flash `/` and `/lib` are still
  there and still on `sys.path`.
- It does **not** replace `main.py`. Nothing of yours runs automatically. At the
  prompt, `import main` runs `/remote/main.py` (the cwd is on `sys.path`).
- `Ctrl-D` soft-reboots and mpremote re-establishes the mount afterwards — but
  the board's *flash* `main.py` has already run by then. So `Ctrl-D` is not a
  "rerun my edited main.py" button; `import main` is.
- `/remote/lib` is **not** on `sys.path`, so `from picozero import ...` still
  resolves from the board's flash `/lib`, not from your `src/lib`.
- A hard reset or unplug drops the mount.

So: `mount` is good for poking at `roboxlib` interactively against live
hardware. Use `sync` for anything you want to persist or test end-to-end.

### Testing a robot program

`src/main.py` expects the user program at `/program.py` and runs it when it
receives the `x04STARTPROG` command. To iterate on a program without the
Bluetooth/USB command dance, run it directly:

```bash
./tools/pico run template_program.py
```

This executes the file from RAM with the real `roboxlib` imports resolved from
the Pico's flash, and streams `print()` output back to your terminal. `Ctrl-C`
stops it.

### Building a release UF2

The README workflow (`picotool save -a … -t uf2`) is wired up as a single task:

**Tasks: Run Task → `UF2: Build release (sync -> BOOTSEL -> save)`**

It runs, in order:

1. `Pico: Upload src/ to board` — put the current source on flash
2. `UF2: Reboot board into BOOTSEL` — `mpremote bootloader`, no BOOTSEL button needed
3. a short wait for the `RPI-RP2` volume to mount
4. `UF2: Save board flash to build/` — `picotool save -a build/robox-<timestamp>.uf2 -t uf2`

The result lands in `build/`, which is gitignored. To put that image on another
board, hold BOOTSEL while plugging it in, then:

```bash
./tools/pico flash build/robox-20260825-101500.uf2
```

---

## Troubleshooting

**"no device found" / "could not open port"**
Only one program can hold the serial port. Close Thonny. Close the MicroPico
vREPL terminal (trash-can icon, not just hide) before running a `tools/pico`
task, and vice versa. This is the cause of roughly every problem here — which
is why `"micropico.openOnStart"` is set to `false` in
`.vscode/settings.json`, so the extension doesn't silently claim the port every
time you open the window. Flip it to `true` if you work mostly in the vREPL.

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

**Pylance flags `machine`, `_thread`, `ustruct`, `utime`**
`.vscode/Pico-W-Stub` is missing — run **MicroPico > Configure project** (setup
step 3). `./tools/pico doctor` reports whether it's there.

**`picotool save` says "no accessible RP2040 devices"**
The board isn't in BOOTSEL mode. Run `UF2: Reboot board into BOOTSEL`, or unplug
and replug while holding the BOOTSEL button. On macOS you may need `sudo` for
`picotool` depending on how it was installed.

**Uploads feel slow**
Most of the cost is per-connection overhead (opening the port, interrupting the
running program, entering the raw REPL), not link speed — the Pico's USB CDC
ignores the baud rate entirely. `./tools/pico sync` therefore chains every file
copy into a single `mpremote` invocation so that cost is paid once. MicroPico's
"Upload project" button spawns its own connection and is a little slower.

---

## What's in the repo

```
.micropico                 marker file; activates the MicroPico extension
.vscode/settings.json      MicroPico config + Pylance MicroPython stub paths
.vscode/extensions.json    recommended (and unwanted) extensions
.vscode/tasks.json         every Pico/UF2 command as a runnable task
tools/pico                 mpremote/picotool wrapper backing all the tasks
requirements-dev.txt       host-side Python deps (mpremote, stubs)
build/                     UF2 output (gitignored)
```

Nothing in `src/` was changed — the firmware is untouched.

`.vscode/Pico-W-Stub` and `build/*` are gitignored (machine-specific and
regenerable); everything else above is committed, so a fresh clone is one
`./tools/pico doctor` away from working.

Note the two files whose upload rules are defined in *two* places, and keep them
in step if you change one:

| | MicroPico | `tools/pico` |
| --- | --- | --- |
| what gets uploaded | `micropico.syncFolder` + `syncFileTypes` | `SYNC_DIR` |
| what gets skipped | `micropico.pyIgnore` (paths relative to `src/`) | `EXCLUDES` in `tools/pico` |

Both are currently set to upload all of `src/` except `.DS_Store`,
`__pycache__`, and `lib/picozero-0.4.2.dist-info/`.
