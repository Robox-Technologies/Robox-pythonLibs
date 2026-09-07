# Ro/Box communication: the benchmark harness

How to measure what the link actually does, and what it currently does. The
protocol itself is [PROTOCOL.md](PROTOCOL.md).

## The harness

```bash
./tools/comm-bench --transport usb --out docs/comm-usb-2.0.0.json
./tools/comm-bench --transport ble --out docs/ble-adaptive.json
./tools/comm-bench --corpus tiny          # fast smoke test
```

It uploads a fixed, seeded corpus through the *same wire behaviour the website
uses*, then reads `program.py` back over USB with `mpremote` and compares byte
for byte. The corpora live in `tools/bench/corpus.py`; they are generated from
a fixed seed so two runs push identical traffic and any difference in the
report comes from the protocol.

**Safety.** The harness never sends `start_program`. It only uploads and reads
back, so it cannot make the robot move. `corpus.assert_safe` runs at import
time and refuses any corpus containing a motor API.

## Reading the report

Three byte counts, and they are not meant to match:

| column | meaning |
|---|---|
| `sent` | raw corpus bytes, the source as generated |
| `expected` | what the protocol should store for it: identical to `sent` apart from one guaranteed trailing newline |
| `stored` | what is actually on the board, read back over USB |

`verdict` is `EXACT` when `stored == expected`. It is never compared against
`sent`, because `sent` is not what a correct upload stores. Blank lines are
preserved, so the only difference is that trailing newline: `expected` is
always `sent + 1` for these corpora and `lost` is zero throughout.

`lost` is bytes the protocol discards **by design, over a perfect link**. A run
can be 100% `EXACT` and still be losing user code, so this is the column that
catches a protocol that silently drops things.

Three throughput numbers, answering different questions:

| column | meaning |
|---|---|
| `goodput` | program bytes landed on the board per second of wall clock |
| `wire` | bytes pushed per second, counting framing and every retransmission |
| `retx` | retransmissions |

`goodput` is the one that matters. `wire` counts framing overhead and repeats,
so **a lossy run scores higher on it**, which is exactly backwards.

## Running the BLE benchmark

`bleak` drives the HM-10 exactly as the browser does (20-byte
write-without-response chunks, service `0xffe0`, characteristic `0xffe1`).
macOS gates CoreBluetooth behind TCC and **aborts** an unauthorised process
with `SIGABRT` rather than raising a catchable error, so a missing permission
looks like a silent crash.

Grant Bluetooth to the app that runs the shell — System Settings → Privacy &
Security → Bluetooth — then:

```bash
python3 -m pip install --user bleak
./tools/comm-bench --transport ble --out docs/ble-adaptive.json
```

## Current numbers

One board, one RF environment. Firmware 2.0.x, adaptive pacing.

| | USB | BLE |
|---|---|---|
| integrity | 8/8 exact | 8/8 exact |
| protocol data loss | 0 | 0 |
| goodput | 5552 B/s | 252 B/s |
| wall clock (all corpora) | – | 37.2 s |
| mean chunk delay | n/a | 24 ms |

USB is a reliable transport and loses nothing on the wire; the framing costs it
roughly 35% of raw throughput, which buys nothing there. The trade pays on BLE,
where loss is real.

### Pacing convergence

The pacer belongs to the connection rather than the upload, so it converges
once and stays there across the corpus run:

| corpus | mean delay |
|---|---|
| tiny | 40.0 ms |
| typical | 27.6 ms |
| long_lines | 25.1 ms |
| unicode_mix | 24.7 ms |
| edge_cases | 24.2 ms |
| stress | 24.0 ms |
| command_injection | 24.0 ms |
| command_guard | 24.0 ms |

`tiny` pays the full 40 ms starting delay, because four acknowledgements is not
enough feedback to converge. Adaptive pacing helps real programs, not trivial
ones, and the starting value is deliberately conservative: the first upload on
an unknown link should be safe rather than fast.

Coalescing does most of the work. Across that run the controller saw **293**
loss signals and acted on **6** of them — one backoff per loss episode, not per
NAK. Against a hand-picked fixed 30 ms it gains 8.4% goodput and 7.7% wall
clock at the same integrity and the same retransmit count.

Higher wire overhead at the same retransmit count is expected, not a
regression: a lower delay puts more frames in flight, so each go-back-N rewind
resends more of them. More redundant bytes, less wall clock, better goodput.

Reproduce with:

```bash
./tools/comm-bench --transport ble --out docs/ble-adaptive.json
```

```bash
./tools/comm-bench --transport ble --chunk-delay-ms 30 --out docs/ble-fixed30.json
```

The report prints a `pacing` line with the **mean** delay, which is the number
that matters: the minimum and maximum cannot show where the controller spent
its time.

### What is still unmeasured

All of this is one board in one RF environment. The stronger argument for
adaptive pacing is the fleet: 30 ms was hand-picked *here*, and a different
HM-10 at a different distance may need 45 ms, where a fixed 30 would thrash and
the controller would simply settle higher. That benefit is real but untested,
because there is only one board to test on.

## Hardware notes

The board is a plain Raspberry Pi Pico with an external HM-10 Bluetooth module
on UART0 (GPIO0/GPIO1). Worth writing down because the MicroPython build
flashed on it reports itself as `Raspberry Pi Pico W with RP2040`, so
`os.uname()` is misleading: there is no wireless chip, and GPIO25 is the onboard
LED as normal.
