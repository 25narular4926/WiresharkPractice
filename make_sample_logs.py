"""Phase 3: generate small sample CAN logs so there's data to parse.

We don't have a real recorded vehicle log, so we synthesize a short EngineData
(id 0x100) trace and write it in three formats:

  - engine.blf          Vector binary (compact)         -> python-can
  - engine.asc          Vector ASCII (human-readable)   -> python-can
  - engine_signals.mf4  MDF with *decoded* signal       -> asammdf
                        channels (the flavor an MDF/MF4
                        often holds: named, scaled values)

The raw-frame logs (BLF/ASC) carry the same 8-byte payloads our Phase 2 DBC
encodes, so read_logs.py can decode them back into signals. The MF4 stores the
engineering values directly as time series.

Usage:  python make_sample_logs.py
"""

import can
import cantools
import numpy as np
from asammdf import MDF, Signal

DBC_PATH = "sample.dbc"
FRAME_ID = 0x100
N = 8                # number of frames
DT = 0.1            # seconds between frames


def build_trace(db):
    """Return a list of (t, signals_dict, raw_bytes) ramping the engine up."""
    msg = db.get_message_by_frame_id(FRAME_ID)
    trace = []
    for i in range(N):
        signals = {
            "EngineSpeed": 800.0 + i * 400.0,    # idle -> revving
            "CoolantTemp": 60 + i * 4,           # warming up
            "ThrottlePos": min(i * 12.0, 100.0),
            "GearRatio": 3.25,
        }
        data = msg.encode(signals)
        trace.append((i * DT, signals, data))
    return trace


def write_raw_logs(trace):
    """Write the same frames to BLF and ASC via python-can."""
    for path, writer_cls in (("engine.blf", can.BLFWriter),
                             ("engine.asc", can.ASCWriter)):
        with writer_cls(path) as writer:
            for t, _signals, data in trace:
                writer.on_message_received(can.Message(
                    timestamp=t,
                    arbitration_id=FRAME_ID,
                    is_extended_id=False,
                    data=data,
                ))
        print(f"wrote {path}  ({N} frames)")


def write_mdf_signals(trace):
    """Write decoded signals as time-series channels via asammdf."""
    t = np.array([row[0] for row in trace], dtype="f8")
    names = ["EngineSpeed", "CoolantTemp", "ThrottlePos", "GearRatio"]
    units = {"EngineSpeed": "rpm", "CoolantTemp": "degC",
             "ThrottlePos": "%", "GearRatio": ""}
    sigs = []
    for name in names:
        samples = np.array([row[1][name] for row in trace], dtype="f8")
        sigs.append(Signal(samples=samples, timestamps=t,
                           name=name, unit=units[name]))
    with MDF() as mdf:
        mdf.append(sigs, comment="Phase 3 synthetic EngineData signals")
        mdf.save("engine_signals.mf4", overwrite=True)
    print(f"wrote engine_signals.mf4  ({len(names)} channels, {N} samples)")


def main():
    db = cantools.database.load_file(DBC_PATH)
    trace = build_trace(db)
    write_raw_logs(trace)
    write_mdf_signals(trace)


if __name__ == "__main__":
    main()
