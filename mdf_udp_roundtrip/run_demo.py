import argparse
import threading
import time

import compare
import make_mdf
import plot
import udp_receiver
import udp_sender


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mdf", nargs="?",
                        help="existing MDF file to stream; omit to generate the demo file")
    parser.add_argument("channel", nargs="?", default=make_mdf.CHANNEL,
                        help="channel/signal name to track")
    parser.add_argument("--plot", action="store_true",
                        help="plot input vs received signal after comparing")
    args = parser.parse_args()

    if args.mdf is None:
        make_mdf.main()
        mdf_path, channel = make_mdf.OUT, make_mdf.CHANNEL
    else:
        mdf_path, channel = args.mdf, args.channel
    print(f"streaming '{channel}' from {mdf_path}")
    print()

    box = {}
    receiver = threading.Thread(target=lambda: box.update(rows=udp_receiver.receive()))
    receiver.start()
    time.sleep(0.3)

    udp_sender.main(mdf_path, channel)
    receiver.join()
    udp_receiver.write_csv(box["rows"])

    print()
    compare.main(mdf_path, channel)

    if args.plot:
        plot.main(mdf_path, channel)


if __name__ == "__main__":
    main()
