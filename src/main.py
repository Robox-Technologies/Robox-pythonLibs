import select
import sys
import time
import json
import _thread
import machine

# Constants
LED = machine.Pin(25, machine.Pin.OUT)
USB_CHARGING_PIN = machine.Pin('GPIO24', machine.Pin.IN)
PROGRAM_FILENAME = "program.py"

# Command dictionary
COMMANDS = {
    "x021STARTPROG": "start_program",
    "x032BEGINUPLD": "begin_upload",
    "x04": "end_upload",
    "x019FIRMCHECK": "firmware_check",
    "x069": "reset_device"
}

# USB Connection check
def is_usb_connected():
    return USB_CHARGING_PIN.value()

# JSON message formatter
def generate_message(msg_type, message):
    return json.dumps({"type": msg_type, "message": message})

# Safely run user program
def run_user_program():
    try:
        import program
    except Exception as e:
        print(generate_message("error", str(e)))

# Main USB communication handler
def usb_mode():
    print("Device is connected: Boot into PROG/TEST mode")
    poller = select.poll()
    poller.register(sys.stdin, select.POLLIN)

    out_file = None

    while True:
        events = poller.poll(1)
        if not events:
            continue

        data = sys.stdin.readline().strip()
        if not data:
            continue

        command = COMMANDS.get(data)

        if command == "start_program":
            LED.on()
            print(generate_message("console", "Starting the program"))
            _thread.start_new_thread(run_user_program, ())

        elif command == "begin_upload":
            with open(PROGRAM_FILENAME, 'w'):
                pass  # Clear file
            out_file = open(PROGRAM_FILENAME, "w")
            print(generate_message("console", "Ready to receive program"))

        elif command == "end_upload":
            if out_file:
                out_file.close()
                out_file = None
            LED.on()
            print(generate_message("download", "Program has been received"))

        elif command == "firmware_check":
            print(generate_message("confirmation", True))

        elif command == "reset_device":
            machine.reset()

        elif out_file:
            LED.toggle()
            out_file.write(data + "\n")

# Entry point
def main():
    LED.on()
    if is_usb_connected():
        usb_mode()
    else:
        print("Device not connected: Run program immediately if available")
        run_user_program()

main()

