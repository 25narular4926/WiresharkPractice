"""Phase 3: read recorded CAN logs and iterate the frames.

Three paths, matching the flavors of log data:

  1. Raw CAN frames (BLF / ASC) via python-can -> each frame is an
     arbitration id + raw bytes, which we decode with the Phase 2 DBC.
     This is exactly the (can_id, data) seam Phase 4 will replay over UDP.

  2. Raw CAN bus logging (MDF/MF4 with a CAN_DataFrame channel) via asammdf ->
     a real CAN logger file: each frame's id + raw bytes are in the file, so we
     read and DBC-decode them just like the BLF/ASC path.

  3. Decoded signals (MDF/MF4 with named channels) via asammdf -> named, scaled
     channels are already in the file; we just list and sample them.

The two MDF flavors (2 vs. 3) are told apart by content, not by suffix.

Usage:
    python read_logs.py                 # read all four sample logs
    python read_logs.py engine.blf      # read one raw log (BLF or ASC)
    python read_logs.py engine_signals.mf4
    python read_logs.py engine_buslog.mf4
"""

import sys

import can
import cantools

DBC_PATH = "sample.dbc"


def read_raw_log(path, db, limit=None):
    """Iterate a BLF/ASC log with python-can; decode each frame via the DBC."""
    # REVIEW [function]: the (can_id, data) seam Phase 4 replays -- read here,
    # decoded here. Mirror this filter/decoding when changing the bridge.
    print(f"=== Raw CAN frames: {path} ===")
    count = 0
    with can.LogReader(path) as reader:   # picks BLFReader/ASCReader by suffix
        for msg in reader:
            # REVIEW: skip non-Message/error records -- MUST stay in sync with the
            # identical filter in can_udp_bridge.iter_frames so the two agree on
            # what counts as a "frame".
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
                line += "  (id not in DBC)"   # REVIEW: catches unknown id only
            print(line)
            if limit and count >= limit:
                break
    print(f"  ({count} frame(s))\n")


def read_buslog_mdf(path, db, limit=None):
    """Read a raw bus-logging MDF (CAN_DataFrame channel); decode via the DBC.

    This is the MDF twin of read_raw_log: the frames are already raw in the
    file, so we pull each id + data row from the standard ASAM bus-logging
    sub-channels and decode them exactly like a BLF/ASC frame -- no
    re-encoding. (Compare read_mdf, which only lists decoded signal channels.)
    """
    # REVIEW [function]: the MDF twin of read_raw_log -- raw frames straight from
    # the CAN_DataFrame sub-channels, decoded like BLF/ASC. Contrast read_mdf,
    # which only LISTS decoded signal channels (the other MDF flavor).
    from asammdf import MDF
    print(f"=== Raw CAN bus logging (MDF): {path} ===")
    count = 0
    with MDF(path) as mdf:
        ids = mdf.get("CAN_DataFrame.ID")
        data_bytes = mdf.get("CAN_DataFrame.DataBytes")
        try:
            dlc = mdf.get("CAN_DataFrame.DLC").samples
        except Exception:
            dlc = None   # REVIEW: no DLC channel -> fall back to full row width
        timestamps = ids.timestamps
        for i in range(len(timestamps)):
            count += 1
            # REVIEW: mask the top bit -- ID can carry the extended-id (IDE) flag;
            # masking yields the plain 11-/29-bit arbitration id. Same logic the
            # bridge's iter_frames_from_buslog_mdf uses.
            can_id = int(ids.samples[i]) & 0x1FFFFFFF   # mask the IDE flag bit
            row = bytes(bytearray(data_bytes.samples[i]))
            n = int(dlc[i]) if dlc is not None else len(row)
            data = row[:n]
            line = (f"  t={float(timestamps[i]):7.3f}  id=0x{can_id:X}  "
                    f"dlc={n}  data={data.hex()}")
            try:
                decoded = db.decode_message(can_id, data)
                pretty = "  ".join(f"{k}={v}" for k, v in decoded.items())
                line += f"\n        -> {pretty}"
            except KeyError:
                line += "  (id not in DBC)"
            print(line)
            if limit and count >= limit:
                break
    print(f"  ({count} frame(s))\n")


def mdf_is_bus_logging(path):
    """True if an MDF/MF4 holds raw CAN bus logging (a CAN_DataFrame channel).

    Same content check the bridge uses: a real CAN logger stores frames under a
    "CAN_DataFrame" channel; a decoded-signal MF4 has only named signals.
    """
    from asammdf import MDF
    with MDF(path) as mdf:
        return "CAN_DataFrame" in mdf.channels_db


def read_mdf(path):
    """Read a *decoded-signal* MDF/MF4 with asammdf; list channels and samples."""
    # REVIEW [function]: only LISTS channels (no frames to decode -- the values
    # are already decoded). The bridge re-encodes these back into frames.
    from asammdf import MDF
    print(f"=== Decoded MDF signals: {path} ===")
    with MDF(path) as mdf:
        # channel names live in the channels DB; skip the time master channel.
        # REVIEW: drops the time channel by NAME ('t'/'time'). This same name
        # filter is duplicated in can_udp_bridge.iter_frames_from_mdf -- a real
        # MDF whose time channel is named differently would slip through both.
        names = [n for n in mdf.channels_db if n.lower() not in ("t", "time")]
        for name in sorted(names):
            sig = mdf.get(name)
            head = ", ".join(f"{v:g}" for v in sig.samples[:4])
            unit = f" {sig.unit}" if sig.unit else ""
            print(f"  {name:<12} [{len(sig.samples)} samples]{unit}: "
                  f"{head} ...")
    print()


def dispatch(path, db):
    # REVIEW [function]: routing. Suffix picks raw-CAN (BLF/ASC) vs MDF; then for
    # an MDF the flavor is chosen by CONTENT, not suffix (mdf_is_bus_logging).
    if path.lower().endswith(".mf4") or path.lower().endswith(".mdf"):
        # An MDF is either raw bus logging or decoded signals -- pick by content.
        if mdf_is_bus_logging(path):
            read_buslog_mdf(path, db)
        else:
            read_mdf(path)
    else:
        read_raw_log(path, db)


def main(args):
    db = cantools.database.load_file(DBC_PATH)
    paths = args or ["engine.blf", "engine.asc",
                     "engine_signals.mf4", "engine_buslog.mf4"]
    for path in paths:
        dispatch(path, db)


if __name__ == "__main__":
    main(sys.argv[1:])
