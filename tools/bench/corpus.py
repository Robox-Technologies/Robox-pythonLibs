"""Deterministic test corpora for the communication harness.

Every payload here is generated from a fixed seed so a "before" run and an
"after" run push byte-identical traffic across the link. That is the whole
point: any difference in the report has to come from the protocol, not from
the input.

SAFETY: these programs are never executed on the board (the harness never
sends START_PROGRAM), but they are written to program.py, so a later manual
run must not be able to drive the motors. `assert_safe` enforces that no
corpus line can touch a motor API or smuggle in a dangerous command, and it
runs at import time.
"""

import random

# Commands the firmware acts on the instant it sees a bare line matching one.
# A corpus line equal to one of these would be swallowed as a command instead
# of being stored -- which is a real bug we want to *demonstrate*, so one
# corpus case does exactly that, but only ever with a harmless command.
# The one handled command with no hardware side effect: it just closes the
# upload file. Every other handled command replies, sleeps an interface,
# rewrites the colour calibration, resets the board, or -- x04STARTPROG --
# runs program.py and drives the motors.
INJECTABLE_COMMAND = "x03ENDUPLD"
# Never allowed as a bare corpus line, at any point, for any reason.
DANGEROUS_COMMANDS = (
    "x02BEGINUPLD",       # truncates the upload mid-flight
    "x04STARTPROG",       # runs program.py -> motors
    "x05COLORCALIBRATE",  # overwrites the user's colour calibration
    "x06RESTART",         # resets the board
    "x07BOOTLOADER",      # drops into BOOTSEL
)

# Substrings that would make a stored program able to move the robot.
MOTOR_TOKENS = ("Motors", "run_motor", "run_motors", "motors.", "PWM(")


def _rng(seed):
    return random.Random(seed)


def tiny():
    """Smallest useful upload: fits in a single BLE chunk pair."""
    return "\n".join(
        [
            "print('hello')",
            "x = 1 + 1",
            "print(x)",
        ]
    )


def typical():
    """Roughly what the block editor emits for a simple sensor program.

    Motor calls are deliberately replaced with prints; the shape (imports,
    defs, loops, f-strings) is what matters for framing, not the payload.
    """
    lines = [
        "from roboxlib import LineSensors, UltrasonicSensor",
        "from machine import Pin",
        "import time",
        "import json",
        "",
        "line = LineSensors()",
        "ultrasonic = UltrasonicSensor()",
        "speed = 60",
        "",
        "def report(kind, message):",
        "    print(json.dumps({'type': kind, 'message': message}))",
        "",
        "def event_begin():",
        "    count = 0",
        "    while count < 5:",
        "        distance = ultrasonic.distance()",
        "        if distance < 20:",
        "            report('console', 'close: ' + str(distance))",
        "        else:",
        "            report('console', 'clear: ' + str(distance))",
        "        left, right = line.read()",
        "        report('console', 'line ' + str(left) + ' ' + str(right))",
        "        time.sleep(0.1)",
        "        count = count + 1",
        "",
        "event_begin()",
    ]
    return "\n".join(lines)


def long_lines():
    """Lines that straddle the 20-byte BLE chunk boundary awkwardly.

    Lengths are chosen to land just under, exactly on, and just over multiples
    of the chunk size, which is where off-by-one framing bugs live.
    """
    out = []
    for length in (19, 20, 21, 39, 40, 41, 59, 60, 61, 79, 80, 81, 96, 120):
        # `# ` prefix keeps it valid Python whatever the length.
        filler = "x" * max(0, length - 2)
        out.append(("# " + filler)[:length])
    return "\n".join(out)


def unicode_mix():
    """Multi-byte UTF-8 that a naive 20-*byte* chunker can split mid-codepoint."""
    return "\n".join(
        [
            "# café naïve über",
            "label = 'résumé'",
            "# 你好世界 こんにちは",
            "print('✓ done')",
            "# \U0001f916 robot \U0001f680 rocket",
        ]
    )


def edge_cases():
    """Payloads that stress the framing rather than the transport."""
    return "\n".join(
        [
            "print('quote: \\'single\\' and \"double\"')",
            "data = {'nested': {'deep': [1, 2, 3]}}",
            "",
            "",
            "print('brace } and { brace')",
            "print('json-ish: {\"type\": \"console\"}')",
            "# x03ENDUPLD <- command text in a comment, must still be stored",
            "print('trailing spaces')   ",
            "\tprint('tab indent is a syntax error but must round-trip')",
        ]
    )


def stress(seed=20260825, lines=200):
    """~4 KB of plausible source: the worst realistic upload."""
    rng = _rng(seed)
    out = ["# generated stress corpus, seed=%d" % seed]
    for i in range(lines):
        kind = rng.randrange(5)
        if kind == 0:
            out.append("var_%03d = %d" % (i, rng.randrange(10**6)))
        elif kind == 1:
            out.append("print('line %03d value', var_%03d if %d else 0)" % (i, max(0, i - 1), i % 2))
        elif kind == 2:
            out.append("# comment %03d %s" % (i, "-" * rng.randrange(5, 60)))
        elif kind == 3:
            out.append("if var_%03d > %d:" % (max(0, i - 1), rng.randrange(1000)))
            out.append("    print('branch %03d')" % i)
        else:
            out.append("values_%03d = [%s]" % (i, ", ".join(str(rng.randrange(100)) for _ in range(rng.randrange(3, 12)))))
    return "\n".join(out)


def command_injection():
    """A program whose 4th line is bare `x03ENDUPLD`.

    Nothing here is a transport problem: over a *perfect* link the current
    firmware still loses everything from that line onward, because user code
    and control commands share one unframed channel. This corpus exists to
    measure that, and to prove the replacement protocol fixes it.
    """
    return "\n".join(
        [
            "print('before the injected command')",
            "a = 1",
            "b = 2",
            INJECTABLE_COMMAND,
            "print('after -- the current firmware never stores this')",
            "c = 3",
        ]
    )


def command_guard():
    """A bare `x01FIRMCHECK` in the middle of an upload.

    The firmware honours only end_upload while an upload is in flight, so this
    line must be *stored as program text*, not executed. Before that guard
    existed it was swallowed, the board replied with its firmware version, and
    it put the other interface to sleep -- mid-upload.

    x01FIRMCHECK is the safe choice for this: even if it somehow were acted on,
    the worst case is a redundant version reply.
    """
    return "\n".join(
        [
            "print('guard test')",
            "value = 41",
            "x01FIRMCHECK",
            "value = value + 1",
            "print(value)",
        ]
    )


# Ordered smallest-first so a run fails fast on the cheap cases.
CORPORA = (
    ("tiny", tiny),
    ("typical", typical),
    ("long_lines", long_lines),
    ("unicode_mix", unicode_mix),
    ("edge_cases", edge_cases),
    ("stress", stress),
    ("command_injection", command_injection),
    ("command_guard", command_guard),
)


def all_corpora():
    """[(name, text)] for every corpus, in run order."""
    return [(name, fn()) for name, fn in CORPORA]


def get(name):
    for corpus_name, fn in CORPORA:
        if corpus_name == name:
            return fn()
    raise KeyError("no such corpus: %s (have: %s)" % (name, ", ".join(n for n, _ in CORPORA)))


def assert_safe(text, name="<corpus>"):
    """Refuse to ship a corpus that could move the robot if it were run."""
    for token in MOTOR_TOKENS:
        if token in text:
            raise AssertionError("corpus %s contains motor token %r" % (name, token))
    for lineno, line in enumerate(text.split("\n"), 1):
        if line.strip() in DANGEROUS_COMMANDS:
            raise AssertionError(
                "corpus %s line %d is the dangerous command %r" % (name, lineno, line.strip())
            )
    return True


for _name, _text in all_corpora():
    assert_safe(_text, _name)
del _name, _text
