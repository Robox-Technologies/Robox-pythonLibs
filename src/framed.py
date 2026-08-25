"""Receive side of the framed protocol, one session per interface.

Framed and legacy traffic share the link: a frame is one line beginning with
SOH, and SOH cannot appear in legacy text, so main.py routes on the first byte
and old clients keep working unchanged.

Frames arrive here already split into lines by the interface, which is safe by
construction: payloads exclude newlines, so one frame is always one line. A
packet dropped mid-frame may leave invalid UTF-8, in which case the interface
discards the line and the missing sequence number is what tells us, exactly as
if the whole frame had vanished.
"""

from binascii import crc32

import protocol as p

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

        #: True only after an END frame whose totals matched. Gates running.
        self.verified = False

    # --- outbound ----------------------------------------------------------

    def _send_flow(self, kind):
        """Write a flow frame straight out, bypassing the message queue.

        Flow control must not queue behind console output: an ACK stuck behind
        a traceback stalls the sender until its timeout fires.
        """
        expected, credit = self.receiver.take_ack()
        payload = "%02x,%04x" % (expected, credit)
        self.comm.write_raw(
            p.encode_frame(self.comm.next_out_seq(), kind, payload.encode())
        )

    def reply(self, message_type, content):
        """Queue a device message. The interface frames it."""
        self.comm.write_message(message_type, content)

    # --- inbound -----------------------------------------------------------

    def feed(self, line):
        """Process one framed line. Returns command names to dispatch.

        Acknowledgements are not sent here: `flush` does that once the caller
        has drained everything that arrived together, which is what makes a
        batch a batch. Acking per line would cost a round trip per frame, and
        acking only at ACK_EVERY would strand an upload shorter than a batch.
        """
        frames, damage = self.reader.feed((line + "\n").encode())
        self.receiver.note_corruption(damage)

        commands = []
        for frame in frames:
            # BEGIN and COMMAND are self-contained: they start a new
            # interaction rather than continuing a stream, so they
            # resynchronise the sequence.
            #
            # The sequence space exists to order the frames *within* one
            # upload. Without this, a second upload restarts at sequence 0
            # while the receiver still expects N and every frame reads as a
            # stale duplicate, and a reconnecting client's firmware check is
            # ignored the same way. The checksum still protects both.
            if frame.kind in (p.KIND_BEGIN, p.KIND_COMMAND):
                self.receiver.reset(frame.seq)

            if self.receiver.accept(frame) != "accept":
                continue
            command = self._apply(frame)
            if command:
                commands.append(command)

        if self.receiver.needs_nak() or self.receiver.should_ack():
            self.flush()

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
            # The only place a payload is read as a command, and only because
            # its frame says so. Data frames are never inspected.
            name = frame.text()
            if name not in p.COMMAND_NAMES:
                self.reply("error", "Unknown command: {}".format(name))
                return None
            return name

        return None

    def _begin(self, frame):
        self.close()
        try:
            self.expected_lines, self.expected_crc = p.parse_begin(frame.payload)
        except Exception:
            self.reply("error", "Malformed upload header")
            return

        self.partial = b""
        self.stored_lines = 0
        self.running_crc = 0
        self.verified = False

        try:
            self.out_file = open(self.filename, "w")
        except Exception as exc:
            self.reply("error", "Could not open program file: {}".format(exc))

    def _store(self, frame):
        if not self.out_file:
            return

        payload = self.partial + frame.payload
        self.partial = b""

        try:
            text = payload.decode()
        except Exception:
            # Cannot happen for an intact frame: the checksum already passed.
            self.reply("error", "Undecodable program line")
            return

        line = text + "\n"
        self.out_file.write(line)
        # Running CRC over exactly what was written, so the comparison at END
        # covers the stored file rather than what we believe we received.
        self.running_crc = crc32(line.encode(), self.running_crc) & 0xFFFFFFFF
        self.stored_lines += 1

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

        # A dict, not a pre-built JSON string: write_message runs json.dumps
        # over it, so a string would arrive at the client escaped inside
        # another string and its verdict check would never match.
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
