from machine import UART, Pin
import json
import sys
import select
import time
import _thread

import protocol as p


UART_RX_BUFFER = 4096
UART_BYTES_PER_SECOND = 960
SEND_HEADROOM = 1.4
MIN_SEND_INTERVAL_MS = 15
MAX_QUEUED_MESSAGES = 64
QUEUE_WAIT_MS = 2000
QUEUE_POLL_MS = 2
DROP_NOTICE = "[%d line(s) of output dropped: the link could not keep up]"
MAX_LINE_LENGTH = 4 * (p.FRAME_OVERHEAD + p.MAX_PAYLOAD)
SOH_BYTE = bytes([p.SOH])
REBOOT_POLL_INTERVAL = 0.5
REBOOT_POLL_ATTEMPTS = 12

REBOOT_POLL_INTERVAL = 0.5
REBOOT_POLL_ATTEMPTS = 12


outgoing_messages = []
queue_lock = _thread.allocate_lock()

dropped_message_count = 0

unreported_drops = {}

draining_thread = _thread.get_ident()


def queue_outgoing_message(comm, message_type, content):
    """Queue one device message, waiting for room if the link is behind."""
    global dropped_message_count

    if _thread.get_ident() != draining_thread:
        deadline = time.ticks_add(time.ticks_ms(), QUEUE_WAIT_MS)
        while _queue_is_full():
            if time.ticks_diff(time.ticks_ms(), deadline) >= 0:
                break
            time.sleep(QUEUE_POLL_MS / 1000)

    queue_lock.acquire()
    try:
        if len(outgoing_messages) >= MAX_QUEUED_MESSAGES:
            victim = outgoing_messages.pop(0)
            dropped_message_count += 1
            unreported_drops[victim[0]] = (
                unreported_drops.get(victim[0], 0) + 1
            )
        outgoing_messages.append((comm, message_type, content))
    finally:
        queue_lock.release()


def _queue_is_full():
    queue_lock.acquire()
    try:
        return len(outgoing_messages) >= MAX_QUEUED_MESSAGES
    finally:
        queue_lock.release()


def flush_outgoing_messages():
    """Send at most one queued message, if an interface is ready for it."""
    pending = None

    queue_lock.acquire()
    try:
        blocked = []
        for index in range(len(outgoing_messages)):
            comm = outgoing_messages[index][0]
            if comm in blocked:
                continue
            if hasattr(comm, "can_send_now") and not comm.can_send_now():
                blocked.append(comm)
                continue
            missing = unreported_drops.get(comm)
            if missing:
                unreported_drops[comm] = 0
                pending = (comm, "console", DROP_NOTICE % missing)
            else:
                pending = outgoing_messages.pop(index)
            break
    finally:
        queue_lock.release()

    if pending is None:
        return False

    comm, message_type, content = pending
    comm._write_message_now(message_type, content)
    return True


def queued_message_count():
    queue_lock.acquire()
    try:
        return len(outgoing_messages)
    finally:
        queue_lock.release()


class CommunicationInterface:
    def __init__(self):
        pass

    def available(self):
        raise NotImplementedError

    def read_line(self):
        raise NotImplementedError

    def read_lines(self, limit=128):
        """Drain up to `limit` complete lines."""
        lines = []
        for _ in range(limit):
            line = self.read_line()
            if not line:
                break
            lines.append(line)
        return lines

    def write_message(self, message_type, content):
        """Thread-safe. Never blocks the main loop."""
        queue_outgoing_message(self, message_type, content)

    def next_out_seq(self):
        """Sequence for the next outbound frame on this interface."""
        seq = self.out_seq
        self.out_seq = (seq + 1) % p.SEQUENCE_MODULO
        return seq

    def encode_reply(self, message_type, content):
        """Frames carrying one device message, split across CONTINUE frames."""
        body = generate_message(message_type, content)
        return [
            p.encode_frame(self.next_out_seq(), kind, payload)
            for kind, payload in p.split_payload(body, p.KIND_REPLY)
        ]

    def _write_message_now(self, message_type, content):
        raise NotImplementedError

    def write_raw(self, data):
        """Send bytes now, bypassing the queue. Flow control only."""
        raise NotImplementedError


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


class BluetoothCommunuication(CommunicationInterface):
    def __init__(self, uart_port=0, baudrate=9600):
        self.name = "Bluetooth"

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

            self.next_send_time = 0
            self.decode_errors = 0

        except Exception:
            self.ok = False

    def available(self):
        return self.ok

    def read_line(self):
        if self.uart.any():
            data = self.uart.read()
            if data:
                self.buffer += (
                    data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                )

        if len(self.buffer) > MAX_LINE_LENGTH:
            start = self.buffer.rfind(SOH_BYTE)
            self.buffer = self.buffer[start:] if start > 0 else b""

        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)

            start = line.find(SOH_BYTE)
            if start > 0:
                line = line[start:]

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

    def configure(self, name):
        """Provision the module with its UUID, characteristic, and name."""
        return self._provision(("AT+UUIDFFE0", "AT+CHARFFE1", "AT+NAME" + name))

    def rename(self, name):
        """Change the module name without changing its UUID or characteristic."""
        return self._provision(("AT+NAME" + name,))

    def _provision(self, commands):
        """Apply AT commands, reset the module, and wait for it to return."""
        self.send_at("AT", wait=0.5)

        rejected = []
        for cmd in commands:
            if "ERROR" in self.send_at(cmd):
                rejected.append(cmd)

        self.send_at("AT+RESET", wait=1.5)

        came_back = False
        for _ in range(REBOOT_POLL_ATTEMPTS):
            if "OK" in self.send_at("AT", wait=REBOOT_POLL_INTERVAL):
                came_back = True
                break
        if not came_back:
            rejected.append("AT (module did not come back)")

        if rejected:
            print("!!! rejected: {}".format(", ".join(rejected)))
        return not rejected

    def send_at(self, cmd, wait=0.3):
        """Send one AT command and return the reply. Blocks; provisioning only."""
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
