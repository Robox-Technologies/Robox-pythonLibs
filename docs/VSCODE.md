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

> **On the "pico-w" in that ID:** the extension used to be called Pico-W-Go and
> still ships under that marketplace ID, and still names its stub folder
> `Pico-W-Stub`. It is not Pico W specific and works fine with a plain Pico.
> This repo doesn't use its stub folder at all (see step 3), so the misleading
> name doesn't appear anywhere in the config.

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

You should see `ok` for mpremote, the stubs and pyrightconfig — and, if you plan
to build UF2s, picotool. Plug in the Pico and run `./tools/pico devs`; it should
list one port.

Confirm type checking is healthy too:

```bash
npx pyright        # or the "Pico: Type-check project" task
```

Expect **3 errors, 0 warnings** — see [Remaining diagnostics](#remaining-diagnostics).
Anything more than that (especially unresolved imports) means step 3 didn't
take.

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
  importable, but its type errors aren't reported as ours. That alone accounted
  for about 60 of the errors.

The `reportOptional*` family is switched off, which needs justifying: MicroPython
drivers routinely use one method for both read and write —

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
.vscode/settings.json      MicroPico config + editor conventions
.vscode/extensions.json    recommended (and unwanted) extensions
.vscode/tasks.json         every Pico/UF2 command as a runnable task
pyrightconfig.json         IntelliSense + type-checking config (authoritative)
typings/                   MicroPython stubs (gitignored; ./tools/pico stubs)
tools/pico                 mpremote/picotool wrapper backing all the tasks
requirements-dev.txt        host-side Python deps (mpremote)
build/                     UF2 output (gitignored)
```

Nothing in `src/` was changed — the firmware is untouched.

`typings/` and `build/*` are gitignored (regenerable); everything else above is
committed, so a fresh clone needs exactly two commands:

```bash
python3 -m pip install --user -r requirements-dev.txt
./tools/pico stubs
```

Note the two files whose upload rules are defined in *two* places, and keep them
in step if you change one:

| | MicroPico | `tools/pico` |
| --- | --- | --- |
| what gets uploaded | `micropico.syncFolder` + `syncFileTypes` | `SYNC_DIR` |
| what gets skipped | `micropico.pyIgnore` (paths relative to `src/`) | `EXCLUDES` in `tools/pico` |

(Type checking has no such split — `pyrightconfig.json` is the only place.)

Both are currently set to upload all of `src/` except `.DS_Store`,
`__pycache__`, and `lib/picozero-0.4.2.dist-info/`.
