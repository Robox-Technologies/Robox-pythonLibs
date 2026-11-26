"""Controller mode: a one-way raw link that drives the motors directly.

See docs/PROTOCOL.md ("Controller mode") and the website's
docs/controller-mode.md for the contract.
"""

from .packets import (
    DRIVE,
    EXIT,
    MAX_SPEED,
    PACKET_LENGTH,
    START,
    TIMEOUT_MS,
    PacketParser,
    crc8,
    encode,
    speed,
)
from .loop import LOOP_SLEEP_MS, run
