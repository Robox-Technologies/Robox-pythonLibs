"""Persistent board configuration."""

import json

_CONFIG_FILE = "config.json"
_MOTOR_CALIBRATION_KEY = "motorCalibration"
_MOTOR_REVERSE_KEY = "motorReverse"
_MOTOR_SWAP_KEY = "motorSwap"
_BLE_NAME_KEY = "bleName"
_BLE_CONFIGURED_KEY = "bleConfigured"
DEFAULT_BLE_NAME = "Robox"


def _read_config():
    try:
        with open(_CONFIG_FILE, "r") as config_file:
            return json.load(config_file)
    except Exception:
        return {}


def _write_config(updates):
    config = _read_config()
    config.update(updates)
    with open(_CONFIG_FILE, "w") as config_file:
        json.dump(config, config_file)


def load_motor_calibration():
    value = _read_config().get(_MOTOR_CALIBRATION_KEY, 0.0)
    return value if isinstance(value, (int, float)) and -1 <= value < 1 else 0.0


def save_motor_calibration(value):
    _write_config({_MOTOR_CALIBRATION_KEY: value})


def load_motor_reverse(index):
    values = _read_config().get(_MOTOR_REVERSE_KEY) or [False, False]
    return bool(values[index]) if index < len(values) else False


def save_motor_reverse(index, value):
    values = list(_read_config().get(_MOTOR_REVERSE_KEY) or [False, False])
    values[index] = bool(value)
    _write_config({_MOTOR_REVERSE_KEY: values})


def load_motor_swap():
    return bool(_read_config().get(_MOTOR_SWAP_KEY, False))


def save_motor_swap(value):
    _write_config({_MOTOR_SWAP_KEY: bool(value)})


def load_ble_name():
    value = _read_config().get(_BLE_NAME_KEY)
    return value if isinstance(value, str) and value else DEFAULT_BLE_NAME


def load_ble_configured():
    return bool(_read_config().get(_BLE_CONFIGURED_KEY, False))


def save_ble_configured(value):
    _write_config({_BLE_CONFIGURED_KEY: bool(value)})


def save_ble_name(name):
    _write_config({_BLE_NAME_KEY: name})
