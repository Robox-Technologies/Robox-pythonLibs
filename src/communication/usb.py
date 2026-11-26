import select
import sys

from .base import CommunicationInterface

RAW_READ_LIMIT = 32

class USBCommunication(CommunicationInterface):
    def __init__(self):
        self.name = "USB"
        self.out_seq = 0

        self.decode_errors = 0
        # Bytes handed back by controller mode, read before stdin.
        self.pending = b""

        self.poller = select.poll()
        self.poller.register(sys.stdin, select.POLLIN)

    def available(self):
        return True

    def read_line(self):
        if self.pending:
            return self._read_pending_line()

        if not self.poller.poll(0):
            return None

        try:
            line = sys.stdin.readline()
        except Exception:
            self.decode_errors += 1
            return None

        return line.rstrip("\n") if line else None

    def _read_pending_line(self):
        """A line from bytes `unread` put back, finished from stdin if need be."""
        if b"\n" not in self.pending:
            if not self.poller.poll(0):
                return None
            try:
                rest = sys.stdin.readline()
            except Exception:
                self.decode_errors += 1
                return None
            self.pending += rest.encode() if isinstance(rest, str) else rest
            if b"\n" not in self.pending:
                return None

        line, self.pending = self.pending.split(b"\n", 1)
        try:
            return line.decode()
        except Exception:
            self.decode_errors += 1
            return None

    def read_raw(self):
        """Whatever bytes are waiting, without line handling. Never blocks."""
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        data = b""
        while len(data) < RAW_READ_LIMIT and self.poller.poll(0):
            chunk = stream.read(1)
            if not chunk:
                break
            data += chunk.encode() if isinstance(chunk, str) else chunk
        return data or None

    def unread(self, data):
        self.pending = bytes(data) + self.pending

    def discard_input(self):
        self.pending = b""

    def _write_message_now(self, message_type, content):
        for frame in self.encode_reply(message_type, content):
            self.write_raw(frame)

    def write_raw(self, data):
        if isinstance(data, str):
            data = data.encode()
        from debug import log

        if not getattr(self, "_writing_debug", False):
            log(
                "usb",
                "tx_bytes",
                level=1,
                bytes=len(data),
                payload=data.hex(),
            )
        sys.stdout.buffer.write(data)
        flush = getattr(sys.stdout.buffer, "flush", None)
        if flush is not None:
            flush()
