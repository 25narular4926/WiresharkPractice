import csv

from asammdf import MDF

MDF_PATH = "signal.mdf"
CHANNEL = "EngineSpeed"
RECEIVED = "received.csv"


def load_input(mdf_path, channel):
    with MDF(mdf_path) as mdf:
        sig = mdf.get(channel)
    return [(float(t), float(v)) for t, v in zip(sig.timestamps, sig.samples)]


def load_output():
    rows = []
    with open(RECEIVED, newline="") as f:
        for r in csv.DictReader(f):
            rows.append((int(r["index"]), float(r["timestamp"]), float(r["value"])))
    rows.sort()
    return [(t, v) for _, t, v in rows]


def main(mdf_path=MDF_PATH, channel=CHANNEL):
    inp = load_input(mdf_path, channel)
    out = load_output()

    print(f"input samples : {len(inp)}")
    print(f"output samples: {len(out)}")
    print()
    print(f"{'idx':>3}  {'in_value':>14}  {'out_value':>14}  {'match':>6}")

    mismatches = 0
    max_diff = 0.0
    for i in range(max(len(inp), len(out))):
        in_v = inp[i][1] if i < len(inp) else None
        out_v = out[i][1] if i < len(out) else None
        if in_v is None or out_v is None:
            match = False
        else:
            diff = abs(in_v - out_v)
            max_diff = max(max_diff, diff)
            match = in_v == out_v
        if not match:
            mismatches += 1
        in_s = f"{in_v:.6f}" if in_v is not None else "--"
        out_s = f"{out_v:.6f}" if out_v is not None else "--"
        print(f"{i:>3}  {in_s:>14}  {out_s:>14}  {'OK' if match else 'DIFF':>6}")

    print()
    print(f"max abs difference: {max_diff}")
    if len(inp) == len(out) and mismatches == 0:
        print("RESULT: PASS - signal reconstructed identically over UDP")
    else:
        print(f"RESULT: FAIL - {mismatches} mismatch(es)")


if __name__ == "__main__":
    main()
