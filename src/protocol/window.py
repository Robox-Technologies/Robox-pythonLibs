from .constants import INITIAL_CREDIT, SEQUENCE_MODULO
from .sequence import sequence_distance

class CreditWindow:
    """Sender-side flow control and retransmission bookkeeping."""

    def __init__(self, frames, start_seq=0, credit=INITIAL_CREDIT):
        self.frames = list(frames)
        self.start_seq = start_seq % SEQUENCE_MODULO
        self.credit = credit
        self.base = 0        # lowest unacknowledged frame
        self.next_index = 0  # next frame to transmit
        self.retransmits = 0

    def _index_for_seq(self, seq):
        return sequence_distance(seq, self.start_seq)

    def in_flight_bytes(self):
        return sum(len(f) for f in self.frames[self.base : self.next_index])

    def ready(self):
        """Frames that may go out now without exceeding the credit."""
        out = []
        budget = self.credit - self.in_flight_bytes()
        index = self.next_index
        while index < len(self.frames) and len(self.frames[index]) <= budget:
            out.append(self.frames[index])
            budget -= len(self.frames[index])
            index += 1
        return out

    def advance(self, count):
        self.next_index = min(len(self.frames), self.next_index + count)

    def on_ack(self, expected_seq, credit):
        """True when the acknowledgement actually moved the window."""
        self.credit = credit or self.credit

        index = self._index_for_seq(expected_seq)
        if index <= self.base:
            return False

        self.base = min(index, len(self.frames))
        if self.next_index < self.base:
            self.next_index = self.base
        return True

    def on_nak(self, expected_seq):
        """Rewind so transmission resumes where the receiver wants."""
        index = self._index_for_seq(expected_seq)
        if index >= len(self.frames):
            return False
        if index < self.next_index:
            self.next_index = index
            self.base = min(self.base, index)
            self.retransmits += 1
            return True
        return False

    def rewind_to_base(self):
        """Resend from the oldest unacknowledged frame. The timeout path."""
        if self.next_index > self.base:
            self.next_index = self.base
            self.retransmits += 1
            return True
        return False

    def complete(self):
        return self.base >= len(self.frames)
