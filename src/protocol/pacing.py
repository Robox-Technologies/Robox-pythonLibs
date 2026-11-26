from .constants import (MIN_CHUNK_DELAY_MS, START_CHUNK_DELAY_MS, MAX_CHUNK_DELAY_MS, CLEAN_BATCHES_BEFORE_PROBE, PROBE_FRACTION, BACKOFF_FACTOR)

class AdaptivePacer:
    """Additive-decrease, multiplicative-increase controller for chunk delay.

    Lives on the connection, not the upload, so a second upload keeps the tuning.
    """

    def __init__(
        self,
        delay_ms=START_CHUNK_DELAY_MS,
        minimum_ms=MIN_CHUNK_DELAY_MS,
        maximum_ms=MAX_CHUNK_DELAY_MS,
        clean_before_probe=CLEAN_BATCHES_BEFORE_PROBE,
        probe_fraction=PROBE_FRACTION,
        backoff=BACKOFF_FACTOR,
    ):
        self.minimum_ms = minimum_ms
        self.maximum_ms = maximum_ms
        self.clean_before_probe = clean_before_probe
        self.probe_fraction = probe_fraction
        self.backoff = backoff

        self.delay_ms = self._clamp(delay_ms)
        self.clean_streak = 0

        self.armed = True

        self.probes = 0
        self.backoffs = 0
        self.episodes_ignored = 0
        self.fastest_ms = self.delay_ms
        self.slowest_ms = self.delay_ms

        self.paced_chunks = 0
        self.delay_ms_total = 0

    def _clamp(self, value):
        return max(self.minimum_ms, min(self.maximum_ms, value))

    def on_clean_batch(self):
        """A batch was acknowledged with nothing lost. Consider probing."""
        self.armed = True
        self.clean_streak += 1
        if self.clean_streak < self.clean_before_probe:
            return False

        self.clean_streak = 0
        if self.delay_ms <= self.minimum_ms:
            return False

        step = max(1, int(self.delay_ms * self.probe_fraction + 0.5))
        self.delay_ms = self._clamp(self.delay_ms - step)
        self.probes += 1
        self.fastest_ms = min(self.fastest_ms, self.delay_ms)
        return True

    def on_loss(self):
        """A NAK, a damaged frame, or a timeout. Backs off once per episode."""
        self.clean_streak = 0

        if not self.armed:
            self.episodes_ignored += 1
            return False

        self.armed = False

        if self.delay_ms >= self.maximum_ms:
            return False

        self.delay_ms = self._clamp(int(self.delay_ms * self.backoff + 0.5))
        self.backoffs += 1
        self.slowest_ms = max(self.slowest_ms, self.delay_ms)
        return True

    def delay_seconds(self):
        """The current delay, and a note that a chunk was paced by it."""
        self.paced_chunks += 1
        self.delay_ms_total += self.delay_ms
        return self.delay_ms / 1000.0

    def mean_delay_ms(self):
        if not self.paced_chunks:
            return None
        return round(self.delay_ms_total / self.paced_chunks, 1)

    def stats(self):
        return {
            "delay_ms": self.delay_ms,
            "mean_delay_ms": self.mean_delay_ms(),
            "fastest_ms": self.fastest_ms,
            "slowest_ms": self.slowest_ms,
            "probes": self.probes,
            "backoffs": self.backoffs,
            "episodes_ignored": self.episodes_ignored,
            "floor_ms": self.minimum_ms,
        }

