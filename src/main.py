import sys
import _thread
import machine

from roboxlib import ColorSensor
from communication import (
    USBCommunication,
    BluetoothCommunuication,
    flush_outgoing_messages,
)
from framed import FRAME_PREFIX, FramedSession

# 2.0.0 is frame-only. There is no unframed path any more, so a client still
# speaking the old bare-line protocol gets no response and must update.
CURRENT_FIRMWARE_VERSION = "2.0.0"
PROTOCOL_VERSION = 2

PROGRAM_FILENAME = "program.py"

# Backstop so a flood cannot starve the outgoing queue. A full 4KB buffer is
# around a hundred lines anyway.
MAX_LINES_PER_POLL = 128

# ----------------------
# Hardware setup
# ----------------------
LED = machine.Pin(25, machine.Pin.OUT)
LED.on()

# None, not False: False narrowed the success branch to Literal[True] and made
# pyright flag .calibrate() as unknown.
colorSensor = None
try:
    colorSensor = ColorSensor()
except Exception:
    colorSensor = None


# ----------------------
# Communication setup
# ----------------------
ble = BluetoothCommunuication()
usb = USBCommunication()

communications = []
current_communication_method = None

if usb.available():
    communications.append(usb)

if ble.available():
    communications.append(ble)
    ble.write_message("connect", "")


# ----------------------
# User program runner
# ----------------------
# One spare core, so a second concurrent run raises. Tracked so a double tap
# on Run reports something useful.
program_running = False

# Framed sessions, created on the first frame from an interface.
framed_sessions = {}


def framed_session(comm):
    session = framed_sessions.get(comm)
    if session is None:
        session = FramedSession(comm, PROGRAM_FILENAME)
        framed_sessions[comm] = session
    return session


def upload_is_verified(comm):
    """True when this interface's last upload passed its checks."""
    session = framed_sessions.get(comm)
    return session is not None and session.verified


def run_user_program(comm):
    global program_running

    try:
        sys.modules.pop("program", None)

        def sandbox_print(*args):
            msg = " ".join(str(arg) for arg in args)
            comm.write_message("console", msg)

        ns = {
            "comm": comm,
            "print": sandbox_print
        }

        with open(PROGRAM_FILENAME) as f:
            code = f.read()

        exec(code, ns)

    except Exception as e:
        comm.write_message("error", str(e))

    finally:
        program_running = False


# ----------------------
# Command handling
# ----------------------
def dispatch_command(comm, command):
    """Act on a control command.

    Commands only arrive inside a COMMAND frame, so nothing here can be
    triggered by program text. That is the whole reason the bare-line protocol
    is gone.
    """
    global current_communication_method, program_running

    # ----------------------
    # Firmware check
    # ----------------------
    if command == "firmware_check":
        if current_communication_method and current_communication_method != comm:
            comm.write_message("error", "Already connected over another interface")
            return

        if comm == usb and ble in communications:
            ble.sleep()

        elif comm == ble and usb in communications:
            usb.sleep()

        current_communication_method = comm
        comm.write_message(
            "firmware",
            "%s+proto%d" % (CURRENT_FIRMWARE_VERSION, PROTOCOL_VERSION),
        )

    # ----------------------
    # Start program
    # ----------------------
    elif command == "start_program":
        if program_running:
            comm.write_message("error", "A program is already running")
            return

        # The point of the whole exercise: refuse to run a program the board
        # cannot confirm it received intact.
        if not upload_is_verified(comm):
            comm.write_message(
                "error", "Upload did not verify, refusing to run it"
            )
            return

        LED.on()
        program_running = True

        try:
            _thread.start_new_thread(run_user_program, (comm,))
        except Exception as e:
            program_running = False
            comm.write_message("error", "Could not start program: {}".format(e))
            return

        comm.write_message("download", "")

    # ----------------------
    # Color calibration
    # ----------------------
    elif command == "calibrate_color":
        if not colorSensor:
            comm.write_message("error", "Color sensor not connected")
        else:
            colorSensor.calibrate()
            comm.write_message("calibrated", "")

    # ----------------------
    # Reset device
    # ----------------------
    elif command == "reset_device":
        if ble in communications and ble.sleeping:
            ble.wake()

        if usb in communications and usb.sleeping:
            usb.wake()

        machine.reset()

    # ----------------------
    # Bootloader
    # ----------------------
    elif command == "boot_loader":
        machine.bootloader()

    # ----------------------
    # Disconnect
    # ----------------------
    elif command == "disconnect_device":
        if comm == current_communication_method:
            current_communication_method = None

        if ble in communications and ble.sleeping:
            ble.wake()

        if usb in communications and usb.sleeping:
            usb.wake()


def handle_line(comm, line):
    """Act on one received line.

    Only frames are accepted. Anything else is either a client that has not
    been updated or noise on the link, and is ignored rather than guessed at.
    """
    if not line.startswith(FRAME_PREFIX):
        return

    for name in framed_session(comm).feed(line):
        dispatch_command(comm, name)


# ----------------------
# Main loop
# ----------------------
while True:
    flush_outgoing_messages()

    for comm in communications:
        if comm.sleeping:
            continue

        # Drain everything buffered. One line per iteration meant a fast burst
        # sat in the UART buffer until it overflowed, losing bytes mid-line.
        lines = comm.read_lines(MAX_LINES_PER_POLL)
        for line in lines:
            handle_line(comm, line)

        # Acknowledge once per drain rather than per frame: whatever arrived
        # together is one batch, which is where the round-trip saving comes
        # from.
        if lines:
            session = framed_sessions.get(comm)
            if session is not None:
                session.flush()
