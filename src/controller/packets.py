"""Controller-mode wire format: fixed 6-byte packets, host to board only.

    start(0xA5) type seq a b crc8(type..b)

Must stay byte-for-byte compatible with the website's `packets.ts`;
tests/vectors/controller.json (owned by the website) checks both sides.
"""

START = 0xA5
PACKET_LENGTH = 6

DRIVE = 0x01
EXIT = 0x04

MAX_SPEED = 100
TIMEOUT_MS = 500

# Unparsed bytes kept between reads. Generous next to one packet, but bounds
# what line noise with no 0xA5 in it can cost.
MAX_BUFFERED = 64


def _build_table():
    table = bytearray(256)
    for index in range(256):
        value = index
        for _ in range(8):
            if value & 0x80:
                value = ((value << 1) ^ 0x07) & 0xFF
            else:
                value = (value << 1) & 0xFF
        table[index] = value
    return bytes(table)


_CRC_TABLE = _build_table()


def crc8(data, start=0, end=None):
    """CRC-8/SMBUS: poly 0x07, init 0, no reflection, no final XOR."""
    if end is None:
        end = len(data)
    crc = 0
    table = _CRC_TABLE
    for index in range(start, end):
        crc = table[crc ^ data[index]]
    return crc


def speed(byte):
    """Two's-complement int8 to a speed, clamped to -100..100."""
    value = byte - 0x100 if byte & 0x80 else byte
    if value > MAX_SPEED:
        return MAX_SPEED
    if value < -MAX_SPEED:
        return -MAX_SPEED
    return value


def encode(packet_type, seq, a=0, b=0):
    """Build one packet. Host side; used by tests and tools."""
    body = bytes((packet_type & 0xFF, seq & 0xFF, a & 0xFF, b & 0xFF))
    return bytes((START,)) + body + bytes((crc8(body),))


class PacketParser:
    """Finds packets in a byte stream that may split or merge them."""

    def __init__(self):
        self.buffer = b""
        self.dropped = 0

    def feed(self, data):
        """Add bytes; return every valid (type, seq, a, b) now complete.

        Stops right after an EXIT: whatever follows it belongs to the framed
        protocol and stays buffered for `take_remainder`, untouched.
        """
        buffer = self.buffer + data if self.buffer else data
        packets = []
        index = 0
        length = len(buffer)

        while True:
            start = buffer.find(b"\xa5", index)
            if start < 0:
                self.dropped += length - index
                index = length
                break
            self.dropped += start - index
            index = start
            if length - index < PACKET_LENGTH:
                break
            if crc8(buffer, index + 1, index + 5) == buffer[index + 5]:
                packets.append(
                    (
                        buffer[index + 1],
                        buffer[index + 2],
                        buffer[index + 3],
                        buffer[index + 4],
                    )
                )
                index += PACKET_LENGTH
                if buffer[index - 5] == EXIT:
                    self.buffer = bytes(buffer[index:])
                    return packets
            else:
                # Only the start byte: a real packet may begin inside this one.
                self.dropped += 1
                index += 1

        rest = buffer[index:]
        if len(rest) > MAX_BUFFERED:
            self.dropped += len(rest) - MAX_BUFFERED
            rest = rest[-MAX_BUFFERED:]
        self.buffer = bytes(rest)
        return packets

    def take_remainder(self):
        """Hand back the unparsed bytes and forget them."""
        rest, self.buffer = self.buffer, b""
        return rest
