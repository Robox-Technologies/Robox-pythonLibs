COMMAND_NAMES = (
    "firmware_check",
    "start_program",
    "get_calibration_motors",
    "get_calibration_colors",
    "reverse_motor_0_0",
    "reverse_motor_0_1",
    "reverse_motor_1_0",
    "reverse_motor_1_1",
    "swap_motors_0",
    "swap_motors_1",
    "move_forward",
    "move_backward",
    "move_left",
    "move_right",
    "stop_motors",
    "calibrate_color_red",
    "calibrate_color_orange",
    "calibrate_color_yellow",
    "calibrate_color_green",
    "calibrate_color_blue",
    "calibrate_color_purple",
    "calibrate_color_black",
    "calibrate_color_white",
    "reset_color_red",
    "reset_color_orange",
    "reset_color_yellow",
    "reset_color_green",
    "reset_color_blue",
    "reset_color_purple",
    "reset_color_black",
    "reset_color_white",
    "color_mode",
    "reset_device",
    "boot_loader",
    "disconnect_device",
    "debug_status",
)

GET_CALIBRATION_PREFIX = "get_calibration_"

CALIBRATE_MOTORS_PREFIX = "calibrate_motors_"

MOTOR_BIAS_MIN = -1
MOTOR_BIAS_MAX = 1


def parse_motor_calibration(name):
    """Parse a motor bias in the supported range."""
    if not name.startswith(CALIBRATE_MOTORS_PREFIX):
        return None
    try:
        value = float(name[len(CALIBRATE_MOTORS_PREFIX):])
    except ValueError:
        return None
    if not (MOTOR_BIAS_MIN <= value < MOTOR_BIAS_MAX):
        return None
    return value


RENAME_DEVICE_PREFIX = "rename_device_"
VERIFY_PROGRAM_PREFIX = "verify_program_"

DEVICE_NAME_MAX_LENGTH = 16
_DEVICE_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def parse_device_name(name):
    """Return the device name from a valid rename command, else None."""
    if not name.startswith(RENAME_DEVICE_PREFIX):
        return None
    value = name[len(RENAME_DEVICE_PREFIX):]
    if not value or len(value) > DEVICE_NAME_MAX_LENGTH:
        return None
    if any(ch not in _DEVICE_NAME_CHARS for ch in value):
        return None
    return value


def parse_program_checksum(name):
    """Return the expected CRC-32 from a program verification command."""
    if not name.startswith(VERIFY_PROGRAM_PREFIX):
        return None
    value = name[len(VERIFY_PROGRAM_PREFIX):]
    if len(value) != 8:
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def is_command_name(name):
    """Return whether a command payload is supported."""
    return (
        name in COMMAND_NAMES
        or parse_motor_calibration(name) is not None
        or parse_device_name(name) is not None
        or parse_program_checksum(name) is not None
    )
