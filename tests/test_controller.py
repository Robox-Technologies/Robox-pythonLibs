"""Controller-mode packets and loop. No hardware required.

tests/vectors/controller.json is the website's file, copied verbatim: both
sides must pass it. Run with: ./tools/run-tests
"""

import json
import os
import sys
import unittest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

import controller as c  # noqa: E402

VECTORS = os.path.join(os.path.dirname(__file__), "vectors", "controller.json")


def load_vectors():
    with open(VECTORS) as vectors:
        return json.load(vectors)


class TestVectors(unittest.TestCase):
    def test_crc8_check_values(self):
        for case in load_vectors()["crc8"]:
            self.assertEqual(c.crc8(bytes(case["input"])), case["crc"])
        self.assertEqual(c.crc8(b"123456789"), 0xF4)

    def test_encode_matches_every_packet(self):
        for case in load_vectors()["packets"]:
            with self.subTest(case["name"]):
                self.assertEqual(case["start"], c.START)
                packet = c.encode(case["type"], case["seq"], case["a"], case["b"])
                self.assertEqual(list(packet), case["bytes"])

    def test_decode_matches_every_packet(self):
        for case in load_vectors()["packets"]:
            with self.subTest(case["name"]):
                parser = c.PacketParser()
                [(kind, seq, a, b)] = parser.feed(bytes(case["bytes"]))
                self.assertEqual(kind, case["type"])
                self.assertEqual(seq, case["seq"])
                self.assertEqual(c.speed(a), case["a"])
                self.assertEqual(c.speed(b), case["b"])
                self.assertEqual(parser.dropped, 0)


def drive(seq, left, right):
    return c.encode(c.DRIVE, seq, left, right)


class TestSpeed(unittest.TestCase):
    def test_int8_is_signed(self):
        self.assertEqual(c.speed(0x00), 0)
        self.assertEqual(c.speed(60), 60)
        self.assertEqual(c.speed(0xC4), -60)

    def test_out_of_range_is_clamped_not_rejected(self):
        self.assertEqual(c.speed(0x7F), 100)
        self.assertEqual(c.speed(0x80), -100)
        self.assertEqual(c.speed(101), 100)
        self.assertEqual(c.speed((-101) & 0xFF), -100)


class TestParser(unittest.TestCase):
    def test_a_packet_split_across_reads(self):
        parser = c.PacketParser()
        packet = drive(7, 10, 20)
        self.assertEqual(parser.feed(packet[:2]), [])
        self.assertEqual(parser.feed(packet[2:5]), [])
        self.assertEqual(parser.feed(packet[5:]), [(c.DRIVE, 7, 10, 20)])

    def test_several_packets_in_one_ble_chunk(self):
        parser = c.PacketParser()
        stream = drive(0, 1, 1) + drive(1, 2, 2) + drive(2, 3, 3)
        chunk, rest = stream[:20], stream[20:]
        self.assertEqual(
            parser.feed(chunk),
            [(c.DRIVE, 0, 1, 1), (c.DRIVE, 1, 2, 2), (c.DRIVE, 2, 3, 3)],
        )
        self.assertEqual(rest, b"")

    def test_garbage_between_packets_is_skipped(self):
        parser = c.PacketParser()
        packets = parser.feed(b"OK+CONN" + drive(1, 5, 5) + b"\x00\xff" + drive(2, 6, 6))
        self.assertEqual(packets, [(c.DRIVE, 1, 5, 5), (c.DRIVE, 2, 6, 6)])
        self.assertEqual(parser.dropped, 9)

    def test_bad_crc_drops_one_byte_so_a_packet_inside_survives(self):
        # A truncated packet whose tail is a real packet: dropping all six
        # bytes on the CRC failure would lose the real one.
        real = drive(9, 40, 40)
        stream = b"\xa5\x01" + real
        parser = c.PacketParser()
        self.assertEqual(parser.feed(stream), [(c.DRIVE, 9, 40, 40)])

    def test_corrupt_packet_is_ignored(self):
        bad = bytearray(drive(3, 50, 50))
        bad[3] ^= 0x01
        parser = c.PacketParser()
        self.assertEqual(parser.feed(bytes(bad)), [])

    def test_unknown_types_are_still_returned(self):
        # The loop ignores them but they still feed the timeout.
        parser = c.PacketParser()
        self.assertEqual(
            parser.feed(c.encode(0x10, 0, 1, 2)), [(0x10, 0, 1, 2)]
        )

    def test_bytes_after_exit_are_left_for_the_framed_reader(self):
        parser = c.PacketParser()
        framed = b"\x01framed command\n"
        packets = parser.feed(
            drive(0, 0, 0) + c.encode(c.EXIT, 1) + framed + drive(2, 9, 9)
        )
        self.assertEqual(packets, [(c.DRIVE, 0, 0, 0), (c.EXIT, 1, 0, 0)])
        self.assertEqual(parser.take_remainder(), framed + drive(2, 9, 9))
        self.assertEqual(parser.take_remainder(), b"")

    def test_noise_cannot_grow_the_buffer_without_bound(self):
        parser = c.PacketParser()
        for _ in range(100):
            parser.feed(b"\xa5" + b"\x00" * 3)
        self.assertLessEqual(len(parser.buffer), c.packets.MAX_BUFFERED)


class FakeComm:
    """Hands out one scripted chunk per read; records what is unread."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.unread_bytes = b""

    def read_raw(self):
        return self.chunks.pop(0) if self.chunks else None

    def unread(self, data):
        self.unread_bytes += data


class FakeMotors:
    def __init__(self, fail_on=None):
        self.runs = []
        self.fail_on = fail_on

    def run_motors(self, left, right):
        self.runs.append((left, right))
        if self.fail_on == (left, right):
            raise RuntimeError("motor driver fault")

    def stop_motors(self):
        self.run_motors(0, 0)


class FakeClock:
    def __init__(self):
        self.now = 1000
        self.sleeps = 0

    def ticks_ms(self):
        return self.now

    @staticmethod
    def ticks_diff(later, earlier):
        return later - earlier

    def sleep_ms(self, ms):
        self.sleeps += 1
        self.now += ms


def run(chunks, motors=None, clock=None):
    comm = FakeComm(chunks)
    motors = motors or FakeMotors()
    clock = clock or FakeClock()
    reason, stats = c.run(
        comm, motors, clock.ticks_ms, clock.ticks_diff, clock.sleep_ms
    )
    return reason, stats, motors, comm, clock


class TestLoop(unittest.TestCase):
    def test_drive_sets_motors_and_exit_stops_them(self):
        reason, stats, motors, _, _ = run(
            [drive(0, 60, 60), drive(1, -60, 100), c.encode(c.EXIT, 2)]
        )
        self.assertEqual(reason, "exit")
        self.assertEqual(motors.runs, [(0, 0), (60, 60), (-60, 100), (0, 0)])
        self.assertEqual(stats["packets"], 3)

    def test_only_the_latest_drive_in_one_read_is_applied(self):
        _, _, motors, _, _ = run(
            [drive(0, 10, 10) + drive(1, 20, 20) + drive(2, 30, 30),
             c.encode(c.EXIT, 3)]
        )
        self.assertEqual(motors.runs, [(0, 0), (30, 30), (0, 0)])

    def test_silence_times_out_with_motors_stopped(self):
        reason, _, motors, _, clock = run([drive(0, 80, 80)])
        self.assertEqual(reason, "timeout")
        self.assertEqual(motors.runs[-1], (0, 0))
        self.assertGreaterEqual(clock.now - 1000, c.TIMEOUT_MS)
        self.assertLess(clock.now - 1000, c.TIMEOUT_MS + 2 * c.LOOP_SLEEP_MS + 2)

    def test_heartbeats_keep_the_mode_alive(self):
        # One DRIVE every 100 ms for a second, then silence.
        chunks = []
        for index in range(10):
            chunks.append(drive(index, 50, 50))
            chunks.extend([None] * (100 // c.LOOP_SLEEP_MS - 1))
        reason, stats, _, _, clock = run(chunks)
        self.assertEqual(reason, "timeout")
        self.assertEqual(stats["drives"], 10)
        # The last DRIVE lands at 900 ms; the timeout counts from there.
        self.assertGreaterEqual(clock.now - 1000, 900 + c.TIMEOUT_MS)

    def test_invalid_bytes_do_not_feed_the_timeout(self):
        # Framed traffic from a reconnecting page, a little every pass.
        noise = [b"\x01framed\n"] * 1000
        reason, stats, _, _, clock = run(noise)
        self.assertEqual(reason, "timeout")
        self.assertEqual(stats["packets"], 0)
        self.assertLess(clock.now - 1000, c.TIMEOUT_MS + 2 * c.LOOP_SLEEP_MS + 2)

    def test_unknown_valid_packets_feed_the_timeout_but_do_nothing(self):
        chunks = [c.encode(0x10, 0, 1, 2)] + [None] * 100 + [c.encode(c.EXIT, 1)]
        reason, _, motors, _, _ = run(chunks)
        self.assertEqual(reason, "exit")
        self.assertEqual(motors.runs, [(0, 0), (0, 0)])

    def test_bytes_after_exit_go_back_to_the_interface(self):
        framed = b"\x01next command\n"
        _, _, _, comm, _ = run([c.encode(c.EXIT, 0) + framed])
        self.assertEqual(comm.unread_bytes, framed)

    def test_motors_stop_even_when_driving_raises(self):
        motors = FakeMotors(fail_on=(70, 70))
        with self.assertRaises(RuntimeError):
            run([drive(0, 70, 70)], motors=motors)
        self.assertEqual(motors.runs[-1], (0, 0))


if __name__ == "__main__":
    unittest.main()
