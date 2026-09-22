import sys
import _thread
import machine
import time

# Keep frozen modules ahead of loose files left on the board.
if "" in sys.path:
    sys.path.remove("")
    sys.path.append("")

from roboxlib import (
    ColorSensor,
    Motors,
    DEFAULT_BLE_NAME,
    color_calibration_status,
    load_ble_configured,
    load_motor_calibration,
    load_motor_reverse,
    load_motor_swap,
    save_ble_configured,
    save_motor_calibration,
    save_motor_reverse,
    save_motor_swap,
)
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

CURRENT_FIRMWARE_VERSION = "2.0.1"
PROTOCOL_VERSION = 2

PROGRAM_FILENAME = "program.py"

MAX_LINES_PER_POLL = 128

# How often a reading goes out while colour mode is active.
COLOR_MODE_INTERVAL_MS = 250

TEST_MOTOR_SPEED = 70

LED = machine.Pin(25, machine.Pin.OUT)
LED.on()

colorSensor = None
try:
    colorSensor = ColorSensor()
except Exception:
    colorSensor = None

motors = Motors()


ble = BluetoothCommunuication()
usb = USBCommunication()

communications = []
current_communication_method = None

if usb.available():
    communications.append(usb)

if ble.available():
    communications.append(ble)
    ble.write_message("connect", "")


def ensure_ble_configured():
    """Configure BLE once after a fresh module or config reset."""
    if not ble.available() or load_ble_configured():
        return False
    if ble.configure(DEFAULT_BLE_NAME):
        save_ble_configured(True)
        return True
    return False


program_running = False

color_mode_comm = None
last_color_send = 0

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


def _motor_calibration():
    """Everything Motors.run_motors applies, in one reply: bias, each
    side's reversal, and whether left/right are swapped."""
    return {
        "bias": load_motor_calibration(),
        "reverse": [load_motor_reverse(0), load_motor_reverse(1)],
        "swap": load_motor_swap(),
    }


def _color_calibration():
    """One boolean per standard colour (plus white/black), so a client can
    show which ones still need calibrating without asking one at a time.
    All False when there's no colour sensor connected."""
    return color_calibration_status(colorSensor)


CALIBRATION_GETTERS = {
    "motors": _motor_calibration,
    "colors": _color_calibration,
}


def dispatch_command(comm, command):
    """Act on a control command. Only ever reached from a COMMAND frame."""
    global current_communication_method, program_running
    global color_mode_comm, last_color_send

    if command != "color_mode":
        color_mode_comm = None

    if command == "firmware_check":
        current_communication_method = comm
        comm.write_message(
            "firmware",
            "%s+proto%d" % (CURRENT_FIRMWARE_VERSION, PROTOCOL_VERSION),
        )

    elif command == "start_program":
        motors.stop_motors()

        if program_running:
            comm.write_message("error", "A program is already running")
            return

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

    elif command.startswith(CALIBRATE_MOTORS_PREFIX):
        bias = parse_motor_calibration(command)
        assert bias is not None  # already validated by is_command_name
        save_motor_calibration(bias)
        motors.calibration = bias
        comm.write_message("calibrated", "motors")

    elif command.startswith("reverse_motor_"):
        index_str, value_str = command[len("reverse_motor_"):].split("_")
        index = int(index_str)
        value = value_str == "1"
        save_motor_reverse(index, value)
        motors.reverse[index] = value
        comm.write_message("calibrated", "reverse_%d" % index)

    elif command.startswith("swap_motors_"):
        value = command[len("swap_motors_"):] == "1"
        save_motor_swap(value)
        motors.swap = value
        comm.write_message("calibrated", "swap")

    elif command == "move_forward":
        motors.run_motors(TEST_MOTOR_SPEED, TEST_MOTOR_SPEED)

    elif command == "move_backward":
        motors.run_motors(-TEST_MOTOR_SPEED, -TEST_MOTOR_SPEED)

    elif command == "move_left":
        motors.run_motors(-TEST_MOTOR_SPEED, TEST_MOTOR_SPEED)

    elif command == "move_right":
        motors.run_motors(TEST_MOTOR_SPEED, -TEST_MOTOR_SPEED)

    elif command == "stop_motors":
        motors.stop_motors()

    elif command.startswith(GET_CALIBRATION_PREFIX):
        name = command[len(GET_CALIBRATION_PREFIX):]
        comm.write_message(
            "calibration", {"name": name, "value": CALIBRATION_GETTERS[name]()}
        )

    elif command == "color_mode":
        if not colorSensor:
            comm.write_message("error", "Color sensor not connected")
        else:
            color_mode_comm = comm
            last_color_send = time.ticks_add(
                time.ticks_ms(), -COLOR_MODE_INTERVAL_MS
            )

    elif command == "reset_device":
        machine.reset()

    elif command == "boot_loader":
        machine.bootloader()

    elif command == "disconnect_device":
        if comm == current_communication_method:
            current_communication_method = None


def handle_line(comm, line):
    """Act on one received line."""
    start = line.find(FRAME_PREFIX)
    if start < 0:
        return

    for name in framed_session(comm).feed(line[start:]):
        dispatch_command(comm, name)


def poll(comm):
    """Read and act on everything one interface has buffered."""
    lines = comm.read_lines(MAX_LINES_PER_POLL)
    for line in lines:
        handle_line(comm, line)

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
            "name": colorSensor.closest_colour_name((r, g, b)),
        },
    )


ensure_ble_configured()

while True:
    flush_outgoing_messages()

    for comm in communications:
        poll(comm)

    send_color_if_due()
