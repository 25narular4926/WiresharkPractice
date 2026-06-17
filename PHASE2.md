# Phase 2 — CAN concepts + DBC decoding

Goal: turn raw CAN bytes into named, scaled engineering values using a DBC,
and prove the encode/decode round-trip. This is the "decoder ring" step that
Phase 4 will reuse to decode replayed frames.

## Files

- `sample.dbc` — hand-written DBC (no proprietary data). One message,
  four signals, deliberately mixing Intel and Motorola byte order.
- `dbc_decode.py` — loads the DBC with `cantools`, lists messages, then
  encodes/decodes a demo frame, shows the byte-order gotcha, and decodes via
  the Phase 1 UDP seam.

## The DBC: `EngineData` (id 0x100, 8 bytes)

| signal      | bits         | order        | scale | offset | unit | notes                         |
|-------------|--------------|--------------|-------|--------|------|-------------------------------|
| EngineSpeed | 0..15        | Intel (`@1`) | 0.25  | 0      | rpm  | classic little-endian 16-bit  |
| CoolantTemp | 16..23       | Intel (`@1`) | 1     | -40    | degC | raw 0 = -40 °C (offset)       |
| ThrottlePos | 24..31       | Intel (`@1`) | 0.4   | 0      | %    | 8-bit                         |
| GearRatio   | bytes 4..5   | Motorola(`@0`)| 0.01 | 0      | —    | big-endian; the gotcha        |

In DBC syntax `@1` = little-endian (Intel), `@0` = big-endian (Motorola), and
the trailing `+` = unsigned. A real `.dbc` can be dropped in and decoded the
same way; this tiny one just avoids proprietary data while learning.

> Authoring note: a hand-typed header kept failing to parse. The fix was the
> node line needs a colon — `BU_: ECU Dashboard`, not `BU_ ECU Dashboard` —
> plus the full standard `NS_`/`BS_` boilerplate. `sample.dbc` here was emitted
> by `cantools.database.dump_file(...)` so the format is guaranteed valid.

## Run it

```bash
# activate the venv first (see CLAUDE.md / WARMUP.md)
python dbc_decode.py            # uses sample.dbc
python dbc_decode.py other.dbc  # or point it at a real DBC
```

## Decoded sample (verified)

Encoding `{EngineSpeed: 2500 rpm, CoolantTemp: 90 degC, ThrottlePos: 40 %,
GearRatio: 3.25}` produces the 8 bytes **`1027 82 64 0145 0000`**:

- `10 27` — 2500 / 0.25 = 10000 = 0x2710, little-endian in bytes 0–1.
- `82`    — 90 − (−40) = 130 = 0x82, byte 2.
- `64`    — 40 / 0.4 = 100 = 0x64, byte 3.
- `01 45` — 3.25 / 0.01 = 325 = 0x0145, **big-endian** in bytes 4–5.

`msg.decode(...)` recovers all four values exactly, and
`encode(decode(x)) == x` (round-trip OK).

### Byte-order gotcha

`GearRatio`'s two bytes are `01 45`. Read big-endian (correct) that's
0x0145 = 325 → **3.25**. Read little-endian by mistake it's 0x4501 = 17665 →
**176.65** — completely wrong. Endianness per signal is the classic CAN-decode
trap, which is why the DBC carries it per signal.

## Tie-in to Phase 1 (and Phase 4)

Phase 1's UDP payload already carries `can_id` (`I`) + `data` (`8s`). The
helper `decode_udp_frame(db, can_id, data)` in `dbc_decode.py` takes exactly
those two fields — as `read_capture.py` unpacks them — and returns the decoded
signal dict. That is the precise seam Phase 4 will use to decode replayed
frames straight off the wire.

## Status

- DBC loads; messages/signals listed. ✓
- Encode → decode round-trip on a known frame: exact match. ✓
- Intel vs Motorola byte order demonstrated. ✓
- Phase 1 `(can_id, data)` seam decodes correctly. ✓

Next: Phase 3 — read real log files (BLF via `python-can`, MDF via `asammdf`)
and iterate frames.
