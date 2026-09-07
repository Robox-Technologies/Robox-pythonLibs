# Frozen-firmware manifest for Robox release builds.
#
# Freezes this project's own core app code (never hand-edited on a device --
# users write program.py on the companion website, uploaded over USB/BLE) so
# it always updates on reflash, and stays off the filesystem entirely.
#
# main.py needs no special handling to autorun: MicroPython's boot sequence
# looks up "main.py" in the frozen module table directly (mp_find_frozen_module,
# in pyexec_file_if_exists), before ever consulting the filesystem -- confirmed
# on real hardware (a frozen main.py runs even with an unrelated loose main.py
# still present). What *does* need explicit handling is everything main.py
# itself imports: MicroPython's default sys.path is ['', '.frozen', '/lib'],
# filesystem before frozen, so a loose roboxlib.py etc. left over from before
# this scheme existed would otherwise keep shadowing the frozen version
# forever. src/main.py fixes that itself (moves '' to the end of sys.path
# before importing anything) -- also confirmed on real hardware. See
# docs/RELEASE.md.
#
# Used via:
#   make BOARD=RPI_PICO FROZEN_MANIFEST=<repo>/firmware/manifest.py
# (see ./tools/pico fw-build). Relative paths below resolve against this
# file's own directory -- see manifestfile.py's include(), which os.chdir()s
# there before exec'ing a manifest.

# Keep the board's own defaults (asyncio, onewire, dht, neopixel, ...)
# rather than replacing them -- nothing here is meant to shrink what a stock
# RPI_PICO build already provides.
include("$(PORT_DIR)/boards/manifest.py")

freeze(
    "../src",
    (
        "main.py",
        "roboxlib.py",
        "communication.py",
        "framed.py",
        "protocol.py",
        "colors.py",
        "calibration.py",
        "matrix.py",
    ),
)

# A string `script` that names a directory inside `path` freezes it as a
# package (picozero/__init__.py, picozero/picozero.py) -- passing the two
# files as a list instead, like the freeze() above, would flatten them into
# top-level modules named "__init__" and "picozero" instead of a package.
freeze("../src/lib", "picozero")
