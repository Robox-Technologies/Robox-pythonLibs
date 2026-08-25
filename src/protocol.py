"""Framed, sequenced, credit-paced transport for the Ro/Box link.

The old protocol put commands and program text on one unframed channel and
compared every line against a command table, so a dropped packet turned code
into different runnable code, and user code equal to a command got executed.
Both come from interpreting payload bytes. Here they are never interpreted: a
frame carries an explicit kind, plus a length and checksum.

Credit-based batching with sequence verification: the sender transmits back to
back and the receiver returns one cumulative ACK per batch, so the round trip
is amortised over ACK_EVERY frames instead of paid per 20-byte chunk. Credit is
counted in bytes, because the UART receive buffer is what actually overflows.

Wire format, fixed-width header so partial frames are detectable at once:

    \\x01 SS LL CCCC K : <payload> \\n

SOH, sequence (2 hex), length (2 hex), checksum (4 hex), kind (1 char),
separator, payload, newline. Payloads exclude SOH and LF, so SOH is an
unambiguous resync point and one frame stays one line on the wire.

The checksum is the low 16 bits of CRC-32, chosen over a bespoke CRC-16 because
binascii.crc32 is native C on the board and standard everywhere else. Bit
corruption is already caught by the BLE link layer; what reaches here is whole
missing packets (caught by the sequence number) and truncation.

Imports nothing from machine, so it runs on CPython for the conformance tests.
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

SEQUENCE_MODULO = 256

#: Ack after this many accepted frames. The amortisation factor.
ACK_EVERY = 8

#: Also ack after this long idle, so a final partial batch is not left waiting.
ACK_IDLE_MS = 50

#: Half the UART receive buffer, so a full window cannot overflow it.
DEFAULT_CREDIT = 2048

#: What the sender assumes before the first ACK.
INITIAL_CREDIT = 512


class ProtocolError(Exception):
    pass


# --- checksums -------------------------------------------------------------

def frame_checksum(seq, kind, payload):
    """Check over the header fields and payload.

    Covers seq and kind too: a corrupted header misroutes a frame, which a
    payload-only checksum would not notice.
    """
    header = bytes((seq & 0xFF, len(payload)))
    return crc32(header + kind.encode() + payload) & 0xFFFF


def normalise_program(text):
    """Canonical form both ends checksum.

    The board stores lines newline-terminated and drops blank ones, so raw
    source would never match stored source even on a perfect link.
    """
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    kept = [line for line in unified.split("\n") if line.strip()]
    return "".join(line + "\n" for line in kept)


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


def split_line(line, limit=MAX_PAYLOAD):
    """Break one source line into frame-sized payloads.

    Splits on encoded bytes but never inside a multi-byte character, which
    would decode to a replacement character and corrupt the stored program.
    Returns [(kind, payload)], last piece KIND_DATA, earlier ones KIND_CONTINUE.
    """
    encoded = line.encode()
    if len(encoded) <= limit:
        return [(KIND_DATA, encoded)]

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
        (KIND_DATA if index == len(pieces) - 1 else KIND_CONTINUE, piece)
        for index, piece in enumerate(pieces)
    ]


def encode_program(text, start_seq=0):
    """Frames for a whole upload: BEGIN, body, END.

    BEGIN and END carry the line count and program CRC, so the receiver can
    verify the result independently of individual frame delivery.
    """
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
    return encode_frame(seq, kind, "%02x,%04x" % (expected_seq & 0xFF, credit))


def parse_flow(payload):
    expected, credit = _text(payload).split(",", 1)
    return int(expected, 16), int(credit, 16)


# --- decoding --------------------------------------------------------------

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
    """Pulls frames from a stream that may be missing pieces.

    `feed` returns (frames, damage). Damage is reported, not swallowed: "we
    lost something" is the signal the old protocol never had.
    """

    def __init__(self, max_buffer=4096):
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
            del self.buffer[: last if last > 0 else len(self.buffer)]
            self.resyncs += 1

        frames = []
        damage = 0

        while True:
            start = self.buffer.find(bytes((SOH,)))
            if start == -1:
                del self.buffer[:]
                break
            if start > 0:
                # Debris from a lost frame.
                del self.buffer[:start]

            if len(self.buffer) < HEADER_LENGTH:
                break

            try:
                seq = int(self.buffer[1:3], 16)
                length = int(self.buffer[3:5], 16)
                checksum = int(self.buffer[5:9], 16)
            except ValueError:
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
            del self.buffer[:total]

        self.damaged += damage
        return frames, damage

    def _resync(self):
        """Skip this sentinel, hunt for the next."""
        next_start = self.buffer.find(bytes((SOH,)), 1)
        if next_start == -1:
            del self.buffer[:]
        else:
            del self.buffer[:next_start]
        self.resyncs += 1


# --- sequence verification -------------------------------------------------

def sequence_distance(later, earlier):
    """Forward distance, accounting for wraparound."""
    return (later - earlier) % SEQUENCE_MODULO


class SequencedReceiver:
    """Accepts in-order frames, asks for a resend when one is missing.

    Go-back-N: on a gap or bad checksum the receiver discards everything after
    it, even intact frames, since accepting them would leave a hole mid-program.
    """

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


class CreditWindow:
    """Sender-side flow control and retransmission bookkeeping.

    Lives here as well as in the TypeScript client so the offline tests can
    drive both halves of the protocol against each other.
    """

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
        self.credit = credit or self.credit
        index = self._index_for_seq(expected_seq)
        if index > self.base:
            self.base = min(index, len(self.frames))
        if self.next_index < self.base:
            self.next_index = self.base

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

    def complete(self):
        return self.base >= len(self.frames)
