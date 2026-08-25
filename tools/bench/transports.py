"""Host-side transports that mirror what the website actually does on the wire.

The point of this module is fidelity, not elegance. `UsbTransport` reproduces
`usb.ts` (one stream write per logical message, newline-terminated) and
`BleTransport` reproduces `webBle.ts`/`iosBle.ts` (20-byte
write-without-response chunks paced by a fixed sleep). If those files change,
these have to change with them or the benchmark stops meaning anything.
"""

import glob
import os
import subprocess
import sys
import time

# Mirrors webBle.ts / iosBle.ts.
BLE_CHUNK_SIZE = 20
BLE_WRITE_TIMEOUT_S = 0.040

UART_SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
UART_CHARACTERISTIC_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

PICO_USB_GLOBS = ("/dev/cu.usbmodem*", "/dev/ttyACM*")


class TransportError(RuntimeError):
    pass


def find_usb_port(explicit=None):
    if explicit:
        return explicit
    env = os.environ.get("PICO_PORT")
    if env and env != "auto":
        return env
    for pattern in PICO_USB_GLOBS:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[0]
    raise TransportError(
        "no Pico serial port found (looked for %s); set PICO_PORT"
        % ", ".join(PICO_USB_GLOBS)
    )


def wait_for_usb_port(port, timeout=15.0):
    """A hard reset makes the CDC device vanish and come back under the same name."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(port):
            # The node appears slightly before the endpoint will accept a open().
            time.sleep(0.4)
            return True
        time.sleep(0.1)
    return False


class Transport:
    """Minimal duplex byte pipe. Subclasses do the platform work."""

    name = "abstract"
    #: True when the transport must chunk writes itself (BLE MTU).
    chunked = False

    def open(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    def write_raw(self, data):
        """Push bytes with no framing of any kind."""
        raise NotImplementedError

    def read_available(self):
        """Return whatever has arrived since the last call (possibly b'')."""
        raise NotImplementedError

    def drain_input(self, settle=0.3):
        """Throw away anything already buffered, e.g. boot chatter."""
        deadline = time.time() + settle
        while time.time() < deadline:
            if self.read_available():
                deadline = time.time() + settle
            time.sleep(0.02)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class UsbTransport(Transport):
    """Web Serial equivalent: an unframed, reliable byte stream at 9600 baud.

    Reproduces usb.ts, which writes each logical message as one stream write
    with a trailing newline and does no chunking.
    """

    name = "usb"
    chunked = False

    def __init__(self, port=None, baudrate=9600):
        self.port_name = find_usb_port(port)
        self.baudrate = baudrate
        self._serial = None

    def open(self):
        try:
            import serial
        except ImportError as exc:  # pragma: no cover - dependency check
            raise TransportError("pyserial is required for the USB transport") from exc

        if not wait_for_usb_port(self.port_name):
            raise TransportError("serial port %s never appeared" % self.port_name)

        try:
            self._serial = serial.Serial(self.port_name, self.baudrate, timeout=0)
        except Exception as exc:
            raise TransportError(
                "could not open %s (%s). Close the REPL/Thonny first -- only one "
                "client may hold the port." % (self.port_name, exc)
            ) from exc

    def close(self):
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def write_raw(self, data):
        if self._serial is None:
            raise TransportError("USB transport is not open")
        self._serial.write(data)
        self._serial.flush()

    def read_available(self):
        if self._serial is None:
            return b""
        waiting = self._serial.in_waiting
        if not waiting:
            return b""
        return self._serial.read(waiting)


class BleTransport(Transport):
    """HM-10 over GATT, driven exactly like the browser drives it.

    bleak is asyncio-only, so this runs a private event loop and blocks -- the
    harness is a synchronous script and the extra machinery is not worth it.
    """

    name = "ble"
    chunked = True

    def __init__(self, address=None, name_prefix="RoBox", scan_timeout=10.0):
        self.address = address
        self.name_prefix = name_prefix
        self.scan_timeout = scan_timeout
        self._client = None
        self._loop = None
        self._rx = bytearray()

    # -- asyncio plumbing ------------------------------------------------
    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def open(self):
        try:
            import asyncio
            from bleak import BleakClient, BleakScanner
        except ImportError as exc:  # pragma: no cover - dependency check
            raise TransportError(
                "bleak is required for the BLE transport: python3 -m pip install --user bleak"
            ) from exc

        self._loop = asyncio.new_event_loop()

        async def connect():
            address = self.address
            if address is None:
                found = await BleakScanner.discover(timeout=self.scan_timeout, return_adv=True)
                candidates = []
                for addr, (device, adv) in found.items():
                    label = device.name or adv.local_name or ""
                    services = " ".join(adv.service_uuids or []).lower()
                    if label.startswith(self.name_prefix) or "ffe0" in services:
                        candidates.append((adv.rssi or -999, addr, label))
                if not candidates:
                    raise TransportError(
                        "no HM-10 style device found (name prefix %r or service 0xffe0). "
                        "Is the Ro/Box powered on?" % self.name_prefix
                    )
                candidates.sort(reverse=True)
                address = candidates[0][1]
                self.address = address

            client = BleakClient(address)
            await client.connect()
            await client.start_notify(UART_CHARACTERISTIC_UUID, self._on_notify)
            return client

        try:
            self._client = self._run(connect())
        except TransportError:
            raise
        except Exception as exc:
            raise TransportError("BLE connect failed: %s" % exc) from exc

    def _on_notify(self, _characteristic, data):
        self._rx.extend(bytes(data))

    def close(self):
        if self._client is not None:
            async def shutdown():
                try:
                    await self._client.stop_notify(UART_CHARACTERISTIC_UUID)
                except Exception:
                    pass
                try:
                    await self._client.disconnect()
                except Exception:
                    pass

            try:
                self._run(shutdown())
            finally:
                self._client = None
        if self._loop is not None:
            self._loop.close()
            self._loop = None

    def write_raw(self, data):
        if self._client is None:
            raise TransportError("BLE transport is not open")
        self._run(
            self._client.write_gatt_char(UART_CHARACTERISTIC_UUID, bytes(data), response=False)
        )

    def read_available(self):
        # Give the notify callbacks a chance to land before we look.
        self._run(_sleep(self._loop, 0))
        out = bytes(self._rx)
        del self._rx[:]
        return out


async def _sleep(_loop, seconds):
    import asyncio

    await asyncio.sleep(seconds)


# -- board control (over USB, via mpremote) -----------------------------------

def _mpremote(*args, port=None, timeout=40):
    port = find_usb_port(port)
    cmd = [sys.executable, "-m", "mpremote", "connect", port] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def reset_board(port=None, settle=1.5):
    """Hard reset so main.py restarts with a clean upload state.

    The port name has to be resolved *before* the reset: the CDC endpoint
    disappears while the board reboots, so a glob run afterwards finds nothing
    and would report the board as missing.
    """
    resolved = find_usb_port(port)
    result = _mpremote("reset", port=resolved)
    if result.returncode != 0:
        raise TransportError(
            "could not reset the board: %s" % (result.stderr or result.stdout).strip()
        )
    time.sleep(1.0)
    if not wait_for_usb_port(resolved):
        raise TransportError("%s did not come back after the reset" % resolved)
    # main.py needs a moment to reach its loop (and to emit its boot chatter).
    time.sleep(settle)
    return resolved


def read_board_file(remote, port=None):
    """Return the bytes of a file on the Pico, or None when it does not exist."""
    result = _mpremote("fs", "cat", remote, port=port)
    if result.returncode != 0:
        return None
    return result.stdout.encode("utf-8", "surrogateescape")


def write_board_file(remote, data, port=None):
    import tempfile

    with tempfile.NamedTemporaryFile("wb", suffix=".py", delete=False) as handle:
        handle.write(data)
        local = handle.name
    try:
        result = _mpremote("fs", "cp", local, ":" + remote, port=port)
        if result.returncode != 0:
            raise TransportError(
                "could not write %s: %s" % (remote, (result.stderr or result.stdout).strip())
            )
    finally:
        os.unlink(local)
