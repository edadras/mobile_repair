"""MediaTek ``nvram`` / ``nvdata`` / ``nvcfg`` image analysis.

What these partitions really are:

* ``nvdata`` (and ``nvcfg``) are plain **ext4 filesystems**. ``nvdata`` holds
  the live NVRAM files (``/nvdata/APCFG/APRDCL/...``, ``/nvdata/md/NVRAM/...``,
  IMEI in ``MP0B_001``). ext4 keeps its own metadata checksums (superblock
  CRC32C when ``metadata_csum`` is on); a user never recomputes them by hand -
  the kernel does when the filesystem is written.
* ``nvram`` is a **raw backup region** written by ``nvram_daemon``
  (``FileOp_BackupToBinRegion_All`` in MediaTek's ``libfile_op``). It starts
  with a ``File_Title_Header`` (per-category file counts as ``short``s,
  ``iFileBufLen``, ``BackupFlag``) followed by ``File_Title`` records
  (``NameSize``, ``FielStartAddr``, ``Filesize``, ``cFileName[128]``) and the
  file bodies. ``BackupFlag == 0xFECF`` (``DATA_FLAG``) marks a valid backup.
  Any integrity value inside the region is produced by ``nvram_daemon`` when
  it writes the backup, and re-checked by it at boot.

So "recomputing the nvram/nvdata checksum" by hand is not an operation that
exists. What exists - and what MediaTek's own daemon does - is: if ``nvdata``
is empty/corrupt at boot, ``nvram_daemon`` restores every NVRAM file from the
``nvram`` backup region (:func:`mrt.modules.efs.mtk_rebuild_nvdata`). This
module verifies the structures offline so that rebuild is only attempted when
a usable backup actually exists.
"""

from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- ext4

EXT4_SB_OFFSET = 1024
EXT4_MAGIC = 0xEF53
EXT4_FEATURE_RO_COMPAT_METADATA_CSUM = 0x0400
EXT4_VALID_FS = 0x0001
EXT4_ERROR_FS = 0x0002


def crc32c(data: bytes, crc: int = 0) -> int:
    """Standard CRC-32C (Castagnoli): reflected poly 0x82F63B78, init/xorout 0xFFFFFFFF."""
    crc ^= 0xFFFFFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x82F63B78 if crc & 1 else crc >> 1
    return crc ^ 0xFFFFFFFF


def ext4_superblock(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse the primary ext4 superblock; ``None`` if the magic is absent."""
    if len(data) < EXT4_SB_OFFSET + 1024:
        return None
    sb = data[EXT4_SB_OFFSET:EXT4_SB_OFFSET + 1024]
    magic = struct.unpack_from("<H", sb, 0x38)[0]
    if magic != EXT4_MAGIC:
        return None
    inodes, blocks_lo = struct.unpack_from("<II", sb, 0)
    log_block_size = struct.unpack_from("<I", sb, 0x18)[0]
    mnt_count, max_mnt = struct.unpack_from("<HH", sb, 0x34)
    state, errors = struct.unpack_from("<HH", sb, 0x3A)
    ro_compat = struct.unpack_from("<I", sb, 0x64)[0]
    volume = sb[0x78:0x88].split(b"\0", 1)[0].decode("latin-1")
    error_count = struct.unpack_from("<I", sb, 0x194)[0]
    stored_csum = struct.unpack_from("<I", sb, 0x3FC)[0]
    out: Dict[str, Any] = {
        "format": "ext4", "volume_name": volume,
        "block_size": 1024 << log_block_size, "blocks": blocks_lo, "inodes": inodes,
        "fs_bytes": blocks_lo * (1024 << log_block_size),
        "mount_count": mnt_count, "max_mount_count": max_mnt,
        "state_clean": bool(state & EXT4_VALID_FS), "state_errors": bool(state & EXT4_ERROR_FS),
        "errors_behaviour": errors, "error_count": error_count,
        "metadata_csum": bool(ro_compat & EXT4_FEATURE_RO_COMPAT_METADATA_CSUM),
    }
    if out["metadata_csum"]:
        # ext4_superblock_csum: crc32c seeded with ~0 over the superblock up to s_checksum, no final xor
        computed = crc32c(sb[:0x3FC]) ^ 0xFFFFFFFF
        out["superblock_csum_stored"] = stored_csum
        out["superblock_csum_ok"] = computed == stored_csum
    return out


# ------------------------------------------------------- nvram bin region

DATA_FLAG = 0xFECF  # libfile_op.h: #define DATA_FLAG (0xfecf)
TITLE_HEADER_SIZE = 24  # 7 (or 8) shorts padded to 16, then iFileBufLen, BackupFlag
TITLE_SIZE = 140  # int NameSize; int FielStartAddr; int Filesize; char cFileName[128]
MAX_NAMESIZE = 128
SCAN_STRIDE = 512
SCAN_LIMIT = 2 << 20


def _sane_name(raw: bytes) -> Optional[str]:
    name = raw.split(b"\0", 1)[0]
    if not name or len(name) >= MAX_NAMESIZE:
        return None
    if any(c < 0x20 or c > 0x7E for c in name):
        return None
    return name.decode("ascii")


def parse_backup_header(data: bytes, offset: int = 0) -> Optional[Dict[str, Any]]:
    """Parse a ``File_Title_Header`` + its ``File_Title`` table at ``offset``.

    Returns ``None`` unless ``BackupFlag`` is ``DATA_FLAG`` and the table is
    plausible (sane counts, printable file names, offsets inside the buffer).
    """
    if offset + TITLE_HEADER_SIZE > len(data):
        return None
    counts = list(struct.unpack_from("<8H", data, offset))  # 8th short is iViaNum or padding
    buf_len, flag = struct.unpack_from("<II", data, offset + 16)
    if flag != DATA_FLAG:
        return None
    total = sum(counts)
    if total == 0 or total > 4096 or buf_len <= 0 or buf_len > len(data) - offset:
        return None
    titles: List[Dict[str, Any]] = []
    pos = offset + TITLE_HEADER_SIZE
    for _ in range(total):
        if pos + TITLE_SIZE > len(data):
            return None
        name_size, start, size = struct.unpack_from("<iii", data, pos)
        name = _sane_name(data[pos + 12:pos + 12 + MAX_NAMESIZE])
        if name is None or size < 0 or start < 0:
            return None
        titles.append({"name": name, "start": start, "size": size})
        pos += TITLE_SIZE
    return {
        "offset": offset, "backup_flag": flag, "file_buf_len": buf_len,
        "counts": {"ap_boot": counts[0], "ap_clean": counts[1], "md_boot": counts[2], "md_clean": counts[3],
                   "md_important": counts[4], "md_core": counts[5], "md_data": counts[6], "via_or_pad": counts[7]},
        "files": len(titles),
        "file_names": [t["name"] for t in titles[:64]],
        "titles": titles,
    }


def find_backup_region(data: bytes) -> Optional[Dict[str, Any]]:
    """Locate the backup region: at offset 0 (normal) or, as a fallback, on any
    512-byte boundary in the first 2 MiB."""
    hdr = parse_backup_header(data, 0)
    if hdr:
        return hdr
    limit = min(len(data), SCAN_LIMIT)
    for off in range(SCAN_STRIDE, limit, SCAN_STRIDE):
        hdr = parse_backup_header(data, off)
        if hdr:
            hdr["found_by_scan"] = True
            return hdr
    return None


def analyze_nvram(data: bytes) -> Dict[str, Any]:
    """Report on a raw ``nvram`` backup-region image."""
    report: Dict[str, Any] = {"format": "mtk-nvram", "bytes": len(data)}
    if not data or data.count(0) == len(data) or data.count(0xFF) == len(data):
        report["state"] = "blank"
        return report
    hdr = find_backup_region(data)
    if not hdr:
        report["state"] = "no-backup-header"
        return report
    titles = hdr.pop("titles")
    hdr["files_within_buffer"] = all(t["start"] + t["size"] <= hdr["file_buf_len"] for t in titles)
    report.update(hdr)
    report["has_imei_file"] = any(n.upper().endswith("MP0B_001") or "MP0B_001" in n.upper() for n in (t["name"] for t in titles))
    report["state"] = "ok" if hdr["files_within_buffer"] else "inconsistent"
    return report


def analyze_nvdata(data: bytes) -> Dict[str, Any]:
    """Report on an ``nvdata`` / ``nvcfg`` (ext4) image."""
    report: Dict[str, Any] = {"format": "ext4", "bytes": len(data)}
    if not data or data.count(0) == len(data) or data.count(0xFF) == len(data):
        report["state"] = "blank"
        return report
    sb = ext4_superblock(data)
    if not sb:
        report["state"] = "no-ext4-superblock"
        return report
    report.update(sb)
    bad = sb["state_errors"] or sb["error_count"] > 0 or sb.get("superblock_csum_ok") is False
    report["state"] = "errors" if bad else "ok"
    return report
