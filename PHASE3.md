# Phase 3 — Log parsing

Goal: read recorded CAN logs and iterate the frames — both the *raw frame*
flavor (BLF/ASC via `python-can`) and the *decoded signal* flavor (MDF/MF4 via
`asammdf`). Raw frames are decoded back to signals with the Phase 2 DBC, which
is the same `(can_id, data)` seam Phase 4 will replay over UDP.

## Files

- `make_sample_logs.py` — synthesizes a short EngineData (id 0x100) trace and
  writes it as `engine.blf`, `engine.asc`, and `engine_signals.mf4`.
- `read_logs.py` — reads those logs back: iterates raw frames and decodes each
  via `sample.dbc`; lists/samples the MF4 signal channels.

We synthesize the logs because there's no real recorded vehicle log in the
project (and to avoid proprietary data). Drop a real `.blf`/`.asc`/`.mf4` in
and `read_logs.py <file>` reads it the same way.

## Generated logs

| file                | format            | holds            | reader            |
|---------------------|-------------------|------------------|-------------------|
| `engine.blf`        | Vector binary     | raw CAN frames   | `can.BLFReader`   |
| `engine.asc`        | Vector ASCII      | raw CAN frames   | `can.ASCReader`   |
| `engine_signals.mf4`| ASAM MDF v4       | decoded signals  | `asammdf.MDF`     |

The trace ramps the engine from idle: `EngineSpeed` 800→3600 rpm, `CoolantTemp`
60→88 °C, `ThrottlePos` 0→84 %, `GearRatio` constant 3.25, 8 frames at 0.1 s.

## Run it

```bash
# activate the venv first (see CLAUDE.md)
python make_sample_logs.py        # (re)generate the three logs
python read_logs.py               # read all three
python read_logs.py engine.blf    # or one at a time
```

## Two flavors of log, two code paths

**Raw frames (python-can).** `can.LogReader(path)` picks the right reader by
file suffix and yields `can.Message` objects — each an arbitration id + raw
bytes + timestamp. The bytes mean nothing until decoded, so we feed
`(msg.arbitration_id, msg.data)` into `db.decode_message(...)`:

```
t=  0.000  id=0x100  dlc=8  data=800c640001450000
      -> EngineSpeed=800.0  CoolantTemp=60  ThrottlePos=0.0  GearRatio=3.25
t=  0.700  id=0x100  dlc=8  data=403880d201450000
      -> EngineSpeed=3600.0  CoolantTemp=88  ThrottlePos=84.0  GearRatio=3.25
```

**Decoded signals (asammdf).** An MDF/MF4 often stores the engineering values
directly as named, time-stamped channels — no DBC needed to read them:

```
CoolantTemp  [8 samples] degC: 60, 64, 68, 72 ...
EngineSpeed  [8 samples] rpm: 800, 1200, 1600, 2000 ...
ThrottlePos  [8 samples] %: 0, 12, 24, 36 ...
```

(python-can can also log raw CAN into MF4 via `can.MF4Writer`; here we used
asammdf directly to show the decoded-signal channel form.)

## Tie-in to Phase 4

`read_logs.read_raw_log` already produces the exact pair Phase 4 needs:
`arbitration_id` + `data` bytes per frame, in timestamp order. Phase 4 packs
those into the `>I d I 8s` UDP payload and replays them to Wireshark.

## Status

- BLF written and re-read; 8/8 frames decode to the original signals. ✓
- ASC written and re-read; identical decode. ✓
- MF4 written with asammdf; 4 signal channels read back with units. ✓

Next: Phase 4 — bridge CAN → UDP → Wireshark (pack parsed frames into UDP and
replay them).
