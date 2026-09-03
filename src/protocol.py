"""Framed, sequenced, credit-paced transport for the Ro/Box link.

Wire format and rationale: docs/PROTOCOL.md. Imports nothing from machine, so
it also runs on CPython for the conformance tests.
"""

from binascii import crc32

SOH = 0x01
LF = 0x0A

#: SOH + seq(2) + len(2) + crc(4) + kind(1) + ':'
HEADER_LENGTH = 11
FRAME_OVERHEAD = HEADER_LENGTH + 1

#: Six BLE chunks including overhead.
MAX_PAYLOAD = 108

# Control and data are told apart here, never by inspecting payload bytes.
KIND_DATA = "D"      # program text, ends a line
KIND_CONTINUE = "C"  # program text, line continues next frame
KIND_BEGIN = "B"     # start upload; payload carries expected totals
KIND_END = "E"       # finish upload; payload carries program CRC
KIND_COMMAND = "X"   # control command, by name
KIND_ACK = "A"       # cumulative ack + credit grant
KIND_NAK = "N"       # gap or bad checksum; resend from here
KIND_REPLY = "R"     # device to host message (JSON)

DATA_KINDS = (KIND_DATA, KIND_CONTINUE)

#: Payloads a COMMAND frame may carry. Anything else is refused, so a malformed
#: frame cannot reach the dispatcher.
COMMAND_NAMES = (
    "firmware_check",
    "start_program",
    "calibrate_color",
    "reset_device",
    "boot_loader",
    "disconnect_device",
)

SEQUENCE_MODULO = 256

#: Ack after this many accepted frames. The amortisation factor.
ACK_EVERY = 8

#: Also ack after this long idle, so a final partial batch is not left waiting.
ACK_IDLE_MS = 50

#: Half the UART receive buffer, so a full window cannot overflow it.
DEFAULT_CREDIT = 2048

#: What the sender assumes before the first ACK.
INITIAL_CREDIT = 512

# --- pacing ----------------------------------------------------------------
#
# Credit bounds what the *board* buffers. It says nothing about the HM-10 in
# between, which drains to the board over a 9600 baud UART and has a small
# buffer of its own, so writes have to be paced as well.

#: Bytes per BLE write-without-response.
BLE_CHUNK_SIZE = 20

#: HM-10 UART egress: 9600 baud, 8N1, ten bits on the wire per byte.
UART_BYTES_PER_SECOND = 960

#: The floor, and it is arithmetic rather than a guess: the time the module
#: needs to forward one chunk to the board. Pace faster than this and bytes are
#: offered faster than they can leave, so overflow is certain given enough of
#: them. Measured sweeps agree: 25ms held with strain, 20ms collapsed to a
#: seventh of the goodput at 7x the wire traffic.
MIN_CHUNK_DELAY_MS = int(
    BLE_CHUNK_SIZE * 1000 / UART_BYTES_PER_SECOND
) + 1

#: Where a fresh connection starts. Deliberately well clear of the floor: the
#: first upload should be safe, not fast.
START_CHUNK_DELAY_MS = 40

#: Ceiling. Past this the link is not slow, it is broken, and crawling is worse
#: than reporting a failure.
MAX_CHUNK_DELAY_MS = 80

#: Clean acknowledged batches required before probing faster.
CLEAN_BATCHES_BEFORE_PROBE = 2

#: Fraction shaved per probe. Proportional rather than a fixed step so the time
#: to recover from a backoff does not depend on how far it went: a fixed 2ms
#: step needed eighty acknowledgements to come back from 100ms, which is longer
#: than most uploads, so the controller spent its life crawling downhill.
PROBE_FRACTION = 0.10

#: Multiplier applied per loss *episode*.
BACKOFF_FACTOR = 1.25


class ProtocolError(Exception):
    pass


# --- checksums -------------------------------------------------------------

def frame_checksum(seq, kind, payload):
    """CRC over seq and kind as well as payload; a bad header misroutes."""
    header = bytes((seq & 0xFF, len(payload)))
    return crc32(header + kind.encode() + payload) & 0xFFFF


def normalise_program(text):
    """Canonical form both ends checksum: LF endings, one trailing newline."""
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    if not unified:
        return ""

    lines = unified.split("\n")
    # split() leaves a trailing empty element when the text ended in a newline;
    # that is not a blank line of its own.
    if lines[-1] == "":
        lines.pop()
    if not lines:
        return ""

    return "".join(line + "\n" for line in lines)


def program_checksum(text):
    """Full CRC-32 of the normalised program, for the end to end check."""
    return crc32(normalise_program(text).encode()) & 0xFFFFFFFF


def program_line_count(text):
    return normalise_program(text).count("\n")


# --- encoding --------------------------------------------------------------

def encode_frame(seq, kind, payload=b""):
    """Build one frame. `payload` may be str or bytes."""
    if isinstance(payload, str):
        payload = payload.encode()

    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError(
            "payload of %d exceeds MAX_PAYLOAD %d" % (len(payload), MAX_PAYLOAD)
        )
    if SOH in payload or LF in payload:
        raise ProtocolError("payload may not contain SOH or LF")
    if len(kind) != 1:
        raise ProtocolError("kind must be a single character")

    header = "%c%02x%02x%04x%s:" % (
        SOH,
        seq & 0xFF,
        len(payload),
        frame_checksum(seq, kind, payload),
        kind,
    )
    return header.encode() + payload + b"\n"


def frame_length(payload_length):
    return FRAME_OVERHEAD + payload_length


def split_payload(text, final_kind, limit=MAX_PAYLOAD):
    """Break text into frame-sized payloads, never splitting mid-character.

    All but the last piece are KIND_CONTINUE.
    """
    encoded = text.encode() if isinstance(text, str) else text
    if len(encoded) <= limit:
        return [(final_kind, encoded)]

    pieces = []
    offset = 0
    while offset < len(encoded):
        end = min(offset + limit, len(encoded))
        while (
            end > offset + 1
            and end < len(encoded)
            and (encoded[end] & 0xC0) == 0x80
        ):
            end -= 1
        pieces.append(encoded[offset:end])
        offset = end

    return [
        (final_kind if index == len(pieces) - 1 else KIND_CONTINUE, piece)
        for index, piece in enumerate(pieces)
    ]


def split_line(line, limit=MAX_PAYLOAD):
    """Break one source line into frame-sized payloads, ending in DATA."""
    return split_payload(line, KIND_DATA, limit)


def encode_program(text, start_seq=0):
    """Frames for a whole upload: BEGIN, body, END."""
    normalised = normalise_program(text)
    lines = normalised.split("\n")[:-1] if normalised else []
    checksum = program_checksum(text)

    frames = []
    seq = start_seq & 0xFF

    def add(kind, payload):
        nonlocal seq
        frames.append(encode_frame(seq, kind, payload))
        seq = (seq + 1) % SEQUENCE_MODULO

    add(KIND_BEGIN, "%d,%08x" % (len(lines), checksum))
    for line in lines:
        for kind, payload in split_line(line):
            add(kind, payload)
    add(KIND_END, "%08x" % checksum)

    return frames


def _text(payload):
    return payload.decode() if isinstance(payload, bytes) else payload


def parse_begin(payload):
    """(line_count, checksum) from a BEGIN payload."""
    count, checksum = _text(payload).split(",", 1)
    return int(count), int(checksum, 16)


def parse_end(payload):
    return int(_text(payload), 16)


def encode_flow(seq, kind, expected_seq, credit):
    """An ACK or NAK. Both carry the next sequence the receiver wants."""
    payload = "%02x,%04x" % (expected_seq & 0xFF, credit)
    return encode_frame(seq, kind, payload.encode())


def parse_flow(payload):
    expected, credit = _text(payload).split(",", 1)
    return int(expected, 16), int(credit, 16)


# --- decoding --------------------------------------------------------------

def _parse_hex(data, offset, width):
    """Parse `width` ASCII hex digits from a buffer, or -1 if not hex."""
    # Hand-rolled: MicroPython's int() will not accept a bytes or bytearray
    # slice, unlike CPython's. Mirrors parseHex in frames.ts.
    value = 0
    for index in range(offset, offset + width):
        byte = data[index]
        if 0x30 <= byte <= 0x39:
            digit = byte - 0x30
        elif 0x61 <= byte <= 0x66:
            digit = byte - 0x61 + 10
        elif 0x41 <= byte <= 0x46:
            digit = byte - 0x41 + 10
        else:
            return -1
        value = value * 16 + digit
    return value


class Frame:
    __slots__ = ("seq", "kind", "payload")

    def __init__(self, seq, kind, payload):
        self.seq = seq
        self.kind = kind
        self.payload = payload

    def text(self):
        return self.payload.decode()

    def __repr__(self):
        return "Frame(seq=%d, kind=%s, payload=%r)" % (
            self.seq,
            self.kind,
            self.payload,
        )


class FrameReader:
    """Pulls frames from a lossy stream. `feed` returns (frames, damage)."""

    def __init__(self, max_buffer=4096):
        # Advanced by slice reassignment rather than `del buffer[:n]`:
        # MicroPython bytearrays do not support item deletion.
        self.buffer = bytearray()
        self.max_buffer = max_buffer
        self.damaged = 0
        self.resyncs = 0

    def feed(self, data):
        if data:
            self.buffer.extend(data)

        # Accumulating garbage rather than frames; drop to the newest SOH so a
        # corrupt run cannot pin the buffer for the rest of the session.
        if len(self.buffer) > self.max_buffer:
            last = self.buffer.rfind(bytes((SOH,)))
            self.buffer = self.buffer[last if last > 0 else len(self.buffer) :]
            self.resyncs += 1

        frames = []
        damage = 0

        while True:
            start = self.buffer.find(bytes((SOH,)))
            if start == -1:
                self.buffer = bytearray()
                break
            if start > 0:
                # Debris from a lost frame.
                self.buffer = self.buffer[start:]

            if len(self.buffer) < HEADER_LENGTH:
                break

            seq = _parse_hex(self.buffer, 1, 2)
            length = _parse_hex(self.buffer, 3, 2)
            checksum = _parse_hex(self.buffer, 5, 4)

            if seq < 0 or length < 0 or checksum < 0:
                damage += 1
                self._resync()
                continue

            kind = chr(self.buffer[9])
            if self.buffer[10:11] != b":" or length > MAX_PAYLOAD:
                damage += 1
                self._resync()
                continue

            total = HEADER_LENGTH + length + 1
            if len(self.buffer) < total:
                # Still arriving, or truncated by a lost packet. A later SOH
                # proves the latter, since payloads cannot contain one.
                if self.buffer.find(bytes((SOH,)), 1) != -1:
                    damage += 1
                    self._resync()
                    continue
                break

            if self.buffer[total - 1] != LF:
                damage += 1
                self._resync()
                continue

            payload = bytes(self.buffer[HEADER_LENGTH : total - 1])
            if frame_checksum(seq, kind, payload) != checksum:
                damage += 1
                self._resync()
                continue

            frames.append(Frame(seq, kind, payload))
            self.buffer = self.buffer[total:]

        self.damaged += damage
        return frames, damage

    def _resync(self):
        """Skip this sentinel, hunt for the next."""
        next_start = self.buffer.find(bytes((SOH,)), 1)
        self.buffer = (
            bytearray() if next_start == -1 else self.buffer[next_start:]
        )
        self.resyncs += 1


# --- sequence verification -------------------------------------------------

def sequence_distance(later, earlier):
    """Forward distance, accounting for wraparound."""
    return (later - earlier) % SEQUENCE_MODULO


class SequencedReceiver:
    """Go-back-N receiver: accepts in-order frames, discards after a gap."""

    def __init__(self, credit=DEFAULT_CREDIT, ack_every=ACK_EVERY):
        self.expected = 0
        self.credit = credit
        self.ack_every = ack_every
        self.since_ack = 0
        self.discarding = False
        self.accepted = 0
        self.duplicates = 0
        self.gaps = 0
        self.corrupt = 0

    def reset(self, expected=0):
        self.expected = expected % SEQUENCE_MODULO
        self.since_ack = 0
        self.discarding = False

    def accept(self, frame):
        """Classify one frame as "accept", "duplicate" or "gap"."""
        if frame.seq == self.expected:
            self.expected = (self.expected + 1) % SEQUENCE_MODULO
            self.since_ack += 1
            self.discarding = False
            self.accepted += 1
            return "accept"

        # From before the window: the sender resent because our ACK went
        # missing. Harmless, re-acknowledge.
        if sequence_distance(self.expected, frame.seq) < SEQUENCE_MODULO // 2:
            self.duplicates += 1
            return "duplicate"

        self.gaps += 1
        self.discarding = True
        return "gap"

    def note_corruption(self, count=1):
        """A damaged frame leaves the next sequence number unknown."""
        if count:
            self.corrupt += count
            self.discarding = True

    def should_ack(self):
        return self.since_ack >= self.ack_every

    def needs_nak(self):
        return self.discarding

    def take_ack(self):
        self.since_ack = 0
        return self.expected, self.credit


class AdaptivePacer:
    """Additive-decrease, multiplicative-increase controller for chunk delay.

    Lives on the connection, not the upload, so a second upload keeps the tuning.
    """

    def __init__(
        self,
        delay_ms=START_CHUNK_DELAY_MS,
        minimum_ms=MIN_CHUNK_DELAY_MS,
        maximum_ms=MAX_CHUNK_DELAY_MS,
        clean_before_probe=CLEAN_BATCHES_BEFORE_PROBE,
        probe_fraction=PROBE_FRACTION,
        backoff=BACKOFF_FACTOR,
    ):
        self.minimum_ms = minimum_ms
        self.maximum_ms = maximum_ms
        self.clean_before_probe = clean_before_probe
        self.probe_fraction = probe_fraction
        self.backoff = backoff

        self.delay_ms = self._clamp(delay_ms)
        self.clean_streak = 0

        # One backoff per loss episode, not per lost frame. Go-back-N resends a
        # whole run of frames after a gap, so a single bad patch of radio draws
        # a NAK for each one. Reacting to every NAK compounded 1.25 six times
        # over and sent the delay to the ceiling for something that warranted
        # one step. Rearmed by the next clean acknowledgement, which is the
        # evidence the episode is over.
        self.armed = True

        # Reported so a run can be explained after the fact.
        self.probes = 0
        self.backoffs = 0
        self.episodes_ignored = 0
        self.fastest_ms = self.delay_ms
        self.slowest_ms = self.delay_ms

        # Time-weighted, via the chunks actually paced: where the controller
        # *spent* its time, which the min and max cannot show.
        self.paced_chunks = 0
        self.delay_ms_total = 0

    def _clamp(self, value):
        return max(self.minimum_ms, min(self.maximum_ms, value))

    def on_clean_batch(self):
        """A batch was acknowledged with nothing lost. Consider probing."""
        self.armed = True
        self.clean_streak += 1
        if self.clean_streak < self.clean_before_probe:
            return False

        self.clean_streak = 0
        if self.delay_ms <= self.minimum_ms:
            return False

        step = max(1, int(self.delay_ms * self.probe_fraction + 0.5))
        self.delay_ms = self._clamp(self.delay_ms - step)
        self.probes += 1
        self.fastest_ms = min(self.fastest_ms, self.delay_ms)
        return True

    def on_loss(self):
        """A NAK, a damaged frame, or a timeout. Backs off once per episode."""
        self.clean_streak = 0

        if not self.armed:
            self.episodes_ignored += 1
            return False

        self.armed = False

        if self.delay_ms >= self.maximum_ms:
            return False

        self.delay_ms = self._clamp(int(self.delay_ms * self.backoff + 0.5))
        self.backoffs += 1
        self.slowest_ms = max(self.slowest_ms, self.delay_ms)
        return True

    def delay_seconds(self):
        """The current delay, and a note that a chunk was paced by it."""
        self.paced_chunks += 1
        self.delay_ms_total += self.delay_ms
        return self.delay_ms / 1000.0

    def mean_delay_ms(self):
        if not self.paced_chunks:
            return None
        return round(self.delay_ms_total / self.paced_chunks, 1)

    def stats(self):
        return {
            "delay_ms": self.delay_ms,
            "mean_delay_ms": self.mean_delay_ms(),
            "fastest_ms": self.fastest_ms,
            "slowest_ms": self.slowest_ms,
            "probes": self.probes,
            "backoffs": self.backoffs,
            "episodes_ignored": self.episodes_ignored,
            "floor_ms": self.minimum_ms,
        }


class CreditWindow:
    """Sender-side flow control and retransmission bookkeeping."""

    def __init__(self, frames, start_seq=0, credit=INITIAL_CREDIT):
        self.frames = list(frames)
        self.start_seq = start_seq % SEQUENCE_MODULO
        self.credit = credit
        self.base = 0        # lowest unacknowledged frame
        self.next_index = 0  # next frame to transmit
        self.retransmits = 0

    def _index_for_seq(self, seq):
        return sequence_distance(seq, self.start_seq)

    def in_flight_bytes(self):
        return sum(len(f) for f in self.frames[self.base : self.next_index])

    def ready(self):
        """Frames that may go out now without exceeding the credit."""
        out = []
        budget = self.credit - self.in_flight_bytes()
        index = self.next_index
        while index < len(self.frames) and len(self.frames[index]) <= budget:
            out.append(self.frames[index])
            budget -= len(self.frames[index])
            index += 1
        return out

    def advance(self, count):
        self.next_index = min(len(self.frames), self.next_index + count)

    def on_ack(self, expected_seq, credit):
        """True when the acknowledgement actually moved the window."""
        self.credit = credit or self.credit

        index = self._index_for_seq(expected_seq)
        if index <= self.base:
            return False

        self.base = min(index, len(self.frames))
        if self.next_index < self.base:
            self.next_index = self.base
        return True

    def on_nak(self, expected_seq):
        """Rewind so transmission resumes where the receiver wants."""
        index = self._index_for_seq(expected_seq)
        if index >= len(self.frames):
            return False
        # Only ever rewind: a duplicate NAK for a gap already being resent must
        # not drag the window back twice.
        if index < self.next_index:
            self.next_index = index
            self.base = min(self.base, index)
            self.retransmits += 1
            return True
        return False

    def rewind_to_base(self):
        """Resend from the oldest unacknowledged frame. The timeout path."""
        # Counts as a retransmit, which is what bounds the caller's retry loop.
        if self.next_index > self.base:
            self.next_index = self.base
            self.retransmits += 1
            return True
        return False

    def complete(self):
        return self.base >= len(self.frames)
