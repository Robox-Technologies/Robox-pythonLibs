from .base import CommunicationInterface


class NullCommunication(CommunicationInterface):
    """A sink for a program started from the BOOTSEL button.

    Nothing is listening in standalone mode, so output is discarded rather
    than queued for a link that is never flushed.
    """

    def __init__(self):
        self.name = "None"
        self.out_seq = 0

    def available(self):
        return False

    def read_line(self):
        return None

    def write_message(self, message_type, content):
        pass

    def _write_message_now(self, message_type, content):
        pass

    def write_raw(self, data):
        pass
