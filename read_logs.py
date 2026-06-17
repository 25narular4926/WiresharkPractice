"""Phase 3: read recorded CAN logs and iterate the frames.

Two paths, matching the two flavors of log data:

  1. Raw CAN frames (BLF / ASC) via python-can -> each frame is an
     arbitration id + raw bytes, which we decode with the Phase 2 DBC.
     This is exactly the (can_id, data) seam Phase 4 will replay over UDP.

  2. Decoded signals (MDF/MF4) via asammdf -> named, scaled channels are
     already in the file; we just list and sample them.

Usage:
    python read_logs.py                 # read all three sample logs
    python read_logs.py engine.blf      # read one raw log (BLF or ASC)
    python read_logs.py engine_signals.mf4
"""

import sys

import can
import cantools

DBC_PATH = "sample.dbc"


def read_raw_log(path, db, limit=None):
    """Iterate a BLF/ASC log with python-can; decode each frame via the DBC."""
    print(f"=== Raw CAN frames: {path} ===")
    count = 0
    with can.LogReader(path) as reader:   # picks BLFReader/ASCReader by suffix
        for msg in reader:
            if not isinstance(msg, can.Message) or msg.is_error_frame:
                continue
            count += 1
            line = (f"  t={msg.timestamp:7.3f}  id=0x{msg.arbitration_id:X}  "
                    f"dlc={msg.dlc}  data={bytes(msg.data).hex()}")
            try:
                decoded = db.decode_message(msg.arbitration_id, msg.data)
                pretty = "  ".join(f"{k}={v}" for k, v in decoded.items())
                line += f"\n        -> {pretty}"
            except KeyError:
                line += "  (id not in DBC)"
            print(line)
            if limit and count >= limit:
                break
    print(f"  ({count} frame(s))\n")


def read_mdf(path):
    """Read an MDF/MF4 with asammdf; list channels and show samples."""
    from asammdf import MDF
    print(f"=== Decoded MDF signals: {path} ===")
    with MDF(path) as mdf:
        # channel names live in the channels DB; skip the time master channel.
        names = [n for n in mdf.channels_db if n.lower() not in ("t", "time")]
        for name in sorted(names):
            sig = mdf.get(name)
            head = ", ".join(f"{v:g}" for v in sig.samples[:4])
            unit = f" {sig.unit}" if sig.unit else ""
            print(f"  {name:<12} [{len(sig.samples)} samples]{unit}: "
                  f"{head} ...")
    print()


def dispatch(path, db):
    if path.lower().endswith(".mf4") or path.lower().endswith(".mdf"):
        read_mdf(path)
    else:
        read_raw_log(path, db)


def main(args):
    db = cantools.database.load_file(DBC_PATH)
    paths = args or ["engine.blf", "engine.asc", "engine_signals.mf4"]
    for path in paths:
        dispatch(path, db)


if __name__ == "__main__":
    main(sys.argv[1:])
