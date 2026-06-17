"""Phase 1 warm-up: receive and unpack the UDP datagrams from udp_sender.py.

Binds to the default port, blocks on recvfrom, and prints each decoded
packet. Stop with Ctrl-C. Struct format must match udp_sender.py.
"""

import socket
import struct

HOST = "0.0.0.0"   # listen on all interfaces
PORT = 5005
PACKET_FORMAT = ">I d I 8s"   # 24 bytes
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))
    print(f"listening on {HOST}:{PORT} (expecting {PACKET_SIZE}-byte packets)"
          "  -- Ctrl-C to stop")
    try:
        while True:
            payload, addr = sock.recvfrom(1024)
            if len(payload) != PACKET_SIZE:
                print(f"  ignored {len(payload)} bytes from {addr}")
                continue
            counter, ts, can_id, data = struct.unpack(PACKET_FORMAT, payload)
            print(f"#{counter} from {addr[0]}:{addr[1]} ts={ts:.3f} "
                  f"id=0x{can_id:X} data={data.hex()}")
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
