import bluetooth
import micropython
from ble_advertising import advertising_payload
from micropython import const

# Enable input buffering to prevent interruptions
micropython.kbd_intr(-1)

_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_GATTS_WRITE = const(3)

_UART_SERVICE_UUID = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
_UART_RX_UUID = bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E")  # Write
_UART_TX_UUID = bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")  # Notify

_UART_TX = (_UART_TX_UUID, bluetooth.FLAG_NOTIFY)
_UART_RX = (_UART_RX_UUID, bluetooth.FLAG_WRITE)

_UART_SERVICE = (_UART_SERVICE_UUID, (_UART_TX, _UART_RX))

class BLE_UART:
    def __init__(self, name="PicoW-REPL"):
        self._ble = bluetooth.BLE()
        self._ble.active(True)
        self._ble.irq(self._irq)
        
        ((self._tx_handle, self._rx_handle),) = self._ble.gatts_register_services((_UART_SERVICE,))
        self._connections = set()

        # Advertise the device
        self._payload = advertising_payload(name=name, services=[_UART_SERVICE_UUID])
        self._advertise()

    def _irq(self, event, data):
        if event == _IRQ_CENTRAL_CONNECT:
            conn_handle, _, _ = data
            self._connections.add(conn_handle)

        elif event == _IRQ_CENTRAL_DISCONNECT:
            conn_handle, _, _ = data
            self._connections.remove(conn_handle)
            self._advertise()  # Restart advertising

        elif event == _IRQ_GATTS_WRITE:
            conn_handle, value_handle = data
            if value_handle == self._rx_handle:
                cmd = self._ble.gatts_read(value_handle).decode().strip()
                self.process_command(cmd)

    def send(self, data):
        for conn_handle in self._connections:
            self._ble.gatts_notify(conn_handle, self._tx_handle, data)

    def _advertise(self):
        self._ble.gap_advertise(100000, adv_data=self._payload)

    def process_command(self, cmd):
        """Intercept special commands before REPL."""
        if cmd == "hello":
            self.send("👋 Hello! How can I help?\n")
        elif cmd == "status":
            self.send("✅ System is running fine.\n")
        elif cmd.startswith("led "):  # Example: led on / led off
            state = cmd.split(" ")[1]
            if state == "on":
                self.send("💡 LED turned ON.\n")
                # Example: Add code to control an LED here
            elif state == "off":
                self.send("💡 LED turned OFF.\n")
            else:
                self.send("⚠️ Unknown LED command.\n")
        else:
            # If not a special command, evaluate as Python REPL
            try:
                output = str(eval(cmd)) + "\n"
            except Exception as e:
                output = str(e) + "\n"
            self.send(output)

# Initialize BLE UART
ble_uart = BLE_UART()

print("🚀 REPL over Bluetooth started. Connect using a BLE terminal.")
