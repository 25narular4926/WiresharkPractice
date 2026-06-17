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
PACKET_FORMAT = ">I d I 8s"   # 24 bytes
SEND_COUNT = 10               # number of datagrams to send
INTERVAL = 0.5                # seconds between sends


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # IPv4 + UDP
    try:
        for counter in range(SEND_COUNT):
            can_id = 0x100 + counter            # placeholder CAN id
            data = bytes(range(counter, counter + 8))  # 8 demo bytes
            payload = struct.pack(
                PACKET_FORMAT, counter, time.time(), can_id, data
            )
            sock.sendto(payload, (HOST, PORT))  # fire-and-forget
            print(f"sent #{counter} -> {HOST}:{PORT} "
                  f"id=0x{can_id:X} data={data.hex()} ({len(payload)} bytes)")
            time.sleep(INTERVAL)
    finally:
        sock.close()


if __name__ == "__main__":
    main()
