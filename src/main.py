import select
import sys
import time
import json
import _thread
import machine
import os

from roboxlib import ColorSensor
from communication import (
    USBCommunication,
    BluetoothCommunuication,
    generate_message,
    flush_outgoing_messages
)

DEBUG = True
CURRENT_FIRMWARE_VERSION = "1.0.0"
PROGRAM_FILENAME = "program.py"

# ----------------------
# Hardware setup
# ----------------------
LED = machine.Pin(25, machine.Pin.OUT)
LED.on()

colorSensor = False
try:
    colorSensor = ColorSensor()
except:
    colorSensor = False


# ----------------------
# Commands
# ----------------------
COMMANDS = {
    "x01FIRMCHECK": "firmware_check",
    "x02BEGINUPLD": "begin_upload",
    "x03ENDUPLD": "end_upload",
    "x04STARTPROG": "start_program",
    "x05COLORCALIBRATE": "calibrate_color",
    "x06RESTART": "reset_device",
    "x07BOOTLOADER": "boot_loader",
    "x08DISCONNECT": "disconnect_device",
}


# ----------------------
# Communication setup
# ----------------------
ble = BluetoothCommunuication()
usb = USBCommunication()

communications = []
current_communication_method = None

# IMPORTANT: DO NOT redefine queue system here


if usb.available():
    communications.append(usb)

if ble.available():
    communications.append(ble)
    ble.write_message("connect", "")  # keep your original behavior


# ----------------------
# User program runner
# ----------------------
def run_user_program(comm):
    try:
        sys.modules.pop("program", None)

        def sandbox_print(*args):
            msg = " ".join(str(arg) for arg in args)
            comm.write_message("console", msg)

        ns = {
            "comm": comm,
            "print": sandbox_print
        }

        with open(PROGRAM_FILENAME) as f:
            code = f.read()

        exec(code, ns)

    except Exception as e:
        print("ERROR", e)
        comm.write_message("error", str(e))


# ----------------------
# Main loop
# ----------------------
out_file = None

while True:
    # ✅ ONLY queue system lives in communication.py
    flush_outgoing_messages()

    for comm in communications:
        if comm.sleeping:
            continue

        line = comm.read_line()

        if not line:
            continue

        if DEBUG:
            print(
                generate_message(
                    "console",
                    "Received over {}: {}".format(comm.name, line)
                )
            )

        command = COMMANDS.get(line.strip())

        # ----------------------
        # Firmware check
        # ----------------------
        if command == "firmware_check":
            if current_communication_method and current_communication_method != comm:
                comm.write_message("error", "Already connected over another interface")
                continue

            if comm == usb and ble in communications:
                ble.sleep()

            elif comm == ble and usb in communications:
                usb.sleep()

            current_communication_method = comm
            comm.write_message("firmware", CURRENT_FIRMWARE_VERSION)

        # ----------------------
        # Start program
        # ----------------------
        elif command == "start_program":
            LED.on()

            _thread.start_new_thread(run_user_program, (comm,))

            comm.write_message("download", "")

        # ----------------------
        # Begin upload
        # ----------------------
        elif command == "begin_upload":
            try:
                os.remove(PROGRAM_FILENAME)
            except OSError:
                pass

            out_file = open(PROGRAM_FILENAME, "w")

        # ----------------------
        # End upload
        # ----------------------
        elif command == "end_upload":
            if out_file:
                out_file.close()
                out_file = None
                comm.write_message("download", "")
            else:
                comm.write_message("error", "No upload in progress")

        # ----------------------
        # Color calibration
        # ----------------------
        elif command == "calibrate_color":
            if not colorSensor:
                comm.write_message("error", "Color sensor not connected")
            else:
                colorSensor.calibrate()
                comm.write_message("calibrated", "")

        # ----------------------
        # Reset device
        # ----------------------
        elif command == "reset_device":
            if ble in communications and ble.sleeping:
                ble.wake()

            if usb in communications and usb.sleeping:
                usb.wake()

            machine.reset()

        # ----------------------
        # Program upload stream
        # ----------------------
        elif out_file:
            LED.toggle()
            out_file.write(line + "\n")
