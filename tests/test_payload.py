import bz2
import lzma
import os
import struct
import zipfile

import pytest

from mrt.core.errors import UnsupportedFormat
from mrt.modules import flash
from mrt.utils import payload as pl


# --- tiny protobuf encoder for building synthetic payloads -----------------

def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def vi(fno, n):
    return varint(fno << 3 | 0) + varint(n)


def ld(fno, data):
    return varint(fno << 3 | 2) + varint(len(data)) + data


def extent(start, num):
    return ld(6, vi(1, start) + vi(2, num))


def build_payload(path, block_size=4096, sig=b"SIG!"):
    blobs = bytearray()
    ops_boot = []

    raw = os.urandom(block_size * 2)
    ops_boot.append(vi(1, pl.OP_REPLACE) + vi(2, len(blobs)) + vi(3, len(raw)) + extent(0, 2))
    blobs += raw

    plain_bz = b"B" * block_size
    comp = bz2.compress(plain_bz)
    ops_boot.append(vi(1, pl.OP_REPLACE_BZ) + vi(2, len(blobs)) + vi(3, len(comp)) + extent(2, 1))
    blobs += comp

    ops_boot.append(vi(1, pl.OP_ZERO) + extent(3, 1))

    plain_xz = b"X" * block_size
    comp_xz = lzma.compress(plain_xz)
    ops_boot.append(vi(1, pl.OP_REPLACE_XZ) + vi(2, len(blobs)) + vi(3, len(comp_xz)) + extent(4, 1))
    blobs += comp_xz

    boot = ld(1, b"boot") + b"".join(ld(8, op) for op in ops_boot)
    vendor = ld(1, b"vendor") + ld(8, vi(1, pl.OP_SOURCE_COPY) + extent(0, 1))
    manifest = vi(3, block_size) + ld(13, boot) + ld(13, vendor)
    header = b"CrAU" + struct.pack(">Q", 2) + struct.pack(">Q", len(manifest)) + struct.pack(">I", len(sig))
    with open(path, "wb") as fh:
        fh.write(header + manifest + sig + bytes(blobs))
    expected_boot = raw + plain_bz + b"\0" * block_size + plain_xz
    return expected_boot


def test_parse_and_extract_full_partition(tmp_path):
    p = tmp_path / "payload.bin"
    expected = build_payload(str(p))
    payload = pl.parse_payload(str(p))
    assert payload.version == 2 and payload.block_size == 4096
    assert [x.name for x in payload.partitions] == ["boot", "vendor"]
    boot = payload.partition("boot")
    assert boot.size == 5 and boot.is_full()
    assert [op.type_name for op in boot.operations] == ["REPLACE", "REPLACE_BZ", "ZERO", "REPLACE_XZ"]
    out = tmp_path / "boot.img"
    res = pl.extract_partition(payload, "boot", str(out))
    assert out.read_bytes() == expected
    assert res["size"] == 5 * 4096


def test_differential_partition_rejected(tmp_path):
    p = tmp_path / "payload.bin"
    build_payload(str(p))
    payload = pl.parse_payload(str(p))
    assert not payload.partition("vendor").is_full()
    with pytest.raises(UnsupportedFormat) as exc:
        pl.extract_partition(payload, "vendor", str(tmp_path / "v.img"))
    assert "SOURCE_COPY" in str(exc.value)


def test_bad_magic(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"nope" * 10)
    with pytest.raises(UnsupportedFormat):
        pl.parse_payload(str(p))


def test_payload_in_ota_zip_and_extract_all(tmp_path):
    raw = tmp_path / "payload.bin"
    expected = build_payload(str(raw))
    z = tmp_path / "ota.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(raw, "payload.bin")
        zf.writestr("META-INF/com/android/metadata", "ota-type=AB\n")
    info = flash.payload_info(str(z))
    assert {p["name"]: p["full"] for p in info["partitions"]} == {"boot": True, "vendor": False}
    results = flash.extract_payload(str(z), str(tmp_path / "out"), only=["boot"])
    assert [r["partition"] for r in results] == ["boot"]
    assert (tmp_path / "out" / "boot.img").read_bytes() == expected
    kind = flash.inspect_rom(str(z))
    assert "payload.bin" in kind["type"]
