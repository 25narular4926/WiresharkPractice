"""Phase 3: generate small sample CAN logs so there's data to parse.

We don't have a real recorded vehicle log, so we synthesize a short EngineData
(id 0x100) trace and write it in four formats:

  - engine.blf          Vector binary (compact)         -> python-can
  - engine.asc          Vector ASCII (human-readable)   -> python-can
  - engine_signals.mf4  MDF with *decoded* signal       -> asammdf
                        channels (the flavor an MDF/MF4
                        often holds: named, scaled values)
  - engine_buslog.mf4   MDF with *raw CAN bus logging*  -> asammdf
                        (a CAN_DataFrame channel holding
                        arbitration id + raw data bytes,
                        the flavor a real CAN logger writes)

The raw-frame logs (BLF/ASC) carry the same 8-byte payloads our Phase 2 DBC
encodes, so read_logs.py can decode them back into signals. engine_signals.mf4
stores the engineering values directly as time series. engine_buslog.mf4 stores
the *raw frames* the way real automotive CAN loggers do (Vector/CSS Electronics
etc.): a structured "CAN_DataFrame" channel whose sub-channels are the
arbitration id, DLC and the 8 raw data bytes -- so it can be replayed as-is,
no DBC re-encode needed (unlike engine_signals.mf4).

Usage:  python make_sample_logs.py
"""

import can
import cantools
import numpy as np
from asammdf import MDF, Signal, Source

DBC_PATH = "sample.dbc"
FRAME_ID = 0x100
N = 8                # number of frames
DT = 0.1            # seconds between frames


def build_trace(db):
    """Return a list of (t, signals_dict, raw_bytes) ramping the engine up."""
    # REVIEW [function]: the single source of the synthetic ramp. All four output
    # files are derived from this same trace, which is why they decode identically.
    msg = db.get_message_by_frame_id(FRAME_ID)
    trace = []
    for i in range(N):
        signals = {
            "EngineSpeed": 800.0 + i * 400.0,    # idle -> revving
            "CoolantTemp": 60 + i * 4,           # warming up
            # REVIEW: clamp at 100, but the DBC allows up to 102 (8-bit @ 0.4/bit).
            # Verify the encoded raw byte never overflows 8 bits across the ramp
            # (it doesn't at these values).
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
    # REVIEW [function]: produces the DECODED-signal MF4. It stores values only --
    # NOT the CAN id -- which is why can_udp_bridge.iter_frames_from_mdf has to
    # recover the id from the DBC and re-encode. All channels share one timebase
    # `t`; the bridge relies on that shared timebase.
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


def write_mdf_buslog(trace):
    """Write the *raw* CAN frames as an MDF bus-logging channel via asammdf.

    This is the real-world flavor: instead of decoded signal channels, a CAN
    logger stores each frame verbatim under a single structured channel named
    "CAN_DataFrame" whose sub-channels follow the ASAM bus-logging convention:

        CAN_DataFrame.BusChannel   which physical bus (1, 2, ...)
        CAN_DataFrame.ID           arbitration id
        CAN_DataFrame.IDE          extended-id flag (0 = 11-bit)
        CAN_DataFrame.DLC          data length code
        CAN_DataFrame.DataLength   actual byte count
        CAN_DataFrame.DataBytes    the raw payload (here fixed 8 bytes)

    asammdf recognizes the group as CAN bus logging because we tag it with a
    Source of type BUS / bus_type CAN; that's the same marker its
    `extract_bus_logging()` looks for. Because the frames are already raw, this
    file replays as-is over UDP -- no DBC re-encode (the inverse of the
    engine_signals.mf4 path). See can_udp_bridge.iter_frames_from_buslog_mdf.
    """
    t = np.array([row[0] for row in trace], dtype="f8")
    n = len(trace)

    bus = np.ones(n, dtype="<u1")                 # one bus, channel 1
    ids = np.full(n, FRAME_ID, dtype="<u4")       # all EngineData (0x100)
    ide = np.zeros(n, dtype="<u1")                # 11-bit (standard) ids
    dlc = np.full(n, 8, dtype="<u1")              # EngineData is 8 bytes
    data_bytes = np.array([list(row[2]) for row in trace], dtype="<u1")

    # A numpy structured (record) array becomes one composed MDF channel whose
    # fields are the sub-channels above -- exactly the CAN_DataFrame layout.
    record_dtype = np.dtype([
        ("CAN_DataFrame.BusChannel", "<u1"),
        ("CAN_DataFrame.ID", "<u4"),
        ("CAN_DataFrame.IDE", "<u1"),
        ("CAN_DataFrame.DLC", "<u1"),
        ("CAN_DataFrame.DataLength", "<u1"),
        ("CAN_DataFrame.DataBytes", "(8,)u1"),
    ])
    samples = np.rec.fromarrays(
        [bus, ids, ide, dlc, dlc, data_bytes], dtype=record_dtype)

    # REVIEW: this Source tag is LOAD-BEARING. Without source_type=BUS /
    # bus_type=CAN the group isn't marked as bus logging, and the content-based
    # detection (mdf_is_bus_logging) plus asammdf's extract_bus_logging would not
    # recognize the file. Don't drop it.
    # Tagging the source as BUS / CAN is what marks this group as bus logging
    # (sets FLAG_CG_BUS_EVENT + bus_type CAN under the hood).
    source = Source(name="CAN", path="CAN", comment="",
                    source_type=Source.SOURCE_BUS,
                    bus_type=Source.BUS_TYPE_CAN)
    frame = Signal(samples=samples, timestamps=t,
                   name="CAN_DataFrame", source=source)

    with MDF() as mdf:
        mdf.append(frame, comment="Phase 3 synthetic EngineData raw bus logging")
        mdf.save("engine_buslog.mf4", overwrite=True)
    print(f"wrote engine_buslog.mf4  (1 CAN_DataFrame channel, {n} frames)")


def main():
    db = cantools.database.load_file(DBC_PATH)
    trace = build_trace(db)
    write_raw_logs(trace)
    write_mdf_signals(trace)
    write_mdf_buslog(trace)


if __name__ == "__main__":
    main()
