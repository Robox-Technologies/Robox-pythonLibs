import os
import sys
import _thread
import machine
import rp2
import time

from build_info import BUILD_TYPE, DEBUG, FIRMWARE_VERSION
from debug import configure as configure_debug
from debug import log, log_error

configure_debug()
log("boot", "started", level=1, version=FIRMWARE_VERSION, build=BUILD_TYPE)

if "" in sys.path:
    sys.path.remove("")
    sys.path.append("")

from roboxlib import (
    ColorSensor,
    Motors,
    DEFAULT_BLE_NAME,
    color_calibration_status,
    load_ble_configured,
    load_ble_name,
    load_motor_calibration,
    load_motor_reverse,
    load_motor_swap,
    save_ble_configured,
    save_ble_name,
    save_motor_calibration,
    save_motor_reverse,
    save_motor_swap,
)
from communication import (
    USBCommunication,
    BluetoothCommunication,
    NullCommunication,
    flush_outgoing_messages,
)
from framed import FRAME_PREFIX, FramedSession
from protocol import (
    CALIBRATE_MOTORS_PREFIX,
    GET_CALIBRATION_PREFIX,
    RENAME_DEVICE_PREFIX,
    VERIFY_PROGRAM_PREFIX,
    parse_device_name,
    parse_motor_calibration,
    parse_program_checksum,
)

PROTOCOL_VERSION = 2

PROGRAM_FILENAME = "program.py"
MAX_LINES_PER_POLL = 128

COLOR_MODE_INTERVAL_MS = 250

TEST_MOTOR_SPEED = 70

# BOOTSEL shares the flash chip-select line, so every read briefly pauses the
# other core (see rp2.bootsel_button). Read it rarely and ask for a hold
# instead: three pressed reads in a row is 500-750 ms of holding.
BOOTSEL_POLL_MS = 250
BOOTSEL_HOLD_POLLS = 3
REFUSE_BLINKS = 2
REFUSE_BLINK_MS = 150

LED = machine.Pin(25, machine.Pin.OUT)
LED.on()

colorSensor = None
try:
    colorSensor = ColorSensor()
except Exception as error:
    log_error("hardware", error, component="color_sensor")
    colorSensor = None

motors = Motors()
log("hardware", "motors_ready")


ble = BluetoothCommunication()
usb = USBCommunication()
if usb.available():
    configure_debug(usb)
    log("communication", "usb_ready", level=1)

communications = []
current_communication_method = None

if usb.available():
    communications.append(usb)

if ble.available():
    communications.append(ble)
    log("communication", "bluetooth_ready")
    ble.write_message("connect", "")
else:
    log("communication", "bluetooth_unavailable")


def ensure_ble_configured():
    """Provision a fresh module once, using its persisted name."""
    if not ble.available() or load_ble_configured():
        return False
    if ble.configure(load_ble_name()):
        log("communication", "bluetooth_configured", level=1)
        save_ble_configured(True)
        return True
    log("communication", "bluetooth_configuration_failed", level=1)
    return False


program_running = False

NULL_COMM = NullCommunication()

last_bootsel_read = 0
bootsel_count = 0
# Starts disarmed so a button still held through a reset does nothing until
# it has been let go once.
bootsel_armed = False

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
            "motors": motors,
            "print": sandbox_print
        }

        with open(PROGRAM_FILENAME) as f:
            code = f.read()

        exec(code, ns)

    except Exception as e:
        log_error("program", e)
        comm.write_message("error", str(e))

    finally:
        program_running = False
        log("program", "stopped", level=1)


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
        log("command", "firmware_check", level=1)
        comm.write_message(
            "firmware",
            "%s+proto%d+%s" % (FIRMWARE_VERSION, PROTOCOL_VERSION, BUILD_TYPE),
        )

    elif command == "start_program":
        log("command", "start_program", level=1)
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
        log("program", "started", level=1)

        try:
            _thread.start_new_thread(run_user_program, (comm,))
        except Exception as e:
            program_running = False
            comm.write_message("error", "Could not start program: {}".format(e))
            return

        comm.write_message("download", "")

    elif command.startswith(VERIFY_PROGRAM_PREFIX):
        expected_crc = parse_program_checksum(command)
        assert expected_crc is not None
        verified = framed_session(comm).verify_existing_program(expected_crc)
        comm.write_message("verified", {"ok": verified})

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
        assert bias is not None
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
        log("command", "color_mode")
        if not colorSensor:
            comm.write_message("error", "Color sensor not connected")
        else:
            color_mode_comm = comm
            last_color_send = time.ticks_add(
                time.ticks_ms(), -COLOR_MODE_INTERVAL_MS
            )

    elif command.startswith(RENAME_DEVICE_PREFIX):
        new_name = parse_device_name(command)
        assert new_name is not None
        comm._write_message_now("renaming", new_name)
        if ble.rename(new_name):
            save_ble_name(new_name)
            comm.write_message("renamed", new_name)
        else:
            comm.write_message("error", "Could not rename device")

    elif command == "reset_device":
        log("command", "reset_device", level=1, force_flush=True)
        motors.stop_motors()
        machine.reset()

    elif command == "boot_loader":
        machine.bootloader()

    elif command == "disconnect_device":
        if comm == current_communication_method:
            current_communication_method = None

    elif command == "debug_status":
        log("command", "debug_status")
        comm.write_message("debug", {"enabled": DEBUG, "build": BUILD_TYPE})


def handle_line(comm, line):
    """Act on one received line."""
    start = line.find(FRAME_PREFIX)
    if start < 0:
        return

    if comm is usb:
        configure_debug(usb)
    for name in framed_session(comm).feed(line[start:]):
        dispatch_command(comm, name)


def poll(comm):
    """Read and act on everything one interface has buffered."""
    lines = comm.read_lines(MAX_LINES_PER_POLL)
    for line in lines:
        session = framed_session(comm)
        try:
            handle_line(comm, line)
        except Exception as error:
            log_error("protocol", error, event="line_failed")
        finally:
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


def bootsel_held():
    """Read BOOTSEL once. True on the read that completes a hold.

    A hold fires once; the button has to be released before it can fire
    again, so holding past the threshold never repeats the action.
    """
    global bootsel_count, bootsel_armed

    if not rp2.bootsel_button():
        bootsel_count = 0
        bootsel_armed = True
        return False
    if not bootsel_armed:
        return False

    bootsel_count += 1
    if bootsel_count < BOOTSEL_HOLD_POLLS:
        return False

    bootsel_count = 0
    bootsel_armed = False
    return True


def stop_robot(reason):
    """Stop the motors, then reboot to regain control from the program thread.

    _thread has no way to kill a thread, so only a reset reliably stops
    arbitrary code. The motor pins are cut first, because user programs
    drive their own Motors() and the pins float during the reset.

    The reset waits for BOOTSEL to be let go: the boot ROM checks the
    button on every reset, and a held button boots into USB flashing mode.
    """
    log("program", "bootsel_stop", level=1, reason=reason, force_flush=True)
    motors.stop_motors()
    motors.cut_power()
    LED.off()
    while rp2.bootsel_button():
        time.sleep_ms(BOOTSEL_POLL_MS)
    machine.reset()


def stored_program_exists():
    try:
        os.stat(PROGRAM_FILENAME)
    except OSError:
        return False
    return True


def upload_in_progress():
    """True while any interface is part-way through writing program.py."""
    for session in framed_sessions.values():
        if session.out_file:
            return True
    return False


def blink_refusal():
    for _ in range(REFUSE_BLINKS):
        LED.off()
        time.sleep_ms(REFUSE_BLINK_MS)
        LED.on()
        time.sleep_ms(REFUSE_BLINK_MS)


def standalone_loop():
    """Watch only the button while the program runs. Never returns.

    No interface is read or flushed here: the robot ignores USB and
    Bluetooth until it resets. The program returning does not end this:
    website programs set the motors or timers going and return at once,
    and the robot carries on until the button is held.
    """
    while True:
        time.sleep_ms(BOOTSEL_POLL_MS)
        LED.toggle()
        if bootsel_held():
            stop_robot("button")


def start_standalone():
    """Run the stored program from the button, then stop communicating."""
    global program_running, color_mode_comm

    if program_running:
        # A program the app started: the same hold stops it.
        stop_robot("button")
        return

    motors.stop_motors()
    if upload_in_progress():
        log("program", "bootsel_upload_in_progress", level=1)
        blink_refusal()
        return
    if not stored_program_exists():
        log("program", "bootsel_no_program", level=1)
        blink_refusal()
        return

    color_mode_comm = None
    program_running = True
    log("program", "bootsel_start", level=1)
    try:
        _thread.start_new_thread(run_user_program, (NULL_COMM,))
    except Exception as error:
        program_running = False
        log_error("program", error, event="bootsel_start_failed")
        blink_refusal()
        return

    standalone_loop()


def poll_bootsel():
    """Read the button if it is due, and act on a completed hold."""
    global last_bootsel_read

    now = time.ticks_ms()
    if time.ticks_diff(now, last_bootsel_read) < BOOTSEL_POLL_MS:
        return
    last_bootsel_read = now

    held = bootsel_held()
    # Dark while the button is down, so people know to keep holding.
    if bootsel_count:
        LED.off()
    else:
        LED.on()
    if held:
        start_standalone()


ensure_ble_configured()
log("boot", "ready", level=1)

while True:
    try:
        flush_outgoing_messages()

        for comm in communications:
            poll(comm)

        send_color_if_due()
        poll_bootsel()
    except Exception as error:
        log_error("runtime", error)
