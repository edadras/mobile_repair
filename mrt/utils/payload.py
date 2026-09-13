"""Minimal Android OTA ``payload.bin`` parser and extractor (full OTA only).

Implements just enough protobuf decoding of ``DeltaArchiveManifest`` to
extract partition images without third-party dependencies.
"""

from __future__ import annotations

import bz2
import lzma
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable, Dict, Iterator, List, Optional, Tuple

from ..core.errors import UnsupportedFormat

MAGIC = b"CrAU"

OP_REPLACE = 0
OP_REPLACE_BZ = 1
OP_MOVE = 2
OP_BSDIFF = 3
OP_SOURCE_COPY = 4
OP_SOURCE_BSDIFF = 5
OP_ZERO = 6
OP_DISCARD = 7
OP_REPLACE_XZ = 8
OP_PUFFDIFF = 9
OP_BROTLI_BSDIFF = 10
OP_ZUCCHINI = 11
OP_LZ4DIFF_BSDIFF = 12
OP_LZ4DIFF_PUFFDIFF = 13

OP_NAMES = {
    OP_REPLACE: "REPLACE", OP_REPLACE_BZ: "REPLACE_BZ", OP_MOVE: "MOVE", OP_BSDIFF: "BSDIFF",
    OP_SOURCE_COPY: "SOURCE_COPY", OP_SOURCE_BSDIFF: "SOURCE_BSDIFF", OP_ZERO: "ZERO", OP_DISCARD: "DISCARD",
    OP_REPLACE_XZ: "REPLACE_XZ", OP_PUFFDIFF: "PUFFDIFF", OP_BROTLI_BSDIFF: "BROTLI_BSDIFF",
    OP_ZUCCHINI: "ZUCCHINI", OP_LZ4DIFF_BSDIFF: "LZ4DIFF_BSDIFF", OP_LZ4DIFF_PUFFDIFF: "LZ4DIFF_PUFFDIFF",
}


# --------------------------------------------------------------- protobuf


def _read_varint(data: bytes, pos: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise UnsupportedFormat("truncated varint in payload manifest")
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def decode_message(data: bytes) -> Iterator[Tuple[int, int, object]]:
    """Yield (field_number, wire_type, value) for each field in ``data``."""
    pos = 0
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        field_no, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _read_varint(data, pos)
        elif wire == 1:
            value = struct.unpack_from("<Q", data, pos)[0]
            pos += 8
        elif wire == 2:
            length, pos = _read_varint(data, pos)
            value = data[pos:pos + length]
            pos += length
        elif wire == 5:
            value = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        else:
            raise UnsupportedFormat(f"unsupported protobuf wire type {wire}")
        yield field_no, wire, value


@dataclass
class Extent:
    start_block: int = 0
    num_blocks: int = 0


@dataclass
class Operation:
    type: int = 0
    data_offset: int = 0
    data_length: int = 0
    dst_extents: List[Extent] = field(default_factory=list)

    @property
    def type_name(self) -> str:
        return OP_NAMES.get(self.type, str(self.type))


@dataclass
class PartitionUpdate:
    name: str
    operations: List[Operation] = field(default_factory=list)

    @property
    def size(self) -> int:
        end = 0
        for op in self.operations:
            for ext in op.dst_extents:
                end = max(end, ext.start_block + ext.num_blocks)
        return end

    def is_full(self) -> bool:
        return all(op.type in (OP_REPLACE, OP_REPLACE_BZ, OP_REPLACE_XZ, OP_ZERO, OP_DISCARD) for op in self.operations)


@dataclass
class Payload:
    path: str
    version: int
    block_size: int
    manifest_size: int
    metadata_signature_size: int
    data_offset: int
    partitions: List[PartitionUpdate]

    def partition(self, name: str) -> PartitionUpdate:
        for p in self.partitions:
            if p.name == name:
                return p
        raise UnsupportedFormat(f"partition '{name}' not present in payload (have: {', '.join(p.name for p in self.partitions)})")


def _parse_extent(data: bytes) -> Extent:
    ext = Extent()
    for fno, _, val in decode_message(data):
        if fno == 1:
            ext.start_block = int(val)
        elif fno == 2:
            ext.num_blocks = int(val)
    return ext


def _parse_operation(data: bytes) -> Operation:
    op = Operation()
    for fno, _, val in decode_message(data):
        if fno == 1:
            op.type = int(val)
        elif fno == 2:
            op.data_offset = int(val)
        elif fno == 3:
            op.data_length = int(val)
        elif fno == 6:
            op.dst_extents.append(_parse_extent(val))
    return op


def _parse_partition(data: bytes) -> PartitionUpdate:
    part = PartitionUpdate(name="")
    for fno, _, val in decode_message(data):
        if fno == 1:
            part.name = val.decode("utf-8", "replace")
        elif fno == 8:
            part.operations.append(_parse_operation(val))
    return part


def parse_payload(path: str) -> Payload:
    with open(path, "rb") as fh:
        header = fh.read(24)
        if len(header) < 24 or header[:4] != MAGIC:
            raise UnsupportedFormat(f"{path} is not an Android OTA payload (missing CrAU magic)")
        version = struct.unpack(">Q", header[4:12])[0]
        manifest_size = struct.unpack(">Q", header[12:20])[0]
        if version >= 2:
            metadata_signature_size = struct.unpack(">I", header[20:24])[0]
            manifest_start = 24
        else:
            metadata_signature_size = 0
            manifest_start = 20
            fh.seek(20)
        fh.seek(manifest_start)
        manifest = fh.read(manifest_size)
    if len(manifest) != manifest_size:
        raise UnsupportedFormat("truncated payload manifest")
    block_size = 4096
    partitions: List[PartitionUpdate] = []
    for fno, _, val in decode_message(manifest):
        if fno == 3:
            block_size = int(val)
        elif fno == 13:
            partitions.append(_parse_partition(val))
    return Payload(
        path=path,
        version=version,
        block_size=block_size,
        manifest_size=manifest_size,
        metadata_signature_size=metadata_signature_size,
        data_offset=manifest_start + manifest_size + metadata_signature_size,
        partitions=partitions,
    )


def extract_partition(payload: Payload, name: str, out_path: str, progress: Optional[Callable[[int], None]] = None) -> Dict[str, object]:
    part = payload.partition(name)
    if not part.is_full():
        kinds = sorted({op.type_name for op in part.operations if op.type not in (OP_REPLACE, OP_REPLACE_BZ, OP_REPLACE_XZ, OP_ZERO, OP_DISCARD)})
        raise UnsupportedFormat(f"partition '{name}' uses differential operations ({', '.join(kinds)}); only full OTA payloads can be extracted")
    bs = payload.block_size
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(payload.path, "rb") as src, open(out_path, "wb") as dst:
        for op in part.operations:
            if op.type in (OP_REPLACE, OP_REPLACE_BZ, OP_REPLACE_XZ):
                src.seek(payload.data_offset + op.data_offset)
                blob = src.read(op.data_length)
                if len(blob) != op.data_length:
                    raise UnsupportedFormat("truncated payload data blob")
                if op.type == OP_REPLACE_BZ:
                    blob = bz2.decompress(blob)
                elif op.type == OP_REPLACE_XZ:
                    blob = lzma.decompress(blob)
                cursor = 0
                for ext in op.dst_extents:
                    length = ext.num_blocks * bs
                    dst.seek(ext.start_block * bs)
                    dst.write(blob[cursor:cursor + length])
                    cursor += length
                    written += length
            elif op.type in (OP_ZERO, OP_DISCARD):
                for ext in op.dst_extents:
                    dst.seek(ext.start_block * bs)
                    _write_zeros(dst, ext.num_blocks * bs)
                    written += ext.num_blocks * bs
            if progress:
                progress(written)
        total = part.size * bs
        dst.truncate(total)
    return {"partition": name, "file": out_path, "size": part.size * bs, "operations": len(part.operations)}


def _write_zeros(fh: BinaryIO, length: int, chunk: int = 1 << 20) -> None:
    zero = b"\0" * min(chunk, length)
    remaining = length
    while remaining > 0:
        n = min(len(zero), remaining)
        fh.write(zero[:n])
        remaining -= n


def extract_all(payload: Payload, out_dir: str, only: Optional[List[str]] = None, progress_factory=None) -> List[Dict[str, object]]:
    results = []
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    for part in payload.partitions:
        if only and part.name not in only:
            continue
        progress = progress_factory(part) if progress_factory else None
        results.append(extract_partition(payload, part.name, str(Path(out_dir) / f"{part.name}.img"), progress=progress))
    return results
