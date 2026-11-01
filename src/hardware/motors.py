from machine import Pin, PWM
from utime import sleep

from .storage import load_motor_calibration, load_motor_reverse, load_motor_swap

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

        self.calibration = load_motor_calibration()
        self.reverse = [load_motor_reverse(0), load_motor_reverse(1)]
        self.swap = load_motor_swap()

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
        if self.swap:
            left_speed, right_speed = right_speed, left_speed
        if self.reverse[0]:
            left_speed = -left_speed
        if self.reverse[1]:
            right_speed = -right_speed

        self.run_motor(1, left_speed * (1 + self.calibration))
        self.run_motor(2, right_speed * (1 - self.calibration))

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
