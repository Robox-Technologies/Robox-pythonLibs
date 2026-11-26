"""The controller-mode loop: raw DRIVE packets straight to the motors.

Blocking by design: nothing else on the board needs the main loop while a
controller is driving, and a tight loop keeps input lag to a few ms. The link
is one-way, so nothing here may write to the interface it reads.
"""

from .packets import DRIVE, EXIT, TIMEOUT_MS, PacketParser, speed

LOOP_SLEEP_MS = 2


def run(comm, motors, ticks_ms, ticks_diff, sleep_ms):
    """Drive from `comm` until EXIT or TIMEOUT_MS of silence.

    Returns (reason, stats). Every way out, exceptions included, leaves the
    motors stopped. Bytes left over after an EXIT go back to `comm` so the
    framed reader sees them.
    """
    parser = PacketParser()
    started = ticks_ms()
    last_valid = started
    packets = 0
    drives = 0
    reason = "error"

    motors.stop_motors()
    try:
        while True:
            data = comm.read_raw()
            if data:
                drive = None
                exited = False
                received = parser.feed(data)
                for packet in received:
                    kind = packet[0]
                    if kind == DRIVE:
                        drive = packet
                    elif kind == EXIT:
                        exited = True
                if received:
                    # Any valid packet, even an unknown type, feeds the timeout.
                    packets += len(received)
                    last_valid = ticks_ms()

                if exited:
                    reason = "exit"
                    break
                if drive is not None:
                    # Latest value wins: only the newest DRIVE in this read.
                    drives += 1
                    motors.run_motors(speed(drive[2]), speed(drive[3]))

            if ticks_diff(ticks_ms(), last_valid) >= TIMEOUT_MS:
                reason = "timeout"
                break

            sleep_ms(LOOP_SLEEP_MS)
    finally:
        motors.stop_motors()
        rest = parser.take_remainder()
        if rest and reason == "exit":
            comm.unread(rest)

    return reason, {
        "packets": packets,
        "drives": drives,
        "dropped": parser.dropped,
        "ms": ticks_diff(ticks_ms(), started),
    }
