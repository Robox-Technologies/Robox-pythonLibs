"""The upload path as it exists today, reproduced faithfully for benchmarking.

This is the "before" side of the before/after comparison, so it deliberately
copies the current behaviour including its flaws: no sequence numbers, no
checksum, no retransmission, and a fire-and-forget write that never waits for
the board to confirm anything.
"""

import json
import re
import time

# Mirrors COMMANDS in Robox-Website/src/libs/communication/communicate.ts.
CMD_FIRMWARE_CHECK = "x01FIRMCHECK\r"
CMD_BEGIN_UPLOAD = "x02BEGINUPLD\r"
CMD_END_UPLOAD = "x03ENDUPLD\r"

# Mirrors COMMANDS in Robox-pythonLibs/src/main.py, but only the entries the
# main loop actually dispatches on. x07BOOTLOADER and x08DISCONNECT are in the
# firmware's table with no matching branch, so they fall through to the
# `elif out_file:` arm and get *stored* as program text -- which is itself a
# bug, and one this harness must not paper over.
HANDLED_COMMANDS = {
    "x01FIRMCHECK": "firmware_check",
    "x02BEGINUPLD": "begin_upload",
    "x03ENDUPLD": "end_upload",
    "x04STARTPROG": "start_program",
    "x05COLORCALIBRATE": "calibrate_color",
    "x06RESTART": "reset_device",
}

# In the firmware's COMMANDS table but never dispatched.
UNHANDLED_COMMANDS = ("x07BOOTLOADER", "x08DISCONNECT")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def simulate_firmware(lines):
    """Replay main.py's receive loop over `lines` and return what it stores.

    A faithful model of the dispatch chain in src/main.py, including the parts
    that are defects rather than features:

    * blank lines never reach the file (BLE read_line skips them; main.py's
      `if not line: continue` drops the USB equivalent);
    * a line equal to a handled command is executed, not stored -- so user code
      can hijack the protocol;
    * begin_upload re-opens the file, truncating anything already written;
    * once end_upload lands, later lines are silently discarded.

    Returns (stored_text, events) where events records each hijack.
    """
    stored = []
    out_open = False
    events = []

    for index, line in enumerate(lines):
        if not line.strip():
            continue
        command = HANDLED_COMMANDS.get(line.strip())

        # Mid-upload, the firmware honours only end_upload and treats every
        # other command-looking line as program text. That shrinks the
        # injection surface to one string; the framed protocol closes it.
        if out_open and command != "end_upload":
            command = None

        if command == "begin_upload":
            if stored:
                events.append((index, line.strip(), "truncated %d stored lines" % len(stored)))
            stored = []
            out_open = True
        elif command == "end_upload":
            if not out_open:
                events.append((index, line.strip(), "no upload in progress"))
            out_open = False
        elif command is not None:
            events.append((index, line.strip(), "executed as %s instead of stored" % command))
        elif out_open:
            stored.append(line)
        else:
            events.append((index, line.strip()[:32], "dropped: no upload open"))

    return "".join(line + "\n" for line in stored), events


def expected_program(code):
    """What the current firmware should store for `code` over a *perfect* link.

    A readback mismatch against this string therefore means bytes really went
    missing on the wire, rather than the protocol throwing them away by design.
    """
    lines = ["x02BEGINUPLD"] + code.split("\n") + ["x03ENDUPLD"]
    stored, _ = simulate_firmware(lines)
    return stored


def protocol_events(code):
    """Places where the protocol itself loses or reinterprets corpus data."""
    lines = ["x02BEGINUPLD"] + code.split("\n") + ["x03ENDUPLD"]
    _, events = simulate_firmware(lines)
    return events


def write_message(transport, message, stats=None):
    """One `communication.write()` call from the website's point of view.

    A newline is appended (both TS implementations do this) and BLE chunks the
    result into MTU-sized writes paced by a fixed sleep.
    """
    from .transports import BLE_CHUNK_SIZE, BLE_WRITE_TIMEOUT_S

    payload = (message + "\n").encode("utf-8")

    if not transport.chunked:
        transport.write_raw(payload)
        if stats is not None:
            stats["bytes_written"] += len(payload)
            stats["writes"] += 1
        return

    for offset in range(0, len(payload), BLE_CHUNK_SIZE):
        chunk = payload[offset : offset + BLE_CHUNK_SIZE]
        transport.write_raw(chunk)
        if stats is not None:
            stats["bytes_written"] += len(chunk)
            stats["writes"] += 1
            stats["chunks"] += 1
        time.sleep(BLE_WRITE_TIMEOUT_S)


def parse_messages(buffer):
    """Device -> host framing, mirroring Pico.parseBufferedMessages in the site.

    Returns (messages, remainder). Anything that is not a well-formed message
    object is discarded silently, exactly as the website discards it.
    """
    sanitized = _CONTROL_CHARS.sub("", buffer)
    messages = []
    cursor = 0
    first_incomplete = -1

    while cursor < len(sanitized):
        start = sanitized.find("{", cursor)
        if start == -1:
            break
        end = _find_object_end(sanitized, start)
        if end == -1:
            if first_incomplete == -1:
                first_incomplete = start
            cursor = start + 1
            continue
        candidate = sanitized[start : end + 1]
        try:
            parsed = json.loads(candidate)
        except ValueError:
            cursor = start + 1
            continue
        if isinstance(parsed, dict) and "type" in parsed and "message" in parsed:
            messages.append(parsed)
            cursor = end + 1
            first_incomplete = -1
            continue
        cursor = start + 1

    remainder = "" if first_incomplete == -1 else sanitized[first_incomplete:]
    return messages, remainder


def _find_object_end(text, start):
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
            if depth < 0:
                return -1
    return -1


class Collector:
    """Accumulates device output and decodes it into messages."""

    def __init__(self):
        self.raw = bytearray()
        self.buffer = ""
        self.messages = []

    def pump(self, transport):
        data = transport.read_available()
        if data:
            self.raw.extend(data)
            self.buffer += data.decode("utf-8", "replace")
            found, self.buffer = parse_messages(self.buffer)
            self.messages.extend(found)
        return bool(data)

    def wait_for(self, transport, message_type, timeout):
        """Block until a message of `message_type` arrives. Returns elapsed or None."""
        started = time.time()
        while time.time() - started < timeout:
            self.pump(transport)
            for message in self.messages:
                if message.get("type") == message_type:
                    return time.time() - started
            time.sleep(0.01)
        return None

    def of_type(self, message_type):
        return [m for m in self.messages if m.get("type") == message_type]


def upload(transport, code, ack_timeout=10.0):
    """Run the legacy upload and report what happened.

    Note what is *not* here: any check that the board received what we sent.
    `sendCode` in the website resolves as soon as the last local write returns,
    which is precisely the defect this measures.
    """
    stats = {"bytes_written": 0, "writes": 0, "chunks": 0}
    collector = Collector()

    transport.drain_input()

    started = time.time()
    write_message(transport, CMD_BEGIN_UPLOAD, stats)
    write_message(transport, code, stats)
    write_message(transport, CMD_END_UPLOAD, stats)
    local_done = time.time()

    # The board confirms an upload with {"type": "download"}. The website throws
    # this away; the harness waits for it so we can measure the gap between
    # "writes returned" and "board actually finished".
    ack_elapsed = collector.wait_for(transport, "download", ack_timeout)
    finished = time.time()

    # Sweep up any trailing console/error output.
    deadline = time.time() + 0.5
    while time.time() < deadline:
        if collector.pump(transport):
            deadline = time.time() + 0.3
        time.sleep(0.02)

    return {
        "bytes_written": stats["bytes_written"],
        "writes": stats["writes"],
        "chunks": stats["chunks"],
        "local_write_seconds": round(local_done - started, 4),
        "total_seconds": round(finished - started, 4),
        "ack_seconds": None if ack_elapsed is None else round(ack_elapsed, 4),
        "ack_received": ack_elapsed is not None,
        "device_messages": collector.messages,
        "errors": [str(m.get("message")) for m in collector.of_type("error")],
    }
