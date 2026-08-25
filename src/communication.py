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

# Unbounded growth on a 264KB device is a crash, so the queue is bounded and a
# program that outruns the link waits for room rather than having its output
# thrown away. See queue_outgoing_message.
MAX_QUEUED_MESSAGES = 64

# How long a printing program waits for room before giving up and dropping.
# The link is the real limit, not the queue: a full queue is a couple of
# seconds of BLE, so this is comfortably longer than a drain while still
# bounded, because a link that has gone away never drains at all.
QUEUE_WAIT_MS = 2000

# Poll interval while waiting. Short enough to be invisible, long enough to
# leave the draining core the GIL.
QUEUE_POLL_MS = 2

# Shown in the terminal where output was lost, so a gap is never silent.
DROP_NOTICE = "[%d line(s) of output dropped: the link could not keep up]"

# Cap on a partial line waiting for its newline. The module's own chatter has
# no terminator, so without this it accumulates for the life of the session.
MAX_LINE_LENGTH = 4 * (p.FRAME_OVERHEAD + p.MAX_PAYLOAD)

# As bytes, for searching a raw receive buffer.
SOH_BYTE = bytes([p.SOH])


# ========================
# Global outgoing queue
# ========================
outgoing_messages = []
queue_lock = _thread.allocate_lock()

# Dropped to keep the queue bounded, counted so the loss is reportable.
dropped_message_count = 0

# Drops not yet announced on the interface they happened on, so the gap can be
# marked in the terminal at the point where it happened.
unreported_drops = {}

# The thread that runs flush_outgoing_messages, recorded at import because that
# happens on the main loop's thread. Waiting for the queue to drain is only
# safe for a thread that is not the one doing the draining: the main loop
# waiting on itself is a deadlock until the timeout fires.
draining_thread = _thread.get_ident()


def queue_outgoing_message(comm, message_type, content):
    """Queue one device message, waiting for room if the link is behind.

    Waiting is what keeps output whole. A program printing in a tight loop
    fills the queue in milliseconds while the link needs seconds to carry it,
    and discarding the overflow silently lost more than a third of a hundred
    printed lines. The user program has its own core, and the main loop keeps
    draining while it sleeps, so the cost of waiting is that a chatty program
    runs at the speed of its own output.

    Bounded, and only ever on the program's thread. A link that has gone away
    never drains, so past QUEUE_WAIT_MS the oldest entry is dropped as before
    and recorded for a marker rather than vanishing.
    """
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
    """Send at most one queued message, if an interface is ready for it.

    The lock is held only long enough to take an entry off the queue. The old
    version released it mid-iteration then used `queue_lock.locked()` to decide
    whether to release again, but that reports the lock's state, not this
    thread's ownership, so it could release a lock belonging to the other core.
    """
    pending = None

    queue_lock.acquire()
    try:
        # An interface that is not ready blocks its own backlog, and is asked
        # once per flush rather than once per entry. `can_send_now` compares
        # against the clock, so asking per entry let the clock cross
        # `next_send_time` part-way down the queue: the oldest entry was judged
        # not ready, a later entry for the *same* interface was, and it went
        # out in front. That is how console output arrived shuffled.
        blocked = []
        for index in range(len(outgoing_messages)):
            comm = outgoing_messages[index][0]
            if comm in blocked:
                continue
            # BLE paces itself; skip while draining and try the next entry,
            # which may belong to a *different*, ready interface.
            if hasattr(comm, "can_send_now") and not comm.can_send_now():
                blocked.append(comm)
                continue
            # Everything dropped for this interface was older than everything
            # still queued for it, so the gap belongs here, in front of the
            # survivors. Sent in place of a real message rather than as well as
            # one, so a flush is still one write and the marker costs no queue
            # space of its own.
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
        Thread-safe. Never blocks the main loop; a user program that prints
        faster than the link carries waits for room instead of losing output.
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
        self.out_seq = 0

        # Tracked so a corrupt link is measurable, not just suspected.
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
            # One undecodable byte used to raise straight out of the main loop
            # and drop the board to a REPL. The frame it belonged to is lost,
            # and the missing sequence number is what reports that.
            self.decode_errors += 1
            return None

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


# ========================
# Bluetooth
# ========================
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

        # Keep everything from the newest sentinel, where a frame can still
        # start.
        if len(self.buffer) > MAX_LINE_LENGTH:
            start = self.buffer.rfind(SOH_BYTE)
            self.buffer = self.buffer[start:] if start > 0 else b""

        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)

            # As bytes, before decoding: chatter in front of a frame need not
            # be valid UTF-8, and decoding first lost the frame with it.
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

    def configure(self, name):
        """Provision the module, once per board: `ble.configure("Robox20")`.

        Set forms only. This clone has no query form, and `AT+NAME?` sets the
        name to "?" rather than reporting it, which is how a board lost its own.

        Returns False and names whatever the module rejected, since a refused
        command otherwise looks exactly like one that applied. `AT+NOTI1` was
        here for a long time and was always one of those.
        """
        rejected = []
        for cmd in ("AT+UUID0xffe0", "AT+CHAR0xffe1", "AT+NAME" + name):
            if "ERROR" in self.send_at(cmd):
                rejected.append(cmd)

        self.send_at("AT+RESET", wait=1.5)
        if "OK" not in self.send_at("AT"):
            rejected.append("AT (module did not come back)")

        if rejected:
            print("!!! rejected: {}".format(", ".join(rejected)))
        return not rejected

    def send_at(self, cmd, wait=0.3):
        """Send one AT command and return the reply.

        Blocking, which is fine: this only runs during provisioning.
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
