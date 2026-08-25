from machine import UART, Pin
import json
import sys
import select
import time
import _thread

import protocol as p


# ========================
# Tuning
# ========================

# The most important number here. The rp2 default of 64 bytes is about 66ms of
# runway at 9600 baud, and the loop does blocking work between reads while a
# user program competes for the GIL. Overflow is silent: bytes vanish mid-line.
UART_RX_BUFFER = 4096

# 9600 baud, 8N1, so ten bits on the wire per byte.
UART_BYTES_PER_SECOND = 960

# Headroom over the line rate, leaving the module's own buffer somewhere to go.
SEND_HEADROOM = 1.4

# Smallest gap between BLE sends. The old flat 300ms capped console output at
# roughly three messages a second regardless of size.
MIN_SEND_INTERVAL_MS = 15

# Unbounded growth on a 264KB device is a crash. Drop oldest when a program
# prints faster than the link drains.
MAX_QUEUED_MESSAGES = 64


# ========================
# Global outgoing queue
# ========================
outgoing_messages = []
queue_lock = _thread.allocate_lock()

# Dropped to keep the queue bounded, counted so the loss is reportable.
dropped_message_count = 0


def queue_outgoing_message(comm, message_type, content):
    global dropped_message_count

    queue_lock.acquire()
    try:
        if len(outgoing_messages) >= MAX_QUEUED_MESSAGES:
            outgoing_messages.pop(0)
            dropped_message_count += 1
        outgoing_messages.append((comm, message_type, content))
    finally:
        queue_lock.release()


def flush_outgoing_messages():
    """Send at most one queued message, if an interface is ready for it.

    The lock is held only long enough to take an entry off the queue. The old
    version released it mid-iteration then used `queue_lock.locked()` to decide
    whether to release again, but that reports the lock's state, not this
    thread's ownership, so it could release a lock belonging to the other core.
    """
    pending = None

    queue_lock.acquire()
    try:
        for index in range(len(outgoing_messages)):
            comm = outgoing_messages[index][0]
            # BLE paces itself; skip while draining and try the next entry,
            # which may belong to a ready interface.
            if hasattr(comm, "can_send_now") and not comm.can_send_now():
                continue
            pending = outgoing_messages.pop(index)
            break
    finally:
        queue_lock.release()

    if pending is None:
        return False

    comm, message_type, content = pending
    # Outside the lock: writing can block on the UART, and the user program's
    # thread must still be able to queue meanwhile.
    comm._write_message_now(message_type, content)
    return True


def queued_message_count():
    queue_lock.acquire()
    try:
        return len(outgoing_messages)
    finally:
        queue_lock.release()


# ========================
# Base interface
# ========================
class CommunicationInterface:
    def __init__(self):
        pass

    def available(self):
        raise NotImplementedError

    def read_line(self):
        raise NotImplementedError

    def read_lines(self, limit=128):
        """Drain up to `limit` complete lines.

        The loop used to take one line per interface per iteration, so a burst
        arriving faster than it spun sat in the UART buffer until overflow.
        """
        lines = []
        for _ in range(limit):
            line = self.read_line()
            if not line:
                break
            lines.append(line)
        return lines

    def write_message(self, message_type, content):
        """
        Public API used everywhere else.
        Thread-safe and non-blocking.
        """
        queue_outgoing_message(self, message_type, content)

    def next_out_seq(self):
        """Sequence for the next outbound frame on this interface."""
        seq = self.out_seq
        self.out_seq = (seq + 1) % p.SEQUENCE_MODULO
        return seq

    def encode_reply(self, message_type, content):
        """Frames carrying one device message.

        Long messages (a traceback, a chatty print) exceed one payload, so they
        are split across CONTINUE frames and terminated by a REPLY frame.
        """
        body = generate_message(message_type, content)
        return [
            p.encode_frame(self.next_out_seq(), kind, payload)
            for kind, payload in p.split_payload(body, p.KIND_REPLY)
        ]

    def _write_message_now(self, message_type, content):
        raise NotImplementedError

    def write_raw(self, data):
        """Send bytes immediately, bypassing the outgoing queue.

        Only for flow control: an ACK queued behind a traceback stalls the
        sender until its timeout fires.
        """
        raise NotImplementedError


# ========================
# USB
# ========================
class USBCommunication(CommunicationInterface):
    def __init__(self):
        self.name = "USB"
        self.sleeping = False

        self.out_seq = 0

        self.poller = select.poll()
        self.poller.register(sys.stdin, select.POLLIN)

    def available(self):
        return True

    def read_line(self):
        if not self.poller.poll(0):
            return None

        line = sys.stdin.readline()
        return line.rstrip("\n") if line else None

    def _write_message_now(self, message_type, content):
        for frame in self.encode_reply(message_type, content):
            self.write_raw(frame)

    def write_raw(self, data):
        # stdout.buffer, not stdout: the text stream translates a lone newline
        # into CRLF, which inserts a byte inside the frame and breaks its
        # length. Frames have to go out exactly as encoded.
        if isinstance(data, str):
            data = data.encode()
        sys.stdout.buffer.write(data)

    def sleep(self):
        self.sleeping = True

    def wake(self):
        self.sleeping = False


# ========================
# Bluetooth
# ========================
class BluetoothCommunuication(CommunicationInterface):
    def __init__(self, uart_port=0, baudrate=9600):
        self.name = "Bluetooth"
        self.sleeping = False

        try:
            self.uart = UART(
                uart_port,
                baudrate=baudrate,
                tx=Pin(0),
                rx=Pin(1),
                rxbuf=UART_RX_BUFFER,
            )

            self.buffer = b""
            self.ok = True
            self.out_seq = 0

            # Rate limiting
            self.next_send_time = 0

            # Tracked so a corrupt link is measurable, not just suspected.
            self.decode_errors = 0

        except Exception:
            self.ok = False

    def available(self):
        return self.ok

    def read_line(self):
        # Empty the hardware buffer first, even if the caller wants one line.
        if self.uart.any():
            data = self.uart.read()
            if data:
                self.buffer += (
                    data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                )

        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)

            if not line.strip():
                continue

            try:
                return line.decode()
            except Exception:
                self.decode_errors += 1
                continue

        return None

    def can_send_now(self):
        return time.ticks_diff(time.ticks_ms(), self.next_send_time) >= 0

    def _write_message_now(self, message_type, content):
        total = 0
        for frame in self.encode_reply(message_type, content):
            self.uart.write(frame)
            total += len(frame)

        # Pace by bytes sent, not a flat delay: the old 300ms throttled a
        # 20-byte status message as hard as a 400-byte traceback.
        transmit_ms = int(
            total * 1000 * SEND_HEADROOM / UART_BYTES_PER_SECOND
        )
        self.next_send_time = time.ticks_add(
            time.ticks_ms(), max(MIN_SEND_INTERVAL_MS, transmit_ms)
        )

    def write_raw(self, data):
        self.uart.write(data)

    def write(self, data):
        self.uart.write((data + "\r\n").encode())

    def sleep(self):
        if self.ok and not self.sleeping:
            self.uart.write("AT+SLEEP\r\n")
            self.sleeping = True

    def wake(self):
        self.uart.write("AT\r\n")
        self.sleeping = False

    def configure(bt):
        bt.send_at("AT+UUID0xffe0")
        bt.send_at("AT+CHAR0xffe1")
        bt.send_at("AT+NOTI1")
        bt.send_at("AT+NAMERoBox1")
        bt.send_at("AT+RESET")
        bt.send_at("AT")

    def send_at(self, cmd, wait=0.3):
        """
        Keep blocking behavior here because AT config
        happens during setup only.
        """
        full = cmd + "\r\n"

        print(">>> {}".format(cmd))
        self.uart.write(full.encode())

        time.sleep(wait)

        response = b""

        while self.uart.any():
            chunk = self.uart.read()
            if chunk:
                response += chunk

        try:
            decoded = response.decode().strip()
        except Exception:
            decoded = str(response)

        print("<<< {}".format(decoded if decoded else "(no response)"))
        print()

        return decoded


# ========================
# JSON formatting
# ========================
def generate_message(message_type, content):
    return json.dumps({
        "type": message_type,
        "message": content
    })
