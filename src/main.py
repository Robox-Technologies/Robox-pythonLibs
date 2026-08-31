import sys
import _thread
import machine
import time

from roboxlib import ColorSensor
from colors import closest_color_name
from communication import (
    USBCommunication,
    BluetoothCommunuication,
    flush_outgoing_messages,
)
from framed import FRAME_PREFIX, FramedSession

# 2.0.0 is frame-only. There is no unframed path any more, so a client still
# speaking the old bare-line protocol gets no response and must update. 2.0.1
# survives link noise in front of a frame; same wire protocol, so the client's
# 2.0.0 minimum is unchanged.
CURRENT_FIRMWARE_VERSION = "2.0.1"
PROTOCOL_VERSION = 2

PROGRAM_FILENAME = "program.py"

# Backstop so a flood cannot starve the outgoing queue. A full 4KB buffer is
# around a hundred lines anyway.
MAX_LINES_PER_POLL = 128

# How often a reading goes out while colour mode is active.
COLOR_MODE_INTERVAL_MS = 250

# ----------------------
# Hardware setup
# ----------------------
LED = machine.Pin(25, machine.Pin.OUT)
LED.on()

# None, not False: False narrowed the success branch to Literal[True] and made
# pyright flag .calibrate_white() as unknown.
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

# The interface currently subscribed to periodic colour readings, or None.
# Not a mode a client can get stuck in: any other command clears it.
color_mode_comm = None
last_color_send = 0

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
    global color_mode_comm, last_color_send

    # Colour mode is a side channel, not a state machine of its own: anything
    # else the client sends means it has moved on, so it is cleared here
    # rather than left for the client to remember to turn off.
    if command != "color_mode":
        color_mode_comm = None

    # ----------------------
    # Firmware check
    # ----------------------
    if command == "firmware_check":
        # Handed over rather than refused, and nothing is slept. The board
        # never learns that a Bluetooth central went away, so a claim only
        # clears explicitly: refusing the next client stranded the board until
        # a power cycle, and a slept interface could only be woken over the
        # other one. A firmware check is a fresh client announcing itself.
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
    # Color calibration. white/black is a per-channel gain+offset fix: any
    # one command improves on the default, and running both fixes the
    # sensor's dark offset too. red/green/blue build a correction matrix on
    # top of that, and are what actually fixes crosstalk between channels -
    # e.g. green reading as cyan - which white/black alone cannot.
    # ----------------------
    elif command == "calibrate_color_white":
        if not colorSensor:
            comm.write_message("error", "Color sensor not connected")
        else:
            colorSensor.calibrate_white()
            comm.write_message("calibrated", "white")

    elif command == "calibrate_color_black":
        if not colorSensor:
            comm.write_message("error", "Color sensor not connected")
        else:
            colorSensor.calibrate_black()
            comm.write_message("calibrated", "black")

    elif command == "calibrate_color_red":
        if not colorSensor:
            comm.write_message("error", "Color sensor not connected")
        else:
            colorSensor.calibrate_red()
            comm.write_message("calibrated", "red")

    elif command == "calibrate_color_green":
        if not colorSensor:
            comm.write_message("error", "Color sensor not connected")
        else:
            colorSensor.calibrate_green()
            comm.write_message("calibrated", "green")

    elif command == "calibrate_color_blue":
        if not colorSensor:
            comm.write_message("error", "Color sensor not connected")
        else:
            colorSensor.calibrate_blue()
            comm.write_message("calibrated", "blue")

    # ----------------------
    # Colour mode: periodic readings until something else is sent
    # ----------------------
    elif command == "color_mode":
        if not colorSensor:
            comm.write_message("error", "Color sensor not connected")
        else:
            color_mode_comm = comm
            # Due immediately rather than after one interval, so switching the
            # mode on feels instant.
            last_color_send = time.ticks_add(
                time.ticks_ms(), -COLOR_MODE_INTERVAL_MS
            )

    # ----------------------
    # Reset device
    # ----------------------
    elif command == "reset_device":
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


def handle_line(comm, line):
    """Act on one received line.

    Only frames are accepted, but taken from wherever the sentinel is rather
    than only from the front: the module's unterminated chatter arrives glued to
    the next frame, and the first frame of a session is the firmware check. SOH
    cannot appear in a payload, so finding it is unambiguous.
    """
    start = line.find(FRAME_PREFIX)
    if start < 0:
        return

    for name in framed_session(comm).feed(line[start:]):
        dispatch_command(comm, name)


def poll(comm):
    """Read and act on everything one interface has buffered."""
    # Drain everything buffered. One line per iteration meant a fast burst sat
    # in the UART buffer until it overflowed, losing bytes mid-line.
    lines = comm.read_lines(MAX_LINES_PER_POLL)
    for line in lines:
        handle_line(comm, line)

    # Acknowledge once per drain rather than per frame: whatever arrived
    # together is one batch, which is where the round-trip saving comes from.
    if lines:
        session = framed_sessions.get(comm)
        if session is not None:
            session.flush()


def send_color_if_due():
    """Queue one colour reading, if colour mode is on and it is time."""
    global last_color_send

    if color_mode_comm is None:
        return

    now = time.ticks_ms()
    if time.ticks_diff(now, last_color_send) < COLOR_MODE_INTERVAL_MS:
        return

    last_color_send = now
    r, g, b = colorSensor.readColor()
    color_mode_comm.write_message(
        "color",
        {
            "r": round(r),
            "g": round(g),
            "b": round(b),
            "name": closest_color_name((r, g, b)),
        },
    )


# ----------------------
# Main loop
# ----------------------
while True:
    flush_outgoing_messages()

    # Every interface is read every pass. Skipping one was how the board ended
    # up unreachable over Bluetooth after a USB session.
    for comm in communications:
        poll(comm)

    send_color_if_due()
