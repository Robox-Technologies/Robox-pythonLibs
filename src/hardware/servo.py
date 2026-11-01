from machine import Pin, PWM
from utime import sleep

_MIN_S = 1300
_MAX_S = 8500
_MIN_ANGLE = 0
_MAX_ANGLE = 180

class Servo:
    def __init__(self, pin=6):
        self.servo = PWM(Pin(pin))
        self.servo.freq(50)
    def angle_to_pulse(self, angle):
        mapped = (angle - _MIN_ANGLE) * (_MAX_S - _MIN_S) / (_MAX_ANGLE - _MIN_ANGLE) + _MIN_S
        return int(max(min(mapped, _MAX_S), _MIN_S))
    def rotate_to_angle(self, angle):
        pulse = self.angle_to_pulse(angle)
        self.servo.duty_u16(pulse)
        sleep(0.2)

