from machine import UART, Pin
import time

import protocol as p

from .base import CommunicationInterface

UART_RX_BUFFER = 4096
UART_BYTES_PER_SECOND = 960
SEND_HEADROOM = 1.4
MIN_SEND_INTERVAL_MS = 15
MAX_LINE_LENGTH = 4 * (p.FRAME_OVERHEAD + p.MAX_PAYLOAD)
SOH_BYTE = bytes([p.SOH])
REBOOT_POLL_INTERVAL = 0.5
REBOOT_POLL_ATTEMPTS = 12

class BluetoothCommunication(CommunicationInterface):
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
