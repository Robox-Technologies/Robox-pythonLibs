"""Offline tests for src/main.py. No hardware required.

main.py is a script: it builds its interfaces at import and then loops forever.
`load_firmware` stubs the MicroPython-only modules and runs everything up to
that loop, so the real dispatch and line handling are what gets tested.

Run with: ./tools/run-tests
"""

import json
import os
import sys
import threading
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


class RaisingColorSensor:
    """Stands in for a board with no colour sensor wired up."""

    def __init__(self):
        raise Exception("no sensor attached")


def load_firmware(color_sensor_cls=RaisingColorSensor):
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
    roboxlib.ColorSensor = color_sensor_cls
    roboxlib.motor_calibrations = []
    roboxlib.save_motor_calibration = roboxlib.motor_calibrations.append
    roboxlib.load_motor_calibration = (
        lambda: roboxlib.motor_calibrations[-1]
        if roboxlib.motor_calibrations
        else 0.0
    )

    roboxlib.reverse_state = [False, False]

    def load_motor_reverse(index):
        return roboxlib.reverse_state[index]

    def save_motor_reverse(index, value):
        roboxlib.reverse_state[index] = bool(value)

    roboxlib.load_motor_reverse = load_motor_reverse
    roboxlib.save_motor_reverse = save_motor_reverse

    roboxlib.swap_state = [False]

    def load_motor_swap():
        return roboxlib.swap_state[0]

    def save_motor_swap(value):
        roboxlib.swap_state[0] = bool(value)

    roboxlib.load_motor_swap = load_motor_swap
    roboxlib.save_motor_swap = save_motor_swap

    roboxlib.motor_runs = []

    class FakeMotors:
        """Stands in for roboxlib.Motors. Records what run_motors was told
        to do, rather than reapplying the calibration math, since that math
        is roboxlib's own responsibility and not what dispatch_command tests
        are checking here."""

        def run_motors(self, left_speed, right_speed):
            roboxlib.motor_runs.append((left_speed, right_speed))

        def stop_motors(self):
            self.run_motors(0, 0)

    roboxlib.Motors = FakeMotors

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
    ns["roboxlib"] = roboxlib

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
    """The device messages an interface has emitted, decoded.

    Fed in chunks, as a link delivers them. The reader caps its buffer at 4KB
    and resyncs past an overflow, so handing it a long stream in one go quietly
    discards most of it. A message longer than one payload arrives as
    CONTINUE frames followed by a REPLY frame, same as a long upload does in
    the other direction, so the pieces are reassembled here rather than
    reading the REPLY frame alone.
    """
    out = []
    reader = p.FrameReader()
    raw = sent_bytes(comm)
    partial = b""
    for offset in range(0, len(raw), 512):
        frames, _ = reader.feed(raw[offset:offset + 512])
        for frame in frames:
            if frame.kind == p.KIND_CONTINUE:
                partial += frame.payload
            elif frame.kind == p.KIND_REPLY:
                out.append(json.loads((partial + frame.payload).decode()))
                partial = b""
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


class TestProvisioning(unittest.TestCase):
    def test_configure_reports_what_the_module_refused(self):
        ns = load_firmware()
        ble, comms = ns["ble"], ns["communication"]
        comms.time.sleep = lambda _seconds: None

        # This module answers ERROR to plenty of commands, and a refusal is
        # otherwise indistinguishable from a setting that took.
        refuse = {"AT+CHAR0xffe1"}

        def answer(data):
            ble.uart.written.append(data)
            cmd = data.decode().strip()
            ble.uart.feed(b"ERROR\r\n" if cmd in refuse else b"OK\r\n")

        ble.uart.write = answer

        self.assertFalse(ble.configure("Robox20"))
        self.assertEqual(
            [w.decode().strip() for w in ble.uart.written],
            [
                "AT+UUID0xffe0",
                "AT+CHAR0xffe1",
                "AT+NAMERobox20",
                "AT+RESET",
                "AT",
            ],
        )

    def test_configure_passes_when_the_module_takes_everything(self):
        ns = load_firmware()
        ble, comms = ns["ble"], ns["communication"]
        comms.time.sleep = lambda _seconds: None

        def answer(data):
            ble.uart.written.append(data)
            ble.uart.feed(b"OK\r\n")

        ble.uart.write = answer

        self.assertTrue(ble.configure("Robox20"))


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


class FakeColorSensor:
    """Returns readings from a fixed queue, one per call."""

    def __init__(self, readings=((1, 2, 3),)):
        self.readings = list(readings)
        self.calibrated = []
        self.palette = {}

    def readColor(self):
        if len(self.readings) > 1:
            return self.readings.pop(0)
        return self.readings[0]

    def calibrate_white(self):
        self.calibrated.append("white")

    def calibrate_black(self):
        self.calibrated.append("black")

    def reset_white(self):
        self.calibrated.append("white_reset")

    def reset_black(self):
        self.calibrated.append("black_reset")

    def calibrate_palette(self, name):
        self.calibrated.append(name)

    def reset_palette(self, name):
        self.calibrated.append(name + "_reset")


class TestColorCalibration(unittest.TestCase):
    COMMANDS = (
        "calibrate_color_red",
        "calibrate_color_orange",
        "calibrate_color_yellow",
        "calibrate_color_green",
        "calibrate_color_blue",
        "calibrate_color_purple",
        "calibrate_color_black",
        "calibrate_color_white",
        "reset_color_red",
        "reset_color_orange",
        "reset_color_yellow",
        "reset_color_green",
        "reset_color_blue",
        "reset_color_purple",
        "reset_color_black",
        "reset_color_white",
    )
    # *_black/*_white run the sensor's brightness-extreme methods instead of
    # storing/clearing a palette point like every other colour does, but the
    # sensor call and the reply happen to land on the same string either way.
    EXPECTED = (
        "red",
        "orange",
        "yellow",
        "green",
        "blue",
        "purple",
        "black",
        "white",
        "red_reset",
        "orange_reset",
        "yellow_reset",
        "green_reset",
        "blue_reset",
        "purple_reset",
        "black_reset",
        "white_reset",
    )

    def test_missing_sensor_reports_an_error_for_every_command(self):
        ns = load_firmware()
        usb = ns["usb"]

        for command in self.COMMANDS:
            ns["dispatch_command"](usb, command)
        drain(ns)

        self.assertEqual(
            [r["message"] for r in replies(usb) if r["type"] == "error"],
            ["Color sensor not connected"] * len(self.COMMANDS),
        )

    def test_each_command_acts_on_its_own_point(self):
        sensor = FakeColorSensor()
        ns = load_firmware(color_sensor_cls=lambda: sensor)
        usb = ns["usb"]

        for command in self.COMMANDS:
            ns["dispatch_command"](usb, command)
        drain(ns)

        self.assertEqual(sensor.calibrated, list(self.EXPECTED))
        self.assertEqual(
            [r["message"] for r in replies(usb) if r["type"] == "calibrated"],
            list(self.EXPECTED),
        )

    def test_an_uncalibrated_colour_is_rejected_by_the_frame_layer(self):
        """The whitelist in protocol.py, not the dispatcher, is the gate."""
        ns = load_firmware()
        ble = ns["ble"]

        ble.uart.feed(command_frame("calibrate_color_turquoise"))
        ns["poll"](ble)
        drain(ns)

        self.assertEqual(
            [r["message"] for r in replies(ble) if r["type"] == "error"],
            ["Unknown command: calibrate_color_turquoise"],
        )


class TestMotorCalibration(unittest.TestCase):
    def test_a_bias_is_persisted_and_acknowledged(self):
        ns = load_firmware()
        usb = ns["usb"]

        ns["dispatch_command"](usb, "calibrate_motors_-0.35")
        drain(ns)

        self.assertEqual(ns["roboxlib"].motor_calibrations, [-0.35])
        self.assertEqual(
            [r["message"] for r in replies(usb) if r["type"] == "calibrated"],
            ["motors"],
        )

    def test_positive_and_boundary_biases_round_trip(self):
        ns = load_firmware()
        usb = ns["usb"]

        ns["dispatch_command"](usb, "calibrate_motors_0.6")
        ns["dispatch_command"](usb, "calibrate_motors_-1")
        drain(ns)

        self.assertEqual(ns["roboxlib"].motor_calibrations, [0.6, -1.0])

    def test_out_of_range_or_malformed_values_are_rejected_by_the_frame_layer(
        self,
    ):
        """Mirrors calibrate_color_turquoise: the whitelist in protocol.py is
        the gate, so a bad value never reaches the dispatcher."""
        ns = load_firmware()
        ble = ns["ble"]

        for index, bad in enumerate(("1", "2.5", "-1.01", "nope")):
            ble.uart.feed(command_frame("calibrate_motors_" + bad, seq=index))
            ns["poll"](ble)
        drain(ns)

        self.assertEqual(
            [r["message"] for r in replies(ble) if r["type"] == "error"],
            ["Unknown command: calibrate_motors_" + bad
             for bad in ("1", "2.5", "-1.01", "nope")],
        )
        self.assertEqual(ns["roboxlib"].motor_calibrations, [])

    def test_get_returns_the_default_when_never_calibrated(self):
        ns = load_firmware()
        usb = ns["usb"]

        ns["dispatch_command"](usb, "get_calibration_motors")
        drain(ns)

        self.assertEqual(
            [r["message"] for r in replies(usb) if r["type"] == "calibration"],
            [
                {
                    "name": "motors",
                    "value": {
                        "bias": 0.0,
                        "reverse": [False, False],
                        "swap": False,
                    },
                }
            ],
        )

    def test_get_returns_the_persisted_bias(self):
        ns = load_firmware()
        usb = ns["usb"]

        ns["dispatch_command"](usb, "calibrate_motors_-0.35")
        ns["dispatch_command"](usb, "get_calibration_motors")
        drain(ns)

        self.assertEqual(
            [r["message"] for r in replies(usb) if r["type"] == "calibration"],
            [
                {
                    "name": "motors",
                    "value": {
                        "bias": -0.35,
                        "reverse": [False, False],
                        "swap": False,
                    },
                }
            ],
        )


class TestMotorReversalAndSwap(unittest.TestCase):
    def _motor_calibration(self, ns, usb):
        ns["dispatch_command"](usb, "get_calibration_motors")
        drain(ns)
        return [
            r["message"] for r in replies(usb) if r["type"] == "calibration"
        ][-1]["value"]

    def test_reverse_0_is_set_absolutely_and_acknowledged(self):
        ns = load_firmware()
        usb = ns["usb"]

        ns["dispatch_command"](usb, "reverse_motor_0_1")
        self.assertEqual(
            self._motor_calibration(ns, usb)["reverse"], [True, False]
        )

        # Setting the same value again is a no-op, not a flip: this is what
        # makes the command safe to resend if an ACK is lost.
        ns["dispatch_command"](usb, "reverse_motor_0_1")
        self.assertEqual(
            self._motor_calibration(ns, usb)["reverse"], [True, False]
        )

        ns["dispatch_command"](usb, "reverse_motor_0_0")
        self.assertEqual(
            self._motor_calibration(ns, usb)["reverse"], [False, False]
        )

        self.assertEqual(
            [r["message"] for r in replies(usb) if r["type"] == "calibrated"],
            ["reverse_0", "reverse_0", "reverse_0"],
        )

    def test_reverse_1_is_set_independently_of_reverse_0(self):
        ns = load_firmware()
        usb = ns["usb"]

        ns["dispatch_command"](usb, "reverse_motor_1_1")
        self.assertEqual(
            self._motor_calibration(ns, usb)["reverse"], [False, True]
        )

    def test_swap_is_set_absolutely_and_acknowledged(self):
        ns = load_firmware()
        usb = ns["usb"]

        ns["dispatch_command"](usb, "swap_motors_1")
        self.assertEqual(self._motor_calibration(ns, usb)["swap"], True)

        ns["dispatch_command"](usb, "swap_motors_1")
        self.assertEqual(self._motor_calibration(ns, usb)["swap"], True)

        ns["dispatch_command"](usb, "swap_motors_0")
        self.assertEqual(self._motor_calibration(ns, usb)["swap"], False)

        self.assertEqual(
            [r["message"] for r in replies(usb) if r["type"] == "calibrated"],
            ["swap", "swap", "swap"],
        )

    def test_get_returns_the_defaults_when_never_set(self):
        ns = load_firmware()
        usb = ns["usb"]

        self.assertEqual(
            self._motor_calibration(ns, usb),
            {"bias": 0.0, "reverse": [False, False], "swap": False},
        )


class TestMotorTestDrive(unittest.TestCase):
    def test_run_motor_0_drives_only_the_left_side(self):
        ns = load_firmware()
        usb = ns["usb"]

        ns["dispatch_command"](usb, "run_motor_0")

        self.assertEqual(
            ns["roboxlib"].motor_runs, [(ns["TEST_MOTOR_SPEED"], 0)]
        )

    def test_run_motor_1_drives_only_the_right_side(self):
        ns = load_firmware()
        usb = ns["usb"]

        ns["dispatch_command"](usb, "run_motor_1")

        self.assertEqual(
            ns["roboxlib"].motor_runs, [(0, ns["TEST_MOTOR_SPEED"])]
        )

    def test_stop_motors_stops_both(self):
        ns = load_firmware()
        usb = ns["usb"]

        ns["dispatch_command"](usb, "run_motor_0")
        ns["dispatch_command"](usb, "stop_motors")

        self.assertEqual(
            ns["roboxlib"].motor_runs,
            [(ns["TEST_MOTOR_SPEED"], 0), (0, 0)],
        )

    def test_starting_a_program_stops_a_test_drive_first(self):
        """A left-running test motor must not fight the program for the pins,
        even when the start attempt itself is about to be refused."""
        ns = load_firmware()
        ble = ns["ble"]

        ns["dispatch_command"](ble, "run_motor_0")
        ns["dispatch_command"](ble, "start_program")

        self.assertEqual(
            ns["roboxlib"].motor_runs,
            [(ns["TEST_MOTOR_SPEED"], 0), (0, 0)],
        )


class TestColorMode(unittest.TestCase):
    def test_missing_sensor_reports_an_error_and_never_enters_the_mode(self):
        ns = load_firmware()
        usb = ns["usb"]

        ns["dispatch_command"](usb, "color_mode")
        drain(ns)

        self.assertEqual(
            [r["message"] for r in replies(usb) if r["type"] == "error"],
            ["Color sensor not connected"],
        )
        self.assertIsNone(ns["color_mode_comm"])

    def test_readings_repeat_on_interval_until_another_command_arrives(self):
        # USB has no send pacing of its own, so this exercises the mode's
        # timing logic without BLE's link-rate throttling in the way.
        ns = load_firmware(color_sensor_cls=lambda: FakeColorSensor(
            [(1, 2, 3), (4, 5, 6), (7, 8, 9)]
        ))
        usb = ns["usb"]

        clock = [0]
        ns["time"].ticks_ms = lambda: clock[0]

        ns["dispatch_command"](usb, "color_mode")
        self.assertIs(ns["color_mode_comm"], usb)

        # Due immediately: turning the mode on should not wait one interval.
        ns["send_color_if_due"]()
        # Not due again yet.
        ns["send_color_if_due"]()
        drain(ns)

        colors = [r["message"] for r in replies(usb) if r["type"] == "color"]
        self.assertEqual(
            colors, [{"r": 1, "g": 2, "b": 3, "name": "black"}]
        )

        clock[0] += ns["COLOR_MODE_INTERVAL_MS"]
        ns["send_color_if_due"]()
        drain(ns)

        colors = [r["message"] for r in replies(usb) if r["type"] == "color"]
        self.assertEqual(
            colors,
            [
                {"r": 1, "g": 2, "b": 3, "name": "black"},
                {"r": 4, "g": 5, "b": 6, "name": "black"},
            ],
        )

        # Any other command exits colour mode, even one that does nothing
        # else observable.
        ns["dispatch_command"](usb, "disconnect_device")
        self.assertIsNone(ns["color_mode_comm"])

        clock[0] += ns["COLOR_MODE_INTERVAL_MS"] * 3
        ns["send_color_if_due"]()
        drain(ns)

        colors = [r["message"] for r in replies(usb) if r["type"] == "color"]
        self.assertEqual(
            colors,
            [
                {"r": 1, "g": 2, "b": 3, "name": "black"},
                {"r": 4, "g": 5, "b": 6, "name": "black"},
            ],
        )

    def test_a_calibrated_palette_point_wins_over_the_default_guess(self):
        # (100, 50, 10) is nearer STANDARD_COLORS' idealised "orange" than
        # "red" by chromaticity; calibrating "red" to this exact reading
        # (as if a real red swatch reads this way on this sensor) has to
        # flip the match, or the calibration is not doing anything.
        sensor = FakeColorSensor([(100, 50, 10)])
        sensor.palette = {"red": (100, 50, 10)}
        ns = load_firmware(color_sensor_cls=lambda: sensor)
        usb = ns["usb"]

        ns["dispatch_command"](usb, "color_mode")
        ns["send_color_if_due"]()
        drain(ns)

        colors = [r["message"] for r in replies(usb) if r["type"] == "color"]
        self.assertEqual(
            colors, [{"r": 100, "g": 50, "b": 10, "name": "red"}]
        )


class TestOutgoingOrder(unittest.TestCase):
    """Console output has to reach the terminal in the order it was printed."""

    def test_pacing_gate_does_not_let_a_later_message_overtake(self):
        """A clock tick part-way down the queue must not reorder one interface.

        `can_send_now` is a comparison against the clock, so asking it once per
        queue entry meant a scan that straddled `next_send_time` judged the
        oldest entry not ready and a later one ready, and sent that first. The
        clock here advances a millisecond per call, which is what a scan
        preempted by the user program's thread looks like on the board.
        """
        ns = load_firmware()
        ble, comms = ns["ble"], ns["communication"]

        for number in range(1, 21):
            ble.write_message("console", str(number))

        calls = [0]

        def creeping_ticks_ms():
            calls[0] += 1
            return calls[0]

        comms.time.ticks_ms = creeping_ticks_ms
        # Not ready until several entries into the scan.
        ble.next_send_time = 5

        for _ in range(40):
            if not comms.flush_outgoing_messages():
                ble.next_send_time = calls[0]
                comms.flush_outgoing_messages()

        sent = [
            int(reply["message"])
            for reply in replies(ble)
            if reply["type"] == "console"
        ]
        self.assertEqual(sent, list(range(1, 21)))

    def test_a_stalled_interface_does_not_hold_up_a_ready_one(self):
        """Blocking is per interface, not global: USB still drains."""
        ns = load_firmware()
        ble, usb, comms = ns["ble"], ns["usb"], ns["communication"]

        # Startup queues a "connect" for Bluetooth; this is about what follows.
        del comms.outgoing_messages[:]

        ble.next_send_time = comms.time.ticks_add(comms.time.ticks_ms(), 10000)
        ble.write_message("console", "bluetooth")
        usb.write_message("console", "usb")

        self.assertTrue(comms.flush_outgoing_messages())
        self.assertEqual(
            [r["message"] for r in replies(usb) if r["type"] == "console"],
            ["usb"],
        )
        self.assertEqual(comms.queued_message_count(), 1)


class TestOutgoingLoss(unittest.TestCase):
    """Printing faster than the link carries must not lose lines."""

    def test_a_hundred_prints_all_arrive_in_order(self):
        """The queue holds 64. A tight print loop used to lose the rest."""
        ns = load_firmware()
        usb, comms = ns["usb"], ns["communication"]
        del comms.outgoing_messages[:]

        def program():
            for number in range(1, 101):
                usb.write_message("console", str(number))

        worker = threading.Thread(target=program)
        worker.start()

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if comms.flush_outgoing_messages():
                continue
            if not worker.is_alive() and not comms.queued_message_count():
                break
            time.sleep(0.001)
        worker.join(timeout=5)

        self.assertEqual(
            [r["message"] for r in replies(usb) if r["type"] == "console"],
            [str(number) for number in range(1, 101)],
        )
        self.assertEqual(comms.dropped_message_count, 0)

    def test_the_draining_thread_never_waits_for_room(self):
        """The main loop cannot wait on a queue only it can empty."""
        ns = load_firmware()
        usb, comms = ns["usb"], ns["communication"]
        del comms.outgoing_messages[:]
        # Long enough that waiting would look like a hang, not a slow test.
        comms.QUEUE_WAIT_MS = 60000

        for number in range(comms.MAX_QUEUED_MESSAGES + 1):
            usb.write_message("console", str(number))

        self.assertEqual(
            comms.queued_message_count(), comms.MAX_QUEUED_MESSAGES
        )

    def test_a_drop_is_announced_where_the_gap_is(self):
        """When the link really is dead, the loss is marked, not silent."""
        ns = load_firmware()
        usb, comms = ns["usb"], ns["communication"]
        del comms.outgoing_messages[:]

        overrun = 3
        for number in range(1, comms.MAX_QUEUED_MESSAGES + overrun + 1):
            usb.write_message("console", str(number))

        drain(ns)

        sent = [r["message"] for r in replies(usb) if r["type"] == "console"]
        self.assertEqual(sent[0], comms.DROP_NOTICE % overrun)
        self.assertEqual(
            sent[1:],
            [
                str(number)
                for number in range(
                    overrun + 1, comms.MAX_QUEUED_MESSAGES + overrun + 1
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
