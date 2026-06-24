# MDF signal → UDP → reconstruct (round-trip check)

A small, self-contained project: take a signal stored in an MDF file, read its
values back out, stream each sample as a UDP datagram over loopback every 2 ms,
receive them on the same machine, and prove the signal that comes out the other
end is **byte-for-byte the same** signal that went in.

It lives in its own folder and only depends on `asammdf` (already in the repo's
`.venv`) plus the Python standard library.

## What "the signal in `.values` format" means here

When you load an MDF channel with asammdf you get a `Signal` object. Its samples
are a NumPy array (`Signal.samples` — the `.values`-style array of numbers), with
a matching `Signal.timestamps` array. That array of numbers *is* the signal. The
whole point of this project is to move that exact array across a UDP socket and
get it back unchanged.

## The five files

| File | Role |
| --- | --- |
| `make_mdf.py` | Creates the input file `signal.mdf` — one channel, `EngineSpeed`, 50 samples of a sine wave (1500 ± 500 rpm) on a 10 ms timebase, saved as a real MDF v3.30 (`.mdf`). |
| `udp_sender.py` | Opens `signal.mdf`, pulls out `EngineSpeed`'s timestamps + samples, and sends one UDP packet per sample to `127.0.0.1:5005`, one every 2 ms. |
| `udp_receiver.py` | Binds `127.0.0.1:5005`, unpacks every datagram, and writes what it received to `received.csv`. |
| `compare.py` | Reads the original values straight from `signal.mdf` and the values from `received.csv`, lines them up by index, and prints a per-sample match table with a PASS/FAIL verdict. |
| `run_demo.py` | Runs all four in order in one process (receiver on a background thread) so you can confirm the whole round-trip with a single command. |

## The packet format

Every sample becomes a 20-byte datagram, packed in network byte order with
`struct` format `>I d d`:

| Field | Type | Meaning |
| --- | --- | --- |
| `I` | uint32 | sample index (0, 1, 2, …) |
| `d` | float64 | the sample's timestamp from the MDF |
| `d` | float64 | the sample value (rpm) |

The value is sent as a **float64 (IEEE-754 double)** — the same width asammdf
stores it in — so no precision is lost on the wire. The index travels with each
packet so the receiver can reorder them if UDP ever delivers them out of order
(UDP makes no ordering promise). The same `>I d d` format string is defined in
the sender, the receiver, and the comparator, so all three agree on the layout.

## How the round-trip works, step by step

1. **Write the signal.** `make_mdf.py` builds a NumPy array of 50 sine values,
   wraps it in an asammdf `Signal` (with timestamps), and saves `signal.mdf`.
   This is the "recorded measurement" we'll stream.
2. **Read the values back.** `udp_sender.py` opens the MDF and calls
   `mdf.get("EngineSpeed")`, which hands back the `Signal`. We take
   `sig.samples` and `sig.timestamps` — the raw arrays.
3. **Pack and send.** For each sample `i`, the sender packs `(i, timestamp,
   value)` into the 20-byte payload and `sendto`s it to `127.0.0.1:5005`, then
   `time.sleep(0.002)` so packets go out roughly every 2 ms.
4. **Receive and record.** `udp_receiver.py` `bind`s the same address and loops
   on `recvfrom`, unpacking each datagram back into `(index, timestamp, value)`
   and appending it to a list. After the last packet it sees a 0.5 s gap with
   nothing arriving, treats that as "the stream is done", and writes every row
   to `received.csv`.
5. **Compare input to output.** `compare.py` loads the original values directly
   from `signal.mdf` (not a copy — the real source) and the values from
   `received.csv`. It sorts the received rows by index, walks both lists
   together, and checks `in_value == out_value` for every sample. Because the
   values are float64 end to end, an exact `==` is the right test, and the
   maximum absolute difference comes out to `0.0`.

## Why the values come back exactly equal

Two things make the match exact rather than "close":

- **Same numeric type the whole way.** asammdf stores the samples as float64;
  the sender packs float64; the receiver unpacks float64. Nothing is rounded to
  a smaller type in between.
- **Lossless text in the CSV.** The receiver writes each number with Python's
  `repr()`, which produces the shortest decimal string that reads back as the
  *identical* float64. So saving to CSV and reading it again doesn't change a
  single bit.

If you sent the value as a 32-bit float, or wrote it to CSV with rounded
formatting, the comparison would show tiny differences instead of `0.0` — which
is exactly the kind of bug this round-trip check is designed to catch.

## Running it

From this folder, using the repo's virtual environment.

**One command (does everything and prints the verdict):**

```bash
../.venv/Scripts/python.exe run_demo.py
```

**Or each piece by hand, to watch the UDP traffic in two terminals:**

```bash
# once, to create the input file
../.venv/Scripts/python.exe make_mdf.py

# terminal 1 - start listening first (it waits for packets, then writes received.csv)
../.venv/Scripts/python.exe udp_receiver.py

# terminal 2 - stream the signal
../.venv/Scripts/python.exe udp_sender.py

# after the receiver finishes, compare input vs output
../.venv/Scripts/python.exe compare.py
```

A successful run ends with:

```
max abs difference: 0.0
RESULT: PASS - signal reconstructed identically over UDP
```

## Generated files

`signal.mdf` and `received.csv` are produced by the scripts and are
git-ignored — regenerate them anytime by running `make_mdf.py` (input) and the
sender/receiver (output). Only the source scripts are tracked.

## Watching it in Wireshark (optional)

The traffic is ordinary UDP on loopback port 5005, so the same capture method
the parent project uses applies: capture on the Npcap loopback adapter with the
display filter `udp.port == 5005` while `udp_sender.py` runs. Each packet is the
20-byte `>I d d` payload described above.
