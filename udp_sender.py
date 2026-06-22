"""Phase 1 warm-up: send fixed-layout UDP datagrams for Wireshark capture.

Payload (network byte order, 24 bytes total):
    >  I    counter        unsigned 32-bit, increments per packet
       d    unix timestamp double (time.time())
       I    CAN id         placeholder for later phases
       8s   data           8 raw bytes (classic CAN max payload)

Standard library only. Default port 5005. Keep this struct format in sync
with udp_receiver.py.
"""

import socket
import struct
import time

HOST = "127.0.0.1"   # loopback; capture needs Npcap loopback adapter on Windows
PORT = 5005
# REVIEW: this wire format is the project's "protocol" and is DUPLICATED verbatim
# in udp_receiver.py, read_capture.py, can_udp_bridge.py. No single source of
# truth -- if you change it here, change it in all four or they silently mismatch.
PACKET_FORMAT = ">I d I 8s"   # 24 bytes
SEND_COUNT = 10               # number of datagrams to send
INTERVAL = 0.5                # seconds between sends


def main():
    # REVIEW [function]: open a UDP socket, then pack+send SEND_COUNT placeholder
    # frames. Pure warm-up: proves the send path before any CAN machinery exists.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # IPv4 + UDP
    try:
        for counter in range(SEND_COUNT):
            can_id = 0x100 + counter            # placeholder CAN id
            # REVIEW: demo filler, NOT real CAN data. bytes(range(...)) would
            # raise once a value exceeds 0xFF (not reached at SEND_COUNT=10).
            data = bytes(range(counter, counter + 8))  # 8 demo bytes
            # REVIEW: the `d` field is send time (time.time()), not a CAN
            # timestamp -- the same meaning the Phase 4 bridge preserves.
            payload = struct.pack(
                PACKET_FORMAT, counter, time.time(), can_id, data
            )
            sock.sendto(payload, (HOST, PORT))  # fire-and-forget (no ack/handshake)
            print(f"sent #{counter} -> {HOST}:{PORT} "
                  f"id=0x{can_id:X} data={data.hex()} ({len(payload)} bytes)")
            time.sleep(INTERVAL)
    finally:
        sock.close()


if __name__ == "__main__":
    main()
