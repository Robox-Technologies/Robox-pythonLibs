"""Offline tests for tools/build_uf2.py -- the host-side UF2 builder.

These are the checks that used to need a Pico: that the artifact really contains
src/, that its layout matches MicroPython's, and that two builds of the same
tree are identical.

Run with: ./tools/run-tests
"""

import contextlib
import io
import os
import struct
import sys
import unittest

TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")
sys.path.insert(0, TOOLS)

import build_uf2 as b  # noqa: E402

try:
    import littlefs  # noqa: F401

    HAVE_LITTLEFS = True
except ImportError:
    HAVE_LITTLEFS = False

needs_littlefs = unittest.skipUnless(
    HAVE_LITTLEFS, "littlefs-python not installed (pip install -r requirements-dev.txt)"
)


def fake_base_uf2(path, size=4096):
    """A minimal stand-in for a stock MicroPython UF2."""
    payload = bytes(range(256))
    blocks = b.split_into_blocks(b.XIP_BASE, payload * (size // 256))
    with open(path, "wb") as fh:
        fh.write(b.uf2_bytes(blocks))
    return path


class ExcludeTest(unittest.TestCase):
    """The builder has to skip exactly what `pico sync` skips."""

    PATTERNS = ["__pycache__", ".DS_Store", "lib/picozero-0.4.2.dist-info"]

    def test_matches_a_whole_path(self):
        self.assertTrue(b.is_excluded("lib/picozero-0.4.2.dist-info", self.PATTERNS))
        self.assertTrue(
            b.is_excluded("lib/picozero-0.4.2.dist-info/RECORD", self.PATTERNS)
        )

    def test_matches_any_single_component(self):
        self.assertTrue(b.is_excluded("__pycache__/main.pyc", self.PATTERNS))
        self.assertTrue(b.is_excluded("lib/picozero/__pycache__/x.pyc", self.PATTERNS))
        self.assertTrue(b.is_excluded("lib/.DS_Store", self.PATTERNS))

    def test_keeps_source(self):
        for path in ("main.py", "lib/picozero/picozero.py", "roboxlib.py"):
            self.assertFalse(b.is_excluded(path, self.PATTERNS), path)

    def test_does_not_match_a_prefix_of_a_name(self):
        self.assertFalse(b.is_excluded("lib/picozero/picozero.py", ["lib/pico"]))


class Uf2ContainerTest(unittest.TestCase):
    def test_round_trip(self):
        blocks = b.split_into_blocks(b.XIP_BASE, b"\x01" * 700)
        self.assertEqual([addr for addr, _ in blocks],
                         [b.XIP_BASE, b.XIP_BASE + 256, b.XIP_BASE + 512])
        path = os.path.join(self.tmp, "round.uf2")
        with open(path, "wb") as fh:
            fh.write(b.uf2_bytes(blocks))
        parsed, family = b.parse_uf2(path)
        self.assertEqual(family, b.RP2040_FAMILY_ID)
        self.assertEqual(parsed, blocks)

    def test_block_numbering_and_size(self):
        blocks = b.split_into_blocks(b.XIP_BASE, b"\x02" * 1024)
        image = b.uf2_bytes(blocks)
        self.assertEqual(len(image), 4 * b.UF2_BLOCK)
        for index in range(4):
            header = struct.unpack("<8I", image[index * 512 : index * 512 + 32])
            self.assertEqual(header[5], index)  # block number
            self.assertEqual(header[6], 4)  # total blocks
            self.assertEqual(header[4], 256)  # payload size

    def test_sparse_drops_erased_blocks(self):
        data = b"\xff" * 256 + b"\x00" * 256 + b"\xff" * 256
        blocks = b.split_into_blocks(b.XIP_BASE, data, skip_erased=True)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][0], b.XIP_BASE + 256)

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)


class LittlefsImageTest(unittest.TestCase):
    @needs_littlefs
    def test_files_survive_a_round_trip(self):
        files = {"main.py": b"print('hi')\n", "lib/picozero/__init__.py": b"x = 1\n"}
        expected = {name: len(data) for name, data in files.items()}
        image, used = b.build_littlefs(files, 64 * 1024)
        self.assertEqual(len(image), 64 * 1024)
        self.assertLess(used, 64 * 1024)
        self.assertEqual(b.read_littlefs(image), expected)

    @needs_littlefs
    def test_geometry_matches_micropython(self):
        """A mismatch here is a board that refuses to mount the release."""
        from littlefs import LittleFS, UserContext

        image, _ = b.build_littlefs({"main.py": b"pass\n"}, 64 * 1024)
        fs = LittleFS(
            context=UserContext(buffer=bytearray(image)),
            block_size=b.LFS_BLOCK_SIZE,
            block_count=len(image) // b.LFS_BLOCK_SIZE,
            mount=False,
        )
        fs.mount()
        stat = fs.fs_stat()
        self.assertEqual(stat.block_size, 4096)  # rp2.Flash() sector size
        self.assertEqual(stat.disk_version, b.LFS_DISK_VERSION)  # littlefs 2.0
        fs.unmount()

    @needs_littlefs
    def test_image_is_reproducible(self):
        files = {"a.py": b"a\n", "b.py": b"b\n"}
        first, _ = b.build_littlefs(files, 64 * 1024)
        second, _ = b.build_littlefs(dict(reversed(list(files.items()))), 64 * 1024)
        self.assertEqual(first, second)


class BuildTest(unittest.TestCase):
    """End-to-end: run `build` over a fake src/ and a fake base firmware."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        self.src = os.path.join(self.tmp, "src")
        os.makedirs(os.path.join(self.src, "lib", "picozero"))
        os.makedirs(os.path.join(self.src, "__pycache__"))
        self.write("main.py", b'CURRENT_FIRMWARE_VERSION = "9.9.9"\n')
        self.write("roboxlib.py", b"# motors\n")
        self.write("lib/picozero/__init__.py", b"x = 1\n")
        self.write("__pycache__/main.pyc", b"junk")
        self.base = fake_base_uf2(os.path.join(self.tmp, "base.uf2"))

    def write(self, rel, data):
        with open(os.path.join(self.src, rel), "wb") as fh:
            fh.write(data)

    def build(self, *extra):
        out = os.path.join(self.tmp, "out.uf2")
        # --fs-base/--fs-size keep the test image small; the real defaults come
        # from the firmware. --offline so a test never reaches the network.
        argv = ["build", "--src", self.src, "-o", out, "--base", self.base,
                "--offline", "--exclude", "__pycache__",
                "--fs-base", str(64 * 1024), "--fs-size", str(64 * 1024)]
        # The builder is chatty; keep its progress out of the test output.
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(b.main(argv + list(extra)), 0)
        return out

    @needs_littlefs
    def test_contains_src_but_not_excluded_files(self):
        out = self.build()
        lowest, image, _blocks = b.flash_image_from_uf2(out)
        self.assertEqual(lowest, b.XIP_BASE)
        region = bytes(image[64 * 1024 : 128 * 1024])
        self.assertEqual(
            sorted(b.read_littlefs(region)),
            ["lib/picozero/__init__.py", "main.py", "roboxlib.py"],
        )

    @needs_littlefs
    def test_keeps_the_base_firmware(self):
        out = self.build()
        _lowest, image, _blocks = b.flash_image_from_uf2(out)
        self.assertEqual(image[:256], bytes(range(256)))

    @needs_littlefs
    def test_drops_the_bases_own_filesystem(self):
        """A full-flash dump can be reused as the base: its stale filesystem
        must not survive into the new artifact."""
        stale = b.split_into_blocks(b.XIP_BASE + 64 * 1024, b"\xaa" * 512)
        # Read before opening for write: "wb" truncates.
        dump = b.uf2_bytes(b.parse_uf2(self.base)[0] + stale)
        with open(self.base, "wb") as fh:
            fh.write(dump)
        out = self.build()
        _lowest, image, _blocks = b.flash_image_from_uf2(out)
        self.assertNotEqual(image[64 * 1024 : 64 * 1024 + 512], b"\xaa" * 512)
        self.assertIn("main.py", b.read_littlefs(bytes(image[64 * 1024 : 128 * 1024])))

    @needs_littlefs
    def test_two_builds_are_identical(self):
        with open(self.build(), "rb") as fh:
            first = fh.read()
        with open(self.build(), "rb") as fh:
            second = fh.read()
        self.assertEqual(first, second)

    @needs_littlefs
    def test_no_base_emits_only_the_filesystem(self):
        out = self.build("--no-base")
        lowest, _image, _blocks = b.flash_image_from_uf2(out)
        self.assertEqual(lowest, b.XIP_BASE + 64 * 1024)

    def test_default_output_is_named_after_the_firmware_version(self):
        self.assertEqual(b.firmware_version(self.src), "9.9.9")
        self.assertTrue(b.default_out_path(self.src).endswith("robox-9.9.9.uf2"))


if __name__ == "__main__":
    unittest.main()
