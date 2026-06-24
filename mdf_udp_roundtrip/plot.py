import argparse

import matplotlib.pyplot as plt

import compare


def main(mdf_path=compare.MDF_PATH, channel=compare.CHANNEL):
    inp = compare.load_input(mdf_path, channel)
    out = compare.load_output()

    in_t = [t for t, _ in inp]
    in_v = [v for _, v in inp]
    out_t = [t for t, _ in out]
    out_v = [v for _, v in out]

    plt.figure()
    plt.plot(in_t, in_v, "-o", label="input (MDF)")
    plt.plot(out_t, out_v, "x", markersize=9, label="received (UDP)")
    plt.xlabel("time (s)")
    plt.ylabel(channel)
    plt.title(f"{channel}: MDF input vs UDP received")
    plt.legend()
    plt.grid(True)
    plt.show()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mdf", nargs="?", default=compare.MDF_PATH)
    parser.add_argument("channel", nargs="?", default=compare.CHANNEL)
    args = parser.parse_args()
    main(args.mdf, args.channel)
