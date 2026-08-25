#!/usr/bin/env python3
"""Build a Robox release UF2 on the host, with no Pico attached.

`picotool save` needs a board in BOOTSEL mode because it reads the artifact back
off real flash. Nothing in that dump has to come from hardware, though: it is a
stock MicroPython build plus a littlefs image holding the files from src/. This
script assembles those two directly, so a release can be built (and diffed, and
built in CI) without a board.

Flash map of the 2 MB Pico this firmware targets:

    0x10000000  +--------------------------------+
                | MicroPython firmware           |  stock RPI_PICO UF2
    0x100a0000  +--------------------------------+
                | littlefs filesystem, 1408 KiB  |  built here from src/
    0x10200000  +--------------------------------+

The split is MicroPython's, not ours: the rp2 port puts the filesystem in the
top MICROPY_HW_FLASH_STORAGE_BYTES of flash (1408 KiB on RPI_PICO), so the base
is flash_size - storage_size. Rather than trust that constant, the firmware is
asked where its filesystem lives -- MicroPython records the window as an
"embedded drive" in its binary-info block, which picotool reads out of a plain
file. The numbers above are only the fallback for when picotool is not
installed; --fs-base/--fs-size override either, and `tools/pico fs-layout`
prints what a connected board actually reports.

Subcommands:

    build_uf2.py build [-o out.uf2] [--base firmware.uf2] [--src src]
    build_uf2.py fetch [--version 1.24.1]
    build_uf2.py inspect <file.uf2>

Run via `tools/pico build`, `tools/pico firmware`, `tools/pico inspect`.
"""

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import urllib.request

# --- UF2 container ---------------------------------------------------------
# https://github.com/microsoft/uf2 -- 512-byte blocks, 256 payload bytes each
# for the RP2040 (the bootrom writes flash in 256-byte pages).
UF2_MAGIC_START0 = 0x0A324655  # "UF2\n"
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
UF2_FLAG_FAMILY_ID = 0x00002000
UF2_PAYLOAD = 256
UF2_BLOCK = 512

RP2040_FAMILY_ID = 0xE48BFF56

# --- board flash map ------------------------------------------------------
XIP_BASE = 0x10000000
FLASH_BYTES = 2 * 1024 * 1024  # RPI_PICO
FS_BYTES = 1408 * 1024  # MICROPY_HW_FLASH_STORAGE_BYTES for RPI_PICO
FS_BASE = FLASH_BYTES - FS_BYTES  # 0xa0000

# --- littlefs geometry ----------------------------------------------------
# These must match what MicroPython's rp2 port uses, or the board will refuse to
# mount the image: block size is the flash sector size exposed by rp2.Flash(),
# and read/prog sizes are os.VfsLfs2()'s defaults.
LFS_BLOCK_SIZE = 4096
LFS_READ_SIZE = 32
LFS_PROG_SIZE = 256
# Write the oldest on-disk revision (2.0). littlefs is backwards compatible but
# not forwards: a 2.1 image (what a current littlefs writes by default) will not
# mount on firmware built against littlefs < 2.5.
LFS_DISK_VERSION = 0x00020000

# --- base firmware --------------------------------------------------------
# Pinned so a build is reproducible and so nobody silently ships a different
# MicroPython than the one the firmware was tested against. Bump deliberately.
MICROPYTHON_UF2 = "RPI_PICO-20241129-v1.24.1.uf2"
MICROPYTHON_URL = "https://micropython.org/resources/firmware/" + MICROPYTHON_UF2
MICROPYTHON_INDEX = "https://micropython.org/download/RPI_PICO/"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRMWARE_CACHE = os.path.join(REPO_ROOT, "build", "firmware")


def die(msg):
    print("error %s" % msg, file=sys.stderr)
    raise SystemExit(1)


def info(msg):
    print("==> %s" % msg)


# ---------------------------------------------------------------------------
# UF2 read/write
# ---------------------------------------------------------------------------


def parse_uf2(path):
    """Return ([(address, payload_bytes), ...], family_id) in file order."""
    with open(path, "rb") as fh:
        data = fh.read()
    if not data or len(data) % UF2_BLOCK:
        die("%s is not a UF2 (size %d is not a multiple of 512)" % (path, len(data)))

    blocks = []
    families = set()
    for offset in range(0, len(data), UF2_BLOCK):
        block = data[offset : offset + UF2_BLOCK]
        m0, m1, flags, addr, size, _no, _total, family = struct.unpack("<8I", block[:32])
        (end,) = struct.unpack("<I", block[508:512])
        if m0 != UF2_MAGIC_START0 or m1 != UF2_MAGIC_START1 or end != UF2_MAGIC_END:
            die("%s: bad UF2 magic in block at offset %d" % (path, offset))
        if flags & UF2_FLAG_FAMILY_ID:
            families.add(family)
        if size > UF2_PAYLOAD:
            die("%s: payload of %d bytes exceeds the RP2040's 256" % (path, size))
        blocks.append((addr, block[32 : 32 + size]))
    if len(families) > 1:
        die("%s mixes family ids (%s)"
            % (path, ", ".join("0x%08x" % f for f in sorted(families))))
    # Carry the base's family through instead of assuming RP2040, so a UF2 for
    # another chip in the family (Pico 2) still comes out flashable.
    return blocks, (families.pop() if families else RP2040_FAMILY_ID)


def uf2_bytes(blocks, family=RP2040_FAMILY_ID):
    """Serialise [(address, payload), ...] into a UF2 image."""
    total = len(blocks)
    out = bytearray()
    for index, (addr, payload) in enumerate(blocks):
        if len(payload) > UF2_PAYLOAD:
            die("internal: payload of %d bytes" % len(payload))
        out += struct.pack(
            "<8I",
            UF2_MAGIC_START0,
            UF2_MAGIC_START1,
            UF2_FLAG_FAMILY_ID,
            addr,
            len(payload),
            index,
            total,
            family,
        )
        out += payload.ljust(476, b"\x00")
        out += struct.pack("<I", UF2_MAGIC_END)
    return bytes(out)


def split_into_blocks(base_addr, data, skip_erased=False):
    """Chop a flash image into UF2-sized payloads starting at base_addr."""
    blocks = []
    for offset in range(0, len(data), UF2_PAYLOAD):
        payload = data[offset : offset + UF2_PAYLOAD]
        # An all-0xff payload is indistinguishable from erased flash, so in
        # sparse mode we let the board keep whatever is already there.
        if skip_erased and payload.count(0xFF) == len(payload):
            continue
        blocks.append((base_addr + offset, payload))
    return blocks


# ---------------------------------------------------------------------------
# littlefs image
# ---------------------------------------------------------------------------


def import_littlefs():
    try:
        from littlefs import LittleFS, UserContext
    except ImportError:
        die(
            "littlefs-python not installed. Install it with:\n"
            "        python3 -m pip install --user -r requirements-dev.txt"
        )
    return LittleFS, UserContext


def build_littlefs(files, fs_bytes):
    """Format a littlefs image of fs_bytes and write `files` (path -> bytes)."""
    LittleFS, UserContext = import_littlefs()

    if fs_bytes % LFS_BLOCK_SIZE:
        die("filesystem size %d is not a multiple of the %d-byte block"
            % (fs_bytes, LFS_BLOCK_SIZE))

    # Erased flash reads as 0xff, so start from that: the blocks littlefs never
    # touches then match a freshly erased board byte for byte.
    image = bytearray(b"\xff" * fs_bytes)
    fs = LittleFS(
        context=UserContext(buffer=image),
        block_size=LFS_BLOCK_SIZE,
        block_count=fs_bytes // LFS_BLOCK_SIZE,
        read_size=LFS_READ_SIZE,
        prog_size=LFS_PROG_SIZE,
        disk_version=LFS_DISK_VERSION,
        mount=False,
    )
    fs.format()
    fs.mount()
    # Sorted so the image is byte-for-byte reproducible: littlefs allocates
    # blocks in write order, and a filesystem walk is not ordered.
    for path in sorted(files):
        parent = os.path.dirname(path)
        if parent:
            fs.makedirs(parent, exist_ok=True)
        with fs.open(path, "wb") as fh:
            fh.write(files[path])
    used = fs.used_block_count * LFS_BLOCK_SIZE
    fs.unmount()
    return bytes(image), used


def read_littlefs(image):
    """Mount an existing image and return {path: size}. Raises on a bad image."""
    LittleFS, UserContext = import_littlefs()
    fs = LittleFS(
        context=UserContext(buffer=bytearray(image)),
        block_size=LFS_BLOCK_SIZE,
        block_count=len(image) // LFS_BLOCK_SIZE,
        read_size=LFS_READ_SIZE,
        prog_size=LFS_PROG_SIZE,
        mount=False,
    )
    fs.mount()
    found = {}
    for root, _dirs, names in fs.walk("/"):
        for name in names:
            path = (root.rstrip("/") + "/" + name).lstrip("/")
            found[path] = fs.stat("/" + path).size
    fs.unmount()
    return found


# ---------------------------------------------------------------------------
# src/ collection
# ---------------------------------------------------------------------------


def is_excluded(rel_path, patterns):
    """Match a pattern against the whole path or any single component.

    Same rule as tools/pico's is_excluded, which passes its list in with
    --exclude so the two stay in step.
    """
    parts = rel_path.split("/")
    for pattern in patterns:
        pattern = pattern.strip("/")
        if not pattern:
            continue
        if rel_path == pattern or rel_path.startswith(pattern + "/"):
            return True
        wanted = pattern.split("/")
        for start in range(len(parts)):
            if parts[start : start + len(wanted)] == wanted:
                return True
    return False


def collect_files(src_dir, excludes):
    files = {}
    skipped = []
    for dirpath, dirnames, filenames in os.walk(src_dir):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, src_dir).replace(os.sep, "/")
            if is_excluded(rel, excludes):
                skipped.append(rel)
                continue
            with open(full, "rb") as fh:
                files[rel] = fh.read()
    return files, skipped


def firmware_version(src_dir):
    """CURRENT_FIRMWARE_VERSION out of main.py, for naming the artifact."""
    try:
        with open(os.path.join(src_dir, "main.py")) as fh:
            match = re.search(
                r'^CURRENT_FIRMWARE_VERSION\s*=\s*["\']([^"\']+)', fh.read(), re.M
            )
    except OSError:
        return None
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# base firmware
# ---------------------------------------------------------------------------


def http_get(url):
    """Fetch a URL, preferring curl.

    A python.org interpreter on macOS ships without a usable CA store until
    "Install Certificates.command" is run, so urllib fails on TLS where curl --
    which uses the system trust store -- is fine. Try curl first and keep urllib
    as the fallback for machines without it.
    """
    if shutil.which("curl"):
        result = subprocess.run(
            ["curl", "--fail", "--silent", "--show-error", "--location",
             "--max-time", "120", url],
            capture_output=True,
        )
        if result.returncode == 0:
            return result.stdout
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError("curl: %s" % (detail or "exit %d" % result.returncode))
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def fetch_firmware(version=None, dest_dir=FIRMWARE_CACHE, force=False):
    """Download a stock MicroPython UF2 into the cache and return its path."""
    if version:
        name, url = resolve_version(version)
    else:
        name, url = MICROPYTHON_UF2, MICROPYTHON_URL

    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, name)
    if os.path.exists(path) and not force:
        info("cached %s" % rel_to_repo(path))
        return path

    info("downloading %s" % url)
    try:
        data = http_get(url)
    except Exception as exc:  # network, DNS, 404, proxy, TLS trust store...
        die("could not download %s (%s)\n"
            "        Download it by hand and pass it with --base, or point\n"
            "        ROBOX_BASE_UF2 at a UF2 you already have." % (url, exc))

    if data[:4] != struct.pack("<I", UF2_MAGIC_START0):
        die("%s did not return a UF2" % url)
    # Write via a temporary name so an interrupted download cannot leave a
    # truncated file in the cache that later builds would happily use.
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    info("saved %s (%.1f MiB)" % (rel_to_repo(path), len(data) / (1 << 20)))
    return path


def resolve_version(version):
    """Map '1.24.1' to the dated filename micropython.org publishes."""
    version = version.lstrip("v")
    info("looking up MicroPython v%s" % version)
    try:
        page = http_get(MICROPYTHON_INDEX).decode("utf-8", "replace")
    except Exception as exc:
        die("could not reach %s (%s)" % (MICROPYTHON_INDEX, exc))
    names = sorted(set(re.findall(
        r"RPI_PICO-\d{8}-v%s\.uf2" % re.escape(version), page)))
    if not names:
        die("no RPI_PICO build for v%s at %s" % (version, MICROPYTHON_INDEX))
    name = names[-1]
    return name, "https://micropython.org/resources/firmware/" + name


def detect_layout(uf2_path):
    """Read the filesystem window out of the firmware's own binary info.

    MicroPython records its filesystem as an "embedded drive" in the binary-info
    block, and picotool prints it for a UF2 file without needing a board. That
    beats hardcoding a per-board constant: a Pico W or Pico 2 base firmware,
    whose filesystem starts somewhere else entirely, comes out right by itself.
    Returns (fs_base, fs_bytes), or None when picotool is missing or silent.
    """
    if not shutil.which("picotool"):
        return None
    try:
        out = subprocess.run(
            ["picotool", "info", "-a", uf2_path],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"embedded drive:\s*0x([0-9a-fA-F]+)-0x([0-9a-fA-F]+)", out)
    if not match:
        return None
    start, end = int(match.group(1), 16), int(match.group(2), 16)
    if not XIP_BASE <= start < end:
        return None
    return start - XIP_BASE, end - start


def resolve_base(explicit, offline):
    """--base, else $ROBOX_BASE_UF2, else the pinned build from the cache."""
    if explicit:
        if not os.path.isfile(explicit):
            die("base UF2 not found: %s" % explicit)
        return explicit
    from_env = os.environ.get("ROBOX_BASE_UF2")
    if from_env:
        if not os.path.isfile(from_env):
            die("ROBOX_BASE_UF2 points at a missing file: %s" % from_env)
        return from_env
    cached = os.path.join(FIRMWARE_CACHE, MICROPYTHON_UF2)
    if os.path.isfile(cached):
        return cached
    if offline:
        die("no base firmware in %s and --offline was given.\n"
            "        Run:  ./tools/pico firmware" % rel_to_repo(FIRMWARE_CACHE))
    return fetch_firmware()


def rel_to_repo(path):
    try:
        rel = os.path.relpath(path, REPO_ROOT)
    except ValueError:
        return path
    return path if rel.startswith("..") else rel


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------


def cmd_build(args):
    src_dir = os.path.abspath(args.src)
    if not os.path.isdir(src_dir):
        die("source directory not found: %s" % src_dir)

    base_path = None if args.no_base else resolve_base(args.base, args.offline)
    family = RP2040_FAMILY_ID
    base_blocks = []
    if base_path:
        info("Base firmware %s" % rel_to_repo(base_path))
        base_blocks, family = parse_uf2(base_path)

    fs_base, fs_bytes = resolve_layout(args, base_path)
    fs_end = fs_base + fs_bytes
    if fs_end > args.flash_size:
        die("filesystem (0x%x..0x%x) runs past the end of a %d-byte flash"
            % (fs_base, fs_end, args.flash_size))

    files, skipped = collect_files(src_dir, args.exclude)
    if not files:
        die("nothing to package from %s (everything excluded?)" % rel_to_repo(src_dir))

    info("Packaging %s/ -> littlefs" % rel_to_repo(src_dir))
    for rel in skipped:
        print("   skip  %s" % rel)
    for rel in sorted(files):
        print("   add   %-40s %6d B" % (rel, len(files[rel])))

    image, used = build_littlefs(files, fs_bytes)
    print("   fs    %d KiB image, %d KiB used by %d file(s)"
          % (fs_bytes // 1024, used // 1024, len(files)))

    blocks = []
    if base_blocks:
        # Dropping the base's own filesystem region is what lets a full-flash
        # `picotool save` dump be reused as the base: the firmware half is kept,
        # the stale filesystem half is replaced by the one just built.
        kept = [b for b in base_blocks if b[0] < XIP_BASE + fs_base]
        dropped = len(base_blocks) - len(kept)
        if dropped:
            print("   note  dropped %d block(s) at or above 0x%08x (the base's own"
                  " filesystem)" % (dropped, XIP_BASE + fs_base))
        if not kept:
            die("%s has no blocks below the filesystem base -- wrong --fs-base?"
                % rel_to_repo(base_path))
        top = max(addr + len(payload) for addr, payload in kept)
        print("   fw    0x%08x..0x%08x  (%d KiB, %d block(s))"
              % (kept[0][0], top, (top - kept[0][0]) // 1024, len(kept)))
        blocks += kept
    else:
        info("Filesystem only -- no MicroPython firmware in this UF2")

    fs_blocks = split_into_blocks(XIP_BASE + fs_base, image, skip_erased=args.sparse)
    print("   fs    0x%08x..0x%08x  (%d block(s)%s)"
          % (XIP_BASE + fs_base, XIP_BASE + fs_end, len(fs_blocks),
             ", sparse" if args.sparse else ""))
    blocks += fs_blocks

    out = args.out or default_out_path(src_dir)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "wb") as fh:
        fh.write(uf2_bytes(blocks, family))
    size = os.path.getsize(out)
    print("  ok wrote %s (%.1f MiB, %d blocks)"
          % (rel_to_repo(out), size / (1 << 20), len(blocks)))

    # Read the artifact back rather than trusting the writer: parse the UF2,
    # mount the filesystem it contains, and compare against what went in.
    verify_uf2(out, expected=files, fs_base=fs_base, fs_bytes=fs_bytes)
    return 0


def resolve_layout(args, base_path):
    """Where the filesystem goes: explicit flags win, then the firmware, then
    the RPI_PICO defaults."""
    if args.fs_base is not None and args.fs_size is not None:
        return args.fs_base, args.fs_size

    detected = detect_layout(base_path) if base_path else None
    fs_base, fs_bytes = detected if detected else (FS_BASE, FS_BYTES)
    if detected:
        print("   map   filesystem 0x%08x..0x%08x (%d KiB), read from the firmware"
              % (XIP_BASE + fs_base, XIP_BASE + fs_base + fs_bytes, fs_bytes // 1024))
    else:
        print("   map   filesystem 0x%08x..0x%08x (%d KiB), RPI_PICO default"
              % (XIP_BASE + FS_BASE, XIP_BASE + FS_BASE + FS_BYTES, FS_BYTES // 1024))
    # A single flag still overrides the corresponding half.
    if args.fs_base is not None:
        fs_base = args.fs_base
    if args.fs_size is not None:
        fs_bytes = args.fs_size
    return fs_base, fs_bytes


def default_out_path(src_dir):
    version = firmware_version(src_dir) or "dev"
    return os.path.join(REPO_ROOT, "build", "robox-%s.uf2" % version)


def flash_image_from_uf2(path):
    """Rebuild a sparse flash image: {offset: byte} collapsed into a bytearray."""
    blocks, _family = parse_uf2(path)
    lowest = min(addr for addr, _ in blocks)
    highest = max(addr + len(payload) for addr, payload in blocks)
    image = bytearray(b"\xff" * (highest - lowest))
    for addr, payload in blocks:
        image[addr - lowest : addr - lowest + len(payload)] = payload
    return lowest, image, blocks


def verify_uf2(path, expected, fs_base, fs_bytes):
    lowest, image, _blocks = flash_image_from_uf2(path)
    start = XIP_BASE + fs_base - lowest
    # Pad rather than fail: a --sparse build deliberately omits erased blocks,
    # and erased flash is exactly the 0xff this fills in.
    region = bytes(image[start : start + fs_bytes]).ljust(fs_bytes, b"\xff")
    if start < 0 or start >= len(image):
        die("verify: the UF2 has no blocks in the filesystem region")
    try:
        found = read_littlefs(region)
    except Exception as exc:
        die("verify: could not mount the filesystem in %s (%s)"
            % (rel_to_repo(path), exc))

    problems = []
    for rel, content in expected.items():
        if rel not in found:
            problems.append("missing %s" % rel)
        elif found[rel] != len(content):
            problems.append("%s is %d bytes, expected %d"
                            % (rel, found[rel], len(content)))
    for rel in found:
        if rel not in expected:
            problems.append("unexpected %s" % rel)
    if problems:
        die("verify failed: " + "; ".join(problems))
    print("  ok verified: %d file(s) mount cleanly from the UF2" % len(found))


def cmd_inspect(args):
    lowest, image, blocks = flash_image_from_uf2(args.file)
    highest = lowest + len(image)
    print("%s" % rel_to_repo(args.file))
    print("  %d blocks, 0x%08x..0x%08x (%d KiB of flash)"
          % (len(blocks), lowest, highest, (highest - lowest) // 1024))

    # The file itself usually says where its filesystem lives, exactly as a
    # board dump would; the flags are for images picotool cannot read.
    fs_base, fs_size = resolve_layout(args, args.file)
    fs_start = XIP_BASE + fs_base

    fw_top = max((addr + len(p) for addr, p in blocks if addr < fs_start), default=None)
    if fw_top is not None:
        print("  firmware    0x%08x..0x%08x (%d KiB)"
              % (lowest, fw_top, (fw_top - lowest) // 1024))
        if fw_top > fs_start:
            print("  warn        firmware overlaps the filesystem base 0x%08x"
                  % fs_start)
    else:
        print("  firmware    none below 0x%08x" % fs_start)

    start = fs_start - lowest
    region = bytes(image[start : start + fs_size]) if start >= 0 else b""
    if not region:
        print("  filesystem  no blocks at 0x%08x" % fs_start)
        return 1
    if len(region) < fs_size:
        print("  filesystem  %d of %d KiB present (a --sparse build omits erased "
              "blocks)" % (len(region) // 1024, fs_size // 1024))
        region = region.ljust(fs_size, b"\xff")
    try:
        found = read_littlefs(region)
    except Exception as exc:
        print("  filesystem  could not mount at 0x%08x (%s)" % (fs_start, exc))
        print("              Try --fs-base/--fs-size if this is not a 2 MB Pico.")
        return 1
    print("  filesystem  0x%08x, %d KiB, %d file(s)"
          % (fs_start, fs_size // 1024, len(found)))
    for rel in sorted(found):
        print("    %-40s %6d B" % (rel, found[rel]))
    return 0


def cmd_fetch(args):
    fetch_firmware(version=args.version, force=args.force)
    return 0


# ---------------------------------------------------------------------------


def main(argv):
    parser = argparse.ArgumentParser(
        prog="build_uf2.py", description=__doc__.splitlines()[0]
    )
    subs = parser.add_subparsers(dest="cmd", required=True)

    def add_layout_flags(sub):
        sub.add_argument("--fs-base", type=lambda v: int(v, 0),
                         help="filesystem offset in flash (default: read from the "
                              "firmware, else 0x%x)" % FS_BASE)
        sub.add_argument("--fs-size", type=lambda v: int(v, 0),
                         help="filesystem size in bytes (default: read from the "
                              "firmware, else %d)" % FS_BYTES)

    build = subs.add_parser("build", help="assemble a UF2 from src/ and a base firmware")
    build.add_argument("-o", "--out", help="output path (default build/robox-<version>.uf2)")
    build.add_argument("--src", default=os.path.join(REPO_ROOT, "src"),
                       help="directory packaged as the board's filesystem")
    build.add_argument("--exclude", action="append", default=[],
                       help="path or path component to leave out (repeatable)")
    build.add_argument("--base", help="base MicroPython UF2 (or a full-flash dump)")
    build.add_argument("--no-base", action="store_true",
                       help="emit only the filesystem, keeping the board's firmware")
    build.add_argument("--sparse", action="store_true",
                       help="omit erased blocks; smaller UF2, leaves old files in "
                            "untouched sectors")
    build.add_argument("--offline", action="store_true",
                       help="never download; fail if the base firmware is not cached")
    build.add_argument("--flash-size", type=lambda v: int(v, 0), default=FLASH_BYTES,
                       help="total flash in bytes (default %d)" % FLASH_BYTES)
    add_layout_flags(build)
    build.set_defaults(func=cmd_build)

    fetch = subs.add_parser("fetch", help="download the base MicroPython UF2")
    fetch.add_argument("--version", help="MicroPython version, e.g. 1.24.1 "
                                        "(default: the pinned %s)" % MICROPYTHON_UF2)
    fetch.add_argument("--force", action="store_true", help="re-download if cached")
    fetch.set_defaults(func=cmd_fetch)

    inspect = subs.add_parser("inspect", help="list the files inside a UF2")
    inspect.add_argument("file")
    add_layout_flags(inspect)
    inspect.set_defaults(func=cmd_inspect)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
