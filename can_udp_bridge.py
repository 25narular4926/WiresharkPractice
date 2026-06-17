"""Phase 4: bridge a recorded CAN log to UDP and replay it for Wireshark.

This is the payoff phase: it joins the three earlier seams into one pipeline.

    log file  --(python-can, Phase 3)-->  (can_id, data) frames
              --(struct ">I d I 8s", Phase 1)-->  UDP datagrams
              --(Wireshark / read_capture.py --dbc, Phase 2)-->  signals

It reads raw CAN frames from a BLF/ASC log (the same `can.LogReader` seam
read_logs.py uses), packs each frame's arbitration id + data into the Phase 1
UDP payload, and fires them at 127.0.0.1:5005. By default the replay is paced
by the log's *own* timestamps, so frames go out with the same spacing they were
recorded with -- exactly how a CAN replay tool feeds a bus. `--speed` scales
that; `--interval` ignores log timing and uses a fixed gap instead.

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
    python can_udp_bridge.py engine.blf --speed 5  # 5x faster
    python can_udp_bridge.py engine.blf --interval 0.5   # fixed 0.5s gap
    python can_udp_bridge.py engine.blf --loop 3   # replay the log 3 times
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


def replay(path, host, port, speed, interval, loops):
    """Read the log and send each frame as a UDP datagram, paced as requested."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # IPv4 + UDP
    counter = 0
    sent = 0
    try:
        for lap in range(loops):
            if loops > 1:
                print(f"--- lap {lap + 1}/{loops} ---")
            prev_log_ts = None
            for log_ts, can_id, data in iter_frames(path):
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
                        help="CAN log to replay (BLF or ASC). Default: engine.blf")
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

    replay(args.log, args.host, args.port, args.speed, args.interval, args.loops)


if __name__ == "__main__":
    main()
