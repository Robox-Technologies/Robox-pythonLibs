from machine import Pin

class LineSensors:
    def __init__(self, left_pin=3, right_pin=2):
        self.sensor_left = Pin(left_pin, Pin.IN)
        self.sensor_right = Pin(right_pin, Pin.IN)

    def read_line_position(self):
        return [self.sensor_left.value(), self.sensor_right.value()]

