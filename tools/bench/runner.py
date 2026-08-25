"""Run one corpus across one transport and grade the result."""

import difflib
import hashlib
import time

from . import framed, transports


def _sha(text):
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def grade(expected, actual):
    """Compare what the board stored against what it should have stored."""
    if actual is None:
        return {
            "exact_match": False,
            "readback_failed": True,
            "expected_bytes": len(expected.encode()),
            "actual_bytes": None,
        }

    expected_lines = expected.split("\n")
    actual_lines = actual.split("\n")

    first_divergence = None
    for index, (left, right) in enumerate(zip(expected, actual)):
        if left != right:
            first_divergence = index
            break
    if first_divergence is None and len(expected) != len(actual):
        first_divergence = min(len(expected), len(actual))

    matcher = difflib.SequenceMatcher(None, expected_lines, actual_lines, autojunk=False)
    missing = 0
    corrupted = 0
    extra = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "delete":
            missing += i2 - i1
        elif tag == "insert":
            extra += j2 - j1
        elif tag == "replace":
            corrupted += max(i2 - i1, j2 - j1)

    # A line that changed but is still valid-looking Python is the dangerous
    # case: the board would run it without ever noticing.
    silently_runnable = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue
        for line in actual_lines[j1:j2]:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                compile(line, "<readback>", "exec")
                silently_runnable += 1
            except SyntaxError:
                pass

    return {
        "exact_match": expected == actual,
        "readback_failed": False,
        "expected_bytes": len(expected.encode()),
        "actual_bytes": len(actual.encode()),
        "expected_sha": _sha(expected),
        "actual_sha": _sha(actual),
        "expected_lines": len([line for line in expected_lines if line]),
        "actual_lines": len([line for line in actual_lines if line]),
        "first_divergence_offset": first_divergence,
        "lines_missing": missing,
        "lines_corrupted": corrupted,
        "lines_extra": extra,
        "corrupt_lines_still_valid_python": silently_runnable,
        "byte_accuracy": (
            round(
                difflib.SequenceMatcher(None, expected, actual, autojunk=False).ratio(), 6
            )
        ),
    }


def run_case(
    transport,
    name,
    code,
    port=None,
    ack_timeout=10.0,
    settle=0.5,
    chunk_delay_ms=None,
):
    """Upload one corpus and read the result back over USB."""
    from . import protocol_shim

    delay = (
        transports.BLE_WRITE_TIMEOUT_S
        if chunk_delay_ms is None
        else chunk_delay_ms / 1000.0
    )

    # Every non-blank line is stored verbatim, so the oracle is just the
    # normalised program. There are no protocol losses left to model.
    expected = protocol_shim.normalise(code)
    upload = framed.upload(
        transport,
        code,
        chunk_size=transports.BLE_CHUNK_SIZE if transport.chunked else None,
        chunk_delay=delay if transport.chunked else 0.0,
    )
    upload["ack_received"] = upload.get("verified", False)
    upload["device_messages"] = upload.pop("replies", [])
    upload["errors"] = [m for m in upload["device_messages"] if '"error"' in m]

    # The readback needs the serial port, and only one client may hold it.
    transport_was_usb = transport.name == "usb"
    if transport_was_usb:
        transport.close()
    time.sleep(settle)

    raw = transports.read_board_file("program.py", port=port)
    actual = None if raw is None else raw.decode("utf-8", "surrogateescape")

    if transport_was_usb:
        transport.open()

    result = {
        "corpus": name,
        "transport": transport.name,
        "protocol": "framed",
        "sent_bytes": len(code.encode()),
        # Data the protocol throws away by design, even over a perfect link.
        # Kept separate from transport loss so a green "EXACT" row cannot hide
        # the fact that user code went missing.
        "protocol_loss_bytes": len(code.encode()) - len(expected.encode()),
        "protocol_events": [],
    }
    result.update(upload)
    result["grade"] = grade(expected, actual)

    written = upload["bytes_written"]
    seconds = upload["total_seconds"] or upload["local_write_seconds"]
    result["throughput_bytes_per_second"] = round(written / seconds, 1) if seconds else None
    return result


def summarise(results):
    total = len(results)
    exact = sum(1 for r in results if r["grade"]["exact_match"])
    acked = sum(1 for r in results if r["ack_received"])
    dangerous = sum(r["grade"].get("corrupt_lines_still_valid_python", 0) for r in results)
    protocol_loss = sum(max(0, r.get("protocol_loss_bytes", 0)) for r in results)
    throughputs = [r["throughput_bytes_per_second"] for r in results if r["throughput_bytes_per_second"]]
    accuracies = [r["grade"].get("byte_accuracy") for r in results if r["grade"].get("byte_accuracy") is not None]
    return {
        "cases": total,
        "exact_matches": exact,
        "integrity_rate": round(exact / total, 4) if total else None,
        "uploads_confirmed_by_board": acked,
        "mean_byte_accuracy": round(sum(accuracies) / len(accuracies), 6) if accuracies else None,
        "corrupt_lines_that_would_still_run": dangerous,
        "bytes_lost_to_protocol_design": protocol_loss,
        "cases_with_protocol_loss": sum(
            1 for r in results if r.get("protocol_loss_bytes", 0) > 1
        ),
        "mean_throughput_bytes_per_second": round(sum(throughputs) / len(throughputs), 1) if throughputs else None,
    }
