# Full Walkthrough — CAN log → UDP → Wireshark

This document explains **every script in the project**, what each line is doing
and why, how to run it, and a single **step-by-step process** to run the whole
thing end-to-end and reproduce the results. It's meant to be read top to bottom
by someone new to the project.

If you only want to *set up* a machine, see the install steps in
[§2](#2-one-time-setup). If you want the short per-phase notes, see
`WARMUP.md`, `PHASE2.md`, `PHASE3.md`, `PHASE4.md`. This file is the long-form
version that ties them together.

---

## 1. What this project does

The end goal: **take a recorded vehicle-bus (CAN) log, parse it in Python,
repackage each CAN frame as a UDP network packet, send it, and inspect it in
Wireshark** — decoding the raw bytes back into named engineering values
(engine speed, coolant temp, …).

It was built in four phases, each one a small, runnable piece that the next
phase reuses. The whole thing is glued together by **three "seams":**

```
  LOG FILE                         UDP PACKET                      SIGNALS
  (engine.blf)                     (24 bytes on :5005)             (rpm, °C, %)

  ┌───────────┐   Phase 3 seam     ┌──────────────┐  Phase 1 seam  ┌──────────┐
  │ raw CAN   │ ───────────────▶   │ can_id +     │ ────────────▶  │ DBC      │
  │ frames    │  (can_id, data)    │ data packed  │  ">I d I 8s"   │ decode   │
  │           │                    │ into UDP     │                │ Phase 2  │
  └───────────┘                    └──────────────┘                └──────────┘
   python-can                       socket/struct                   cantools
```

- **Phase 1 seam** — a fixed 24-byte UDP payload layout (`>I d I 8s`) that
  reserves a slot for a CAN id + 8 data bytes.
- **Phase 2 seam** — `(can_id, data)` fed into a DBC turns raw bytes into
  named, scaled signals.
- **Phase 3 seam** — reading a log yields exactly `(can_id, data)` per frame.
- **Phase 4** — joins all three: read log → pack into UDP → capture → decode.

---

## 2. One-time setup

You need **Python 3.13** and (only for capture) **Wireshark + Npcap**.

```powershell
# from the project folder, Windows / PowerShell
python -m venv .venv                 # create the virtual environment
.\.venv\Scripts\Activate.ps1         # activate it (prompt shows (.venv))
pip install -r requirements.txt      # install python-can, cantools, asammdf, scapy, ...
```

```bash
# Linux / macOS equivalent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- **Activate the venv in every terminal** before running a script, or the
  imports (`can`, `cantools`, `scapy`) won't be found.
- **Wireshark/Npcap** is only needed for the *capture* steps. On Windows,
  install Wireshark and enable Npcap with **"Support loopback traffic
  capture"** so `127.0.0.1` is visible (interface `\Device\NPF_Loopback`). On
  Linux capture on `lo`, on macOS `lo0`.

The full machine-setup notes (what to copy, what to regenerate) are in CLAUDE.md
and the earlier conversation; this guide assumes the venv is ready.

---

## 3. The payload layout (read this once)

Every UDP packet in this project uses the **same 24-byte layout**, described by
the struct format string `">I d I 8s"`:

| part | format | bytes | meaning                                    |
|------|--------|-------|--------------------------------------------|
| `>`  | —      | —     | **network byte order** (big-endian)        |
| `I`  | uint32 | 4     | `counter` — increments once per packet     |
| `d`  | double | 8     | `timestamp` — `time.time()` when sent       |
| `I`  | uint32 | 4     | `can_id` — the CAN arbitration id          |
| `8s` | bytes  | 8     | `data` — the raw CAN payload (classic max) |

`struct.pack(fmt, ...)` turns Python values into these 24 bytes;
`struct.unpack(fmt, payload)` reverses it. **The format string must be byte-for-byte
identical in every script that touches the wire** — that's why the same
`PACKET_FORMAT = ">I d I 8s"` line appears in the sender, receiver, capture
reader, and bridge.

---

## 4. The DBC — `sample.dbc`

A **DBC** is the "decoder ring": it maps a CAN id + bit ranges to named signals
with scaling, offset, units, and byte order. Ours defines one message:

```
BO_ 256 EngineData: 8 ECU                       <- message id 256 (0x100), 8 bytes
 SG_ GearRatio   : 39|16@0+ (0.01,0) [..] ""       Motorola/big-endian
 SG_ ThrottlePos : 24|8@1+  (0.4,0)  [..] "%"       Intel/little-endian
 SG_ CoolantTemp : 16|8@1+  (1,-40)  [..] "degC"    Intel, with -40 offset
 SG_ EngineSpeed : 0|16@1+  (0.25,0) [..] "rpm"     Intel
```

Reading a signal definition `start|length@order±  (scale,offset)`:

- **`EngineSpeed : 0|16@1+`** — starts at bit 0, 16 bits long, `@1` = Intel
  (little-endian), `+` = unsigned. Raw value × `0.25` = rpm.
- **`GearRatio : 39|16@0+`** — `@0` = Motorola (big-endian). This is the
  deliberate **byte-order gotcha**: its two bytes are read most-significant
  first, opposite to the Intel signals.
- **`CoolantTemp`** has offset `-40`, so raw byte `0` means `-40 °C` (a common
  automotive encoding).

This is why `data=800c640001450000` decodes to EngineSpeed=800, CoolantTemp=60,
ThrottlePos=0, GearRatio=3.25 — see the byte-by-byte breakdown in PHASE4.md.

---

## 5. File-by-file reference

Grouped by phase. For each: **what it's for**, **how it works**, **how to run
it**, and **what you should see**.

### Phase 1 — UDP warm-up

#### `udp_sender.py` — send test datagrams

**Purpose:** prove the UDP send path and produce traffic to capture, before any
CAN machinery exists.

**How it works:**
- Opens a UDP socket: `socket.socket(AF_INET, SOCK_DGRAM)` (IPv4 + UDP).
- Loops 10 times. Each iteration builds a placeholder frame —
  `can_id = 0x100 + counter`, `data = bytes(range(counter, counter+8))` (8 demo
  bytes) — and `struct.pack`s it into the 24-byte payload.
- `sock.sendto(payload, (HOST, PORT))` fires it to `127.0.0.1:5005`
  (fire-and-forget; UDP has no handshake or ack). Sleeps 0.5 s between sends.

**Run:** `python udp_sender.py`

**See:** ten `sent #N -> 127.0.0.1:5005 id=0x... data=...` lines.

#### `udp_receiver.py` — receive and unpack

**Purpose:** the other end of the warm-up; confirms packets arrive and unpack.

**How it works:**
- `sock.bind(("0.0.0.0", 5005))` listens on all interfaces.
- Blocks on `sock.recvfrom(1024)`. For each datagram, checks the length matches
  the 24-byte format, then `struct.unpack`s it back into
  `counter, ts, can_id, data` and prints them. `Ctrl-C` to stop.

**Run (in its own terminal, started *before* the sender):**
`python udp_receiver.py`

**See:** one `#N from 127.0.0.1:... id=0x... data=...` line per packet received.

#### `read_capture.py` — decode a saved capture

**Purpose:** read a `.pcapng` capture file (what Wireshark/dumpcap saves) and
show each UDP frame **without needing the Wireshark GUI**. With `--dbc` it also
decodes the CAN payload back into signals.

**How it works:**
- `rdpcap(path)` (from scapy) loads all packets from the file.
- For each packet that has IP+UDP layers, it pulls the raw UDP payload, and if
  it's 24 bytes, `struct.unpack`s it with the same `>I d I 8s` format → prints
  source/dest, counter, timestamp, can_id, data.
- `--dbc [file]` (default `sample.dbc`): lazily imports cantools, loads the DBC,
  and calls `db.decode_message(can_id, data)` per frame to print the named
  signals. This is the Phase 2 seam applied to captured packets.

**Run:**
```bash
python read_capture.py capture_5005.pcapng           # just unpack
python read_capture.py phase4_capture.pcapng --dbc   # unpack + decode signals
```

**See:** one `[i] src -> dst | counter=.. can_id=.. data=..` line per packet,
plus a `-> EngineSpeed=.. CoolantTemp=..` line when `--dbc` is used.

### Phase 2 — DBC decoding

#### `dbc_decode.py` — load a DBC and decode/encode frames

**Purpose:** demonstrate turning raw CAN bytes into engineering values (and
back), and show the byte-order gotcha. Pure cantools.

**How it works (four demos in `main`):**
1. `list_messages` — loads the DBC and prints every message and signal
   (start bit, length, endianness, scale, offset, unit). A table of contents.
2. `decode_demo` — `msg.encode({...})` turns a dict of named values into bytes,
   then `msg.decode(bytes)` turns them back; asserts the round-trip matches.
3. `byte_order_demo` — encodes GearRatio (Motorola) and shows that reading its
   two bytes big-endian gives the right value while little-endian gives garbage.
4. `phase1_seam_demo` / `decode_udp_frame` — pretends a UDP packet arrived and
   decodes its `can_id` + `data` exactly as Phase 4 will.

**Run:** `python dbc_decode.py`

**See:** the message list, an encode→decode round-trip ("round-trip OK"), the
big-vs-little endian comparison, and a decoded sample frame.

### Phase 3 — log parsing

#### `make_sample_logs.py` — generate sample logs

**Purpose:** there's no real recorded vehicle log in the project (and we want to
avoid proprietary data), so this **synthesizes** one short trace and writes it
in three formats to parse later.

**How it works:**
- `build_trace` makes `N = 8` frames at `DT = 0.1 s`, ramping the engine:
  EngineSpeed 800→3600 rpm, CoolantTemp 60→88 °C, ThrottlePos 0→84 %, GearRatio
  constant 3.25. Each frame's signal dict is `msg.encode(...)`-ed into 8 bytes.
- `write_raw_logs` writes those raw frames to **`engine.blf`** (Vector binary)
  and **`engine.asc`** (Vector ASCII) via python-can's `BLFWriter`/`ASCWriter`.
- `write_mdf_signals` writes the *decoded* signals as time-series channels to
  **`engine_signals.mf4`** via asammdf — the flavor an MDF/MF4 often holds
  (named, scaled values rather than raw frames).

**Run:** `python make_sample_logs.py`

**See:** `wrote engine.blf (8 frames)`, `wrote engine.asc (8 frames)`,
`wrote engine_signals.mf4 (4 channels, 8 samples)`. **Run this before
`read_logs.py` or `can_udp_bridge.py`**, since they consume these files.

#### `read_logs.py` — read the logs back

**Purpose:** iterate recorded logs and recover the data — both flavors.

**How it works:**
- `read_raw_log` (BLF/ASC): `can.LogReader(path)` auto-picks the right reader by
  file suffix and yields `can.Message` objects (arbitration id + raw bytes +
  timestamp). Each frame's `(arbitration_id, data)` is fed to
  `db.decode_message(...)` to print the named signals. **This is the exact pair
  Phase 4 replays.**
- `read_mdf` (MF4): `asammdf.MDF(path)` already holds decoded channels, so it
  just lists each channel name, sample count, unit, and the first few samples.

**Run:**
```bash
python read_logs.py                 # all three logs
python read_logs.py engine.blf      # one raw log
python read_logs.py engine_signals.mf4
```

**See:** per-frame `t=.. id=0x100 data=..  -> EngineSpeed=.. CoolantTemp=..`
for the raw logs, and a channel listing for the MF4.

### Phase 4 — the bridge

#### `can_udp_bridge.py` — replay a CAN log over UDP

**Purpose:** the payoff — read a log and replay every frame as a UDP packet so
it can be captured/inspected in Wireshark. Joins all three seams. Handles
**both** Phase 3 log flavors.

**How it works:**
- For **BLF/ASC** (raw frames): `iter_frames(path)` wraps `can.LogReader` (the
  Phase 3 reader) and yields `(timestamp, arbitration_id, data)` per frame —
  no DBC needed, the frames are already raw bytes.
- For **MF4/MDF** (decoded signals): `iter_frames_from_mdf(path, db)` reads the
  named signal channels with asammdf and **re-encodes** them into CAN bytes via
  the DBC (`msg.encode(...)`, the inverse of Phase 2's decode) — because an MF4
  stores values, not frames, so the CAN id has to be recovered from the DBC.
  The re-encoded frames come out byte-identical to `engine.blf`. Needs `--dbc`
  (default `sample.dbc`); `main` picks this path by the `.mf4`/`.mdf` suffix.
- `replay(...)` opens a UDP socket and, for each frame, **paces then sends**:
  - default: sleeps the gap between consecutive log timestamps (so it replays at
    real speed); `--speed N` divides that gap (N× faster); `--interval S`
    ignores log timing and uses a fixed S-second gap.
  - packs `counter, time.time(), can_id, data` into the `>I d I 8s` payload
    (`8s` pads short payloads with nulls) and `sendto`s it. `--loop K` repeats
    the whole log K times.
- The `timestamp` field stays the **send time**, so the wire format is identical
  to the warm-up; the log's recorded time is shown as `log_t=` in stdout only.

**Run:**
```bash
python can_udp_bridge.py                      # replay engine.blf, real-time
python can_udp_bridge.py engine.asc           # the ASC log instead
python can_udp_bridge.py engine_signals.mf4   # MF4 signals -> re-encoded frames
python can_udp_bridge.py engine.blf --speed 5 # 5x faster
python can_udp_bridge.py engine.blf --interval 0.5   # fixed 0.5s gap
python can_udp_bridge.py engine.blf --loop 3  # replay 3 times
python can_udp_bridge.py --help               # all options
```

**See:** `sent #N log_t=.. id=0x100 data=.. -> 127.0.0.1:5005 (24 bytes)` per
frame, then `done: 8 frame(s) replayed`.

---

## 6. Step-by-step: run the whole pipeline and get the results

This reproduces the verified Phase 4 result: a recorded log replayed over UDP,
captured on the network, and decoded back into the original signals.

### Step 0 — activate the venv (every terminal)

```powershell
.\.venv\Scripts\Activate.ps1        # PowerShell  (or: source .venv/Scripts/activate in Git Bash)
```

### Step 1 — generate the sample log

```bash
python make_sample_logs.py
```
Creates `engine.blf`, `engine.asc`, `engine_signals.mf4`. (Skip if they already
exist and you haven't changed `sample.dbc`.)

### Step 2 — sanity-check the pieces (optional but recommended)

```bash
python dbc_decode.py        # DBC loads, round-trip OK
python read_logs.py         # 8 frames decode to the ramp; MF4 channels listed
```

### Step 3 — see the bridge live (no capture)

Two terminals, venv active in both:

```bash
# terminal 1 — receiver
python udp_receiver.py

# terminal 2 — bridge
python can_udp_bridge.py
```
The receiver prints 8 lines as the bridge sends them. `Ctrl-C` the receiver when
done. This proves the send/receive path without any Wireshark dependency.

### Step 4 — capture the replay (Windows, dumpcap)

This needs Wireshark/Npcap installed with loopback support. In **Git Bash**:

```bash
DUMPCAP="/c/Program Files/Wireshark/dumpcap.exe"
# start capturing UDP/5005 on the loopback adapter; auto-stop after 6s
"$DUMPCAP" -i "\\Device\\NPF_Loopback" -f "udp port 5005" \
          -w phase4_capture.pcapng -a duration:6 &
sleep 1.5                                   # let dumpcap come up
python can_udp_bridge.py engine.blf --speed 5   # generate the traffic
wait                                        # dumpcap stops itself at the duration
```
Expect `Packets captured: 8` and `8/0` (8 captured, 0 dropped).

> On Linux use `-i lo`; on macOS `-i lo0`. If you have the Wireshark GUI instead,
> start a capture on the loopback adapter with display filter `udp.port == 5005`,
> run the bridge, then stop and save as `phase4_capture.pcapng`.

### Step 5 — decode the capture back into signals

```bash
python read_capture.py phase4_capture.pcapng --dbc
```

**Expected result** — the capture decodes back to the exact ramp the log held:

```
phase4_capture.pcapng: 8 packet(s)

[0] 127.0.0.1:... -> 127.0.0.1:5005  len=24B | counter=0 ... can_id=0x100 data=800c640001450000
      -> EngineSpeed=800.0  CoolantTemp=60  ThrottlePos=0.0  GearRatio=3.25
...
[7] 127.0.0.1:... -> 127.0.0.1:5005  len=24B | counter=7 ... can_id=0x100 data=403880d201450000
      -> EngineSpeed=3600.0  CoolantTemp=88  ThrottlePos=84.0  GearRatio=3.25
```

That closes the loop: **log file → UDP packets → network capture → decoded
signals**, identical to what `make_sample_logs.py` originally wrote. Done.

### (Optional) Step 6 — inspect in the Wireshark GUI

Open `phase4_capture.pcapng` in Wireshark, filter `udp.port == 5005`, click a
packet, and expand **Data** to see the 24 payload bytes laid out as
counter (4) · timestamp (8) · CAN id (4) · data (8).

---

## 7. Quick reference

| File                  | Phase | Run it with                          | Produces / shows                  |
|-----------------------|-------|--------------------------------------|-----------------------------------|
| `udp_sender.py`       | 1     | `python udp_sender.py`               | 10 demo UDP packets               |
| `udp_receiver.py`     | 1     | `python udp_receiver.py`             | prints received packets           |
| `read_capture.py`     | 1/4   | `python read_capture.py f.pcapng --dbc` | decodes a capture (+ signals)  |
| `dbc_decode.py`       | 2     | `python dbc_decode.py`               | DBC list, encode/decode demo      |
| `sample.dbc`          | 2     | (data file)                          | the EngineData message + signals  |
| `make_sample_logs.py` | 3     | `python make_sample_logs.py`         | `engine.blf/.asc/_signals.mf4`    |
| `read_logs.py`        | 3     | `python read_logs.py`                | decoded frames + MF4 channels     |
| `can_udp_bridge.py`   | 4     | `python can_udp_bridge.py`           | replays a log over UDP            |

## 8. Troubleshooting

- **`ModuleNotFoundError: No module named 'can'` (or cantools/scapy)** — the
  venv isn't activated in this terminal. Run the activate command from §2.
- **Receiver prints nothing** — start `udp_receiver.py` *before* the sender/
  bridge; UDP doesn't queue for a listener that isn't up yet. Check both use
  port 5005 and no firewall blocks loopback.
- **dumpcap captures 0 packets** — you're on the wrong interface, or Npcap
  loopback support isn't installed. On Windows the loopback adapter is
  `\Device\NPF_Loopback`; on Linux/macOS use `lo`/`lo0`.
- **`pip install` complains about an invalid requirement** — your
  `requirements.txt` got the wrong contents during copying; recreate it from the
  pinned list (UTF-8, no BOM).
- **No `engine.blf`** — run `python make_sample_logs.py` first.
