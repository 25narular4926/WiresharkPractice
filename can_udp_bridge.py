"""Phase 4: bridge a recorded CAN log to UDP and replay it for Wireshark.

This is the payoff phase: it joins the three earlier seams into one pipeline.

    log file  --(python-can / asammdf, Phase 3)-->  (can_id, data) frames
              --(struct ">I d I 8s", Phase 1)-->  UDP datagrams
              --(Wireshark / read_capture.py --dbc, Phase 2)-->  signals

It handles three flavors of log:

  - BLF / ASC  -- *raw CAN frames*. `can.LogReader` already yields an
    arbitration id + data bytes per frame; we send them as-is. No DBC needed.
  - MF4 / MDF (raw bus logging) -- a *real CAN logger* file: each frame is
    stored verbatim under a "CAN_DataFrame" channel (id + raw data bytes). Like
    BLF/ASC, the frames are already on hand, so we replay them as-is -- no DBC
    re-encode. See iter_frames_from_buslog_mdf.
  - MF4 / MDF (decoded signals) -- *named, scaled signal channels* (the CAN id
    is gone). To put these back on the wire we re-encode each sample row into
    CAN bytes with the DBC -- the inverse of Phase 2's decode -- so this flavor
    needs `--dbc`. See iter_frames_from_mdf.

The two MDF flavors are told apart automatically (see mdf_is_bus_logging), not
by file suffix -- a real recorded `.mf4` could be either.

Each frame's arbitration id + data is packed into the Phase 1 UDP payload and
fired at 127.0.0.1:5005. By default the replay is paced by the log's *own*
timestamps, so frames go out with the same spacing they were recorded with --
exactly how a CAN replay tool feeds a bus. `--speed` scales that; `--interval`
ignores log timing and uses a fixed gap instead.

The `d` timestamp field stays send-time `time.time()` (its documented meaning
in the Phase 1 layout), so the wire format is byte-for-byte identical to the
warm-up and udp_receiver.py / read_capture.py unpack it unchanged. The log's
recorded timestamp is shown in this script's own stdout for reference.

Inspect the result three ways:
  - live:     run udp_receiver.py in another terminal (prints can_id + data);
  - decode:   capture with dumpcap, then `read_capture.py --dbc` to see signals;
  - GUI:      Wireshark display filter `udp.port == 5005`.
See PHASE4.md.

Usage:
    python can_udp_bridge.py                       # replay engine.blf (real-time)
    python can_udp_bridge.py engine.asc            # ASC instead of BLF
    python can_udp_bridge.py engine_signals.mf4    # MF4 signals -> re-encoded frames
    python can_udp_bridge.py engine_buslog.mf4     # MF4 raw bus logging -> replayed as-is
    python can_udp_bridge.py engine.blf --speed 5  # 5x faster
    python can_udp_bridge.py engine.blf --interval 0.5   # fixed 0.5s gap
    python can_udp_bridge.py engine.blf --loop 3   # replay the log 3 times
    python can_udp_bridge.py engine_signals.mf4 --dbc other.dbc   # explicit DBC
"""

import argparse
import socket
import struct
import time

import can

HOST = "127.0.0.1"            # loopback; capture needs the Npcap loopback adapter
PORT = 5005
# REVIEW: must stay byte-identical to the same constant in udp_sender.py,
# udp_receiver.py, read_capture.py. Duplicated, not shared (no single source).
PACKET_FORMAT = ">I d I 8s"   # 24 bytes -- identical to udp_sender.py


def iter_frames(path):
    """Yield (timestamp, arbitration_id, data_bytes) for each CAN frame.

    Wraps can.LogReader, which picks BLFReader/ASCReader by file suffix --
    the same reader read_logs.read_raw_log uses. Error/non-message records
    are skipped so only real frames reach the wire.
    """
    # REVIEW [function]: BLF/ASC source -- simplest path, frames already raw, no
    # DBC. The skip filter must match read_logs.read_raw_log's.
    with can.LogReader(path) as reader:
        for msg in reader:
            if not isinstance(msg, can.Message) or msg.is_error_frame:
                continue
            yield msg.timestamp, msg.arbitration_id, bytes(msg.data)


def mdf_is_bus_logging(path):
    """True if an MDF/MF4 holds *raw CAN bus logging* (a CAN_DataFrame channel).

    A real CAN logger stores each frame verbatim under a structured channel
    called "CAN_DataFrame" (with .ID / .DataBytes / .DLC sub-channels). A
    decoded-signal MF4 (like engine_signals.mf4) has none of that -- just named
    signal channels. So the presence of "CAN_DataFrame" cleanly tells the two
    apart, which lets us pick the right replay path by *content* rather than by
    file suffix (a `.mf4` could be either flavor).
    """
    # REVIEW [function]: the content-based router for the two MDF flavors. Same
    # check is duplicated in read_logs.mdf_is_bus_logging -- keep them identical.
    from asammdf import MDF
    with MDF(path) as mdf:
        return "CAN_DataFrame" in mdf.channels_db


def iter_frames_from_buslog_mdf(path):
    """Yield (timestamp, arbitration_id, data_bytes) from a raw bus-logging MDF.

    The frames are already raw in the file -- this is the MDF equivalent of the
    BLF/ASC path, so NO DBC is needed (unlike iter_frames_from_mdf, which has to
    re-encode decoded signals). We read the standard ASAM bus-logging
    sub-channels written by CAN loggers:

        CAN_DataFrame.ID         arbitration id (mask off the IDE flag bit)
        CAN_DataFrame.DataBytes  the raw payload, one row of bytes per frame
        CAN_DataFrame.DLC        data length code (how many of those bytes count)

    Each row becomes one (timestamp, can_id, data) frame, exactly the seam
    replay() consumes. (asammdf also offers MDF.extract_bus_logging(dbc) to
    decode such a file straight to signals; we don't need that here because we
    only want to put the raw bytes back on the wire -- read_capture.py --dbc /
    read_logs.py do the decoding on the receive side.)
    """
    # REVIEW [function]: real-recorder MDF path. NO DBC needed (frames are already
    # raw) -- this is the MDF equivalent of iter_frames, not of
    # iter_frames_from_mdf. Mirrors read_logs.read_buslog_mdf's extraction.
    from asammdf import MDF

    frames = []
    with MDF(path) as mdf:
        ids = mdf.get("CAN_DataFrame.ID")
        data_bytes = mdf.get("CAN_DataFrame.DataBytes")
        try:
            dlc = mdf.get("CAN_DataFrame.DLC").samples
        except Exception:
            dlc = None   # no DLC channel: fall back to the full row width

        timestamps = ids.timestamps
        for i in range(len(timestamps)):
            # REVIEW: mask off the IDE flag bit -> plain 11-/29-bit id (same as
            # read_logs.read_buslog_mdf).
            can_id = int(ids.samples[i]) & 0x1FFFFFFF
            row = bytes(bytearray(data_bytes.samples[i]))
            # REVIEW: truncate the fixed-width DataBytes row to the real DLC, so a
            # <8-byte frame doesn't carry trailing padding onto the wire.
            n = int(dlc[i]) if dlc is not None else len(row)
            frames.append((float(timestamps[i]), can_id, row[:n]))

    frames.sort(key=lambda f: f[0])   # send in recorded time order
    return iter(frames)


def iter_frames_from_mdf(path, db):
    """Reconstruct CAN frames from an MDF/MF4 of *decoded* signals.

    An MF4 like engine_signals.mf4 stores named, scaled signal channels rather
    than raw CAN frames, so the message identity (the CAN id) isn't in the file.
    We recover it from the DBC: group the available channels by the message
    whose signals they are, then for each timestamp row encode the values back
    into bytes with `msg.encode(...)` -- the exact inverse of the Phase 2 decode.

    Yields (timestamp, can_id, data_bytes) in timestamp order. A message is only
    replayed if *all* its DBC signals are present as channels (cantools needs
    every signal to encode a frame); partially-present messages are skipped with
    a note.
    """
    from asammdf import MDF

    # REVIEW [function]: the trickiest reader. Three assumptions a reviewer should
    # check against any *real* MDF: (1) signal names are globally unique across
    # the DBC (see sig_to_msg below); (2) a message is only sent if ALL its
    # signals are present (partial messages dropped); (3) all signals of a message
    # share one timebase and align by index. Our synthetic file satisfies all
    # three; a real recording may not.
    # Which message does each signal name belong to?
    # REVIEW: collides if two DBC messages share a signal name (last one wins).
    sig_to_msg = {sig.name: msg for msg in db.messages for sig in msg.signals}

    frames = []
    with MDF(path) as mdf:
        # Channel names, minus the time master (matches read_logs.read_mdf).
        channels = [n for n in mdf.channels_db
                    if n.lower() not in ("t", "time")]

        # Bucket the present channels by their DBC message.
        by_msg = {}   # frame_id -> {"msg": msg, "sigs": {name: Signal}}
        for name in channels:
            msg = sig_to_msg.get(name)
            if msg is None:
                continue            # channel not in the DBC; ignore it
            slot = by_msg.setdefault(msg.frame_id, {"msg": msg, "sigs": {}})
            slot["sigs"][name] = mdf.get(name)

        if not by_msg:
            raise SystemExit(
                f"no channels in {path} match any signal in the DBC -- "
                f"is this the right .dbc for this log?")

        for frame_id, slot in by_msg.items():
            msg, sigs = slot["msg"], slot["sigs"]
            needed = {s.name for s in msg.signals}
            # REVIEW: cantools.encode needs EVERY signal -> a message missing any
            # channel is skipped wholesale. A real MDF with a signal subset would
            # be dropped here, not partially sent.
            if set(sigs) != needed:
                missing = sorted(needed - set(sigs))
                print(f"  skipping {msg.name} (0x{frame_id:X}): "
                      f"missing channel(s) {missing}")
                continue
            # REVIEW: assumes one shared timebase, aligning samples by index. Fine
            # for our generator; a real MDF with per-channel timestamps would
            # misalign signals into the wrong frames here.
            timestamps = next(iter(sigs.values())).timestamps
            for i in range(len(timestamps)):
                values = {name: float(sig.samples[i])
                          for name, sig in sigs.items()}
                data = bytes(msg.encode(values))
                frames.append((float(timestamps[i]), frame_id, data))

    frames.sort(key=lambda f: f[0])   # interleave messages by time
    return iter(frames)


def replay(frame_source, host, port, speed, interval, loops):
    """Read the log and send each frame as a UDP datagram, paced as requested."""
    # REVIEW [function]: format-agnostic sender -- takes any (ts, id, data) source
    # (BLF/ASC, bus-log MDF, or re-encoded signal MDF) and is where all three
    # converge. The only place the wire is written.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # IPv4 + UDP
    # REVIEW: counter climbs across --loop laps (unique packet numbering), while
    # log_t resets per lap -- intentional.
    counter = 0
    sent = 0
    try:
        for lap in range(loops):
            if loops > 1:
                print(f"--- lap {lap + 1}/{loops} ---")
            prev_log_ts = None
            for log_ts, can_id, data in frame_source():
                # REVIEW: pacing. --interval overrides log timing; else sleep the
                # recorded gap / speed. First frame has no prev_ts so no sleep;
                # gap>0 guard drops zero/negative gaps (out-of-order timestamps
                # would send with no delay). sleep() also ignores send time, so
                # pacing drifts under load -- acceptable for a demo.
                if interval is not None:
                    if prev_log_ts is not None:
                        time.sleep(interval)
                elif prev_log_ts is not None:
                    gap = (log_ts - prev_log_ts) / speed
                    if gap > 0:
                        time.sleep(gap)
                prev_log_ts = log_ts

                # REVIEW: `8s` silently pads <8 bytes with nulls and truncates >8.
                # The true DLC is NOT carried on the wire -- a known simplification.
                payload = struct.pack(
                    PACKET_FORMAT, counter, time.time(), can_id, data
                )
                sock.sendto(payload, (host, port))
                print(f"sent #{counter}  log_t={log_ts:7.3f}  "
                      f"id=0x{can_id:X}  data={data.hex()}  "
                      f"-> {host}:{port} ({len(payload)} bytes)")
                counter += 1
                sent += 1
    finally:
        sock.close()
    print(f"\ndone: {sent} frame(s) replayed to {host}:{port}")


def main():
    # REVIEW [function]: arg parsing + reader selection. Suffix picks raw-CAN vs
    # MDF; for an MDF, mdf_is_bus_logging picks the flavor by content.
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", nargs="?", default="engine.blf",
                        help="CAN log to replay: BLF/ASC (raw frames) or "
                             "MF4/MDF (decoded signals). Default: engine.blf")
    parser.add_argument("--dbc", default="sample.dbc",
                        help="DBC used to re-encode MF4/MDF signal channels "
                             "into CAN frames (default sample.dbc; "
                             "ignored for BLF/ASC)")
    parser.add_argument("--host", default=HOST, help=f"destination IP (default {HOST})")
    parser.add_argument("--port", type=int, default=PORT,
                        help=f"destination UDP port (default {PORT})")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="replay speed multiplier vs. log timestamps (default 1.0)")
    parser.add_argument("--interval", type=float, default=None,
                        help="fixed seconds between frames; overrides log timing")
    parser.add_argument("--loop", type=int, default=1, dest="loops",
                        help="number of times to replay the log (default 1)")
    args = parser.parse_args()

    # REVIEW: input validation -- guards the pacing math (speed as a divisor) and
    # the loop count.
    if args.speed <= 0:
        parser.error("--speed must be > 0")
    if args.loops < 1:
        parser.error("--loop must be >= 1")

    # Pick the reader. BLF/ASC always hold raw frames. An MF4/MDF holds one of
    # two flavors, told apart by content (not suffix): raw bus logging replays
    # as-is, while decoded signals must be re-encoded into frames via the DBC.
    if args.log.lower().endswith((".mf4", ".mdf")):
        if mdf_is_bus_logging(args.log):
            print(f"reading {args.log} as raw CAN bus logging "
                  f"(CAN_DataFrame); replaying frames as-is")
            frame_source = lambda: iter_frames_from_buslog_mdf(args.log)
        else:
            import cantools
            db = cantools.database.load_file(args.dbc)
            print(f"reading {args.log} as decoded signals; "
                  f"re-encoding via {args.dbc}")
            frame_source = lambda: iter_frames_from_mdf(args.log, db)
    else:
        frame_source = lambda: iter_frames(args.log)

    replay(frame_source, args.host, args.port,
           args.speed, args.interval, args.loops)


if __name__ == "__main__":
    main()
