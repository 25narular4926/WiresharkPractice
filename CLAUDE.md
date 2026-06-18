# Project: CAN log → UDP → Wireshark pipeline

This file is the standing context for the project. It is read automatically by
Claude Code at the start of each session. It was handed off from a planning
conversation in claude.ai.

## Goal

Build a learning pipeline, in phases, that ends with: read a recorded vehicle-bus
log file, parse it in Python, repackage each CAN frame as a UDP datagram, send it,
and inspect it in Wireshark.

## Background already established (so we don't relitigate)

- **UDP**: connectionless, fire-and-forget transport. No handshake, no delivery
  guarantee, tiny header. Chosen for low latency, broadcast, and loss-tolerance —
  the right fit for streaming/telemetry-style data.
- **Sockets in Python**: `socket(AF_INET, SOCK_DGRAM)` = IPv4 + UDP. Sender uses
  `sendto`, receiver `bind` + `recvfrom`.
- **Wireshark**: packet capture/analysis. Capture on the right interface, filter
  with e.g. `udp.port == 5005`, inspect bytes. Loopback capture needs the Npcap
  loopback adapter (Windows) or interface `lo` (Linux).
- **CAN bus**: broadcast, message-based (not address-based). Each frame has an
  arbitration ID (also = priority), DLC, and 0–8 data bytes (classic CAN) or up to
  64 (CAN FD). Payload is raw bytes and means nothing without a decoder.
- **DBC file**: the decoder ring — maps CAN ID + bit ranges to named signals,
  scaling, units, and byte order (Intel/little vs Motorola/big-endian).
- **Log formats**: BLF (Vector binary, compact), ASC (Vector ASCII, readable),
  MDF/MF4 (ASAM measurement format, often decoded signals), TRC (PEAK), CSV.
- **Python libs**: `python-can` (hardware + read/write logs), `cantools` (DBC
  decode/encode), `asammdf` (MDF/MF4), `canmatrix` (format conversion).

## Roadmap / phases

1. **[DONE] UDP + Wireshark warm-up** — `udp_sender.py` / `udp_receiver.py` work;
   loopback round-trip decodes all 10 packets, and a `dumpcap` capture on the
   loopback adapter (`capture_5005.pcapng`) confirms the frames byte-for-byte via
   `read_capture.py`. See WARMUP.md.
2. **[DONE] CAN concepts + DBC decoding** — `sample.dbc` (hand-authored, Intel +
   Motorola signals) loads with cantools; `dbc_decode.py` lists messages,
   round-trips encode/decode on a known frame, demonstrates the byte-order
   gotcha, and decodes via the Phase 1 `(can_id, data)` seam. See PHASE2.md.
3. **[DONE] Log parsing** — `make_sample_logs.py` writes a synthetic EngineData
   trace as BLF/ASC (raw frames) and MF4 (decoded signals); `read_logs.py`
   iterates them, decoding raw frames back to signals via the Phase 2 DBC and
   listing the MF4 channels. See PHASE3.md.
4. **[DONE] Bridge CAN → UDP → Wireshark** — `can_udp_bridge.py` reads a log,
   packs each frame's `(can_id, data)` into the Phase 1 `>I d I 8s` payload, and
   replays it to `127.0.0.1:5005`, paced by the log's own timestamps
   (`--speed`/`--interval`/`--loop`). Handles both Phase 3 flavors: BLF/ASC raw
   frames sent as-is, and MF4/MDF decoded signals re-encoded back into CAN bytes
   via the DBC (`--dbc`, the inverse of Phase 2). `read_capture.py --dbc` decodes
   a capture back to signals. Verified end-to-end: BLF *and* MF4 → UDP → dumpcap
   loopback capture (8/8, 0 dropped) → DBC decode reproduces the Phase 3 ramp;
   the MF4-re-encoded frames are byte-identical to `engine.blf`. See PHASE4.md.

**All four phases are complete — the project goal is met.** Possible
extensions: multiple CAN ids / a richer DBC, a live DBC-decoding receiver,
replaying a real recorded log, or carrying the log timestamp on the wire.

## Payload layout (current warm-up)

Network byte order (`>`): `I` counter, `d` unix timestamp, `I` CAN id, `8s` data.
24 bytes total. Keep sender/receiver formats in sync.

## Environment (already set up)

- **venv:** `.venv/` (Python 3.13). Activate with
  `.\.venv\Scripts\Activate.ps1` (PowerShell) or
  `source .venv/Scripts/activate` (Git Bash). `.venv/` is git-ignored.
- **Installed libs:** `python-can`, `cantools`, `asammdf` (+ `canmatrix`,
  `numpy`, `pandas`), and `scapy` (used by `read_capture.py` to decode pcapng).
  Pinned in `requirements.txt` — restore with `pip install -r requirements.txt`.
- **Wireshark:** this machine has Npcap + `dumpcap.exe` (the capture engine) but
  **not** the Wireshark GUI / `tshark`. Loopback adapter is present
  (`\Device\NPF_Loopback`). Capture via dumpcap CLI; to view in the GUI, install
  full Wireshark and open `capture_5005.pcapng`. Details in WARMUP.md.

## Files

- `udp_sender.py` / `udp_receiver.py` — Phase 1 warm-up (stdlib only).
- `read_capture.py` — decode a `.pcapng` and unpack the payload (scapy); with
  `--dbc` also DBC-decodes each frame back to signals (used by Phase 4).
- `capture_5005.pcapng` — verified loopback capture of the warm-up traffic.
- `WARMUP.md` — Phase 1 run/capture instructions and status.
- `sample.dbc` — Phase 2 hand-authored DBC (Intel + Motorola signals).
- `dbc_decode.py` — Phase 2 DBC load / decode / encode demo (cantools).
- `PHASE2.md` — Phase 2 run instructions, decoded sample, and status.
- `make_sample_logs.py` — Phase 3 log generator (BLF/ASC raw + MF4 signals).
- `read_logs.py` — Phase 3 log reader (python-can frames + asammdf channels).
- `engine.blf` / `engine.asc` / `engine_signals.mf4` — generated sample logs.
- `PHASE3.md` — Phase 3 run instructions and status.
- `can_udp_bridge.py` — Phase 4 bridge: replay a CAN log over UDP for Wireshark.
- `phase4_capture.pcapng` — verified loopback capture of a Phase 4 replay.
- `PHASE4.md` — Phase 4 run/capture instructions and status.
- `GUIDE.md` — full walkthrough: every script explained + step-by-step run.

## Conventions

- Python 3, standard library only for the warm-up; later-phase libs go in the
  venv via `pip` (and into `requirements.txt`).
- Default UDP port: 5005.
- Keep scripts small and commented; this is a learning project.