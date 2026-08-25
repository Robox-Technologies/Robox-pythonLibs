from machine import UART, Pin
import json
import sys
import select
import time
import _thread


# ========================
# Global outgoing queue
# ========================
outgoing_messages = []
queue_lock = _thread.allocate_lock()


def queue_outgoing_message(comm, message_type, content):
    queue_lock.acquire()
    try:
        outgoing_messages.append((comm, message_type, content))
    finally:
        queue_lock.release()


def flush_outgoing_messages():
    queue_lock.acquire()
    try:
        if not outgoing_messages:
            return

        # iterate over copy so we can rotate safely
        for i in range(len(outgoing_messages)):
            comm, message_type, content = outgoing_messages.pop(0)

            # BLE throttle check
            if hasattr(comm, "can_send_now") and not comm.can_send_now():
                # put it back at end of queue
                outgoing_messages.append(
                    (comm, message_type, content)
                )
                continue

            queue_lock.release()
            comm._write_message_now(message_type, content)
            return

    finally:
        if queue_lock.locked():
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

    def write_message(self, message_type, content):
        """
        Public API used everywhere else.
        Thread-safe and non-blocking.
        """
        queue_outgoing_message(self, message_type, content)

    def _write_message_now(self, message_type, content):
        raise NotImplementedError


# ========================
# USB
# ========================
USB_CHARGING_PIN = Pin("GPIO24", Pin.IN)


class USBCommunication(CommunicationInterface):
    def __init__(self):
        self.name = "USB"
        self.sleeping = False

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
        message = generate_message(message_type, content)
        print(message)

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
                rx=Pin(1)
            )

            self.buffer = b""
            self.ok = True

            # Rate limiting
            self.next_send_time = 0

        except:
            self.ok = False

    def available(self):
        return self.ok

    def read_line(self):
        # Return complete buffered lines first
        if b"\n" in self.buffer:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)

                if line.strip():
                    try:
                        return line.decode()
                    except:
                        return None

            return None

        if not self.uart.any():
            return None

        data = self.uart.read()

        if not data:
            return None

        self.buffer += (
            data.replace(b"\r\n", b"\n")
            .replace(b"\r", b"\n")
        )

        return self.read_line()

    def can_send_now(self):
        return time.ticks_diff(
            time.ticks_ms(),
            self.next_send_time
        ) >= 0

    def _write_message_now(self, message_type, content):
        message = generate_message(message_type, content)

        print(
            "Sending over BLE: {}".format(message)
        )

        self.uart.write(
            (message + "\n").encode()
        )

        # throttle future sends
        self.next_send_time = time.ticks_add(
            time.ticks_ms(),
            300
        )

    def write(self, data):
        self.uart.write(
            (data + "\r\n").encode()
        )

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
        except:
            decoded = str(response)

        print(
            "<<< {}".format(
                decoded if decoded else "(no response)"
            )
        )
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
