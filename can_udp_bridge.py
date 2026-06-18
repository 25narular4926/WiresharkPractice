"""Phase 4: bridge a recorded CAN log to UDP and replay it for Wireshark.

This is the payoff phase: it joins the three earlier seams into one pipeline.

    log file  --(python-can / asammdf, Phase 3)-->  (can_id, data) frames
              --(struct ">I d I 8s", Phase 1)-->  UDP datagrams
              --(Wireshark / read_capture.py --dbc, Phase 2)-->  signals

It handles two flavors of log, the same two Phase 3 reads:

  - BLF / ASC  -- *raw CAN frames*. `can.LogReader` already yields an
    arbitration id + data bytes per frame; we send them as-is. No DBC needed.
  - MF4 / MDF  -- *decoded signal channels* (named, scaled values; the CAN id
    is gone). To put these back on the wire we re-encode each sample row into
    CAN bytes with the DBC -- the inverse of Phase 2's decode -- so an MF4
    needs `--dbc`. See iter_frames_from_mdf.

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
PACKET_FORMAT = ">I d I 8s"   # 24 bytes -- identical to udp_sender.py


def iter_frames(path):
    """Yield (timestamp, arbitration_id, data_bytes) for each CAN frame.

    Wraps can.LogReader, which picks BLFReader/ASCReader by file suffix --
    the same reader read_logs.read_raw_log uses. Error/non-message records
    are skipped so only real frames reach the wire.
    """
    with can.LogReader(path) as reader:
        for msg in reader:
            if not isinstance(msg, can.Message) or msg.is_error_frame:
                continue
            yield msg.timestamp, msg.arbitration_id, bytes(msg.data)


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

    # Which message does each signal name belong to?
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
            if set(sigs) != needed:
                missing = sorted(needed - set(sigs))
                print(f"  skipping {msg.name} (0x{frame_id:X}): "
                      f"missing channel(s) {missing}")
                continue
            # All our generated signals share one timebase; use it and align by
            # index (each Signal's samples line up with its own timestamps).
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
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # IPv4 + UDP
    counter = 0
    sent = 0
    try:
        for lap in range(loops):
            if loops > 1:
                print(f"--- lap {lap + 1}/{loops} ---")
            prev_log_ts = None
            for log_ts, can_id, data in frame_source():
                # Pace before sending: replay the gap between recorded frames.
                if interval is not None:
                    if prev_log_ts is not None:
                        time.sleep(interval)
                elif prev_log_ts is not None:
                    gap = (log_ts - prev_log_ts) / speed
                    if gap > 0:
                        time.sleep(gap)
                prev_log_ts = log_ts

                # `8s` pads short payloads with nulls and truncates >8 bytes.
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

    if args.speed <= 0:
        parser.error("--speed must be > 0")
    if args.loops < 1:
        parser.error("--loop must be >= 1")

    # Pick the reader by file type. MF4/MDF holds decoded signals, so it needs
    # the DBC to re-encode them into frames; BLF/ASC already hold raw frames.
    if args.log.lower().endswith((".mf4", ".mdf")):
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
