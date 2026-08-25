"""Human-readable rendering of a benchmark report."""

import json


def render(report):
    lines = []
    meta = report["meta"]
    lines.append("Ro/Box communication benchmark")
    lines.append("=" * 62)
    lines.append("protocol   %s" % meta["protocol"])
    lines.append("transport  %s" % meta["transport"])
    lines.append("firmware   %s" % meta.get("firmware", "?"))
    lines.append("started    %s" % meta["started"])
    lines.append("")

    header = "%-18s %7s %7s %9s %8s %7s %6s" % (
        "corpus", "sent", "stored", "accuracy", "thr B/s", "ack", "lost")
    lines.append(header)
    lines.append("-" * len(header))
    for result in report["results"]:
        grade = result["grade"]
        # `lost` counts bytes the protocol discards even over a perfect link;
        # the +/-1 wobble is just the trailing newline the firmware adds.
        loss = result.get("protocol_loss_bytes", 0)
        lines.append(
            "%-18s %7d %7s %9s %8s %7s %6s"
            % (
                result["corpus"],
                result["sent_bytes"],
                grade.get("actual_bytes", "-"),
                "EXACT" if grade["exact_match"] else "%.4f" % (grade.get("byte_accuracy") or 0),
                result["throughput_bytes_per_second"] or "-",
                "yes" if result["ack_received"] else "NO",
                loss if loss > 1 else "-",
            )
        )

    lines.append("")
    summary = report["summary"]
    lines.append("integrity            %d/%d exact (%.1f%%)" % (
        summary["exact_matches"], summary["cases"], 100.0 * (summary["integrity_rate"] or 0)))
    lines.append("board confirmations  %d/%d uploads" % (
        summary["uploads_confirmed_by_board"], summary["cases"]))
    lines.append("mean byte accuracy   %s" % summary["mean_byte_accuracy"])
    lines.append("mean throughput      %s B/s" % summary["mean_throughput_bytes_per_second"])
    lines.append("corrupt-but-runnable %d lines  <-- silently executed garbage" %
                 summary["corrupt_lines_that_would_still_run"])
    lines.append("protocol data loss   %d bytes across %d case(s)  <-- lost by design, not by the link" % (
        summary["bytes_lost_to_protocol_design"], summary["cases_with_protocol_loss"]))

    failures = [r for r in report["results"] if not r["grade"]["exact_match"]]
    if failures:
        lines.append("")
        lines.append("Mismatches")
        lines.append("-" * 62)
        for result in failures:
            grade = result["grade"]
            lines.append("  %s" % result["corpus"])
            if grade.get("readback_failed"):
                lines.append("    readback failed (no program.py on the board)")
                continue
            lines.append(
                "    missing=%d corrupted=%d extra=%d first_diff_at=%s"
                % (
                    grade["lines_missing"],
                    grade["lines_corrupted"],
                    grade["lines_extra"],
                    grade["first_divergence_offset"],
                )
            )
            for event in result["protocol_events"]:
                lines.append("    protocol: line %s %r -> %s" % (event["line"], event["text"], event["effect"]))
            for error in result["errors"]:
                lines.append("    device error: %s" % error)

    return "\n".join(lines)


def write_json(report, path):
    with open(path, "w") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
