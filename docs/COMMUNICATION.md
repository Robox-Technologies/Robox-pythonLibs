# Ro/Box communication: baseline, protocol, and harness

## Why this document exists

Bluetooth uploads were corrupting programs, and the board had no way to know
it. This records what the link actually does today (measured, not guessed),
the protocol that replaces it, and how to re-run the measurement.

## The harness

```bash
./tools/comm-bench --transport usb --out docs/comm-baseline-usb.json
./tools/comm-bench --transport ble --out docs/comm-baseline-ble.json
./tools/comm-bench --corpus tiny          # fast smoke test
```

It uploads a fixed, seeded corpus through the *same wire behaviour the website
uses*, then reads `program.py` back over USB with `mpremote` and compares byte
for byte. The corpora live in `tools/bench/corpus.py`; they are generated from
a fixed seed so a "before" and an "after" run push identical traffic.

**Safety.** The harness never sends `START_PROGRAM`. It only uploads and reads
back, so it cannot make the robot move. `corpus.assert_safe` runs at import
time and refuses any corpus containing a motor API or a dangerous bare command.

### Reading the report

`accuracy` is the transport verdict: `EXACT` means the board stored precisely
what the protocol *should* have stored. `lost` is separate and more important —
bytes the protocol discards **by design, over a perfect link**. A run can be
100% `EXACT` and still be losing user code.

## Baseline (firmware 1.0.0, legacy protocol)

### USB — 2026-08-25

| corpus | sent | stored | accuracy | thr B/s | ack | lost |
|---|---|---|---|---|---|---|
| tiny | 33 | 34 | EXACT | 793 | yes | – |
| typical | 664 | 661 | EXACT | 6855 | yes | 3 |
| long_lines | 829 | 830 | EXACT | 9672 | yes | – |
| unicode_mix | 113 | 114 | EXACT | 3618 | yes | – |
| edge_cases | 299 | 298 | EXACT | 6405 | yes | – |
| stress | 7213 | 7214 | EXACT | 7287 | yes | – |
| command_injection | 122 | 49 | EXACT | 5775 | yes | **73** |

Integrity 7/7 exact. **USB loses nothing on the wire** — CDC is a reliable
transport, so the byte-level corruption users see is BLE-specific.

But USB still loses data at the *protocol* level:

* `typical` — 3 bytes: blank lines are discarded. Harmless here, but it means
  the stored program is never identical to the sent program, so no end-to-end
  comparison is even possible today.
* `command_injection` — **73 of 122 bytes silently discarded.** The corpus has
  a bare `x03ENDUPLD` on line 4. The firmware executes it, closes the file, and
  drops every following line. No error, no warning, and the board will happily
  run the truncated result.

### BLE

Not yet measured: driving GATT from Python on this machine needs macOS
Bluetooth permission (see "Running the BLE benchmark" below).

## The command-injection hole

Commands travel **in-band**: `main.py` compares each received line against a
table, so a line of user code that happens to match is executed instead of
stored.

```python
command = COMMANDS.get(line.strip())   # src/main.py
```

Anything typed in the Python editor can trigger it. Consequences by command:

| bare line | effect |
|---|---|
| `x01FIRMCHECK` | replies, and sleeps the *other* interface |
| `x02BEGINUPLD` | truncates the upload in flight |
| `x03ENDUPLD` | ends the upload; all later lines dropped |
| `x04STARTPROG` | **runs `program.py` — drives the motors** |
| `x05COLORCALIBRATE` | overwrites the stored colour calibration |
| `x06RESTART` | resets the board |
| `x07BOOTLOADER`, `x08DISCONNECT` | in the table with no handler, so they fall through and are stored as program text |

Picking a more obscure sentinel does not fix this. Any in-band marker is a
string a user can type. The fix is structural: frame the channel so control and
data are distinguished by a **header field**, never by payload content, and
length-prefix the payload so it is never scanned for sentinels at all.

## Running the BLE benchmark

`bleak` drives the HM-10 exactly as the browser does (20-byte
write-without-response chunks, 40 ms apart, service `0xffe0`, characteristic
`0xffe1`). macOS gates CoreBluetooth behind TCC and **aborts** an unauthorised
process with `SIGABRT` rather than raising a catchable error, so a missing
permission looks like a silent crash.

Grant Bluetooth to the app that runs the shell — System Settings → Privacy &
Security → Bluetooth — then:

```bash
python3 -m pip install --user bleak
./tools/comm-bench --transport ble --out docs/comm-baseline-ble.json
```
