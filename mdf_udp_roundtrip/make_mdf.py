import numpy as np
from asammdf import MDF, Signal

OUT = "signal.mdf"
CHANNEL = "EngineSpeed"
N = 50
DT = 0.01


def build_samples():
    t = np.arange(N) * DT
    values = 1500.0 + 500.0 * np.sin(2.0 * np.pi * t)
    return t, values


def main():
    t, values = build_samples()
    sig = Signal(samples=values, timestamps=t, name=CHANNEL, unit="rpm")
    with MDF(version="3.30") as mdf:
        mdf.append(sig, comment="synthetic EngineSpeed sine")
        mdf.save(OUT, overwrite=True)
    print(f"wrote {OUT}: {N} samples of {CHANNEL}")


if __name__ == "__main__":
    main()
