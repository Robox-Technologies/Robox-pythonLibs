import select
import sys

from .base import CommunicationInterface

class USBCommunication(CommunicationInterface):
    def __init__(self):
        self.name = "USB"
        self.out_seq = 0

        self.decode_errors = 0

        self.poller = select.poll()
        self.poller.register(sys.stdin, select.POLLIN)

    def available(self):
        return True

    def read_line(self):
        if not self.poller.poll(0):
            return None

        try:
            line = sys.stdin.readline()
        except Exception:
            self.decode_errors += 1
            return None

        return line.rstrip("\n") if line else None

    def _write_message_now(self, message_type, content):
        for frame in self.encode_reply(message_type, content):
            self.write_raw(frame)

    def write_raw(self, data):
        if isinstance(data, str):
            data = data.encode()
        sys.stdout.buffer.write(data)


