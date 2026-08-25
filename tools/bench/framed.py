"""Host side of the framed upload, for the benchmark.

Mirrors what the website's uploader does, so a number measured here means
something for the real client. Sends frames back to back within the credit,
reads ACK/NAK to advance or rewind, and refuses to call an upload good until
the board reports a matching line count and CRC.
"""

import json
import sys
import time

sys.path.insert(0, __file__.rsplit("/tools/", 1)[0] + "/src")

import protocol as p  # noqa: E402

#: How long to wait for an ACK before resending from the window base.
ACK_TIMEOUT_S = 0.75

#: Give up after this many rewinds, rather than looping on a dead link.
MAX_RETRANSMITS = 40

#: Hard ceiling per upload. A pacing setting too aggressive for the link sends
#: go-back-N into a thrash that technically converges but takes minutes, which
#: reads as a hang. Fail with the numbers instead.
MAX_UPLOAD_SECONDS = 120.0

#: How long to wait for the board's upload verdict after the END frame.
VERDICT_TIMEOUT_S = 6.0


def upload(transport, code, chunk_size=None, chunk_delay=0.0):
    """Send `code` as frames and return what happened.

    `chunk_size` splits each write to imitate a BLE MTU; None sends whole
    frames, which is what a reliable stream does.
    """
    frames = p.encode_program(code)
    window = p.CreditWindow(frames, credit=p.INITIAL_CREDIT)
    reader = p.FrameReader()

    stats = {
        "bytes_written": 0,
        "writes": 0,
        "chunks": 0,
        "frames_total": len(frames),
        "retransmits": 0,
        "acks": 0,
        "naks": 0,
        "damage": 0,
    }

    transport.drain_input()

    verdict = None
    replies = []
    continuation = []
    started = time.time()
    last_progress = started

    def pump():
        """Read whatever arrived and apply it to the window."""
        nonlocal verdict
        data = transport.read_available()
        if not data:
            return False

        found, damage = reader.feed(data)
        stats["damage"] += damage

        for frame in found:
            if frame.kind == p.KIND_ACK:
                expected, credit = p.parse_flow(frame.payload)
                window.on_ack(expected, credit)
                stats["acks"] += 1
            elif frame.kind == p.KIND_NAK:
                expected, _ = p.parse_flow(frame.payload)
                if window.on_nak(expected):
                    stats["naks"] += 1
            elif frame.kind == p.KIND_CONTINUE:
                # A device message longer than one payload. Hold the piece
                # until the terminating REPLY frame arrives.
                continuation.append(frame.payload)
            elif frame.kind == p.KIND_REPLY:
                body = (b"".join(continuation) + frame.payload).decode(
                    "utf-8", "replace"
                )
                del continuation[:]
                replies.append(body)
                parsed = _parse_message(body)
                if parsed and parsed.get("type") == "uploaded":
                    verdict = parsed.get("message")
        return True

    def send(frame):
        if chunk_size:
            for offset in range(0, len(frame), chunk_size):
                chunk = frame[offset : offset + chunk_size]
                transport.write_raw(chunk)
                stats["chunks"] += 1
                stats["writes"] += 1
                if chunk_delay:
                    time.sleep(chunk_delay)
        else:
            transport.write_raw(frame)
            stats["writes"] += 1
        stats["bytes_written"] += len(frame)

    while not window.complete():
        if time.time() - started > MAX_UPLOAD_SECONDS:
            stats["gave_up"] = "exceeded MAX_UPLOAD_SECONDS"
            break

        batch = window.ready()

        if batch:
            # Account for the batch before sending: an ACK can land mid-write,
            # and advancing afterwards would push past unsent frames.
            window.advance(len(batch))
            for frame in batch:
                send(frame)
            last_progress = time.time()
        else:
            # Nothing may go out: either the credit is spent or an ACK went
            # missing. Wait, then resend from the base. This is the timeout
            # path that stops a lost ACK from stalling forever.
            pump()
            if time.time() - last_progress > ACK_TIMEOUT_S:
                if window.retransmits >= MAX_RETRANSMITS:
                    stats["gave_up"] = "hit MAX_RETRANSMITS"
                    break
                # rewind_to_base, not a bare next_index assignment: it counts
                # the retransmit, and the counter is what bounds this loop.
                # Without it a peer that has gone quiet spins here forever.
                window.rewind_to_base()
                last_progress = time.time()
            else:
                time.sleep(0.005)
            continue

        pump()

    local_done = time.time()

    # Wait for the board's verdict on the whole upload.
    deadline = time.time() + VERDICT_TIMEOUT_S
    while verdict is None and time.time() < deadline:
        if not pump():
            time.sleep(0.01)

    stats["retransmits"] = window.retransmits
    stats.setdefault("gave_up", None)
    stats["local_write_seconds"] = round(local_done - started, 4)
    stats["total_seconds"] = round(time.time() - started, 4)
    stats["verdict"] = verdict
    # Checked as structure, not as a substring: a substring match passed even
    # when the board was double-encoding the verdict into a JSON string.
    stats["verified"] = bool(
        isinstance(verdict, dict)
        and verdict.get("ok") is True
        and verdict.get("crc") == "%08x" % p.program_checksum(code)
    )
    stats["replies"] = replies
    stats["expected_crc"] = "%08x" % p.program_checksum(code)
    stats["expected_lines"] = p.program_line_count(code)
    return stats


def _parse_message(body):
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def send_command(transport, name):
    """Send one control command as a COMMAND frame."""
    transport.write_raw(p.encode_frame(0, p.KIND_COMMAND, name))
