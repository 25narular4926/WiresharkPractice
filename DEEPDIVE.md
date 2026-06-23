# Deep Dive — how the CAN → UDP → Wireshark pipeline actually works

`GUIDE.md` tells you **what each script does and how to run it**. This document
is the layer underneath: **why the pieces are shaped the way they are, and what
is really happening at the byte and protocol level** as a CAN frame travels from
a log file, across a UDP socket, onto the wire, into a capture file, and back
out as named signals.

Read `GUIDE.md` first if you just want to run things. Read this when you want to
*understand* things — for an interview, a writeup, or to extend the project into
something closer to a real telemetry system.

Table of contents:

1. [The one big idea: protocol layers and a payload-in-a-payload](#1-the-one-big-idea)
2. [CAN, from the wire up](#2-can-from-the-wire-up)
3. [From raw bytes to engineering values: the DBC and bit math](#3-from-raw-bytes-to-engineering-values)
4. [Endianness, in painful detail](#4-endianness-in-painful-detail)
5. [`struct` and the 24-byte payload: serialization](#5-struct-and-the-24-byte-payload)
6. [UDP and sockets: what "fire-and-forget" really means](#6-udp-and-sockets)
7. [What is actually on the wire: encapsulation](#7-what-is-actually-on-the-wire)
8. [Packet capture: how dumpcap/Wireshark see your loopback traffic](#8-packet-capture)
9. [Pacing and replay: making a log behave like a live bus](#9-pacing-and-replay)
10. [The three seams as an architecture](#10-the-three-seams-as-an-architecture)
11. [Where this is a simplification of reality](#11-where-this-is-a-simplification)
12. [Glossary](#12-glossary)

---

## 1. The one big idea

Almost everything in networking is **layering**: each layer wraps the layer
above it in a header (and sometimes a trailer) it knows how to read, and treats
everything inside as an opaque "payload" it doesn't look at. The classic model:

```
  Application   <- your data has meaning here (a CAN frame, an HTTP request)
  Transport     <- UDP / TCP: ports, (for TCP) reliability
  Network       <- IP: addresses, routing between machines
  Link          <- Ethernet / loopback:framing on one physical hop
```

When `udp_sender.py` calls `sock.sendto(payload, (HOST, PORT))`, the operating
system wraps your 24 bytes in a UDP header, wraps *that* in an IP header, wraps
*that* in a link-layer header, and hands it to the interface. Each header is
added by a different layer and is meaningful only to its peer on the other side.
**Your 24 bytes are the innermost matryoshka doll.** (Section 7 shows the actual
nesting for a loopback packet.)

This project adds **one more layer of its own, inside the application payload**:

```
  CAN frame  =  can_id + data       <- the thing we actually care about
       packed into
  Our 24-byte record  ">I d I 8s"   <- our application-level "protocol"
       carried by
  UDP / IP / loopback               <- the OS network stack
```

So we are tunneling a CAN frame inside a homemade binary record inside UDP. That
"homemade binary record" is a real, if tiny, **application-layer protocol** — it
has a fixed format both ends must agree on, exactly like a real protocol header.
The single most important consequence: **every program that touches the wire must
use the identical format string `">I d I 8s"`.** A protocol is only a protocol if
both ends parse the bytes the same way. That one line is duplicated, deliberately,
in `udp_sender.py`, `udp_receiver.py`, `read_capture.py`, and `can_udp_bridge.py`.

---

## 2. CAN, from the wire up

You never touch real CAN hardware in this project, but understanding the frame
is what makes the DBC and the `(can_id, data)` seam make sense.

**CAN is a broadcast, message-based bus, not an addressed network.** There is no
"send to node 5." Every node hears every frame; each frame is labeled by an
**arbitration ID**, and nodes individually decide which IDs they care about
(*message filtering*). Contrast this with IP, where packets carry a source and
destination address. On CAN the ID identifies *the kind of message* ("this is
EngineData"), not *who it's for*.

**A classic CAN data frame, on the wire, is roughly:**

```
 SOF | Arbitration ID (11 or 29 bits) | RTR | control (incl. DLC) |
     | 0–8 data bytes | CRC | ACK | EOF
```

The parts that matter for us:

- **Arbitration ID** — 11 bits (standard) or 29 bits (extended). It is *also the
  priority*: when two nodes start transmitting at once, the bus performs
  **bitwise arbitration** — a dominant `0` bit wins over a recessive `1`, so the
  numerically lower ID wins the bus and the loser backs off and retries. This is
  why ID = priority. Our message is ID `256` = `0x100`.
- **DLC (Data Length Code)** — how many data bytes follow, 0–8 for classic CAN
  (up to 64 for CAN FD). Our `EngineData` is 8 bytes.
- **Data** — 0–8 raw bytes. **This is the only part with the actual signal
  values, and on its own it means nothing.** `80 0c 64 00 01 45 00 00` is just
  eight bytes until a DBC tells you how to slice them.

Everything else (SOF, CRC, ACK, EOF, stuffing bits) is the bus's own framing and
integrity machinery and is handled by the CAN controller hardware. By the time
`python-can` hands you a `can.Message`, all of that is stripped and you are left
with exactly the two things this project cares about:

```python
msg.arbitration_id   # 256
msg.data             # b'\x80\x0c\x64\x00\x01\x45\x00\x00'
```

That pair — **`(can_id, data)`** — is the atom of this whole project. Phase 3
produces it from a log, Phase 1's payload reserves a slot for it (`I` + `8s`),
and Phase 2 consumes it with a DBC. Hold onto that pair and everything else is
plumbing.

---

## 3. From raw bytes to engineering values

A **DBC** ("CAN database") is the decoder ring. It says: for message ID 256,
here is where each named signal lives inside the 8 data bytes, how to scale it,
and which byte order to read it in. Here is our actual `sample.dbc` message:

```
BO_ 256 EngineData: 8 ECU
 SG_ GearRatio   : 39|16@0+ (0.01,0) [0|655.35]    ""      Dashboard
 SG_ ThrottlePos : 24|8@1+  (0.4,0)  [0|102]       "%"     Dashboard
 SG_ CoolantTemp : 16|8@1+  (1,-40)  [-40|215]     "degC"  Dashboard
 SG_ EngineSpeed : 0|16@1+  (0.25,0) [0|16383.75]  "rpm"   Dashboard
```

Decoding one signal definition `name : start|length@order±  (scale,offset) [min|max] "unit"`:

- **`start`** — the bit position where the signal begins.
- **`length`** — how many bits it occupies.
- **`@order`** — `@1` = Intel / little-endian, `@0` = Motorola / big-endian
  (section 4).
- **`±`** — `+` unsigned, `-` signed.
- **`(scale, offset)`** — the linear transform: `physical = raw * scale + offset`.
- **`[min|max]`** and **`"unit"`** — documentation/validation only.

### Walking a real frame: `data = 80 0c 64 00 01 45 00 00`

Byte indices: `[0]=80 [1]=0c [2]=64 [3]=00 [4]=01 [5]=45 [6]=00 [7]=00`.

**EngineSpeed** — `0|16@1+ (0.25,0)`, Intel, bytes 0–1:
- Little-endian 16-bit from bytes `80 0c` → `0x0C80` = `3200`.
- `3200 * 0.25 + 0` = **800 rpm**.

**CoolantTemp** — `16|8@1+ (1,-40)`, byte 2:
- Raw `0x64` = `100`.
- `100 * 1 + (-40)` = **60 °C**. (The −40 offset is a real automotive convention
  so that a single unsigned byte can express temperatures below freezing.)

**ThrottlePos** — `24|8@1+ (0.4,0)`, byte 3:
- Raw `0x00` = `0`. `0 * 0.4` = **0 %**.

**GearRatio** — `39|16@0+ (0.01,0)`, Motorola, bytes 4–5:
- Big-endian 16-bit from bytes `01 45` → `0x0145` = `325`.
- `325 * 0.01` = **3.25**.

So `800c640001450000` decodes to
`EngineSpeed=800, CoolantTemp=60, ThrottlePos=0, GearRatio=3.25` — exactly the
first row of the ramp `make_sample_logs.py` generated, and exactly what
`read_capture.py --dbc` prints back out at the far end of the pipeline. The fact
that the value survives the round trip *unchanged* is the whole proof that the
pipeline is lossless.

`cantools` does this math for you in both directions:

```python
db.decode_message(256, b'\x80\x0c\x64\x00\x01\x45\x00\x00')
# -> {'EngineSpeed': 800.0, 'CoolantTemp': 60, 'ThrottlePos': 0.0, 'GearRatio': 3.25}

msg.encode({'EngineSpeed': 800, 'CoolantTemp': 60, 'ThrottlePos': 0, 'GearRatio': 3.25})
# -> b'\x80\x0c\x64\x00\x01\x45\x00\x00'
```

**Encode is the exact inverse of decode**, and that inverse is what makes the MF4
path in Phase 4 possible: an MF4 stores *decoded* values, so to put them back on
a wire as CAN frames `can_udp_bridge.py` calls `msg.encode(...)` to rebuild the
bytes (`iter_frames_from_mdf`). The re-encoded bytes come out byte-identical to
the BLF the same values were generated from — that's the proof encode/decode are
true inverses here.

---

## 4. Endianness, in painful detail

This is the single most common source of "the value is garbage" bugs in CAN
work, so it gets its own section. `sample.dbc` includes both byte orders *on
purpose* to make the difference visible.

**Endianness is the order in which a multi-byte number is laid out in memory.**
Take the number `0x0145` (decimal 325), which needs two bytes:

- **Big-endian (Motorola, `@0`)**: most-significant byte first → `01 45`.
- **Little-endian (Intel, `@1`)**: least-significant byte first → `45 01`.

Same number, opposite byte order. If you read bytes that were written
big-endian as if they were little-endian, you get `0x4501` = `17665`, not `325`
— total garbage, but no error is raised, which is what makes it nasty.

In our frame:
- **GearRatio** is Motorola, stored in bytes 4–5 as `01 45`. Read MSB-first →
  `0x0145` = 325 → ×0.01 = 3.25. ✓
- **EngineSpeed** is Intel, stored in bytes 0–1 as `80 0c`. Read LSB-first →
  the real value is `0x0C80` = 3200 (the `0c` is the high byte even though it
  comes second). ×0.25 = 800. ✓

Notice EngineSpeed's bytes `80 0c` would be `0x800C` = 32780 if you read them
big-endian — wrong by a factor of ten. **The DBC's `@0`/`@1` flag is the only
thing that tells the decoder which interpretation is correct**, and there is no
way to guess it from the bytes alone. This is why "the payload means nothing
without a decoder" is literally true: even the *numeric* value of a field is
ambiguous until the DBC resolves the byte order.

There are *two different* uses of byte order in this project, and it's worth
keeping them separate in your head:

1. **CAN signal byte order** (above) — Intel vs Motorola, decided per-signal by
   the DBC, internal to the 8 data bytes.
2. **Network byte order** (section 5) — the `>` in `">I d I 8s"`, which is
   big-endian and governs how *our wrapper fields* (`counter`, `timestamp`,
   `can_id`) are laid out on the wire. This is a convention of the Internet
   protocols, unrelated to what the CAN signals inside `8s` do.

The `8s` field is just opaque bytes to `struct`, so the CAN signal endianness
inside it is untouched by the network byte order of the wrapper. Two independent
byte-order systems, nested, neither aware of the other.

---

## 5. `struct` and the 24-byte payload

A socket sends **bytes**, not Python objects. `struct` is the bridge:
**serialization** turns numbers into a fixed byte layout, **deserialization**
turns them back. The format string `">I d I 8s"` is the contract.

| token | meaning | size | our field |
|-------|---------|------|-----------|
| `>`   | big-endian (network byte order) | — | applies to all fields |
| `I`   | unsigned 32-bit int | 4 | `counter` |
| `d`   | IEEE-754 double | 8 | `timestamp` (`time.time()`) |
| `I`   | unsigned 32-bit int | 4 | `can_id` |
| `8s`  | 8 raw bytes | 8 | `data` (the CAN payload) |

Total: **24 bytes**, fixed, every packet. A few subtleties worth knowing:

- **Why `>` (big-endian) for the wrapper?** Because it's the **network byte
  order** convention. It's somewhat arbitrary which you pick, but picking the
  network standard means the bytes on the wire read "naturally" in Wireshark and
  match how every other Internet protocol orders its header fields. The
  alternative `<` would work too as long as both ends agreed — but agreement is
  the whole point, and `>` is the agreement everyone else already uses.
- **Why a fixed layout instead of, say, JSON?** Three reasons that all matter for
  telemetry: it's **compact** (24 bytes vs. a much larger text blob), it's
  **fast** (no parsing, just a memory copy in/out of the struct), and it's
  **self-synchronizing by length** — the receiver checks `len(payload) == 24`
  and knows instantly whether it got a well-formed record. Real CAN-over-UDP
  protocols (e.g. parts of the SAE/AUTOSAR world) use fixed binary layouts for
  exactly these reasons.
- **What `8s` does to short or long data.** `struct.pack` with `8s`
  **right-pads with null bytes** if you give it fewer than 8, and **truncates**
  if you give it more. So a 3-byte CAN payload becomes `xx yy zz 00 00 00 00 00`
  on the wire. The receiver can't tell padding from real zero bytes — which is
  fine here because every `EngineData` frame is exactly 8 bytes, but in a real
  system you'd add a DLC field to record the true length. (This is one of the
  documented simplifications; see section 11.)
- **`struct.unpack` is strict about length.** Hand it the wrong number of bytes
  and it raises, rather than silently misreading — which is why `udp_receiver.py`
  guards on length before unpacking. That strictness is a feature: it turns a
  malformed packet into an obvious error instead of garbage signals.

So `struct.pack(">I d I 8s", 0, 1718000000.0, 256, b'\x80\x0c\x64\x00\x01\x45\x00\x00')`
produces a 24-byte string whose first 4 bytes are the counter (big-endian), next
8 are the timestamp, next 4 are `00 00 01 00` (256 big-endian), and last 8 are
the CAN data verbatim. That is the exact byte string that hits the socket.

---

## 6. UDP and sockets

### Why UDP and not TCP

TCP gives you a reliable, ordered, connection-oriented byte *stream*: handshake,
acknowledgements, retransmission, flow control, congestion control. UDP gives you
**datagrams**: discrete messages, no handshake, no acks, no ordering, no
retransmit. Each `sendto` either gets dropped into the network or doesn't; you're
never told which.

For **telemetry / streaming sensor data**, UDP is usually the right call:

- **Low latency** — no handshake round-trip before the first byte, no
  head-of-line blocking waiting for a lost packet to be resent.
- **Loss-tolerant** — if you're streaming engine RPM 100×/second, a dropped
  sample is irrelevant; the next one is already on its way. Re-sending stale
  telemetry is worse than dropping it.
- **One-to-many** — UDP supports broadcast/multicast naturally, which fits CAN's
  own broadcast nature.
- **Tiny header** — 8 bytes (src port, dst port, length, checksum) vs. TCP's 20+.

The trade is that *you* own anything you need beyond best-effort delivery
(sequencing, gap detection — which is exactly what our `counter` field is for: it
lets a receiver notice a missing packet even though UDP won't tell it).

### The socket API as a tiny state machine

A socket is an OS-managed endpoint. The two sides use different calls:

**Sender** (`udp_sender.py`, `can_udp_bridge.py`):
```python
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # IPv4 + UDP
sock.sendto(payload, (HOST, PORT))                        # fire-and-forget
```
- `AF_INET` = IPv4 address family; `SOCK_DGRAM` = datagram (UDP) semantics.
  (`SOCK_STREAM` would be TCP.) The OS picks an ephemeral source port for you.
- `sendto` names the destination on **every** call, because UDP is connectionless
  — there's no stored peer. The call returns as soon as the OS accepts the bytes;
  it does **not** wait for, or learn about, delivery.

**Receiver** (`udp_receiver.py`):
```python
sock.bind(("0.0.0.0", 5005))      # claim port 5005 on all interfaces
data, addr = sock.recvfrom(1024)  # block until a datagram arrives
```
- `bind` reserves the well-known port so arriving packets are routed to this
  socket. The sender doesn't bind because it doesn't care what source port it
  uses.
- `recvfrom` **blocks** until a whole datagram is available, then returns the
  payload *and the sender's address*. UDP preserves message boundaries: one
  `sendto` of 24 bytes is received as exactly one 24-byte `recvfrom`, never split
  or merged (unlike a TCP stream, where you must frame messages yourself).

### The ordering gotcha

Because UDP doesn't queue for a listener that isn't up yet, **the receiver must
be running before the sender**. Start the sender first and its early datagrams
hit a port with nothing bound — they're silently dropped, no error on either
side. This is the single most common "it printed nothing" confusion, and it's a
direct consequence of fire-and-forget: there is no connection to establish, so
there's nothing to tell the sender the receiver isn't there.

---

## 7. What is actually on the wire

When you send 24 bytes to `127.0.0.1:5005`, here's the full set of dolls the OS
builds around them for a loopback packet:

```
┌─────────────────────────────────────────────────────────────────────┐
│ Loopback / link-layer header  (interface-specific, a few bytes)      │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ IPv4 header  (20 bytes: src 127.0.0.1, dst 127.0.0.1, proto=UDP) │ │
│ │ ┌─────────────────────────────────────────────────────────────┐ │ │
│ │ │ UDP header  (8 bytes: src port, dst port=5005, len, checksum)│ │ │
│ │ │ ┌─────────────────────────────────────────────────────────┐ │ │ │
│ │ │ │ OUR 24-BYTE PAYLOAD   ">I d I 8s"                        │ │ │ │
│ │ │ │   counter(4) timestamp(8) can_id(4) data(8)             │ │ │ │
│ │ │ └─────────────────────────────────────────────────────────┘ │ │ │
│ │ └─────────────────────────────────────────────────────────────┘ │ │
│ └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

- **You wrote only the innermost box.** The UDP, IP, and link headers are added
  by the kernel's network stack on the way out and stripped on the way in. This
  is layering (section 1) made concrete.
- **The UDP header's destination port `5005`** is what lets the OS deliver the
  datagram to the socket that `bind`ed to 5005 — and it's what your Wireshark
  filter `udp.port == 5005` matches on.
- **MTU / fragmentation.** A link has a maximum frame size (the MTU, ~1500 bytes
  on Ethernet). At 24 bytes we're nowhere near it, so each record is exactly one
  packet. If a UDP payload exceeds the path MTU, IP fragments it and a single
  lost fragment loses the whole datagram — another reason telemetry favors small,
  fixed records like ours.
- **Loopback is real networking, minus the wire.** `127.0.0.1` never leaves the
  machine; the kernel short-circuits it through a virtual loopback interface. The
  full IP/UDP headers are still constructed (which is why capture and decoding
  work identically to a real network), but no NIC or cable is involved. That's
  exactly why this whole project can be developed and verified on one laptop.

---

## 8. Packet capture

### How capture works at all

Normally an application only sees traffic addressed to its own sockets. A
**packet capture** library taps in lower down, at the link layer, and copies
*every* frame the interface sees before the normal stack processing — this is
what Wireshark, `tshark`, and `dumpcap` are built on.

- On Linux/macOS this is **libpcap**; on Windows it's **Npcap** (the modern
  WinPcap successor). `dumpcap.exe` is Wireshark's lightweight capture engine
  that talks to Npcap. This machine has dumpcap + Npcap but **not** the Wireshark
  GUI, which is why the project decodes captures with `read_capture.py` (scapy)
  instead of opening them in the GUI.
- **Capturing loopback needs a special shim.** Loopback traffic never hits a real
  adapter, so by default it's invisible to a capture. Npcap's "support loopback
  traffic capture" option installs a virtual adapter, `\Device\NPF_Loopback`, that
  exposes `127.0.0.1` traffic to libpcap-style capture. On Linux you capture the
  `lo` interface; on macOS `lo0`. Picking the wrong interface here is the usual
  cause of "0 packets captured."
- A **capture filter** like `-f "udp port 5005"` is compiled to BPF (Berkeley
  Packet Filter) and applied *in the capture path*, so non-matching packets are
  dropped before they're ever written — cheaper than a display filter, which runs
  after capture.

### The `.pcapng` file

`dumpcap` writes **pcapng** (PCAP Next Generation): a block-structured binary
format holding interface metadata plus one record per captured packet, each with
a timestamp and the raw bytes of the *entire* frame (link + IP + UDP + payload).
It is a faithful recording of what was on the interface — which is why
`read_capture.py` can reconstruct everything.

### How `read_capture.py` reverses the stack

```python
for pkt in rdpcap(path):          # scapy parses every layer of every packet
    if UDP in pkt:
        payload = bytes(pkt[UDP].payload)   # peel back to our 24 bytes
        if len(payload) == 24:
            counter, ts, can_id, data = struct.unpack(">I d I 8s", payload)
```

scapy **dissects** the same nested headers from section 7 — it knows the link,
IP, and UDP layouts — and `pkt[UDP].payload` hands you exactly the 24 bytes you
originally packed. From there it's the same `struct.unpack` the receiver uses,
and with `--dbc` the same `db.decode_message` from Phase 2. **The capture path
and the live-receive path converge on the identical unpack+decode**, which is
why both reproduce the original ramp.

---

## 9. Pacing and replay

A subtle but important detail: a CAN log isn't just a list of frames, it's a list
of frames *with timestamps*. Replaying them back-to-back as fast as possible
would not resemble the original bus. `can_udp_bridge.py`'s `replay()` therefore
**paces** the output:

- **Default (real-time):** before each frame it sleeps
  `(this_timestamp - prev_timestamp)`, reproducing the exact spacing the frames
  were recorded with. This is what a hardware CAN replay tool does when it feeds a
  bus.
- **`--speed N`:** divides that gap by N — a 10-second log replays in 1 second at
  `--speed 10`, with all relative spacing preserved.
- **`--interval S`:** ignores log timing entirely and uses a fixed S-second gap —
  useful for a slow, readable demo.
- **`--loop K`:** repeats the whole sequence K times, with the `counter` field
  continuing to climb across laps so each packet is still uniquely numbered.

One design decision worth calling out: **the `timestamp` field on the wire is the
*send* time (`time.time()`), not the log's recorded time.** This keeps the wire
format byte-for-byte identical to the Phase 1 warm-up, so `udp_receiver.py` and
`read_capture.py` need zero changes. The log's original timestamp is printed to
the bridge's stdout as `log_t=` for reference, but it doesn't ride on the wire.
Carrying the original timestamp instead would be a clean extension (section 11) —
it just means redefining the `d` field's meaning and updating every end that
reads it, which is precisely the kind of change a fixed-format protocol forces
you to coordinate.

The two log flavors converge here too:

- **BLF/ASC** are raw frames → `iter_frames()` yields `(ts, can_id, data)`
  straight from `can.LogReader`. No DBC involved.
- **MF4** holds decoded signals → `iter_frames_from_mdf()` re-encodes each
  timestamped row of values back into CAN bytes via the DBC, recovering the
  `can_id` (which an MF4 doesn't store) from the DBC message, then sorts by
  timestamp. Both paths end as the same `(ts, can_id, data)` tuples feeding the
  same `replay()`.

---

## 10. The three seams as an architecture

The whole project is held together by three deliberately narrow interfaces. The
value of naming them is that each phase could be built and tested in isolation,
and Phase 4 is almost trivial because it just connects three things that already
worked.

```
  LOG FILE                         UDP PACKET                      SIGNALS
  (engine.blf)                     (24 bytes on :5005)             (rpm, °C, %)

  ┌───────────┐   Phase 3 seam     ┌──────────────┐  Phase 1 seam  ┌──────────┐
  │ raw CAN   │ ───────────────▶   │ can_id +     │ ────────────▶  │ DBC      │
  │ frames    │  (can_id, data)    │ data packed  │  ">I d I 8s"   │ decode   │
  └───────────┘                    └──────────────┘                └──────────┘
   python-can                       socket/struct                   cantools
```

- **Phase 1 seam — the 24-byte payload layout (`">I d I 8s"`).** A fixed
  application-level record that reserves a slot for a CAN id + 8 data bytes. Both
  ends agree on it; that agreement *is* the protocol.
- **Phase 2 seam — `(can_id, data)` → DBC → signals.** The decoder ring that
  turns opaque bytes into named, scaled engineering values, and back.
- **Phase 3 seam — a log iterator yielding `(can_id, data)` per frame.** Whether
  the source is BLF, ASC, or a re-encoded MF4, it presents the same tuple.
- **Phase 4 — composition.** Read log (seam 3) → pack into UDP (seam 1) →
  capture → decode (seam 2). No new concepts, just wiring.

This is a small but honest example of **designing to interfaces**: each seam is a
contract narrow enough to reason about, and the phases on either side of a seam
don't need to know each other's internals. That's why swapping BLF for MF4, or
the live receiver for a capture file, requires no change on the other side of the
seam.

---

## 11. Where this is a simplification

Being explicit about what's idealized here is exactly the kind of thing that
makes a good writeup or interview answer — it shows you know the difference
between the teaching model and the real system.

- **One CAN id, one message.** Real buses carry dozens to hundreds of distinct
  IDs, often on multiple buses. Our DBC defines a single `EngineData` message.
  Extending to many IDs is mostly more DBC entries; the seams don't change.
- **Fixed 8-byte data, no DLC on the wire.** We always send 8 bytes and rely on
  every frame being exactly 8. A real bridge would carry the **DLC** so the
  receiver knows the true payload length, and would handle **CAN FD** (up to 64
  data bytes), which our `8s`/`>I d I 8s` layout can't express as-is.
- **Send-time timestamp, not record time.** As noted in section 9, the original
  log timestamp doesn't ride on the wire. Carrying it would mean redefining the
  `d` field and updating every reader in lockstep.
- **No sequencing/reliability beyond a counter.** We include a `counter` so a gap
  is *detectable*, but nothing acts on it — no retransmit, no reorder buffer.
  That's appropriate for loss-tolerant telemetry but wouldn't do for control.
- **Loopback only.** Everything runs on `127.0.0.1`. Across real machines you'd
  hit MTU, real packet loss, NAT/firewalls, and clock skew between sender and
  receiver — none of which loopback exercises.
- **Synthetic, monotonic data.** `make_sample_logs.py` generates a clean ramp.
  Real logs have noise, bursts, error frames, bus-off events, and messages whose
  signals you don't have a DBC for.

Natural next steps, in roughly increasing effort: add more IDs/signals to the
DBC; write a **live DBC-decoding receiver** (decode in `udp_receiver.py` instead
of after capture); **carry the DLC and the original timestamp** on the wire;
replay a **real recorded log** instead of the synthetic one; run sender and
receiver on **two machines** over a real network.

---

## 12. Glossary

- **Arbitration ID** — the label on a CAN frame; identifies the message type and
  doubles as its bus priority (lower wins arbitration).
- **BPF** — Berkeley Packet Filter; the kernel-level filtering used by capture
  tools (your `-f "udp port 5005"` compiles to this).
- **CAN** — Controller Area Network; a broadcast, message-based vehicle bus.
- **CAN FD** — Flexible Data-rate CAN; up to 64 data bytes per frame.
- **cantools** — Python library that loads a DBC and encodes/decodes frames.
- **Datagram** — a single, self-contained UDP message; preserves boundaries.
- **DBC** — CAN database file mapping IDs + bit ranges to named, scaled signals.
- **DLC** — Data Length Code; the number of data bytes in a CAN frame (0–8 classic).
- **dumpcap** — Wireshark's minimal capture engine; writes `.pcapng`.
- **Endianness** — byte order of a multi-byte value (big = MSB first, little = LSB first).
- **Ephemeral port** — the temporary source port the OS assigns an unbound sender.
- **Intel / Motorola** — DBC shorthand for little-endian (`@1`) / big-endian (`@0`) signals.
- **libpcap / Npcap** — the packet-capture libraries (Unix / Windows).
- **Loopback** — the virtual `127.0.0.1` interface; traffic never leaves the host.
- **MTU** — Maximum Transmission Unit; largest payload a link carries unfragmented.
- **Network byte order** — big-endian, the Internet convention; the `>` in our format.
- **pcapng** — the block-structured capture file format dumpcap writes.
- **python-can** — Python library for CAN hardware and log read/write.
- **Serialization** — turning in-memory values into a defined byte layout (`struct`).
- **Socket** — an OS endpoint for network I/O; `AF_INET`+`SOCK_DGRAM` = IPv4 UDP.
- **UDP** — User Datagram Protocol; connectionless, unreliable, low-overhead transport.
```
