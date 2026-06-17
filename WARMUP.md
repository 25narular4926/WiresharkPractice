# Phase 1 — UDP + Wireshark warm-up

Goal: prove the UDP send/receive loop works and see the packets in Wireshark
before any CAN/log machinery is added.

## Files

- `udp_sender.py` — sends 10 fixed-layout datagrams to `127.0.0.1:5005`.
- `udp_receiver.py` — binds `0.0.0.0:5005`, unpacks and prints each datagram.

## Payload layout

Network byte order, **24 bytes**, struct format `>I d I 8s`:

| field     | type | meaning                              |
|-----------|------|--------------------------------------|
| counter   | `I`  | per-packet counter (uint32)          |
| timestamp | `d`  | `time.time()` (float64)              |
| can_id    | `I`  | placeholder CAN id (used in phase 4) |
| data      | `8s` | 8 raw bytes (classic CAN max)        |

Sender and receiver must keep this format in sync.

## Run it (two terminals)

Activate the venv first:

```powershell
# PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# Git Bash
source .venv/Scripts/activate
```

Terminal 1 (receiver):

```bash
python udp_receiver.py
```

Terminal 2 (sender):

```bash
python udp_sender.py
```

You should see all 10 packets printed by the receiver.

## Capture in Wireshark (GUI)

1. Windows loopback (127.0.0.1) is **not** captured by default — install the
   **Npcap loopback adapter** (Npcap installer option) and capture on the
   `Adapter for loopback traffic capture` interface. On Linux, capture on `lo`.
2. Apply the display filter: `udp.port == 5005`.
3. Start the capture, run `udp_sender.py`, and watch 10 UDP packets appear.
4. Click a packet → expand **Data** to see the 24 payload bytes; the first
   4 bytes are the counter, the next 8 the timestamp double, then 4 bytes of
   CAN id, then the 8 data bytes.
5. Or open the saved `capture_5005.pcapng` (below) via **File → Open**.

## Capture without the GUI (dumpcap CLI)

This machine currently has Npcap + `dumpcap.exe` but not the Wireshark GUI /
`tshark`. `dumpcap` is Wireshark's own capture engine, so the file it produces
is a real Wireshark `.pcapng`. To reproduce the verified capture:

```bash
DUMPCAP="/c/Program Files/Wireshark/dumpcap.exe"
# capture UDP/5005 on the loopback adapter, auto-stop after 9s
"$DUMPCAP" -i "\\Device\\NPF_Loopback" -f "udp port 5005" \
          -w capture_5005.pcapng -a duration:9 &
sleep 1.5
python udp_sender.py        # generate the traffic
wait                        # dumpcap stops itself at the duration
```

Decode/inspect the result (mirrors Wireshark's packet detail pane):

```bash
python read_capture.py capture_5005.pcapng   # uses scapy
```

## Status

- Sender → receiver loopback round-trip: decodes all 10 packets. ✓
- `dumpcap` capture on the loopback adapter: 10/10 packets, 0 dropped, saved to
  `capture_5005.pcapng`; `read_capture.py` confirms each frame's counter / CAN
  id / data byte-for-byte match what the sender emitted. ✓

Next: Phase 2 — load a DBC with `cantools` and decode sample CAN frames.
