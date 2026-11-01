from .constants import SEQUENCE_MODULO, DEFAULT_CREDIT, ACK_EVERY

def sequence_distance(later, earlier):
    """Forward distance, accounting for wraparound."""
    return (later - earlier) % SEQUENCE_MODULO


class SequencedReceiver:
    """Go-back-N receiver: accepts in-order frames, discards after a gap."""

    def __init__(self, credit=DEFAULT_CREDIT, ack_every=ACK_EVERY):
        self.expected = 0
        self.credit = credit
        self.ack_every = ack_every
        self.since_ack = 0
        self.discarding = False
        self.accepted = 0
        self.duplicates = 0
        self.gaps = 0
        self.corrupt = 0

    def reset(self, expected=0):
        self.expected = expected % SEQUENCE_MODULO
        self.since_ack = 0
        self.discarding = False

    def accept(self, frame):
        """Classify one frame as "accept", "duplicate" or "gap"."""
        if frame.seq == self.expected:
            self.expected = (self.expected + 1) % SEQUENCE_MODULO
            self.since_ack += 1
            self.discarding = False
            self.accepted += 1
            return "accept"

        if sequence_distance(self.expected, frame.seq) < SEQUENCE_MODULO // 2:
            self.duplicates += 1
            return "duplicate"

        self.gaps += 1
        self.discarding = True
        return "gap"

    def note_corruption(self, count=1):
        """A damaged frame leaves the next sequence number unknown."""
        if count:
            self.corrupt += count
            self.discarding = True

    def should_ack(self):
        return self.since_ack >= self.ack_every

    def needs_nak(self):
        return self.discarding

    def take_ack(self):
        self.since_ack = 0
        return self.expected, self.credit

