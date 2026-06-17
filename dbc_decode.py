"""Phase 2: load a DBC, list its messages, and decode/encode CAN frames.

This turns raw CAN bytes into named, scaled engineering values using cantools.
It also proves the round-trip (encode -> decode) and demonstrates the
Intel (little-endian) vs Motorola (big-endian) byte-order gotcha.

The demo payload uses the *same two fields* Phase 1's UDP layout carries --
a CAN id and 8 data bytes -- which is the exact seam Phase 4 will reuse to
decode replayed frames.

Usage:  python dbc_decode.py [sample.dbc]
"""

import sys

import cantools

DBC_PATH = "sample.dbc"
DEMO_FRAME_ID = 0x100   # 256 = EngineData


def list_messages(db):
    """Print every message and its signals, like a DBC table of contents."""
    print("=== Messages in DBC ===")
    for msg in db.messages:
        print(f"  {msg.name}  id=0x{msg.frame_id:X} ({msg.frame_id})  "
              f"length={msg.length}B")
        for sig in msg.signals:
            order = "Intel/little" if sig.byte_order == "little_endian" \
                else "Motorola/big"
            print(f"      {sig.name:<12} start={sig.start:<3} len={sig.length:<3}"
                  f" {order:<13} scale={sig.scale} offset={sig.offset}"
                  f" unit={sig.unit!r}")
    print()


def decode_demo(db):
    """Encode a known set of values, then decode the bytes back."""
    msg = db.get_message_by_frame_id(DEMO_FRAME_ID)

    # The engineering values we want on the wire.
    values = {
        "EngineSpeed": 2500.0,   # rpm
        "CoolantTemp": 90,       # degC
        "ThrottlePos": 40.0,     # %
        "GearRatio": 3.25,       # ratio
    }

    # encode: signals -> raw bytes
    data = msg.encode(values)
    print("=== Encode (signals -> bytes) ===")
    print(f"  input : {values}")
    print(f"  bytes : {data.hex()}  ({len(data)} bytes)\n")

    # decode: raw bytes -> signals (the Phase 4 direction)
    decoded = msg.decode(data)
    print("=== Decode (bytes -> signals) ===")
    for name, val in decoded.items():
        print(f"  {name:<12} = {val}")
    print()

    # Round-trip sanity check: re-encoding the decoded values must match.
    assert msg.encode(dict(decoded)) == data, "round-trip mismatch!"
    print("round-trip OK: encode(decode(x)) == x\n")


def byte_order_demo(db):
    """Show why endianness matters: same bytes, opposite interpretation.

    GearRatio is Motorola (big-endian) across bytes 4-5. If those two bytes
    were read as little-endian instead, the value would be completely wrong.
    """
    msg = db.get_message_by_frame_id(DEMO_FRAME_ID)
    data = msg.encode({
        "EngineSpeed": 0, "CoolantTemp": -40, "ThrottlePos": 0,
        "GearRatio": 3.25,   # raw 325 = 0x0145
    })
    b = bytearray(data)
    print("=== Byte-order gotcha (GearRatio, Motorola @0) ===")
    print(f"  full frame      : {data.hex()}")
    print(f"  GearRatio bytes : {b[4]:02x} {b[5]:02x}  (byte4=MSB, byte5=LSB)")
    big = (b[4] << 8) | b[5]
    little = (b[5] << 8) | b[4]
    print(f"  read big-endian : 0x{big:04x} = {big} raw -> {big * 0.01:.2f}  <- correct")
    print(f"  read little-end : 0x{little:04x} = {little} raw -> {little * 0.01:.2f}  <- WRONG")
    print()


def decode_udp_frame(db, can_id, data):
    """Decode a CAN frame using exactly the two fields Phase 1's UDP payload
    carries (`I` can_id + `8s` data, as read_capture.py unpacks them).

    This is the seam Phase 4 will use to decode replayed frames. Returns the
    {signal: value} dict, or None if the id isn't in the DBC.
    """
    try:
        msg = db.get_message_by_frame_id(can_id)
    except KeyError:
        return None
    return msg.decode(data)


def phase1_seam_demo(db):
    """Pretend a UDP packet just arrived; decode its can_id + data."""
    # These are the exact values our struct ">I d I 8s" would yield.
    can_id = 0x100
    data = bytes.fromhex("1027826401450000")
    print("=== Phase 1 seam (UDP can_id + data -> signals) ===")
    decoded = decode_udp_frame(db, can_id, data)
    print(f"  can_id=0x{can_id:X} data={data.hex()}")
    if decoded is None:
        print("  (id not in DBC)")
    else:
        for name, val in decoded.items():
            print(f"  {name:<12} = {val}")
    print()


def main(path=DBC_PATH):
    db = cantools.database.load_file(path)
    print(f"loaded {path}\n")
    list_messages(db)
    decode_demo(db)
    byte_order_demo(db)
    phase1_seam_demo(db)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DBC_PATH)
