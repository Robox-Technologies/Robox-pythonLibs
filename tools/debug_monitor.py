#!/usr/bin/env python3
"""Print compact debug messages from a debug Robox over USB."""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "bench"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import protocol
from transports import TransportError, UsbTransport


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=None, help="serial port (default: auto-detect)")
    args = parser.parse_args(argv)

    try:
        transport = UsbTransport(port=args.port)
        transport.open()
    except TransportError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1

    reader = protocol.FrameReader()
    partial = b""
    print("monitoring %s over USB (Ctrl-C to stop)" % transport.port_name)
    try:
        while True:
            try:
                chunk = transport.read_available()
            except (OSError, TransportError):
                transport.close()
                partial = b""
                print(
                    "\nboard disconnected; waiting for %s to reconnect..."
                    % transport.port_name,
                    flush=True,
                )
                while True:
                    try:
                        transport.open()
                        reader = protocol.FrameReader()
                        print("board reconnected", flush=True)
                        break
                    except TransportError:
                        time.sleep(0.25)
                continue

            if not chunk:
                time.sleep(0.05)
                continue
            frames, _ = reader.feed(chunk)
            for frame in frames:
                if frame.kind == protocol.KIND_CONTINUE:
                    partial += frame.payload
                elif frame.kind == protocol.KIND_REPLY:
                    body = partial + frame.payload
                    partial = b""
                    try:
                        message = json.loads(body.decode())
                    except (UnicodeDecodeError, ValueError):
                        continue
                    if message.get("type") == "debug":
                        print(message.get("message", ""), flush=True)
    except KeyboardInterrupt:
        print("\nmonitor stopped")
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
