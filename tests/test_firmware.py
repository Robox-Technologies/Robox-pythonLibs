"""Offline tests for src/main.py. No hardware required.

main.py is a script: it builds its interfaces at import and then loops forever.
`load_firmware` stubs the MicroPython-only modules and runs everything up to
that loop, so the real dispatch and line handling are what gets tested.

Run with: ./tools/run-tests
"""

import json
import os
import sys
import time
import types
import unittest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

import protocol as p  # noqa: E402


class FakeUart:
    """A UART we can push bytes into, standing in for the module."""

    def __init__(self, *args, **kwargs):
        self.rx = b""
        self.written = []

    def feed(self, data):
        self.rx += data

    def any(self):
        return len(self.rx)

    def read(self):
        data, self.rx = self.rx, b""
        return data or None

    def write(self, data):
        self.written.append(data)


def load_firmware():
    """Run main.py's setup against stubbed hardware and return its namespace."""
    machine = types.ModuleType("machine")

    class Pin:
        OUT = 1

        def __init__(self, *args, **kwargs):
            pass

        def on(self):
            pass

        def off(self):
            pass

    machine.Pin = Pin
    machine.UART = FakeUart
    machine.resets = 0

    def reset():
        machine.resets += 1

    machine.reset = reset
    machine.bootloader = lambda: None

    # MicroPython's monotonic tick helpers, which CPython's time lacks.
    clock = types.ModuleType("time")
    for name in dir(time):
        if not name.startswith("_"):
            setattr(clock, name, getattr(time, name))
    clock.ticks_ms = lambda: int(time.monotonic() * 1000)
    clock.ticks_add = lambda ticks, delta: ticks + delta
    clock.ticks_diff = lambda later, earlier: later - earlier

    roboxlib = types.ModuleType("roboxlib")

    class ColorSensor:
        def __init__(self):
            raise Exception("no sensor attached")

    roboxlib.ColorSensor = ColorSensor

    saved = {
        name: sys.modules.get(name)
        for name in ("machine", "roboxlib", "time")
    }
    sys.modules["machine"] = machine
    sys.modules["roboxlib"] = roboxlib
    sys.modules["time"] = clock

    # A fresh communication module each time, so its global outgoing queue does
    # not leak between tests.
    for name in ("communication", "framed", "main"):
        sys.modules.pop(name, None)

    try:
        import communication

        source = open(os.path.join(SRC, "main.py")).read()
        body = source.split("# ----------------------\n# Main loop")[0]

        ns = {"__name__": "__main__"}
        exec(compile(body, "main.py", "exec"), ns)
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    ns["machine"] = machine
    ns["communication"] = communication

    # Bluetooth already records everything through its FakeUart. USB writes to
    # stdout, so give it somewhere harmless to put its bytes.
    usb = ns["usb"]
    usb.sent = bytearray()
    usb.write_raw = usb.sent.extend

    return ns


def sent_bytes(comm):
    """Everything an interface has put on its link."""
    if hasattr(comm, "uart"):
        return b"".join(comm.uart.written)
    return bytes(comm.sent)


def replies(comm):
    """The device messages an interface has emitted, decoded."""
    out = []
    frames, _ = p.FrameReader().feed(sent_bytes(comm))
    for frame in frames:
        if frame.kind == p.KIND_REPLY:
            out.append(json.loads(frame.text()))
    return out


def drain(ns, timeout=2.0):
    """Empty the outgoing queue. BLE paces itself, so this waits it out."""
    comms = ns["communication"]
    deadline = time.monotonic() + timeout
    while comms.queued_message_count() and time.monotonic() < deadline:
        if not comms.flush_outgoing_messages():
            time.sleep(0.005)


def command_frame(name, seq=0):
    return p.encode_frame(seq, p.KIND_COMMAND, name.encode())


class TestModuleChatter(unittest.TestCase):
    """The Bluetooth module shares the board's UART and is not newline-tidy."""

    def test_firmware_check_survives_a_status_string_in_front_of_it(self):
        ns = load_firmware()
        ble = ns["ble"]

        # No terminator, which is how the module reports link state and
        # answers AT commands. It lands in front of the next frame.
        ble.uart.feed(b"OK+CONN")
        ble.uart.feed(command_frame("firmware_check"))

        ns["poll"](ble)
        drain(ns)

        self.assertEqual(
            [r["type"] for r in replies(ble)],
            ["connect", "firmware"],
            "the check must be answered even with chatter glued to its frame",
        )

    def test_binary_garbage_does_not_swallow_the_next_frame(self):
        ns = load_firmware()
        ble = ns["ble"]

        ble.uart.feed(b"%\x11\xfd5)\xff")
        ble.uart.feed(command_frame("firmware_check"))

        ns["poll"](ble)
        drain(ns)

        self.assertIn("firmware", [r["type"] for r in replies(ble)])

    def test_unterminated_chatter_cannot_grow_without_bound(self):
        ns = load_firmware()
        ble = ns["ble"]

        for _ in range(200):
            ble.uart.feed(b"OK+LOST")
            ns["poll"](ble)

        self.assertLessEqual(
            len(ble.buffer), ns["communication"].MAX_LINE_LENGTH
        )

        # And the link still works afterwards.
        ble.uart.feed(command_frame("firmware_check"))
        ns["poll"](ble)
        drain(ns)

        self.assertIn("firmware", [r["type"] for r in replies(ble)])


    def test_undecodable_bytes_do_not_kill_the_loop(self):
        ns = load_firmware()
        usb, comms = ns["usb"], ns["communication"]

        class Boom:
            def readline(self):
                raise UnicodeError

        usb.poller = types.SimpleNamespace(poll=lambda _timeout: [1])
        real_stdin = comms.sys.stdin
        comms.sys.stdin = Boom()
        self.addCleanup(setattr, comms.sys, "stdin", real_stdin)

        ns["poll"](usb)

        self.assertEqual(usb.decode_errors, 1)


class TestInterfaceClaim(unittest.TestCase):
    def test_a_second_interface_takes_the_board_over(self):
        ns = load_firmware()
        usb, ble = ns["usb"], ns["ble"]

        ns["dispatch_command"](usb, "firmware_check")
        ns["dispatch_command"](ble, "firmware_check")
        drain(ns)

        # Refusing the second one stranded the board until a power cycle,
        # because nothing can tell it the first client is gone.
        self.assertIn("firmware", [r["type"] for r in replies(usb)])
        self.assertIn("firmware", [r["type"] for r in replies(ble)])
        self.assertEqual(
            [r for r in replies(ble) if r["type"] == "error"], []
        )
        self.assertIs(ns["current_communication_method"], ble)

    def test_a_usb_session_never_sleeps_the_bluetooth_module(self):
        ns = load_firmware()
        usb, ble = ns["usb"], ns["ble"]

        ns["dispatch_command"](usb, "firmware_check")
        drain(ns)

        self.assertEqual(
            [w for w in ble.uart.written if b"SLEEP" in w],
            [],
            "an interface the board stops reading cannot be woken from outside",
        )

        # Bluetooth is still live: the board reads it and answers.
        ble.uart.feed(command_frame("firmware_check"))
        ns["poll"](ble)
        drain(ns)

        self.assertIn("firmware", [r["type"] for r in replies(ble)])

    def test_disconnect_releases_the_claim(self):
        ns = load_firmware()
        usb = ns["usb"]

        ns["dispatch_command"](usb, "firmware_check")
        self.assertIs(ns["current_communication_method"], usb)

        ns["dispatch_command"](usb, "disconnect_device")
        self.assertIsNone(ns["current_communication_method"])


class TestCommandGating(unittest.TestCase):
    def test_unverified_upload_will_not_run(self):
        ns = load_firmware()
        ble = ns["ble"]

        ns["dispatch_command"](ble, "start_program")
        drain(ns)

        self.assertEqual(
            [r["message"] for r in replies(ble) if r["type"] == "error"],
            ["Upload did not verify, refusing to run it"],
        )

    def test_program_text_spelling_a_command_is_not_executed(self):
        """The reason frames exist at all."""
        ns = load_firmware()
        ble = ns["ble"]

        ble.uart.feed(p.encode_frame(0, p.KIND_DATA, b"reset_device"))
        ns["poll"](ble)

        self.assertEqual(ns["machine"].resets, 0)


if __name__ == "__main__":
    unittest.main()
