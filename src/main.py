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

# Echo every received line back as a console message. Off by default: it
# doubles upload traffic and, over BLE, competes with the upload for the same
# 9600-baud link, which makes the very corruption it would be used to diagnose
# more likely.
DEBUG = False

CURRENT_FIRMWARE_VERSION = "1.0.0"
PROGRAM_FILENAME = "program.py"

# Backstop on how many lines one interface may hand over per loop iteration, so
# a flood cannot starve the outgoing queue. The receive buffer is 4KB, so a full
# drain is around a hundred lines anyway.
MAX_LINES_PER_POLL = 128

# ----------------------
# Hardware setup
# ----------------------
LED = machine.Pin(25, machine.Pin.OUT)
LED.on()

# None rather than False: `False` made pyright narrow the successful branch to
# Literal[True] and flag `.calibrate()` as an unknown attribute, which is the
# reportAttributeAccessIssue noted in docs/VSCODE.md.
colorSensor = None
try:
    colorSensor = ColorSensor()
except Exception:
    colorSensor = None


# ----------------------
# Commands
# ----------------------
# NOTE: these are matched against every received line, so a line of *user code*
# equal to one of them is executed instead of being stored. Keep in step with
# COMMANDS in Robox-Website/src/libs/communication/protocol.ts.
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

# The RP2040 has exactly one spare core, so a second concurrent run raises
# instead of starting. Tracked so a double tap on Run reports a useful error.
program_running = False


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

    Split out of the main loop so the loop can drain every buffered line per
    iteration instead of one, which is what keeps the UART buffer from
    overflowing mid-upload.
    """
    global out_file, current_communication_method, program_running

    if DEBUG:
        comm.write_message(
            "console", "Received over {}: {}".format(comm.name, line)
        )

    command = COMMANDS.get(line.strip())

    # While an upload is in flight, the only command that can legitimately
    # arrive is end_upload. Anything else is far more likely to be a line of
    # user code that collides with the command table, so store it rather than
    # act on it.
    #
    # This is what keeps the in-band dispatch from being actively dangerous in
    # the meantime: without it a program containing `x04STARTPROG` starts the
    # motors mid-upload, `x02BEGINUPLD` truncates the file, and `x07BOOTLOADER`
    # drops the board into BOOTSEL. It shrinks the injection surface to a single
    # string, which the framed protocol then removes entirely.
    if out_file and command != "end_upload":
        command = None

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
        comm.write_message("firmware", CURRENT_FIRMWARE_VERSION)

    # ----------------------
    # Start program
    # ----------------------
    elif command == "start_program":
        if program_running:
            comm.write_message("error", "A program is already running")
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
    # Begin upload
    # ----------------------
    elif command == "begin_upload":
        if out_file:
            # A second begin without an end means the first upload never
            # finished. Close the handle rather than leaking it.
            try:
                out_file.close()
            except Exception:
                pass

        try:
            os.remove(PROGRAM_FILENAME)
        except OSError:
            pass

        out_file = open(PROGRAM_FILENAME, "w")

    # ----------------------
    # End upload
    # ----------------------
    elif command == "end_upload":
        if out_file:
            out_file.close()
            out_file = None
            comm.write_message("download", "")
        else:
            comm.write_message("error", "No upload in progress")

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
        # Previously in the command table with no branch here, so the site's
        # bootloaderMode() was a silent no-op -- and worse, during an upload the
        # line fell through to the storage arm and was written into program.py.
        machine.bootloader()

    # ----------------------
    # Disconnect
    # ----------------------
    elif command == "disconnect_device":
        # Same story as boot_loader: tabled, unhandled, and stored as program
        # text if it arrived mid-upload.
        if comm == current_communication_method:
            current_communication_method = None

        if ble in communications and ble.sleeping:
            ble.wake()

        if usb in communications and usb.sleeping:
            usb.wake()

    # ----------------------
    # Program upload stream
    # ----------------------
    elif out_file:
        LED.toggle()
        out_file.write(line + "\n")


# ----------------------
# Main loop
# ----------------------
while True:
    flush_outgoing_messages()

    for comm in communications:
        if comm.sleeping:
            continue

        # Drain everything buffered. Taking a single line per iteration meant a
        # burst arriving faster than the loop spun sat in the UART buffer until
        # it overflowed, silently losing bytes mid-line.
        for line in comm.read_lines(MAX_LINES_PER_POLL):
            handle_line(comm, line)
