# 
# _____  ____ _____  ____ __  __
# | () )/ () \| () )/ () \\ \/ /
# |_|\_\\____/|_()_)\____//_/\_\
# 
# 
# This is generated code from the block code editor.
# All code is written by me!
# 

from machine import Pin, PWM, time_pulse_us
from utime import sleep, sleep_us

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
