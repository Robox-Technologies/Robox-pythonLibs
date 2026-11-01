from binascii import crc32

from .constants import (KIND_BEGIN, KIND_END, KIND_DATA, KIND_CONTINUE, MAX_PAYLOAD, SEQUENCE_MODULO)
from .frames import encode_frame

def normalise_program(text):
    """Canonical form both ends checksum: LF endings, one trailing newline."""
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    if not unified:
        return ""

    lines = unified.split("\n")
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


def split_payload(text, final_kind, limit=MAX_PAYLOAD):
    """Break text into frame-sized payloads without splitting UTF-8."""
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
