import sys
import _thread
import machine
import time

from roboxlib import (
    ColorSensor,
    Motors,
    load_motor_calibration,
    load_motor_reverse,
    load_motor_swap,
    save_motor_calibration,
    save_motor_reverse,
    save_motor_swap,
)
from colors import closest_color_name
from communication import (
    USBCommunication,
    BluetoothCommunuication,
    flush_outgoing_messages,
)
from framed import FRAME_PREFIX, FramedSession
from protocol import (
    CALIBRATE_MOTORS_PREFIX,
    GET_CALIBRATION_PREFIX,
    parse_motor_calibration,
)

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

# Fixed speed for run_motor_0/1: a wiring/calibration check, not a program,
# so it does not need a variable speed.
TEST_MOTOR_SPEED = 70

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
def _motor_calibration():
    """Everything Motors.run_motors applies, in one reply: bias, each
    side's reversal, and whether left/right are swapped."""
    return {
        "bias": load_motor_calibration(),
        "reverse": [load_motor_reverse(0), load_motor_reverse(1)],
        "swap": load_motor_swap(),
    }


# One entry per calibration a client can read back with
# `get_calibration_<name>`. Add to this and to COMMAND_NAMES in protocol.py
# together when a new calibration needs to be queryable.
CALIBRATION_GETTERS = {
    "motors": _motor_calibration,
}


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
        # A run_motor_0/1 test-drive left running must not fight the program
        # for the same pins. Stopped unconditionally, before either check
        # below, since the safety concern applies even to a start attempt
        # that is about to be refused.
        Motors().stop_motors()

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
    # Colour calibration: one command per preselected colour, each with its
    # own reset, used by colour mode to name a reading against swatches
    # actually seen by this sensor rather than an idealised RGB guess. Each
    # colour calibrates and resets independently; the whitelist in
    # protocol.py is what limits `command` to a real colour name here.
    #
    # White and black are not stored like the others: they are the
    # sensor's own brightness extremes, so calibrating (or resetting) them
    # instead acts on the white/black points the rest of readColor() scales
    # against (a white point alone cannot correct the sensor's dark offset,
    # which is why both matter). A point captured after that will already
    # land near (255,255,255) or (0,0,0) for a genuine white/black swatch,
    # so no separate entry is needed for either.
    # ----------------------
    elif command.startswith("calibrate_color_"):
        name = command[len("calibrate_color_"):]
        if not colorSensor:
            comm.write_message("error", "Color sensor not connected")
        elif name == "white":
            colorSensor.calibrate_white()
            comm.write_message("calibrated", "white")
        elif name == "black":
            colorSensor.calibrate_black()
            comm.write_message("calibrated", "black")
        else:
            colorSensor.calibrate_palette(name)
            comm.write_message("calibrated", name)

    elif command.startswith("reset_color_"):
        name = command[len("reset_color_"):]
        if not colorSensor:
            comm.write_message("error", "Color sensor not connected")
        elif name == "white":
            colorSensor.reset_white()
            comm.write_message("calibrated", "white_reset")
        elif name == "black":
            colorSensor.reset_black()
            comm.write_message("calibrated", "black_reset")
        else:
            colorSensor.reset_palette(name)
            comm.write_message("calibrated", name + "_reset")

    # ----------------------
    # Motor calibration: a left/right trim bias, applied by every Motors
    # instance a user program creates (see Motors.run_motors in roboxlib.py).
    # No live hardware object is needed here, unlike colour calibration: this
    # only ever persists a number for the next Motors() to pick up.
    # ----------------------
    elif command.startswith(CALIBRATE_MOTORS_PREFIX):
        save_motor_calibration(parse_motor_calibration(command))
        comm.write_message("calibrated", "motors")

    # ----------------------
    # Motor reversal: sets one logical side's spin direction, for a motor
    # wired backwards. Independent of swap_motors below: swap decides which
    # physical motor serves which side, this corrects that side's polarity
    # once it has been decided. `reverse_motor_<index>_<value>`, 0 is left
    # and 1 is right, matching CALIBRATION_GETTERS' "reverse_0"/"reverse_1"
    # read back below. Absolute set rather than a toggle: a command frame
    # is not deduplicated the way a data frame is (see FramedSession._apply
    # in framed.py), so a resent frame must be a no-op, not a second flip.
    # ----------------------
    elif command.startswith("reverse_motor_"):
        index_str, value_str = command[len("reverse_motor_"):].split("_")
        index = int(index_str)
        save_motor_reverse(index, value_str == "1")
        comm.write_message("calibrated", "reverse_%d" % index)

    # ----------------------
    # Motor swap: the motor wired to the left side answers to right_speed
    # and vice versa, for a robot with its motors crossed. `swap_motors_0`
    # or `swap_motors_1`, same absolute-set reasoning as reversal above.
    # ----------------------
    elif command.startswith("swap_motors_"):
        value_str = command[len("swap_motors_"):]
        save_motor_swap(value_str == "1")
        comm.write_message("calibrated", "swap")

    # ----------------------
    # Motor test-drive: run one motor at a fixed speed so a client can see
    # which physical motor spins and which way, to check wiring or a
    # calibration change. A fresh Motors() each time rather than one kept
    # around, so it always picks up whatever calibration is persisted right
    # now (see Motors.__init__ in roboxlib.py) instead of a stale snapshot
    # from whenever this module first ran. PWM keeps driving the pins after
    # the object is dropped, so nothing needs to be kept alive here.
    # ----------------------
    elif command == "run_motor_0":
        Motors().run_motors(TEST_MOTOR_SPEED, 0)

    elif command == "run_motor_1":
        Motors().run_motors(0, TEST_MOTOR_SPEED)

    elif command == "stop_motors":
        Motors().stop_motors()

    # ----------------------
    # Calibration readback: one command, one reply shape, for every
    # calibration listed in CALIBRATION_GETTERS above. `name` is always a
    # known key here, since the frame layer already refused anything not
    # literally in COMMAND_NAMES.
    # ----------------------
    elif command.startswith(GET_CALIBRATION_PREFIX):
        name = command[len(GET_CALIBRATION_PREFIX):]
        comm.write_message(
            "calibration", {"name": name, "value": CALIBRATION_GETTERS[name]()}
        )

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
            "name": closest_color_name((r, g, b), colorSensor.palette),
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
