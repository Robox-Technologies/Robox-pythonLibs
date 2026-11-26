from machine import Pin, time_pulse_us
from utime import sleep_us, ticks_diff, ticks_ms

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

        if ticks_diff(now, self.last_read_time) < self.min_interval_ms:
            return self.last_distance if self.last_distance is not None else 999

        self.last_read_time = now

        new_distance = self._single_read()

        self.last_distance = new_distance
        return new_distance
    def _single_read(self):
        self.trig.value(0)
        sleep_us(5)

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
