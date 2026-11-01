import protocol as p

from .messages import generate_message
from .queue import queue_outgoing_message


class CommunicationInterface:
    def available(self):
        raise NotImplementedError

    def read_line(self):
        raise NotImplementedError

    def read_lines(self, limit=128):
        lines = []
        for _ in range(limit):
            line = self.read_line()
            if not line:
                break
            lines.append(line)
        return lines

    def write_message(self, message_type, content):
        queue_outgoing_message(self, message_type, content)

    def next_out_seq(self):
        seq = self.out_seq
        self.out_seq = (seq + 1) % p.SEQUENCE_MODULO
        return seq

    def encode_reply(self, message_type, content):
        body = generate_message(message_type, content)
        return [
            p.encode_frame(self.next_out_seq(), kind, payload)
            for kind, payload in p.split_payload(body, p.KIND_REPLY)
        ]

    def _write_message_now(self, message_type, content):
        raise NotImplementedError

    def write_raw(self, data):
        raise NotImplementedError
