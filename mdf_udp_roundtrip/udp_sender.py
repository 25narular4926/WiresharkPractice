import socket
import struct
import time

from asammdf import MDF

HOST = "127.0.0.1"
PORT = 5005
PACKET_FORMAT = ">I d d"
MDF_PATH = "signal.mdf"
CHANNEL = "EngineSpeed"
INTERVAL = 0.002


def load_signal():
    with MDF(MDF_PATH) as mdf:
        sig = mdf.get(CHANNEL)
    return sig.timestamps, sig.samples


def main():
    timestamps, samples = load_signal()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for i in range(len(samples)):
            payload = struct.pack(
                PACKET_FORMAT, i, float(timestamps[i]), float(samples[i])
            )
            sock.sendto(payload, (HOST, PORT))
            print(f"sent #{i:3d}  t={timestamps[i]:6.3f}  value={samples[i]:.6f}")
            time.sleep(INTERVAL)
    finally:
        sock.close()
    print(f"done: {len(samples)} samples sent to {HOST}:{PORT}")


if __name__ == "__main__":
    main()
