"""Decode a .pcapng captured from Wireshark/dumpcap and show each UDP frame.

This mirrors what Wireshark's packet list + detail panes display: per packet,
the IP/UDP headers and the 24-byte application payload, unpacked with the same
struct layout udp_sender.py used. Lets us confirm the captured frames are the
ones we sent, without needing the Wireshark GUI or tshark.

With --dbc it also DBC-decodes each frame's can_id + data back into named,
scaled signals (the Phase 2 seam) -- so a Phase 4 replay capture shows the
EngineSpeed/CoolantTemp/... values, closing the log -> UDP -> signals loop.

Usage:
    python read_capture.py [capture_file.pcapng]
    python read_capture.py capture_5005.pcapng --dbc          # + decode signals
    python read_capture.py capture_5005.pcapng --dbc sample.dbc
"""

import argparse
import struct

from scapy.all import rdpcap, IP, UDP

PACKET_FORMAT = ">I d I 8s"   # must match udp_sender.py / udp_receiver.py


def main(path, dbc_path=None):
    # REVIEW [function]: load a pcapng, and for each IP+UDP packet peel the
    # payload, unpack the 24 bytes, and (with --dbc) decode to signals. This is
    # the capture-side twin of udp_receiver.main -- same unpack, same decode.
    # REVIEW: lazy DBC import -- only paid when --dbc is given, so the plain
    # unpack path stays scapy-only / dependency-light.
    db = None
    if dbc_path:
        import cantools
        db = cantools.database.load_file(dbc_path)

    packets = rdpcap(path)
    print(f"{path}: {len(packets)} packet(s)\n")
    for i, pkt in enumerate(packets):
        # REVIEW: non-IP/UDP packets are silently skipped -- a malformed capture
        # just yields fewer lines, never an error.
        if not (pkt.haslayer(UDP) and pkt.haslayer(IP)):
            continue
        ip, udp = pkt[IP], pkt[UDP]
        payload = bytes(udp.payload)   # scapy dissected the headers; this is our 24 bytes
        line = (f"[{i}] {ip.src}:{udp.sport} -> {ip.dst}:{udp.dport}  "
                f"len={len(payload)}B")
        # REVIEW: only unpack when length matches exactly; other UDP payloads
        # print a header line only (intended, but worth knowing on a busy capture).
        if len(payload) == struct.calcsize(PACKET_FORMAT):
            counter, ts, can_id, data = struct.unpack(PACKET_FORMAT, payload)
            line += (f"  | counter={counter} ts={ts:.3f} "
                     f"can_id=0x{can_id:X} data={data.hex()}")
            if db is not None:
                try:
                    decoded = db.decode_message(can_id, data)
                    pretty = "  ".join(f"{k}={v}" for k, v in decoded.items())
                    line += f"\n      -> {pretty}"
                except KeyError:
                    # REVIEW: only catches an unknown can_id. A malformed/short
                    # `data` would raise a different error and not be swallowed.
                    line += "\n      -> (id not in DBC)"
        print(line)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("capture", nargs="?", default="capture_5005.pcapng",
                        help="pcapng file to read (default capture_5005.pcapng)")
    parser.add_argument("--dbc", nargs="?", const="sample.dbc", default=None,
                        help="DBC-decode each frame's can_id+data "
                             "(default DBC: sample.dbc)")
    args = parser.parse_args()
    main(args.capture, args.dbc)
