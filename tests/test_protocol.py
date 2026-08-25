"""Offline tests for src/protocol.py. No hardware required.

Run with: ./tools/run-tests
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import protocol as p  # noqa: E402


class TestChecksums(unittest.TestCase):
    def test_frame_checksum_covers_header(self):
        """A corrupted seq or kind must change the checksum."""
        base = p.frame_checksum(0, p.KIND_DATA, b"hello")
        self.assertNotEqual(base, p.frame_checksum(1, p.KIND_DATA, b"hello"))
        self.assertNotEqual(base, p.frame_checksum(0, p.KIND_COMMAND, b"hello"))
        self.assertNotEqual(base, p.frame_checksum(0, p.KIND_DATA, b"hellp"))

    def test_checksum_is_16_bit(self):
        for seq in range(0, 256, 17):
            value = p.frame_checksum(seq, p.KIND_DATA, bytes([seq]) * 10)
            self.assertTrue(0 <= value <= 0xFFFF)

    def test_normalise_collapses_line_endings(self):
        self.assertEqual(
            p.normalise_program("a\r\n\r\nb\rc\n"), "a\n\nb\nc\n"
        )

    def test_normalise_preserves_blank_lines(self):
        """A blank line is explicit now that a frame states its length."""
        self.assertEqual(
            p.normalise_program("x = 1\n\n\ny = 2\n"), "x = 1\n\n\ny = 2\n"
        )
        self.assertEqual(p.program_line_count("x = 1\n\n\ny = 2\n"), 4)

    def test_normalise_guarantees_one_trailing_newline(self):
        self.assertEqual(p.normalise_program("a\nb"), "a\nb\n")
        self.assertEqual(p.normalise_program("a\nb\n"), "a\nb\n")

    def test_normalise_preserves_trailing_whitespace(self):
        """Trailing spaces are meaningful inside string literals."""
        self.assertEqual(p.normalise_program("x = 'a  '  \n"), "x = 'a  '  \n")

    def test_program_checksum_is_stable_across_line_endings(self):
        self.assertEqual(
            p.program_checksum("a\nb\n"), p.program_checksum("a\r\nb\r\n")
        )

    def test_program_checksum_detects_a_single_byte_change(self):
        self.assertNotEqual(
            p.program_checksum("motors.forward(60)\n"),
            p.program_checksum("motors.forward(61)\n"),
        )

    def test_empty_program(self):
        self.assertEqual(p.normalise_program(""), "")
        self.assertEqual(p.program_line_count(""), 0)


class TestEncoding(unittest.TestCase):
    def test_round_trip(self):
        frame = p.encode_frame(0x2A, p.KIND_DATA, "print('hi')")
        reader = p.FrameReader()
        frames, damage = reader.feed(frame)
        self.assertEqual(damage, 0)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].seq, 0x2A)
        self.assertEqual(frames[0].kind, p.KIND_DATA)
        self.assertEqual(frames[0].text(), "print('hi')")

    def test_header_is_fixed_width(self):
        for payload in (b"", b"x", b"y" * p.MAX_PAYLOAD):
            frame = p.encode_frame(0, p.KIND_DATA, payload)
            self.assertEqual(len(frame), p.frame_length(len(payload)))
            self.assertEqual(frame[0], p.SOH)
            self.assertEqual(frame[-1], p.LF)
            self.assertEqual(frame[10:11], b":")

    def test_payload_may_not_contain_sentinels(self):
        with self.assertRaises(p.ProtocolError):
            p.encode_frame(0, p.KIND_DATA, b"a\x01b")
        with self.assertRaises(p.ProtocolError):
            p.encode_frame(0, p.KIND_DATA, b"a\nb")

    def test_oversized_payload_rejected(self):
        with self.assertRaises(p.ProtocolError):
            p.encode_frame(0, p.KIND_DATA, b"x" * (p.MAX_PAYLOAD + 1))

    def test_sequence_wraps(self):
        frame = p.encode_frame(300, p.KIND_DATA, b"")
        frames, _ = p.FrameReader().feed(frame)
        self.assertEqual(frames[0].seq, 300 % 256)

    def test_split_line_respects_character_boundaries(self):
        """A split must never land mid-codepoint."""
        line = "# " + "é" * 200  # two bytes each
        pieces = p.split_line(line)
        rebuilt = b"".join(payload for _, payload in pieces)
        self.assertEqual(rebuilt.decode(), line)
        for _, payload in pieces:
            payload.decode()  # would raise if split mid-character
        self.assertEqual(pieces[-1][0], p.KIND_DATA)
        for kind, _ in pieces[:-1]:
            self.assertEqual(kind, p.KIND_CONTINUE)

    def test_split_line_handles_four_byte_characters(self):
        line = "\U0001f916" * 60
        pieces = p.split_line(line)
        self.assertEqual(
            b"".join(payload for _, payload in pieces).decode(), line
        )

    def test_encode_program_brackets_body_with_begin_and_end(self):
        frames = p.encode_program("a = 1\nb = 2\n")
        reader = p.FrameReader()
        decoded, damage = reader.feed(b"".join(frames))
        self.assertEqual(damage, 0)
        self.assertEqual(
            [f.kind for f in decoded],
            [p.KIND_BEGIN, p.KIND_DATA, p.KIND_DATA, p.KIND_END],
        )
        self.assertEqual([f.seq for f in decoded], [0, 1, 2, 3])

        count, checksum = p.parse_begin(decoded[0].payload)
        self.assertEqual(count, 2)
        self.assertEqual(checksum, p.program_checksum("a = 1\nb = 2\n"))
        self.assertEqual(p.parse_end(decoded[-1].payload), checksum)

    def test_command_payload_is_never_confused_with_data(self):
        """The whole point: a data frame spelling a command stays data."""
        frames = p.encode_program("x03ENDUPLD\nafter = 1\n")
        decoded, _ = p.FrameReader().feed(b"".join(frames))
        body = [f for f in decoded if f.kind in p.DATA_KINDS]
        self.assertEqual([f.text() for f in body], ["x03ENDUPLD", "after = 1"])

    def test_flow_frames_round_trip(self):
        frame = p.encode_flow(7, p.KIND_ACK, 0x2B, 2048)
        decoded, _ = p.FrameReader().feed(frame)
        self.assertEqual(decoded[0].kind, p.KIND_ACK)
        self.assertEqual(p.parse_flow(decoded[0].payload), (0x2B, 2048))


class TestFrameReader(unittest.TestCase):
    def setUp(self):
        self.reader = p.FrameReader()

    def test_split_across_arbitrary_boundaries(self):
        """A frame arriving one byte at a time must still decode exactly once."""
        frame = p.encode_frame(5, p.KIND_DATA, b"chunked delivery")
        out = []
        for index in range(len(frame)):
            frames, damage = self.reader.feed(frame[index : index + 1])
            self.assertEqual(damage, 0)
            out.extend(frames)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].text(), "chunked delivery")

    def test_leading_garbage_is_skipped(self):
        frame = p.encode_frame(1, p.KIND_DATA, b"payload")
        frames, _ = self.reader.feed(b"noise before the sentinel" + frame)
        self.assertEqual(len(frames), 1)

    def test_corrupt_checksum_is_reported_not_delivered(self):
        frame = bytearray(p.encode_frame(1, p.KIND_DATA, b"payload"))
        frame[HEADER := p.HEADER_LENGTH] ^= 0xFF  # flip a payload byte
        frames, damage = self.reader.feed(bytes(frame))
        self.assertEqual(frames, [])
        self.assertEqual(damage, 1)

    def test_truncated_frame_is_detected_when_the_next_one_arrives(self):
        """A dropped BLE packet truncates a frame; the next SOH proves it."""
        first = p.encode_frame(1, p.KIND_DATA, b"a" * 60)
        second = p.encode_frame(2, p.KIND_DATA, b"intact")
        frames, damage = self.reader.feed(first[:30] + second)
        self.assertEqual(damage, 1)
        self.assertEqual([f.text() for f in frames], ["intact"])

    def test_recovers_after_damage(self):
        good = p.encode_frame(9, p.KIND_DATA, b"recovered")
        bad = bytearray(p.encode_frame(8, p.KIND_DATA, b"broken"))
        bad[6] = ord("z") if bad[6] != ord("z") else ord("a")
        frames, damage = self.reader.feed(bytes(bad) + good)
        self.assertGreaterEqual(damage, 1)
        self.assertIn("recovered", [f.text() for f in frames])

    def test_unterminated_sentinel_run_does_not_pin_the_buffer(self):
        """The failure mode the old JSON framer had: unbounded growth."""
        reader = p.FrameReader(max_buffer=256)
        for _ in range(200):
            reader.feed(b"\x01" + b"x" * 40)
        self.assertLessEqual(len(reader.buffer), 512)

    def test_stream_with_no_sentinel_is_discarded(self):
        frames, damage = self.reader.feed(b"just plain text, no frames here")
        self.assertEqual(frames, [])
        self.assertEqual(len(self.reader.buffer), 0)


class TestSequencedReceiver(unittest.TestCase):
    def test_in_order_frames_are_accepted(self):
        receiver = p.SequencedReceiver()
        for seq in range(10):
            verdict = receiver.accept(p.Frame(seq, p.KIND_DATA, b"x"))
            self.assertEqual(verdict, "accept")
        self.assertEqual(receiver.expected, 10)

    def test_gap_triggers_discard(self):
        receiver = p.SequencedReceiver()
        receiver.accept(p.Frame(0, p.KIND_DATA, b"x"))
        self.assertEqual(receiver.accept(p.Frame(5, p.KIND_DATA, b"x")), "gap")
        self.assertTrue(receiver.needs_nak())
        self.assertEqual(receiver.expected, 1)

    def test_frames_after_a_gap_are_still_refused(self):
        """Go-back-N: accepting them would leave a hole mid-program."""
        receiver = p.SequencedReceiver()
        receiver.accept(p.Frame(0, p.KIND_DATA, b"x"))
        receiver.accept(p.Frame(5, p.KIND_DATA, b"x"))
        self.assertEqual(receiver.accept(p.Frame(6, p.KIND_DATA, b"x")), "gap")

    def test_resend_after_a_gap_is_accepted(self):
        receiver = p.SequencedReceiver()
        receiver.accept(p.Frame(0, p.KIND_DATA, b"x"))
        receiver.accept(p.Frame(5, p.KIND_DATA, b"x"))
        self.assertEqual(receiver.accept(p.Frame(1, p.KIND_DATA, b"x")), "accept")
        self.assertFalse(receiver.needs_nak())

    def test_duplicate_is_recognised(self):
        receiver = p.SequencedReceiver()
        for seq in range(5):
            receiver.accept(p.Frame(seq, p.KIND_DATA, b"x"))
        self.assertEqual(
            receiver.accept(p.Frame(2, p.KIND_DATA, b"x")), "duplicate"
        )
        self.assertEqual(receiver.expected, 5)

    def test_acks_every_batch(self):
        receiver = p.SequencedReceiver(ack_every=8)
        for seq in range(7):
            receiver.accept(p.Frame(seq, p.KIND_DATA, b"x"))
        self.assertFalse(receiver.should_ack())
        receiver.accept(p.Frame(7, p.KIND_DATA, b"x"))
        self.assertTrue(receiver.should_ack())
        self.assertEqual(receiver.take_ack(), (8, p.DEFAULT_CREDIT))
        self.assertFalse(receiver.should_ack())

    def test_sequence_wraparound(self):
        receiver = p.SequencedReceiver()
        receiver.reset(254)
        for seq in (254, 255, 0, 1):
            self.assertEqual(
                receiver.accept(p.Frame(seq, p.KIND_DATA, b"x")), "accept"
            )
        self.assertEqual(receiver.expected, 2)


class TestCreditWindow(unittest.TestCase):
    def test_credit_bounds_what_goes_out(self):
        frames = [p.encode_frame(i, p.KIND_DATA, b"x" * 88) for i in range(20)]
        window = p.CreditWindow(frames, credit=300)
        ready = window.ready()
        self.assertEqual(len(ready), 3)  # 100 bytes each
        window.advance(len(ready))
        self.assertEqual(window.ready(), [])

    def test_ack_frees_the_window(self):
        frames = [p.encode_frame(i, p.KIND_DATA, b"x" * 88) for i in range(20)]
        window = p.CreditWindow(frames, credit=300)
        window.advance(3)
        window.on_ack(3, 300)
        self.assertEqual(len(window.ready()), 3)

    def test_nak_rewinds(self):
        frames = [p.encode_frame(i, p.KIND_DATA, b"x") for i in range(10)]
        window = p.CreditWindow(frames, credit=10000)
        window.advance(10)
        self.assertTrue(window.on_nak(4))
        self.assertEqual(window.next_index, 4)
        self.assertEqual(window.retransmits, 1)

    def test_duplicate_nak_does_not_rewind_twice(self):
        frames = [p.encode_frame(i, p.KIND_DATA, b"x") for i in range(10)]
        window = p.CreditWindow(frames, credit=10000)
        window.advance(10)
        window.on_nak(4)
        self.assertFalse(window.on_nak(4))
        self.assertEqual(window.retransmits, 1)

    def test_completes_when_everything_is_acked(self):
        frames = [p.encode_frame(i, p.KIND_DATA, b"x") for i in range(5)]
        window = p.CreditWindow(frames, credit=10000)
        window.advance(5)
        self.assertFalse(window.complete())
        window.on_ack(5, 2048)
        self.assertTrue(window.complete())


class TestAdaptivePacer(unittest.TestCase):
    def test_floor_is_the_uart_drain_time(self):
        """The floor is arithmetic, not a tuning guess."""
        drain_ms = p.BLE_CHUNK_SIZE * 1000 / p.UART_BYTES_PER_SECOND
        self.assertGreater(p.MIN_CHUNK_DELAY_MS, drain_ms)
        self.assertLess(p.MIN_CHUNK_DELAY_MS, drain_ms + 2)

    def test_clean_link_probes_down_to_the_floor_and_stops(self):
        pacer = p.AdaptivePacer()
        for _ in range(500):
            pacer.on_clean_batch()
        self.assertEqual(pacer.delay_ms, p.MIN_CHUNK_DELAY_MS)

    def test_never_goes_below_the_floor(self):
        """Below the floor, overflow is arithmetic. It must be unreachable."""
        pacer = p.AdaptivePacer(delay_ms=p.MIN_CHUNK_DELAY_MS)
        for _ in range(50):
            pacer.on_clean_batch()
            self.assertGreaterEqual(pacer.delay_ms, p.MIN_CHUNK_DELAY_MS)

    def test_probing_needs_a_streak(self):
        pacer = p.AdaptivePacer(delay_ms=40, clean_before_probe=3)
        pacer.on_clean_batch()
        pacer.on_clean_batch()
        self.assertEqual(pacer.delay_ms, 40)
        pacer.on_clean_batch()
        self.assertLess(pacer.delay_ms, 40)

    def test_loss_backs_off_multiplicatively(self):
        pacer = p.AdaptivePacer(delay_ms=30)
        pacer.on_loss()
        self.assertGreater(pacer.delay_ms, 30)
        self.assertLessEqual(pacer.delay_ms, p.MAX_CHUNK_DELAY_MS)

    def test_one_backoff_per_loss_episode(self):
        """Go-back-N NAKs a whole run of frames for one bad patch of radio.

        Treating each NAK as its own congestion signal compounded the factor
        repeatedly and sent the delay to the ceiling for something that
        warranted a single step. Measured on hardware as a 38% goodput
        regression against a fixed delay.
        """
        pacer = p.AdaptivePacer(delay_ms=21)
        for _ in range(6):
            pacer.on_loss()

        self.assertEqual(pacer.backoffs, 1)
        self.assertEqual(pacer.episodes_ignored, 5)
        self.assertLess(pacer.delay_ms, 30)

    def test_a_clean_batch_rearms_the_backoff(self):
        pacer = p.AdaptivePacer(delay_ms=21)
        pacer.on_loss()
        pacer.on_loss()
        self.assertEqual(pacer.backoffs, 1)

        pacer.on_clean_batch()
        pacer.on_loss()
        self.assertEqual(pacer.backoffs, 2)

    def test_recovery_time_is_independent_of_how_far_it_backed_off(self):
        """A fixed step made recovery from the ceiling longer than an upload."""
        def probes_back_to_floor(start):
            pacer = p.AdaptivePacer(delay_ms=start)
            probes = 0
            while pacer.delay_ms > p.MIN_CHUNK_DELAY_MS and probes < 1000:
                pacer.on_clean_batch()
                pacer.on_clean_batch()
                probes += 1
            return probes

        from_ceiling = probes_back_to_floor(p.MAX_CHUNK_DELAY_MS)
        self.assertLess(from_ceiling, 20, "recovery from the ceiling too slow")
        self.assertLess(probes_back_to_floor(26), 5)

    def test_tracks_where_it_spent_its_time(self):
        """Min and max cannot show this, and it is what diagnosed the bug."""
        pacer = p.AdaptivePacer(delay_ms=40)
        for _ in range(3):
            pacer.delay_seconds()
        self.assertEqual(pacer.mean_delay_ms(), 40.0)

        pacer.on_loss()
        for _ in range(1):
            pacer.delay_seconds()
        self.assertGreater(pacer.mean_delay_ms(), 40.0)

    def test_loss_resets_the_clean_streak(self):
        """Otherwise a single clean batch after loss would probe straight back."""
        pacer = p.AdaptivePacer(delay_ms=40, clean_before_probe=2)
        pacer.on_clean_batch()
        pacer.on_loss()
        before = pacer.delay_ms
        pacer.on_clean_batch()
        self.assertEqual(pacer.delay_ms, before)

    def test_capped_above(self):
        pacer = p.AdaptivePacer(delay_ms=p.MAX_CHUNK_DELAY_MS)
        self.assertFalse(pacer.on_loss())
        self.assertEqual(pacer.delay_ms, p.MAX_CHUNK_DELAY_MS)

    def test_converges_around_a_links_true_capacity(self):
        """Simulate a link that loses below `capacity` and check where it sits.

        Loss arrives in bursts of NAKs, because that is what go-back-N produces
        from one bad patch. An earlier version of this test used a single loss
        per event and passed while the controller was badly broken on hardware.
        """
        for capacity in (24, 28, 34, 45):
            pacer = p.AdaptivePacer()
            seen = []
            for _ in range(400):
                if pacer.delay_ms < capacity:
                    for _ in range(6):
                        pacer.on_loss()
                else:
                    pacer.on_clean_batch()
                seen.append(pacer.delay_ms)

            settled = seen[-100:]
            self.assertGreaterEqual(
                min(settled), p.MIN_CHUNK_DELAY_MS,
                "capacity %d: went below the floor" % capacity,
            )
            average = sum(settled) / len(settled)
            self.assertLess(
                abs(average - capacity), capacity * 0.35,
                "capacity %d: settled at %.1f, too far off" % (capacity, average),
            )
            self.assertLessEqual(
                max(settled), capacity * 1.6,
                "capacity %d: spiked to %d, a burst is one episode"
                % (capacity, max(settled)),
            )

    def test_state_survives_for_the_next_upload(self):
        """The pacer belongs to the connection, not the upload."""
        pacer = p.AdaptivePacer()
        for _ in range(20):
            pacer.on_clean_batch()
        learned = pacer.delay_ms
        self.assertLess(learned, p.START_CHUNK_DELAY_MS)
        # A second upload keeps going from here rather than restarting at 40.
        pacer.on_clean_batch()
        self.assertLessEqual(pacer.delay_ms, learned)


class LossyLink:
    """Seeded channel that drops, truncates and duplicates whole BLE chunks.

    Loss is applied per 20-byte chunk because that is how BLE actually fails:
    an entire write-without-response goes missing, not scattered bits.
    """

    CHUNK = 20

    def __init__(self, seed, drop=0.0, truncate=0.0, duplicate=0.0):
        self.rng = random.Random(seed)
        self.drop = drop
        self.truncate = truncate
        self.duplicate = duplicate
        self.dropped = 0

    def transmit(self, data):
        out = bytearray()
        for offset in range(0, len(data), self.CHUNK):
            chunk = data[offset : offset + self.CHUNK]
            roll = self.rng.random()
            if roll < self.drop:
                self.dropped += 1
                continue
            if roll < self.drop + self.truncate and len(chunk) > 4:
                out.extend(chunk[: self.rng.randrange(1, len(chunk))])
                self.dropped += 1
                continue
            out.extend(chunk)
            if self.rng.random() < self.duplicate:
                out.extend(chunk)
        return bytes(out)


def run_upload(text, link, max_rounds=400):
    """Drive a full upload over a lossy link and return what the receiver stored.

    A compressed model of both endpoints: the sender's credit window and the
    receiver's sequence checks, with the link in between. Enough to prove
    go-back-N converges without needing hardware.
    """
    frames = p.encode_program(text)
    window = p.CreditWindow(frames, credit=p.INITIAL_CREDIT)
    receiver = p.SequencedReceiver()
    reader = p.FrameReader()

    stored = []
    partial = b""
    expected_checksum = None
    verified = None

    for _ in range(max_rounds):
        if window.complete():
            break

        batch = window.ready()
        if not batch:
            # Nothing may go out, so the sender would be waiting on an ACK that
            # was lost. Retransmit from the base, which is the timeout path.
            window.next_index = window.base
            batch = window.ready()
            if not batch:
                break

        window.advance(len(batch))
        received, damage = reader.feed(link.transmit(b"".join(batch)))
        receiver.note_corruption(damage)

        for frame in received:
            verdict = receiver.accept(frame)
            if verdict != "accept":
                continue

            if frame.kind == p.KIND_BEGIN:
                stored = []
                partial = b""
                _, expected_checksum = p.parse_begin(frame.payload)
            elif frame.kind == p.KIND_CONTINUE:
                partial += frame.payload
            elif frame.kind == p.KIND_DATA:
                stored.append((partial + frame.payload).decode())
                partial = b""
            elif frame.kind == p.KIND_END:
                text_stored = "".join(line + "\n" for line in stored)
                verified = (
                    p.crc32(text_stored.encode()) & 0xFFFFFFFF
                ) == p.parse_end(frame.payload)

        if receiver.needs_nak():
            window.on_nak(receiver.expected)
        else:
            expected, credit = receiver.take_ack()
            window.on_ack(expected, credit)

    return "".join(line + "\n" for line in stored), verified, expected_checksum


class TestLossyLink(unittest.TestCase):
    PROGRAM = "\n".join(
        ["# lossy link corpus"]
        + ["value_%03d = %d" % (i, i * 7) for i in range(60)]
        + ["print('done')"]
    )

    def test_perfect_link_delivers_exactly(self):
        stored, verified, _ = run_upload(self.PROGRAM, LossyLink(1))
        self.assertEqual(stored, p.normalise_program(self.PROGRAM))
        self.assertTrue(verified)

    def test_recovers_from_heavy_loss(self):
        """20% of chunks dropped and it still delivers byte-exact."""
        for seed in range(8):
            link = LossyLink(seed, drop=0.20)
            stored, verified, _ = run_upload(self.PROGRAM, link)
            self.assertEqual(
                stored,
                p.normalise_program(self.PROGRAM),
                "seed %d lost data (%d chunks dropped)" % (seed, link.dropped),
            )
            self.assertTrue(verified, "seed %d failed verification" % seed)
            self.assertGreater(link.dropped, 0, "seed %d dropped nothing" % seed)

    def test_recovers_from_truncation_and_duplication(self):
        for seed in range(8):
            link = LossyLink(seed, drop=0.08, truncate=0.08, duplicate=0.08)
            stored, verified, _ = run_upload(self.PROGRAM, link)
            self.assertEqual(stored, p.normalise_program(self.PROGRAM))
            self.assertTrue(verified)

    def test_long_lines_survive_loss(self):
        program = "\n".join("x%d = '%s'" % (i, "y" * 300) for i in range(10))
        stored, verified, _ = run_upload(program, LossyLink(3, drop=0.15))
        self.assertEqual(stored, p.normalise_program(program))
        self.assertTrue(verified)

    def test_unicode_survives_loss(self):
        program = "\n".join(
            ["# café naïve 你好 \U0001f916" * 3 for _ in range(10)]
        )
        stored, verified, _ = run_upload(program, LossyLink(4, drop=0.15))
        self.assertEqual(stored, p.normalise_program(program))
        self.assertTrue(verified)

    def test_command_text_in_user_code_is_stored_not_executed(self):
        """The injection the old protocol could not survive."""
        program = "\n".join(
            [
                "print('before')",
                "x02BEGINUPLD",
                "x03ENDUPLD",
                "x04STARTPROG",
                "print('after')",
            ]
        )
        stored, verified, _ = run_upload(program, LossyLink(5, drop=0.10))
        self.assertEqual(stored, p.normalise_program(program))
        self.assertTrue(verified)
        self.assertIn("x04STARTPROG", stored)


if __name__ == "__main__":
    unittest.main(verbosity=2)
