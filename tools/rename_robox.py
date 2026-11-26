#!/usr/bin/env python3
"""Rename a Robox over its USB serial connection.

Run without a name to be prompted:

    python3 tools/rename_robox.py

Or provide it directly:

    python3 tools/rename_robox.py MyRobot
    python3 tools/rename_robox.py --port /dev/cu.usbmodem2101 MyRobot
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import protocol as p  # noqa: E402

from send_command import main as send_command  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "name",
        nargs="?",
        help="new advertised name (1-16 letters, numbers, '_' or '-')",
    )
    parser.add_argument("--port", default=None, help="USB serial port (default: auto-detect)")
    parser.add_argument(
        "--listen",
        type=float,
        default=3.0,
        help="seconds to wait for the rename reply (default: 3)",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    name = args.name
    if name is None:
        name = input("New Robox name: ").strip()

    if p.parse_device_name("rename_device_" + name) is None:
        print(
            "error: name must be 1-16 characters using only letters, numbers, "
            "'_' or '-'",
            file=sys.stderr,
        )
        return 2

    command_args = ["--listen", str(args.listen)]
    if args.port is not None:
        command_args.extend(["--port", args.port])
    command_args.append("rename_device_" + name)
    return send_command(command_args)


if __name__ == "__main__":
    sys.exit(main())
