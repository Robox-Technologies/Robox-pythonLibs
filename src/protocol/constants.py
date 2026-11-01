SOH = 0x01
LF = 0x0A

HEADER_LENGTH = 11
FRAME_OVERHEAD = HEADER_LENGTH + 1

MAX_PAYLOAD = 108

KIND_DATA = "D"      # program text, ends a line
KIND_CONTINUE = "C"  # program text, line continues next frame
KIND_BEGIN = "B"     # start upload; payload carries expected totals
KIND_END = "E"       # finish upload; payload carries program CRC
KIND_COMMAND = "X"   # control command, by name
KIND_ACK = "A"       # cumulative ack + credit grant
KIND_NAK = "N"       # gap or bad checksum; resend from here
KIND_REPLY = "R"     # device to host message (JSON)

DATA_KINDS = (KIND_DATA, KIND_CONTINUE)
SEQUENCE_MODULO = 256

ACK_EVERY = 8

ACK_IDLE_MS = 50

DEFAULT_CREDIT = 2048

INITIAL_CREDIT = 512

BLE_CHUNK_SIZE = 20

UART_BYTES_PER_SECOND = 960

MIN_CHUNK_DELAY_MS = int(
    BLE_CHUNK_SIZE * 1000 / UART_BYTES_PER_SECOND
) + 1

START_CHUNK_DELAY_MS = 40

MAX_CHUNK_DELAY_MS = 80

CLEAN_BATCHES_BEFORE_PROBE = 2

PROBE_FRACTION = 0.10

BACKOFF_FACTOR = 1.25
