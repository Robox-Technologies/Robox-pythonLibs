#!/usr/bin/env python3
"""Scan for a Robox BLE advertised name.

Examples:

    python3 tools/scan_ble.py --name Robox
    python3 tools/scan_ble.py --name MyRobot --timeout 10
    python3 tools/scan_ble.py --name MyRobot --show-all

The exit status is zero when the requested advertised name is found and one
when it is not found. This makes the command useful for checking a rename
manually or from a small test script.
"""

import argparse
import asyncio
import sys


def advertised_name(device, advertisement):
    """Return the name advertised by a Bleak discovery result."""
    return advertisement.local_name or device.name or ""


def matches_name(device, advertisement, expected_name):
    """Return whether a discovery result advertises ``expected_name``."""
    return advertised_name(device, advertisement) == expected_name


async def scan(expected_name, timeout, show_all):
    try:
        from bleak import BleakScanner
    except ImportError as exc:
        raise RuntimeError(
            "bleak is required: python3 -m pip install --user -r "
            "requirements-dev.txt"
        ) from exc

    print("Scanning for %r for %.1fs..." % (expected_name, timeout))
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    matched = False

    for address, result in sorted(found.items()):
        device, advertisement = result
        name = advertised_name(device, advertisement)
        if name == expected_name:
            matched = True
            marker = "MATCH"
        elif not show_all:
            continue
        else:
            marker = "      "
        rssi = advertisement.rssi
        rssi_text = "%d dBm" % rssi if rssi is not None else "RSSI unavailable"
        print("%-5s %-16s %-24s %s" % (marker, name or "<unnamed>", address, rssi_text))

    if matched:
        print("Found advertised name %r." % expected_name)
    else:
        print("Did not find advertised name %r." % expected_name, file=sys.stderr)
    return matched


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--name",
        default="Robox",
        help="exact advertised name to find (default: Robox)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="scan duration in seconds (default: 5)",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="also print nearby devices with a different advertised name",
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")

    try:
        return 0 if asyncio.run(scan(args.name, args.timeout, args.show_all)) else 1
    except RuntimeError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nScan cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
