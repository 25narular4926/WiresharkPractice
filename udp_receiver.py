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
    # REVIEW [function]: bind the port, block on recvfrom, unpack+print each
    # datagram. The other end of the warm-up.
    # REVIEW: ordering trap -- this MUST be running BEFORE any sender. UDP does
    # not queue for an unbound port, so a sender that starts first just has its
    # early packets dropped silently (the usual "nothing printed" confusion).
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))
    print(f"listening on {HOST}:{PORT} (expecting {PACKET_SIZE}-byte packets)"
          "  -- Ctrl-C to stop")
    try:
        while True:
            payload, addr = sock.recvfrom(1024)
            # REVIEW: defensive length guard BEFORE unpack -- struct.unpack raises
            # on a wrong-sized buffer, so a stray packet is skipped, not crashed
            # on. PACKET_SIZE comes from struct.calcsize, so it tracks the format.
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
