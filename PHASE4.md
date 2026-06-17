# Phase 4 — Bridge CAN → UDP → Wireshark

Goal: the payoff. Read a recorded CAN log, repackage each frame as a UDP
datagram, replay it on the wire, and watch / decode it in Wireshark. This joins
all three earlier seams into one pipeline:

```
engine.blf  --(python-can, Phase 3)-->  (can_id, data) frames
            --(struct ">I d I 8s", Phase 1)-->  UDP datagrams on :5005
            --(Wireshark / read_capture.py --dbc, Phase 2)-->  named signals
```

## Files

- `can_udp_bridge.py` — reads a BLF/ASC log with `can.LogReader` (the Phase 3
  reader), packs each frame's `arbitration_id` + `data` into the Phase 1
  `>I d I 8s` payload, and sends it to `127.0.0.1:5005`. Paced by the log's own
  timestamps by default (`--speed` scales, `--interval` overrides with a fixed
  gap, `--loop` repeats).
- `read_capture.py` — now takes `--dbc [file]`: after unpacking each captured
  frame it DBC-decodes `can_id` + `data` back into signals (Phase 2 seam).

## Wire format unchanged

The bridge emits the **exact** 24-byte `>I d I 8s` payload from the warm-up, so
`udp_receiver.py` and `read_capture.py` read it without modification. The `d`
field stays send-time `time.time()` (its documented meaning); the log's recorded
timestamp is printed to the bridge's stdout (`log_t=`) for reference, not put on
the wire.

## Run it

Activate the venv first (see CLAUDE.md), then:

```bash
# simplest: replay engine.blf in real time
python can_udp_bridge.py

python can_udp_bridge.py engine.asc          # ASC log instead of BLF
python can_udp_bridge.py engine.blf --speed 5    # 5x faster than recorded
python can_udp_bridge.py engine.blf --interval 0.5   # fixed 0.5s gap
python can_udp_bridge.py engine.blf --loop 3     # replay 3 times
```

### See it three ways

**Live (no capture).** In one terminal run the Phase 1 receiver, in another the
bridge:

```bash
python udp_receiver.py            # terminal 1: prints can_id + data per frame
python can_udp_bridge.py          # terminal 2
```

**Capture + decode (no GUI).** This machine has `dumpcap` but not the Wireshark
GUI. Capture on the loopback adapter while replaying, then decode the pcapng
straight back into signals:

```bash
DUMPCAP="/c/Program Files/Wireshark/dumpcap.exe"
"$DUMPCAP" -i "\\Device\\NPF_Loopback" -f "udp port 5005" \
          -w phase4_capture.pcapng -a duration:6 &
sleep 1.5
python can_udp_bridge.py engine.blf --speed 5
wait
python read_capture.py phase4_capture.pcapng --dbc
```

**GUI.** Open `phase4_capture.pcapng` in Wireshark (or capture live on the
loopback adapter) with display filter `udp.port == 5005`; expand **Data** to see
the 24 payload bytes — counter (4), timestamp (8), CAN id (4), data (8).

## Verified result

Captured 8/8 frames, 0 dropped, and `read_capture.py --dbc` decoded the replay
back to the original Phase 3 ramp — log → UDP → capture → signals round-trips:

```
[0] 127.0.0.1 -> 127.0.0.1:5005  can_id=0x100 data=800c640001450000
      -> EngineSpeed=800.0  CoolantTemp=60  ThrottlePos=0.0  GearRatio=3.25
...
[7] 127.0.0.1 -> 127.0.0.1:5005  can_id=0x100 data=403880d201450000
      -> EngineSpeed=3600.0  CoolantTemp=88  ThrottlePos=84.0  GearRatio=3.25
```

`EngineSpeed` 800→3600 rpm, `CoolantTemp` 60→88 °C, `ThrottlePos` 0→84 %,
`GearRatio` constant 3.25 — identical to the trace `make_sample_logs.py` wrote.

## Status

- BLF replay → UDP → loopback capture: 8/8 frames, 0 dropped. ✓
- `read_capture.py --dbc` decodes the capture to the original signals. ✓
- ASC reader and `--speed` / `--interval` / `--loop` pacing modes work. ✓

This completes the project goal: a recorded vehicle-bus log parsed in Python,
each CAN frame repackaged as a UDP datagram, sent, and inspected in Wireshark.
