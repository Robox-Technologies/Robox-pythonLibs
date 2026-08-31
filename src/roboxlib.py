#
# _____  ____ _____  ____ __  __
# | () )/ () \| () )/ () \\ \/ /
# |_|\_\\____/|_()_)\____//_/\_\
#
# Ro/Box library to interface with other components.
# All code is written by Yuma Soerianto and Sebastien Taylor!
# Copyright (c) 2023-2026 Robox Technologies Pty Ltd
#

from machine import Pin, PWM, time_pulse_us, I2C
from utime import sleep, sleep_us, ticks_diff, ticks_ms

import ustruct
import json

from calibration import DEFAULT as _CALIBRATION_DEFAULT
from calibration import normalise as _normalise_calibration
from calibration import scale as _scale_calibration

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
_CALIBRATION_SAMPLES = 10

_MIN_S = 1300
_MAX_S = 8500

_MIN_ANGLE = 0
_MAX_ANGLE = 180


class Motors:
    def __init__(self, a1_pin=13, a2_pin=12, b1_pin=11, b2_pin=10):
        self.a1 = PWM(Pin(a1_pin, Pin.OUT))
        self.a1.freq(500)
        self.a2 = PWM(Pin(a2_pin, Pin.OUT))
        self.a2.freq(500)

        self.b1 = PWM(Pin(b1_pin, Pin.OUT))
        self.b1.freq(500)
        self.b2 = PWM(Pin(b2_pin, Pin.OUT))
        self.b2.freq(500)

    def run_motor(self, motor, speed):
        speed = min(100, max(-100, speed))
        pwm_duty = abs(int(speed/100*65025))

        if motor == 1:
            self.a1.duty_u16(pwm_duty if speed > 0 else 0)
            self.a2.duty_u16(0 if speed > 0 else pwm_duty)
        if motor == 2:
            self.b1.duty_u16(pwm_duty if speed > 0 else 0)
            self.b2.duty_u16(0 if speed > 0 else pwm_duty)

    def run_motors(self, left_speed, right_speed):
        self.run_motor(1, left_speed)
        self.run_motor(2, right_speed)

    def stop_motors(self):
        self.run_motors(0, 0)

    def run_motors_for_time(self, left_speed, right_speed, time):
        self.run_motors(left_speed, right_speed)
        sleep(time)
        self.stop_motors()

    def _motor_power(self, orientation, direction, speed):
        speed = abs(speed)
        if speed == 0:
            return 0
        return min(speed, max(-speed, speed - orientation*direction/(50/speed)))
    def steer_motors(self, direction, speed):
        left_motor_power = self._motor_power(-1, direction, speed)
        right_motor_power = self._motor_power(1, direction, speed)
        self.run_motors(left_motor_power, right_motor_power)

    def steer_motors_for_time(self, direction, speed, time):
        self.steer_motors(direction, speed)
        sleep(time)
        self.stop_motors()

class UltrasonicSensor:
    def __init__(self, trig_pin=4, echo_pin=5, echo_timeout=500*2*30):
        self.echo_timeout = echo_timeout

        self.trig = Pin(trig_pin, Pin.OUT)
        self.trig.value(0)

        self.echo = Pin(echo_pin, Pin.IN)

        self.last_distance = None
        self.last_read_time = 0
        self.min_interval_ms = 50  # 20Hz max refresh

        self.FALLBACK_ECHO = echo_timeout*1.2 # Some fallback beyond timeout range

    def convert_us_to_cm(self, us_time):
        return us_time / (10_000 / 343)
    def distance(self):
        now = ticks_ms()

        # If called too soon, return cached value
        if ticks_diff(now, self.last_read_time) < self.min_interval_ms:
            return self.last_distance if self.last_distance is not None else 999

        self.last_read_time = now

        new_distance = self._single_read()

        self.last_distance = new_distance
        return new_distance
    def _single_read(self):
        # Ensure trig is LOW
        self.trig.value(0)
        sleep_us(5)

        # Trig: 10us pulse
        self.trig.value(1)
        sleep_us(10)
        self.trig.value(0)

        echo_time = 0

        try:
            echo_time = time_pulse_us(self.echo, 1, self.echo_timeout)
            if echo_time < 0:
                echo_time = self.FALLBACK_ECHO
        except OSError as ex:

            print("Error obtaining ultrasonic value.")
            echo_time = self.FALLBACK_ECHO

        return self.convert_us_to_cm(echo_time/2) # Halve time to remove return trip time
class Servo:
    def __init__(self, pin=20):
        self.servo = PWM(Pin(pin))
        self.servo.freq(50)
    def angle_to_pulse(self, angle):
        mapped = (angle - _MIN_ANGLE) * (_MAX_S - _MIN_S) / (_MAX_ANGLE - _MIN_ANGLE) + _MIN_S
        return int(max(min(mapped, _MAX_S), _MIN_S))
    def rotate_to_angle(self, angle):
        pulse = self.angle_to_pulse(angle)
        self.servo.duty_u16(pulse)
        sleep(0.2)

class LineSensors:
    def __init__(self, left_pin=3, right_pin=2):
        self.sensor_left = Pin(left_pin, Pin.IN)
        self.sensor_right = Pin(right_pin, Pin.IN)

    def read_line_position(self):
        return [self.sensor_left.value(), self.sensor_right.value()]

class ColorSensor:
    def __init__(self, i2c=I2C(0, sda=Pin(20), scl=Pin(21)), address=0x29):
        self.i2c = i2c
        self.address = address
        self._active = False
        self.integration_time(2.4)
        self.gain(60)
        self.active(True)
        self.loadCalibration()

        sensor_id = self.sensor_id()
        if sensor_id not in (0x44, 0x10, 0x4d):
            raise RuntimeError("wrong sensor id 0x{:x}".format(sensor_id))

    def loadCalibration(self):
        # Load calibration data
        config = { _CALIBRATION_KEY: _CALIBRATION_DEFAULT }

        try:
            with open('config.json', 'r') as configFile:
                config = json.load(configFile)
        except:
            with open('config.json', 'w') as configFile:
                json.dump(config, configFile)

        self.calibration = _normalise_calibration(config.get(_CALIBRATION_KEY))

    def calibrate_white(self):
        # Calibration, two steps:
        # 1. Place Ro/Box on a white reference surface, call this.
        # 2. Place Ro/Box on a black reference (or block all light reaching
        #    the sensor), call calibrate_black().
        # Either step alone still improves on the default; both together is
        # what gives accurate readings across the full brightness range.
        self.calibration["white"] = self._extreme_raw_samples(max)
        self._save_calibration()

    def calibrate_black(self):
        self.calibration["black"] = self._extreme_raw_samples(min)
        self._save_calibration()

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

    def _save_calibration(self):
        with open('config.json', 'w') as configFile:
            json.dump({ _CALIBRATION_KEY: self.calibration }, configFile)

    def resetCalibration(self):
        self.calibration = {
            name: list(values) for name, values in _CALIBRATION_DEFAULT.items()
        }
        self._save_calibration()

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
            # & 0b11 preserves only the two LSBs
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

        # No light: return black (prevent div 0 error)
        if c == 0:
            return 0, 0, 0

        red = pow((int((r/c) * 256) / 255), 2.5) * c
        green = pow((int((g/c) * 256) / 255), 2.5) * c
        blue = pow((int((b/c) * 256) / 255), 2.5) * c

        return red, green, blue

    def _calibrated_rgb(self, rgb):
        return _scale_calibration(rgb, self.calibration)
