"""IMEI helpers: validation and the per-transport encodings.

These are the pure, device-free building blocks the IMEI service layer sits on
(:mod:`mrt.modules.imei`). Nothing here touches a phone; everything is a
deterministic transform you can unit-test:

* **Luhn** check/complete (the 15th IMEI digit).
* **Qualcomm NV_UE_IMEI** (NV item 550): the 9-byte packed-BCD form stored in
  NV / inside a QCN. :func:`encode_nv_imei` / :func:`decode_nv_imei`.
* **Qualcomm DIAG** NV read/write request frames (command 0x26 / 0x27) with the
  X.25 CRC and HDLC framing a DIAG port expects. :func:`build_diag_nv_frame`.
* **AT+EGMR** command strings Samsung/MediaTek modems use over their AT port.
  :func:`build_at_egmr`.

Writing IMEI is a licensed repair operation (e.g. after a mainboard swap, to
restore the device's *own* original number). These helpers deliberately never
invent an IMEI; the caller supplies it.
"""

from __future__ import annotations

from typing import List, Optional

from .nvchecksum import crc16_x25

NV_UE_IMEI = 550  # Qualcomm NV item number for IMEI (subscription 0)
NV_ITEM_PAYLOAD = 128  # DIAG NV item packet payload size
DIAG_NV_READ_F = 0x26
DIAG_NV_WRITE_F = 0x27


def normalize(imei: str) -> str:
    """Strip spaces/dashes; keep digits only."""
    return "".join(c for c in imei if c.isdigit())


def luhn_checksum(digits: str) -> int:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:  # double every second digit from the right (the check digit is not doubled)
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10


def luhn_check_digit(first14: str) -> int:
    """The check digit that makes ``first14 + digit`` Luhn-valid."""
    if len(first14) != 14 or not first14.isdigit():
        raise ValueError("need exactly 14 digits to compute the check digit")
    return (10 - luhn_checksum(first14 + "0")) % 10


def is_valid(imei: str) -> bool:
    imei = normalize(imei)
    return len(imei) == 15 and imei.isdigit() and luhn_checksum(imei) == 0


def complete(imei14: str) -> str:
    """Append the Luhn check digit to a 14-digit IMEI."""
    imei14 = normalize(imei14)
    return imei14 + str(luhn_check_digit(imei14))


def validate(imei: str, *, allow_14: bool = False) -> str:
    """Return a clean 15-digit IMEI or raise ValueError.

    With ``allow_14`` a 14-digit input is completed with its Luhn check digit.
    """
    d = normalize(imei)
    if allow_14 and len(d) == 14:
        d = complete(d)
    if len(d) != 15:
        raise ValueError(f"IMEI must be 15 digits (got {len(d)})")
    if luhn_checksum(d) != 0:
        raise ValueError(f"IMEI {d} fails the Luhn checksum (last digit should be {luhn_check_digit(d[:14])})")
    return d


# ------------------------------------------------------- Qualcomm NV_UE_IMEI


def encode_nv_imei(imei: str) -> bytes:
    """Pack a 15-digit IMEI into the 9-byte NV_UE_IMEI (item 550) layout.

    byte0 = 0x08 (8 data bytes follow); byte1 = (d0 << 4) | 0x0A (odd-length +
    IMEI type marker); then digits are packed two per byte, low nibble first::

        raw[1] = (d0 << 4) | 0x0A
        raw[2] = (d2 << 4) | d1
        ...
        raw[8] = (d14 << 4) | d13
    """
    d = [int(c) for c in validate(imei)]
    raw = bytearray(9)
    raw[0] = 0x08
    raw[1] = (d[0] << 4) | 0x0A
    for i in range(7):
        raw[2 + i] = (d[2 + i * 2] << 4) | d[1 + i * 2]
    return bytes(raw)


def decode_nv_imei(raw: bytes) -> Optional[str]:
    """Recover the IMEI from an NV_UE_IMEI value (9+ bytes, trailing padding ok).

    Returns ``None`` if the bytes are not a plausible packed IMEI."""
    if len(raw) < 9 or raw[0] != 0x08 or (raw[1] & 0x0F) != 0x0A:
        return None
    digits = [(raw[1] >> 4) & 0x0F]
    for i in range(7):
        b = raw[2 + i]
        digits.append(b & 0x0F)
        digits.append((b >> 4) & 0x0F)
    if any(x > 9 for x in digits):
        return None
    return "".join(str(x) for x in digits)


def nv_imei_value(imei: str, length: int = 9) -> bytes:
    """Encoded IMEI padded/truncated to ``length`` (QCN streams are often longer)."""
    raw = encode_nv_imei(imei)
    if length <= len(raw):
        return raw[:length]
    return raw + b"\x00" * (length - len(raw))


# ------------------------------------------------------------ Qualcomm DIAG


def _hdlc_wrap(payload: bytes) -> bytes:
    """Append the X.25 CRC and HDLC-escape + terminate, as a DIAG port expects."""
    framed = bytearray(payload)
    framed += crc16_x25(payload).to_bytes(2, "little")
    out = bytearray()
    for b in framed:
        if b in (0x7E, 0x7D):
            out.append(0x7D)
            out.append(b ^ 0x20)
        else:
            out.append(b)
    out.append(0x7E)
    return bytes(out)


def build_diag_nv_frame(item: int, data: bytes = b"", write: bool = False) -> bytes:
    """Build a raw DIAG NV_READ_F/NV_WRITE_F request frame (CRC + HDLC framed).

    This is the exact byte stream to send to a Qualcomm DIAG (``/dev/diag``,
    QPST/QFIL) port; it is NOT sendable over adb. ``data`` is the 128-byte NV
    item payload (zero-padded); for a write pass the encoded item value.
    """
    payload = bytearray()
    payload.append(DIAG_NV_WRITE_F if write else DIAG_NV_READ_F)
    payload += int(item).to_bytes(2, "little")
    body = bytearray(data[:NV_ITEM_PAYLOAD])
    body += b"\x00" * (NV_ITEM_PAYLOAD - len(body))
    payload += body
    payload += b"\x00\x00"  # nv_stat (response fills it in)
    return _hdlc_wrap(bytes(payload))


def build_diag_imei_write(imei: str) -> bytes:
    """DIAG NV_WRITE_F frame that programs NV item 550 with ``imei``."""
    return build_diag_nv_frame(NV_UE_IMEI, encode_nv_imei(imei), write=True)


# --------------------------------------------------------------- AT+EGMR


def build_at_egmr(imei: Optional[str] = None, sim: int = 1) -> str:
    """AT+EGMR command MediaTek/Samsung modems use for IMEI.

    Read: ``AT+EGMR=0,<n>``; write: ``AT+EGMR=1,<n>,"<imei>"`` where n is 7 for
    IMEI1 and 10 for IMEI2.
    """
    n = {1: 7, 2: 10}.get(sim)
    if n is None:
        raise ValueError("sim must be 1 or 2")
    if imei is None:
        return f"AT+EGMR=0,{n}"
    return f'AT+EGMR=1,{n},"{validate(imei)}"'


# --------------------------------------- MediaTek nvram MP0B_001 (read)

MTK_IMEI_FILE = "MP0B_001"


def decode_mtk_mp0b(data: bytes, count: int = 2) -> List[str]:
    """Extract IMEIs from an MP0B_001 blob. Each record is the same packed-BCD
    IMEI form as NV_UE_IMEI, laid out back to back."""
    out: List[str] = []
    for i in range(count):
        chunk = data[i * 9:i * 9 + 9]
        imei = decode_nv_imei(chunk)
        if imei:
            out.append(imei)
    return out


def encode_mtk_mp0b(imeis: List[str], record: int = 9) -> bytes:
    """Pack one or two IMEIs into an MP0B_001 body (each 9-byte packed-BCD)."""
    body = bytearray()
    for imei in imeis:
        body += nv_imei_value(imei, record)
    return bytes(body)
