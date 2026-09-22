#!/usr/bin/env python3
"""Send one control command to a Pico running the Robox firmware, over USB,
and print back whatever REPLY frames come in.

Talks to the *running* main.py exactly like a real client (the website)
would: a single framed X (COMMAND) frame. Does not open the REPL, does not
interrupt or reset the running program.

    python3 tools/send_command.py firmware_check
    python3 tools/send_command.py rename_device_MyRobot
    python3 tools/send_command.py --port /dev/cu.usbmodem2101 --listen 5 rename_device_MyRobot

Only ever sends the one command you name -- same safety note as comm-bench:
this cannot make the robot move unless you explicitly ask it to
(start_program, move_forward, etc. are ordinary command names like any other).
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "bench"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import protocol as p  # noqa: E402
from transports import TransportError, UsbTransport  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command", help="command name, e.g. firmware_check or rename_device_MyRobot"
    )
    parser.add_argument("--port", default=None, help="serial port (default: auto-detect)")
    parser.add_argument(
        "--listen", type=float, default=3.0,
        help="seconds to wait for reply frames after sending (default: 3)",
    )
    args = parser.parse_args(argv)

    if not p.is_command_name(args.command):
        print(
            "warning: %r is not a recognised command name -- the firmware's "
            "frame layer will silently drop it (no reply at all)" % args.command,
            file=sys.stderr,
        )

    try:
        transport = UsbTransport(port=args.port)
        transport.open()
    except TransportError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1

    try:
        frame = p.encode_frame(0, p.KIND_COMMAND, args.command.encode())
        transport.write_raw(frame)
        print(">>> %s" % args.command)

        reader = p.FrameReader()
        partial = b""
        seen_any = False
        deadline = time.time() + args.listen
        while time.time() < deadline:
            chunk = transport.read_available()
            if not chunk:
                time.sleep(0.05)
                continue

            frames, _ = reader.feed(chunk)
            for frame in frames:
                if frame.kind == p.KIND_CONTINUE:
                    partial += frame.payload
                elif frame.kind == p.KIND_REPLY:
                    seen_any = True
                    body = partial + frame.payload
                    partial = b""
                    try:
                        message = json.loads(body.decode())
                    except Exception:
                        message = body
                    print("<<< %s" % json.dumps(message))

        if not seen_any:
            print("(no reply frames in %.1fs)" % args.listen, file=sys.stderr)
    finally:
        transport.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
