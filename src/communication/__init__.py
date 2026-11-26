"""Firmware communication interfaces and their bounded output queue."""

from .base import CommunicationInterface
from .bluetooth import (
    MAX_LINE_LENGTH,
    REBOOT_POLL_ATTEMPTS,
    REBOOT_POLL_INTERVAL,
    BluetoothCommunication,
)
from .messages import generate_message
from .queue import (
    MAX_QUEUED_MESSAGES,
    flush_outgoing_messages,
    queued_message_count,
    queue_outgoing_message,
)
from .usb import USBCommunication
