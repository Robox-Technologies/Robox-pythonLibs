from binascii import crc32

from .constants import (SOH, LF, HEADER_LENGTH, FRAME_OVERHEAD, MAX_PAYLOAD, KIND_CONTINUE, KIND_DATA, KIND_BEGIN, KIND_END, KIND_ACK, KIND_NAK, KIND_REPLY, SEQUENCE_MODULO)

class ProtocolError(Exception):
    pass


def frame_checksum(seq, kind, payload):
    """CRC over seq and kind as well as payload; a bad header misroutes."""
    header = bytes((seq & 0xFF, len(payload)))
    return crc32(header + kind.encode() + payload) & 0xFFFF


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


def _parse_hex(data, offset, width):
    """Parse `width` ASCII hex digits from a buffer, or -1 if not hex."""
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
        self.buffer = bytearray()
        self.max_buffer = max_buffer
        self.damaged = 0
        self.resyncs = 0

    def feed(self, data):
        if data:
            self.buffer.extend(data)

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
