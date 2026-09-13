"""Qualcomm QCN file support.

A ``.qcn`` (QPST backup) is a Microsoft Compound File Binary Format (CFBF /
OLE2 structured storage) container. NV items are stored as streams named by
their decimal item number, usually under an ``NV_ITEM_ARRAY`` storage;
EFS-backed items live under ``Provisioning_Item_Files``. This module implements
a dependency-free CFBF reader and writer plus QCN-level helpers so NV items can
be listed, extracted and edited **offline**.

Important scope note: a QCN stream holds the *raw* NV item value. The 16-bit
CRC that Qualcomm's DIAG protocol puts around an NV item only exists on the
wire when the item is written to the modem; it is not stored inside the QCN.
Writing a QCN back onto a device therefore needs QPST/QFIL over a DIAG port and
is **not** possible over adb - this module edits the file, it does not flash it.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from ..core.errors import UnsupportedFormat

SIGNATURE = bytes([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1])
FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
DIFSECT = 0xFFFFFFFC
NOSTREAM = 0xFFFFFFFF
MINI_CUTOFF = 4096

T_STORAGE = 1
T_STREAM = 2
T_ROOT = 5

Tree = Dict[str, Union["Tree", bytes]]


@dataclass
class DirEntry:
    name: str
    type: int
    left: int = NOSTREAM
    right: int = NOSTREAM
    child: int = NOSTREAM
    start: int = ENDOFCHAIN
    size: int = 0
    data: bytes = b""


# ============================================================ reader


class CompoundFile:
    """Minimal read-only CFBF parser (handles v3/512 and v4/4096, DIFAT, mini)."""

    def __init__(self, raw: bytes):
        if raw[:8] != SIGNATURE:
            raise UnsupportedFormat("not a Compound File (bad OLE2 signature); this is not a QCN")
        self.raw = raw
        (self.minor, self.major, self.byte_order, self.sector_shift, self.mini_shift) = struct.unpack_from("<HHHHH", raw, 24)
        if self.byte_order != 0xFFFE:
            raise UnsupportedFormat("unexpected byte order in compound file")
        self.sector_size = 1 << self.sector_shift
        self.mini_size = 1 << self.mini_shift
        (self.num_fat, self.first_dir, _txn, self.mini_cutoff, self.first_minifat,
         self.num_minifat, self.first_difat, self.num_difat) = struct.unpack_from("<IIIIIIII", raw, 44)
        self.mini_cutoff = self.mini_cutoff or MINI_CUTOFF
        self._difat_header = list(struct.unpack_from("<109I", raw, 76))
        self.fat = self._read_fat()
        self.dir_entries = self._read_directory()
        self.minifat = self._read_minifat()
        self.mini_stream = self._read_mini_stream()

    # -- low level
    def _sector_offset(self, n: int) -> int:
        return (n + 1) * self.sector_size

    def _sector(self, n: int) -> bytes:
        off = self._sector_offset(n)
        return self.raw[off:off + self.sector_size]

    def _fat_sectors(self) -> List[int]:
        sectors = [s for s in self._difat_header if s not in (FREESECT, ENDOFCHAIN)]
        nxt = self.first_difat
        guard = 0
        while nxt not in (FREESECT, ENDOFCHAIN) and guard < 1 << 20:
            data = self._sector(nxt)
            entries = struct.unpack(f"<{self.sector_size // 4}I", data)
            sectors += [s for s in entries[:-1] if s not in (FREESECT, ENDOFCHAIN)]
            nxt = entries[-1]
            guard += 1
        return sectors[:self.num_fat] if self.num_fat else sectors

    def _read_fat(self) -> List[int]:
        fat: List[int] = []
        per = self.sector_size // 4
        for s in self._fat_sectors():
            fat += list(struct.unpack(f"<{per}I", self._sector(s)))
        return fat

    def _chain(self, start: int) -> List[int]:
        out: List[int] = []
        cur = start
        seen = set()
        while cur not in (ENDOFCHAIN, FREESECT) and cur < len(self.fat) and cur not in seen:
            seen.add(cur)
            out.append(cur)
            cur = self.fat[cur]
        return out

    def _read_stream_fat(self, start: int, size: Optional[int] = None) -> bytes:
        data = b"".join(self._sector(s) for s in self._chain(start))
        return data[:size] if size is not None else data

    def _read_directory(self) -> List[DirEntry]:
        raw = self._read_stream_fat(self.first_dir)
        entries: List[DirEntry] = []
        for off in range(0, len(raw), 128):
            chunk = raw[off:off + 128]
            if len(chunk) < 128:
                break
            name_len = struct.unpack_from("<H", chunk, 64)[0]
            etype = chunk[66]
            if etype == 0:
                entries.append(DirEntry("", 0))
                continue
            name = chunk[:max(0, name_len - 2)].decode("utf-16-le", "replace")
            left, right, child = struct.unpack_from("<III", chunk, 68)
            start, size = struct.unpack_from("<IQ", chunk, 116)
            if self.major == 3:
                size &= 0xFFFFFFFF
            entries.append(DirEntry(name, etype, left, right, child, start, size))
        return entries

    def _read_minifat(self) -> List[int]:
        if self.num_minifat == 0 or self.first_minifat in (ENDOFCHAIN, FREESECT):
            return []
        raw = self._read_stream_fat(self.first_minifat)
        return list(struct.unpack(f"<{len(raw) // 4}I", raw))

    def _read_mini_stream(self) -> bytes:
        root = self.dir_entries[0]
        if root.size == 0 or root.start in (ENDOFCHAIN, FREESECT):
            return b""
        return self._read_stream_fat(root.start, root.size)

    def _read_mini_chain(self, start: int, size: int) -> bytes:
        out = bytearray()
        cur = start
        seen = set()
        while cur not in (ENDOFCHAIN, FREESECT) and cur < len(self.minifat) and cur not in seen:
            seen.add(cur)
            out += self.mini_stream[cur * self.mini_size:(cur + 1) * self.mini_size]
            cur = self.minifat[cur]
        return bytes(out[:size])

    def read_entry(self, entry: DirEntry) -> bytes:
        if entry.type != T_STREAM:
            return b""
        if entry.size >= self.mini_cutoff:
            return self._read_stream_fat(entry.start, entry.size)
        return self._read_mini_chain(entry.start, entry.size)

    # -- tree
    def to_tree(self) -> Tree:
        root = self.dir_entries[0]
        tree: Tree = {}
        self._walk(root.child, tree)
        return tree

    def _walk(self, idx: int, into: Tree) -> None:
        if idx == NOSTREAM or idx >= len(self.dir_entries):
            return
        e = self.dir_entries[idx]
        self._walk(e.left, into)
        if e.type == T_STORAGE:
            sub: Tree = {}
            self._walk(e.child, sub)
            into[e.name] = sub
        elif e.type == T_STREAM:
            into[e.name] = self.read_entry(e)
        self._walk(e.right, into)

    def stream_disk_ranges(self, entry: DirEntry) -> List[Tuple[int, int]]:
        """(file_offset, length) byte ranges backing a stream (for in-place edits)."""
        ranges: List[Tuple[int, int]] = []
        remaining = entry.size
        if entry.size >= self.mini_cutoff:
            for s in self._chain(entry.start):
                take = min(self.sector_size, remaining)
                ranges.append((self._sector_offset(s), take))
                remaining -= take
                if remaining <= 0:
                    break
            return ranges
        # mini stream: map each mini sector into the big sectors backing the mini stream
        root = self.dir_entries[0]
        big = self._chain(root.start)
        cur = entry.start
        seen = set()
        while cur not in (ENDOFCHAIN, FREESECT) and remaining > 0 and cur not in seen:
            seen.add(cur)
            mini_off = cur * self.mini_size
            big_index = mini_off // self.sector_size
            within = mini_off % self.sector_size
            take = min(self.mini_size, remaining)
            ranges.append((self._sector_offset(big[big_index]) + within, take))
            remaining -= take
            cur = self.minifat[cur]
        return ranges


# ============================================================ writer


def _dir_key(name: str) -> Tuple[int, str]:
    return (len(name.encode("utf-16-le")) + 2, name.upper())


def _build_bst(ids: List[int], entries: List[DirEntry]) -> int:
    """Balanced BST over sibling ids ordered by the CFBF comparison key."""
    if not ids:
        return NOSTREAM
    ids = sorted(ids, key=lambda i: _dir_key(entries[i].name))

    def build(lo: int, hi: int) -> int:
        if lo > hi:
            return NOSTREAM
        mid = (lo + hi) // 2
        entries[ids[mid]].left = build(lo, mid - 1)
        entries[ids[mid]].right = build(mid + 1, hi)
        return ids[mid]

    return build(0, len(ids) - 1)


def _ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b if a else 0


def serialize(tree: Tree, sector_size: int = 512) -> bytes:
    """Serialize a nested {name: bytes|dict} tree into a v3 CFBF byte string."""
    mini_size = 64
    entries: List[DirEntry] = [DirEntry("Root Entry", T_ROOT)]

    def add(node: Tree) -> List[int]:
        ids: List[int] = []
        for name, value in node.items():
            if len(name.encode("utf-16-le")) > 62:
                raise UnsupportedFormat(
                    f"compound-file entry name too long (max 31 chars): {name!r}")
            if isinstance(value, dict):
                e = DirEntry(name, T_STORAGE)
                entries.append(e)
                idx = len(entries) - 1
                e.child = _build_bst(add(value), entries)
            else:
                data = bytes(value)
                e = DirEntry(name, T_STREAM, size=len(data), data=data)
                entries.append(e)
                idx = len(entries) - 1
            ids.append(idx)
        return ids

    entries[0].child = _build_bst(add(tree), entries)

    # split streams
    small = [e for e in entries if e.type == T_STREAM and 0 < e.size < MINI_CUTOFF]
    big = [e for e in entries if e.type == T_STREAM and e.size >= MINI_CUTOFF]

    # mini stream + mini FAT (contiguous chains)
    mini_stream = bytearray()
    minifat: List[int] = []
    for e in small:
        nsec = _ceil_div(e.size, mini_size)
        e.start = len(minifat)
        for i in range(nsec):
            minifat.append(len(minifat) + 1 if i < nsec - 1 else ENDOFCHAIN)
        padded = e.data + b"\x00" * (nsec * mini_size - e.size)
        mini_stream += padded
    for e in small:
        e.data = b""  # data now lives in mini_stream

    # directory bytes
    ndir_entries = ((len(entries) + 3) // 4) * 4
    ndir_sec = _ceil_div(ndir_entries * 128, sector_size)

    nminifat_bytes = len(minifat) * 4
    nminifat_sec = _ceil_div(nminifat_bytes, sector_size)
    nmini_stream_sec = _ceil_div(len(mini_stream), sector_size)

    # allocate data sectors: [dir][minifat][ministream][big streams...]
    p = 0
    dir_start = p; p += ndir_sec
    minifat_start = p if nminifat_sec else ENDOFCHAIN; p += nminifat_sec
    mini_stream_start = p if nmini_stream_sec else ENDOFCHAIN; p += nmini_stream_sec
    for e in big:
        e.start = p
        p += _ceil_div(e.size, sector_size)
    ndata = p

    # number of FAT sectors (fixpoint)
    nfat = _ceil_div(ndata, sector_size // 4) or 1
    while True:
        total = ndata + nfat
        need = _ceil_div(total, sector_size // 4)
        if need == nfat:
            break
        nfat = need
    if nfat > 109:
        raise UnsupportedFormat("QCN too large for this writer (needs DIFAT sectors); edit in place instead")
    total = ndata + nfat

    # FAT
    per = sector_size // 4
    fat = [FREESECT] * (nfat * per)

    def chain(start: int, count: int) -> None:
        for i in range(count):
            fat[start + i] = (start + i + 1) if i < count - 1 else ENDOFCHAIN

    chain(dir_start, ndir_sec)
    if nminifat_sec:
        chain(minifat_start, nminifat_sec)
    if nmini_stream_sec:
        chain(mini_stream_start, nmini_stream_sec)
    for e in big:
        chain(e.start, _ceil_div(e.size, sector_size))
    for i in range(ndata, ndata + nfat):
        fat[i] = FATSECT

    # root points at the mini stream
    root = entries[0]
    root.start = mini_stream_start if nmini_stream_sec else ENDOFCHAIN
    root.size = len(mini_stream)

    # header
    header = bytearray(sector_size)
    header[0:8] = SIGNATURE
    struct.pack_into("<HHHHH", header, 24, 0x003E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIII", header, 44, nfat, dir_start, 0, MINI_CUTOFF,
                     minifat_start if nminifat_sec else ENDOFCHAIN, nminifat_sec, ENDOFCHAIN, 0)
    difat = [ndata + i for i in range(nfat)] + [FREESECT] * (109 - nfat)
    struct.pack_into("<109I", header, 76, *difat)

    # directory sectors
    dir_bytes = bytearray()
    for i in range(ndir_entries):
        e = entries[i] if i < len(entries) else DirEntry("", 0)
        rec = bytearray(128)
        nm = e.name.encode("utf-16-le")[:62]
        rec[0:len(nm)] = nm
        struct.pack_into("<H", rec, 64, (len(nm) + 2) if e.type else 0)
        rec[66] = e.type
        rec[67] = 1  # black
        struct.pack_into("<III", rec, 68, e.left, e.right, e.child)
        struct.pack_into("<IQ", rec, 116, e.start if e.type else 0, e.size if e.type in (T_STREAM, T_ROOT) else 0)
        dir_bytes += rec
    dir_bytes += b"\x00" * (ndir_sec * sector_size - len(dir_bytes))

    # minifat sectors
    minifat_bytes = b"".join(struct.pack("<I", v) for v in minifat)
    minifat_bytes += b"\xff" * (nminifat_sec * sector_size - len(minifat_bytes))

    mini_bytes = bytes(mini_stream) + b"\x00" * (nmini_stream_sec * sector_size - len(mini_stream))

    big_bytes = bytearray()
    for e in big:
        secs = _ceil_div(e.size, sector_size)
        big_bytes += e.data + b"\x00" * (secs * sector_size - e.size)

    fat_bytes = b"".join(struct.pack("<I", v) for v in fat)

    out = bytes(header) + bytes(dir_bytes) + minifat_bytes + mini_bytes + bytes(big_bytes) + fat_bytes
    return out


# ============================================================ QCN helpers

NV_STORAGES = ("NV_ITEM_ARRAY", "NV_Items", "Provisioning_Item_Files")


@dataclass
class QcnItem:
    storage: str
    item: str
    value: bytes


class Qcn:
    def __init__(self, tree: Tree):
        self.tree = tree

    @classmethod
    def load(cls, path: str) -> "Qcn":
        with open(path, "rb") as fh:
            return cls(CompoundFile(fh.read()).to_tree())

    def save(self, path: str) -> None:
        with open(path, "wb") as fh:
            fh.write(serialize(self.tree))

    def items(self) -> List[QcnItem]:
        out: List[QcnItem] = []
        for sname, sval in self.tree.items():
            if isinstance(sval, dict):
                for name, val in sval.items():
                    if isinstance(val, (bytes, bytearray)):
                        out.append(QcnItem(sname, name, bytes(val)))
        return out

    def nv_items(self) -> List[QcnItem]:
        return [it for it in self.items() if it.item.isdigit()]

    def get(self, item: Union[int, str], storage: Optional[str] = None) -> Optional[QcnItem]:
        key = str(item)
        for it in self.items():
            if it.item == key and (storage is None or it.storage == storage):
                return it
        return None

    def set(self, item: Union[int, str], value: bytes, storage: str = "NV_ITEM_ARRAY") -> None:
        self.tree.setdefault(storage, {})
        node = self.tree[storage]
        assert isinstance(node, dict)
        node[str(item)] = bytes(value)

    def metadata(self) -> Dict[str, Any]:
        meta: Dict[str, Any] = {}
        for name in ("Version", "Mobile_Property", "File_Version", "OEM_INFO"):
            v = self.tree.get(name)
            if isinstance(v, (bytes, bytearray)):
                meta[name] = v.hex()
        meta["storages"] = [k for k, v in self.tree.items() if isinstance(v, dict)]
        meta["nv_item_count"] = len(self.nv_items())
        return meta


def edit_item_inplace(path: str, item: Union[int, str], new_value: bytes, storage: Optional[str] = None) -> bool:
    """Overwrite an NV item value on disk without rebuilding the container.

    Only works when ``len(new_value)`` equals the current value length (the
    safest edit: every other byte of the QCN is preserved). Returns True on
    success; raises if the item is missing or the length differs.
    """
    with open(path, "rb") as fh:
        raw = bytearray(fh.read())
    cf = CompoundFile(bytes(raw))
    key = str(item)
    target: Optional[DirEntry] = None
    # find the stream entry (respecting storage if given)
    for idx, e in enumerate(cf.dir_entries):
        if e.type == T_STREAM and e.name == key:
            if storage is None or _entry_parent_name(cf, idx) == storage:
                target = e
                break
    if target is None:
        raise UnsupportedFormat(f"NV item {key} not found in {path}")
    if len(new_value) != target.size:
        raise UnsupportedFormat(
            f"in-place edit needs equal length (item {key} is {target.size} bytes, new value is {len(new_value)}); "
            "use rebuild edit instead")
    pos = 0
    for off, length in cf.stream_disk_ranges(target):
        raw[off:off + length] = new_value[pos:pos + length]
        pos += length
    with open(path, "wb") as fh:
        fh.write(raw)
    return True


def _entry_parent_name(cf: CompoundFile, target_idx: int) -> Optional[str]:
    for i, e in enumerate(cf.dir_entries):
        if e.type in (T_STORAGE, T_ROOT):
            stack = [e.child]
            while stack:
                idx = stack.pop()
                if idx == NOSTREAM or idx >= len(cf.dir_entries):
                    continue
                if idx == target_idx:
                    return e.name
                stack += [cf.dir_entries[idx].left, cf.dir_entries[idx].right]
    return None


def read_tree(path: str) -> Tree:
    with open(path, "rb") as fh:
        return CompoundFile(fh.read()).to_tree()
