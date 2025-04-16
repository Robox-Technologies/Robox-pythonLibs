import select
import sys
import time
import json
import _thread

import machine

led = machine.Pin(25, machine.Pin.OUT)

led.on()

# Polling object
poll_obj = select.poll()
poll_obj.register(sys.stdin, select.POLLIN)

# TEST: Check if USB port is connected
SIE_STATUS=const(0x50110000+0x50)
CONNECTED=const(1<<16)
SUSPENDED=const(1<<4)
def isUSBConnected():
    time.sleep(0.1)
    vbus = machine.Pin('WL_GPIO2', machine.Pin.IN)
    return vbus()

def protectedProgCall():
    try:
        import program
    except Exception as e: # Program/Misc error
        print(generatePrint("error", e))
    
    # We may need to do a restart so we can use the program again
    # machine.soft_reset()

def generatePrint(typ, message):
    jsmessage = {"type": typ, "message": message}
    return json.dumps(jsmessage)
if isUSBConnected(): # THIS DOES NOT ALWAYS CONNECT
    # Boot into program/test mode
    print("Device is connected: Boot into PROG/TEST mode")
    
    readingFile = False
    outFilename = "program.py" # Name of program file
    outFile = False
    while True:
        poll_results = poll_obj.poll(1) # Delay 1 microsecond before polling
        
        if poll_results:
            # Read the data from stdin (read data coming from PC)
            
            data = sys.stdin.readline()
            dataStripped = data.strip() # Use this to read exact system commands
            # Program begin
            if (dataStripped == ""):
                continue
            if dataStripped == "x021STARTPROG":
                led.on()
                print(generatePrint("console", "Starting the program"))
                _thread.start_new_thread(protectedProgCall, ())
                
                continue
            
            # Program upload
            elif dataStripped == "x04":
                print(generatePrint("download", "Program has been recieved"))
                # Don't read termination line
                readingFile = False
                led.on()
                
                # Close file
                outFile.close()
                outFile = False
            elif dataStripped == "x019FIRMCHECK":
                print(generatePrint("confirmation", True))
            elif dataStripped == "x032BEGINUPLD":
                open(outFilename, 'w').close() # Clear file
                outFile = open(outFilename, "w") # Open file for writing
                print(generatePrint("console", "Ready to receive program"))
            elif dataStripped == "x069":
                machine.reset()
                
            elif(not outFile == False):
                led.toggle() # Flash LED for debug
                
                # Write the data to program file
                outFile.write(data)
        else:
            # No response, check if USB is still alive
#             if not isUSBConnected():
#                 machine.soft_reset() # Restart
            
            continue
else:
    print("Device not connected: Run program immediately if available")
    protectedProgCall()

    




