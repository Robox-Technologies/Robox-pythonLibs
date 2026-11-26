"""Public robot hardware API.

Implementation is split into hardware modules; this facade keeps user programs
and the firmware command layer on one explicit import surface.
"""

from hardware import (
    ColorSensor,
    DEFAULT_BLE_NAME,
    LineSensors,
    Motors,
    Servo,
    UltrasonicSensor,
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
from colors import hsv_to_rgb, rgb_to_hsv
