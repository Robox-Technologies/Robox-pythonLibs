from machine import I2C, Pin
from utime import sleep_us
import ustruct

from colors import (
    DEFAULT as _CALIBRATION_DEFAULT,
    STANDARD_COLORS as _STANDARD_COLORS,
    apply as _apply_matrix,
    closest_color_name as _closest_color_name,
    fit as _fit_matrix,
    normalise as _normalise_calibration,
    scale as _scale_calibration,
)
from .storage import _read_config, _write_config

_COMMAND_BIT = const(0x80)
_REGISTER_ENABLE = const(0x00)
_REGISTER_ATIME = const(0x01)
_REGISTER_CONTROL = const(0x0f)
_REGISTER_SENSORID = const(0x12)
_REGISTER_STATUS = const(0x13)
_REGISTER_CDATA = const(0x14)
_REGISTER_RDATA = const(0x16)
_REGISTER_GDATA = const(0x18)
_REGISTER_BDATA = const(0x1a)
_ENABLE_AEN = const(0x02)
_ENABLE_PON = const(0x01)
_GAINS = (1, 4, 16, 60)
_CALIBRATION_KEY = "colorCalibration"
_SAMPLES_KEY = "colorSamples"
_CALIBRATION_SAMPLES = 10

class ColorSensor:
    def __init__(self, i2c=I2C(0, sda=Pin(20), scl=Pin(21)), address=0x29):
        self.i2c = i2c
        self.address = address
        self._active = False
        self.integration_time(24)
        self.gain(16)
        self.active(True)
        self.loadCalibration()

        sensor_id = self.sensor_id()
        if sensor_id not in (0x44, 0x10, 0x4d):
            raise RuntimeError("wrong sensor id 0x{:x}".format(sensor_id))

    def loadCalibration(self):
        config = _read_config()
        self.calibration = _normalise_calibration(config.get(_CALIBRATION_KEY))
        self.samples = dict(config.get(_SAMPLES_KEY) or {})
        self._recompute()

    def calibrate_white(self):
        self.calibration["white"] = self._extreme_raw_samples(max)
        self._recalibrate()

    def calibrate_black(self):
        self.calibration["black"] = self._extreme_raw_samples(min)
        self._recalibrate()

    def reset_white(self):
        self.calibration["white"] = list(_CALIBRATION_DEFAULT["white"])
        self._recalibrate()

    def reset_black(self):
        self.calibration["black"] = list(_CALIBRATION_DEFAULT["black"])
        self._recalibrate()

    def calibrate_palette(self, name):
        self.samples[name] = self._average_raw_samples()
        self._recalibrate()

    def reset_palette(self, name):
        self.samples.pop(name, None)
        self._recalibrate()

    def _recalibrate(self):
        self._recompute()
        self._save_config()

    def _recompute(self):
        """Refit the correction matrix from whatever is calibrated right
        now, then re-derive the palette through it.

        Both are recomputed from scratch rather than adjusted incrementally,
        because every point's fitted matrix changes when any one point
        does: there's no cheaper way to keep them consistent.
        """
        black = self.calibration["black"]
        white = self.calibration["white"]

        def subtract_black(raw):
            return [raw[i] - black[i] for i in range(3)]

        points = [(subtract_black(white), list(_STANDARD_COLORS["white"]))]
        for name, raw in self.samples.items():
            points.append((subtract_black(raw), list(_STANDARD_COLORS[name])))

        self._matrix = _fit_matrix(points)
        self.palette = {
            name: self._calibrated_rgb(raw) for name, raw in self.samples.items()
        }

    def _extreme_raw_samples(self, extreme, count=_CALIBRATION_SAMPLES):
        """The min or max, per channel, of several raw readings.

        Not the average: for the white point, a brief dip in ambient light
        must not lower it below what the sensor can genuinely see, or real
        bright readings later clip to 255 and lose the top of the range.
        Symmetrically, the black point must not be nudged up by a stray
        flicker, or real dark readings clip to 0. `extreme` is `max` for
        white, `min` for black, each erring the safe way.
        """
        samples = [self.readColor(raw=True) for _ in range(count)]
        return [extreme(channel) for channel in zip(*samples)]

    def _average_raw_samples(self, count=_CALIBRATION_SAMPLES):
        samples = [self.readColor(raw=True) for _ in range(count)]
        return [sum(channel) / count for channel in zip(*samples)]

    def _save_config(self):
        _write_config(
            { _CALIBRATION_KEY: self.calibration, _SAMPLES_KEY: self.samples }
        )

    def resetCalibration(self):
        self.calibration = {
            name: list(values) for name, values in _CALIBRATION_DEFAULT.items()
        }
        self._recalibrate()

    def active(self, value=None):
        if value is None:
            return self._active
        value = bool(value)
        if self._active == value:
            return
        self._active = value
        enable = self._register8(_REGISTER_ENABLE)
        if value:
            self._register8(_REGISTER_ENABLE, enable | _ENABLE_PON)
            sleep_us(3000)
            self._register8(_REGISTER_ENABLE,
                enable | _ENABLE_PON | _ENABLE_AEN)
        else:
            self._register8(_REGISTER_ENABLE,
                enable & ~(_ENABLE_PON | _ENABLE_AEN))

    def sensor_id(self):
        return self._register8(_REGISTER_SENSORID)

    def integration_time(self, value=None):
        if value is None:
            return self._integration_time
        value = min(614.4, max(2.4, value))
        cycles = int(value / 2.4)
        self._integration_time = cycles * 2.4
        return self._register8(_REGISTER_ATIME, 256 - cycles)

    def gain(self, value):
        if value is None:
            return _GAINS[self._register8(_REGISTER_CONTROL) & 0b11]
        if value not in _GAINS:
            raise ValueError("gain must be 1, 4, 16 or 60")
        return self._register8(_REGISTER_CONTROL, _GAINS.index(value))

    def readColor(self, raw=False):
        was_active = self.active()
        self.active(True)
        while not self._valid():
            sleep_us(int(self._integration_time + 0.9) * 1000)
        data = tuple(self._register16(register) for register in (
            _REGISTER_RDATA,
            _REGISTER_GDATA,
            _REGISTER_BDATA,
            _REGISTER_CDATA,
        ))
        self.active(was_active)

        rgb = self._parse_rgb(data)
        if raw:
            return rgb
        else:
            r, g, b = self._calibrated_rgb(rgb)
            return r, g, b

    def closest_colour_name(self, rgb=None):
        """The name of the palette colour nearest `rgb` (a fresh reading by
        default), using this sensor's own calibrated palette."""
        if rgb is None:
            rgb = self.readColor()
        return _closest_color_name(rgb, self.palette)

    def _register8(self, register, value=None):
        register |= _COMMAND_BIT
        if value is None:
            return self.i2c.readfrom_mem(self.address, register, 1)[0]
        data = ustruct.pack('<B', value)
        self.i2c.writeto_mem(self.address, register, data)

    def _register16(self, register, value=None):
        register |= _COMMAND_BIT
        if value is None:
            data = self.i2c.readfrom_mem(self.address, register, 2)
            return ustruct.unpack('<H', data)[0]
        data = ustruct.pack('<H', value)
        self.i2c.writeto_mem(self.address, register, data)

    def _valid(self):
        return bool(self._register8(_REGISTER_STATUS) & 0x01)

    def _parse_rgb(self, data):
        r, g, b, c = data

        if c == 0:
            return 0, 0, 0

        red = pow((int((r/c) * 256) / 255), 2.5) * c
        green = pow((int((g/c) * 256) / 255), 2.5) * c
        blue = pow((int((b/c) * 256) / 255), 2.5) * c

        return red, green, blue

    def _calibrated_rgb(self, rgb):
        if self._matrix is None:
            return _scale_calibration(rgb, self.calibration)
        black = self.calibration["black"]
        subtracted = [rgb[i] - black[i] for i in range(3)]
        return _apply_matrix(self._matrix, subtracted)




def color_calibration_status(sensor):
    if sensor is None:
        return {name: False for name in _STANDARD_COLORS}
    status = {}
    for name in _STANDARD_COLORS:
        if name == "white":
            status[name] = sensor.calibration["white"] != list(_CALIBRATION_DEFAULT["white"])
        elif name == "black":
            status[name] = sensor.calibration["black"] != list(_CALIBRATION_DEFAULT["black"])
        else:
            status[name] = name in sensor.samples
    return status
