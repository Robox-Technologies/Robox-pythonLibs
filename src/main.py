import sys
import _thread
import machine
import os

from roboxlib import ColorSensor
from communication import (
    USBCommunication,
    BluetoothCommunuication,
    flush_outgoing_messages,
)
from framed import FRAME_PREFIX, FramedSession

# Echo received lines back as console messages. Off by default: it doubles
# upload traffic on the same 9600-baud link it would be used to diagnose.
DEBUG = False

# 1.1.0 advertises framed protocol support. The website falls back to the
# legacy path against 1.0.0, which is also how the benchmark compares them.
CURRENT_FIRMWARE_VERSION = "1.1.0"
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
# Commands
# ----------------------
# Matched against every received line, so user code equal to one of these is
# executed rather than stored. Keep in step with the website's protocol.ts.
COMMANDS = {
    "x01FIRMCHECK": "firmware_check",
    "x02BEGINUPLD": "begin_upload",
    "x03ENDUPLD": "end_upload",
    "x04STARTPROG": "start_program",
    "x05COLORCALIBRATE": "calibrate_color",
    "x06RESTART": "reset_device",
    "x07BOOTLOADER": "boot_loader",
    "x08DISCONNECT": "disconnect_device",
}


# ----------------------
# Communication setup
# ----------------------
ble = BluetoothCommunuication()
usb = USBCommunication()

communications = []
current_communication_method = None

# IMPORTANT: DO NOT redefine queue system here


if usb.available():
    communications.append(usb)

if ble.available():
    communications.append(ble)
    ble.write_message("connect", "")  # keep your original behavior


# ----------------------
# User program runner
# ----------------------
out_file = None

# One spare core, so a second concurrent run raises. Tracked so a double tap
# on Run reports something useful.
program_running = False

# Framed sessions, created on the first framed line from an interface.
framed_sessions = {}


def framed_session(comm):
    session = framed_sessions.get(comm)
    if session is None:
        session = FramedSession(comm, PROGRAM_FILENAME)
        framed_sessions[comm] = session
    return session


def upload_is_verified(comm):
    """True when this interface's last framed upload passed its checks.

    Legacy uploads have nothing to verify against, so they pass rather than
    being blocked by a check they cannot satisfy.
    """
    session = framed_sessions.get(comm)
    return session is None or session.verified


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
def handle_line(comm, line):
    """Act on one received line.

    Split out of the loop so it can drain every buffered line per iteration,
    which is what keeps the UART buffer from overflowing mid-upload.
    """
    global out_file

    if DEBUG:
        comm.write_message(
            "console", "Received over {}: {}".format(comm.name, line)
        )

    # A frame is one line starting with SOH, which legacy text cannot contain,
    # so the two protocols share the link without a mode switch.
    if line.startswith(FRAME_PREFIX):
        for name in framed_session(comm).feed(line):
            dispatch_command(comm, name)
        return

    command = COMMANDS.get(line.strip())

    # Mid-upload the only legitimate command is end_upload; anything else is
    # far more likely to be user code colliding with the table, so store it.
    # Without this, a program containing x04STARTPROG starts the motors,
    # x02BEGINUPLD truncates the file, and x07BOOTLOADER drops into BOOTSEL.
    # Shrinks the injection surface to one string.
    if out_file and command != "end_upload":
        command = None

    if command in ("begin_upload", "end_upload"):
        handle_legacy_upload(comm, command)
    elif command:
        dispatch_command(comm, command)
    elif out_file:
        LED.toggle()
        out_file.write(line + "\n")


def handle_legacy_upload(comm, command):
    """The unframed upload path, kept for firmware 1.0.0 clients."""
    global out_file

    if command == "begin_upload":
        if out_file:
            # A second begin without an end means the first never finished.
            try:
                out_file.close()
            except Exception:
                pass
        try:
            os.remove(PROGRAM_FILENAME)
        except OSError:
            pass
        out_file = open(PROGRAM_FILENAME, "w")
        return

    if out_file:
        out_file.close()
        out_file = None
        comm.write_message("download", "")
    else:
        comm.write_message("error", "No upload in progress")


def dispatch_command(comm, command):
    """Act on a control command, however it arrived."""
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
        # Tabled with no branch before now, so bootloaderMode() was a no-op
        # and mid-upload the line was written into program.py instead.
        machine.bootloader()

    # ----------------------
    # Disconnect
    # ----------------------
    elif command == "disconnect_device":
        # Same story as boot_loader: tabled, unhandled, stored as text.
        if comm == current_communication_method:
            current_communication_method = None

        if ble in communications and ble.sleeping:
            ble.wake()

        if usb in communications and usb.sleeping:
            usb.wake()


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
