"""Command line driver for the communication benchmark.

    python3 -m bench --transport usb  --out docs/comm-baseline-usb.json
    python3 -m bench --transport ble  --out docs/comm-baseline-ble.json

The harness never sends START_PROGRAM: it only ever *uploads* and then reads
program.py back over USB. Nothing it does can make the robot move.
"""

import argparse
import datetime
import json
import sys

from . import corpus, legacy, report as report_module, runner, transports


def build_parser():
    parser = argparse.ArgumentParser(prog="bench", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--transport", choices=("usb", "ble"), default="usb")
    parser.add_argument("--protocol", choices=("legacy", "v2"), default="legacy",
                        help="legacy is the unframed path; v2 is framed with "
                             "sequence numbers and checksums")
    parser.add_argument("--corpus", action="append", default=None,
                        help="run only this corpus (repeatable); default is all")
    parser.add_argument("--repeat", type=int, default=1,
                        help="run the whole corpus set N times")
    parser.add_argument("--port", default=None, help="serial port (default: auto)")
    parser.add_argument("--ble-address", default=None, help="skip the BLE scan")
    parser.add_argument("--ack-timeout", type=float, default=10.0)
    parser.add_argument("--out", default=None, help="write the JSON report here")
    parser.add_argument("--no-reset", action="store_true",
                        help="do not hard-reset the board before each case")
    parser.add_argument("--restore", default=None,
                        help="upload this local file to program.py when finished")
    return parser


def firmware_version(port=None):
    """Read CURRENT_FIRMWARE_VERSION straight out of the board's main.py."""
    raw = transports.read_board_file("main.py", port=port)
    if raw is None:
        return None
    for line in raw.decode("utf-8", "replace").split("\n"):
        if line.startswith("CURRENT_FIRMWARE_VERSION"):
            return line.split("=", 1)[1].strip().strip('"\'')
    return None


def make_transport(args):
    if args.transport == "usb":
        return transports.UsbTransport(port=args.port)
    return transports.BleTransport(address=args.ble_address)


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.corpus:
        cases = []
        for name in args.corpus:
            cases.append((name, corpus.get(name)))
    else:
        cases = corpus.all_corpora()

    for name, text in cases:
        corpus.assert_safe(text, name)

    started = datetime.datetime.now().isoformat(timespec="seconds")
    version = firmware_version(args.port)

    results = []
    transport = make_transport(args)

    for iteration in range(args.repeat):
        for name, text in cases:
            if not args.no_reset:
                # A clean boot guarantees out_file is None, so one case cannot
                # leak upload state into the next.
                transports.reset_board(args.port)

            transport.open()
            try:
                result = runner.run_case(
                    transport,
                    name,
                    text,
                    port=args.port,
                    ack_timeout=args.ack_timeout,
                    protocol=args.protocol,
                )
            finally:
                transport.close()

            result["iteration"] = iteration
            results.append(result)
            grade = result["grade"]
            print(
                "  %-18s %-4s %s  accuracy=%s ack=%s"
                % (
                    name,
                    transport.name,
                    "EXACT" if grade["exact_match"] else "MISMATCH",
                    grade.get("byte_accuracy"),
                    "yes" if result["ack_received"] else "NO",
                ),
                flush=True,
            )

    if args.restore:
        with open(args.restore, "rb") as handle:
            transports.write_board_file("program.py", handle.read(), port=args.port)
        print("restored program.py from %s" % args.restore)

    output = {
        "meta": {
            "protocol": args.protocol,
            "transport": args.transport,
            "firmware": version,
            "started": started,
            "repeat": args.repeat,
            "chunk_size": transports.BLE_CHUNK_SIZE if args.transport == "ble" else None,
            "write_delay_s": transports.BLE_WRITE_TIMEOUT_S if args.transport == "ble" else None,
        },
        "results": results,
        "summary": runner.summarise(results),
    }

    print()
    print(report_module.render(output))

    if args.out:
        report_module.write_json(output, args.out)
        print()
        print("wrote %s" % args.out)

    return 0 if output["summary"]["exact_matches"] == output["summary"]["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
