import csv
import socket
import struct

HOST = "127.0.0.1"
PORT = 5005
PACKET_FORMAT = ">I d d"
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)
OUT = "received.csv"
IDLE_TIMEOUT = 0.5


def receive():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))
    rows = []
    print(f"listening on {HOST}:{PORT} ...")
    while True:
        try:
            data, _ = sock.recvfrom(1024)
        except socket.timeout:
            break
        index, timestamp, value = struct.unpack(PACKET_FORMAT, data)
        rows.append((index, timestamp, value))
        print(f"recv #{index:3d}  t={timestamp:6.3f}  value={value:.6f}")
        sock.settimeout(IDLE_TIMEOUT)
    sock.close()
    return rows


def write_csv(rows):
    with open(OUT, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["index", "timestamp", "value"])
        for index, timestamp, value in rows:
            writer.writerow([index, repr(timestamp), repr(value)])
    print(f"wrote {OUT}: {len(rows)} samples")


def main():
    rows = receive()
    write_csv(rows)


if __name__ == "__main__":
    main()
