"""Receive side of the framed protocol, one session per interface."""

from binascii import crc32

import protocol as p
from debug import log, log_error

FRAME_PREFIX = chr(p.SOH)


class FramedSession:
    """Tracks one peer's framed upload and its flow control."""

    def __init__(self, comm, filename):
        self.comm = comm
        self.filename = filename

        self.reader = p.FrameReader()
        self.receiver = p.SequencedReceiver()

        self.out_file = None
        self.partial = b""
        self.stored_lines = 0
        self.running_crc = 0
        self.expected_lines = 0
        self.expected_crc = 0

        self.verified = False

    def _send_flow(self, kind):
        """Write a flow frame straight out, bypassing the message queue."""
        expected, credit = self.receiver.take_ack()
        payload = "%02x,%04x" % (expected, credit)
        log(
            "protocol",
            "tx_flow",
            level=1,
            seq=self.comm.out_seq,
            kind=kind,
            expected=expected,
            credit=credit,
            bytes=len(payload),
        )
        self.comm.write_raw(
            p.encode_frame(self.comm.next_out_seq(), kind, payload.encode())
        )

    def reply(self, message_type, content):
        """Queue a device message. The interface frames it."""
        self.comm.write_message(message_type, content)

    def feed(self, line):
        """Process one framed line. Returns command names to dispatch."""
        frames, damage = self.reader.feed((line + "\n").encode())
        self.receiver.note_corruption(damage)
        if damage:
            log("protocol", "rx_corruption", level=1, count=damage)

        commands = []
        for frame in frames:
            log(
                "protocol",
                "rx_frame",
                level=1,
                seq=frame.seq,
                kind=frame.kind,
                bytes=len(frame.payload),
                payload=frame.payload.hex(),
            )
            if frame.kind in (p.KIND_BEGIN, p.KIND_COMMAND):
                self.receiver.reset(frame.seq)

            if self.receiver.accept(frame) != "accept":
                continue
            command = self._apply(frame)
            if command:
                commands.append(command)

        return commands

    def flush(self):
        """Send the pending NAK or ACK, if either is owed."""
        if self.receiver.needs_nak():
            self._send_flow(p.KIND_NAK)
        elif self.receiver.since_ack:
            self._send_flow(p.KIND_ACK)

    def _apply(self, frame):
        kind = frame.kind

        if kind == p.KIND_BEGIN:
            self._begin(frame)
        elif kind == p.KIND_CONTINUE:
            self.partial += frame.payload
        elif kind == p.KIND_DATA:
            self._store(frame)
        elif kind == p.KIND_END:
            self._end(frame)
        elif kind == p.KIND_COMMAND:
            name = frame.text()
            log("command", "received", level=1, name=name)
            if not p.is_command_name(name):
                log("protocol", "unknown_command", name=name)
                self.reply("error", "Unknown command: {}".format(name))
                return None
            return name

        return None

    def _begin(self, frame):
        self.close()
        try:
            self.expected_lines, self.expected_crc = p.parse_begin(frame.payload)
        except Exception:
            log("upload", "malformed_begin", level=1)
            self.reply("error", "Malformed upload header")
            return

        self.partial = b""
        self.stored_lines = 0
        self.running_crc = 0
        self.verified = False

        try:
            self.out_file = open(self.filename, "w")
        except Exception as exc:
            log_error("upload", exc, event="open_failed")
            self.reply("error", "Could not open program file: {}".format(exc))
        else:
            log("upload", "started", level=1, lines=self.expected_lines)

    def _store(self, frame):
        if not self.out_file:
            return

        payload = self.partial + frame.payload
        self.partial = b""

        try:
            text = payload.decode()
        except Exception:
            log("upload", "undecodable_line", level=1)
            self.reply("error", "Undecodable program line")
            return

        line = text + "\n"
        self.out_file.write(line)
        self.running_crc = crc32(line.encode(), self.running_crc) & 0xFFFFFFFF
        self.stored_lines += 1
        if self.stored_lines == 1 or self.stored_lines % 10 == 0:
            log(
                "upload",
                "stored",
                level=1,
                lines=self.stored_lines,
                bytes=len(line),
            )

    def _end(self, frame):
        if not self.out_file:
            self.reply("error", "No upload in progress")
            return

        self.out_file.close()
        self.out_file = None

        try:
            declared = p.parse_end(frame.payload)
        except Exception:
            declared = None

        lines_ok = self.stored_lines == self.expected_lines
        crc_ok = declared is not None and self.running_crc == declared
        self.verified = lines_ok and crc_ok
        log(
            "upload",
            "verified" if self.verified else "failed",
            level=1,
            lines=self.stored_lines,
            crc="%08x" % self.running_crc,
        )

        self.reply(
            "uploaded",
            {
                "ok": self.verified,
                "lines": self.stored_lines,
                "expected": self.expected_lines,
                "crc": "%08x" % self.running_crc,
                "want": "%08x" % (declared or 0),
            },
        )

    def verify_existing_program(self, expected_crc):
        """Verify the existing program without modifying it."""
        if self.out_file:
            self.verified = False
            return False

        try:
            with open(self.filename, "rb") as program:
                actual_crc = 0
                while True:
                    chunk = program.read(256)
                    if not chunk:
                        break
                    actual_crc = crc32(chunk, actual_crc) & 0xFFFFFFFF
        except Exception:
            self.verified = False
            return False

        self.verified = actual_crc == expected_crc
        return self.verified

    def close(self):
        if self.out_file:
            try:
                self.out_file.close()
            except Exception:
                pass
            self.out_file = None

    def stats(self):
        return {
            "accepted": self.receiver.accepted,
            "gaps": self.receiver.gaps,
            "corrupt": self.receiver.corrupt,
            "duplicates": self.receiver.duplicates,
            "resyncs": self.reader.resyncs,
        }
