
# 
# _____  ____ _____  ____ __  __
# | () )/ () \| () )/ () \\ \/ /
# |_|\_\\____/|_()_)\____//_/\_\
# 
# 
# This is generated code from the block code editor.
# All code is written by me!
# 

from machine import Pin, PWM, time_pulse_us, I2C
from utime import sleep, sleep_us
import ustruct
import json
import time

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
_CALIBRATION_DEFAULT = [160.14, 87.63174, 62.94521]
_STANDARD_COLORS = {
                    "red": (255, 0, 0),
                    "orange": (255, 165, 0),
                    "yellow": (255, 255, 0),
                    "green": (0, 128, 0),
                    "blue": (0, 0, 255),
                    "purple": (128, 0, 128),
                    "black": (0, 0, 0),
                    "white": (255, 255, 255)
                }

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
        
        self.FALLBACK_ECHO = echo_timeout*1.2 # Some fallback beyond timeout range
    
    def convert_us_to_cm(self, us_time):
        return us_time / (10_000 / 343)
    
    def distance(self):
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
                print(echo_time)
                echo_time = self.FALLBACK_ECHO
        except OSError as ex:
            
            print("Error obtaining ultrasonic value.")
            echo_time = self.FALLBACK_ECHO
        
        return self.convert_us_to_cm(echo_time/2) # Halve time to remove return trip time

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
        
        self.calibration = config[_CALIBRATION_KEY]
    
    def calibrate(self):
        # Calibration steps:
        # 1. Place Ro/Box on white calibration surface
        # 2. Run this calibration method
        # 3. Calibration will automatically save. Enjoy!
        maxR = maxG = maxB = 0
    
        for _ in range(10):
            r, g, b, c = self.readColor(raw=True)
            
            maxR = max(r, maxR)
            maxG = max(g, maxG)
            maxB = max(b, maxB)
            
        self.calibration = [maxR, maxG, maxB]
        with open('config.json', 'w') as configFile:
            json.dump({ _CALIBRATION_KEY: self.calibration }, configFile)
    
    def resetCalibration(self):
        with open('config.json', 'w') as configFile:
            json.dump({ _CALIBRATION_KEY: _CALIBRATION_DEFAULT }, configFile)

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
            return _GAINS[self._register8(_REGISTER_CONTROL)]
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
        if c == 0:
            return 0, 0, 0
        ir = max((r + g + b - c) // 2, 0)
        r = max(0, r - ir)
        g = max(0, g - ir)
        b = max(0, b - ir)
        return r / c, g / c, b / c, c
    def closest_colour_name(self):
        r, g, b = self.readColor()
        h, s, v = rgb_to_hsv(r, g, b)

        # Quick white/black check
        if s < 0.1 and v > 0.9:
            return "white"
        if v < 0.1:
            return "black"

        # Heuristic: if hue is in cyan range and value is low, prefer green
        # Cyan roughly around 180° to 210°
        if 180 <= h <= 210 and v < 0.5:
            return "green"

        def hue_distance(h1, h2):
            d = abs(h1 - h2)
            return min(d, 360 - d) / 180  # normalized 0 to 1

        best_match = None
        best_score = float('inf')

        for name, (r_std, g_std, b_std) in _STANDARD_COLORS.items():
            h_std, s_std, v_std = rgb_to_hsv(r_std, g_std, b_std)
            hd = hue_distance(h, h_std)
            sd = abs(s - s_std)
            vd = abs(v - v_std)

            score = hd * 3 + sd * 1 + vd * 1

            if score < best_score:
                best_score = score
                best_match = name

        return best_match
    def _calibrated_rgb(self, rgbc):
        r, g, b, c = rgbc
        calibratedR = r/self.calibration[0]
        calibratedG = g/self.calibration[1]
        calibratedB = b/self.calibration[2]
        maxClr = max(max(max(calibratedR, calibratedG), calibratedB), 1)
        clrFac = 255/maxClr
        calibratedR = calibratedR * clrFac
        calibratedG = calibratedG * clrFac
        calibratedB = calibratedB * clrFac
        return self._boost_contrast([calibratedR, calibratedG, calibratedB])
    
    def _boost_contrast(self, rgb, factor=1.5, threshold=0.15):
        h, s, v = rgb_to_hsv(*rgb)
        if s > threshold:
            s = s + (1 - s) * (factor - 1)
            s = min(s, 1)
        return hsv_to_rgb(h, s, v)
    
def rgb_to_hsv(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0  # Normalize to [0,1]
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    delta = max_c - min_c

    # Hue
    if delta == 0:
        h = 0
    elif max_c == r:
        h = (60 * ((g - b) / delta)) % 360
    elif max_c == g:
        h = (60 * ((b - r) / delta)) + 120
    elif max_c == b:
        h = (60 * ((r - g) / delta)) + 240

    # Saturation
    s = 0 if max_c == 0 else delta / max_c

    # Value
    v = max_c

    return h, s, v  # h in [0,360), s and v in [0,1]

def hsv_to_rgb(h, s, v):
    c = v * s  # Chroma
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = v - c

    if 0 <= h < 60:
        rp, gp, bp = c, x, 0
    elif 60 <= h < 120:
        rp, gp, bp = x, c, 0
    elif 120 <= h < 180:
        rp, gp, bp = 0, c, x
    elif 180 <= h < 240:
        rp, gp, bp = 0, x, c
    elif 240 <= h < 300:
        rp, gp, bp = x, 0, c
    elif 300 <= h < 360:
        rp, gp, bp = c, 0, x
    else:
        rp, gp, bp = 0, 0, 0  # fallback

    r = int((rp + m) * 255)
    g = int((gp + m) * 255)
    b = int((bp + m) * 255)

    return r, g, b
if __name__ == "__main__":
    color_sensor = ColorSensor()
    color_sensor.calibrate()
    # motors.run_motors(-100, 100)
    while True:
        r, g, b = color_sensor.readColor()
        color = color_sensor.closest_colour_name()
        print(f"RGB: ({r}, {g}, {b}), COLOR: {color}", "\n")
        time.sleep(2)


