"""Qualcomm EFS2 image analysis (``modemst1`` / ``modemst2`` / ``fsg``).

EFS2 is the log-structured flash filesystem the Qualcomm modem keeps its NV
items and calibration in. On eMMC/UFS devices the AP only serves raw sectors
to the modem (rmtfs); the modem itself runs the filesystem. The on-media layout
is known from Qualcomm's ``fs_super.h`` and from public reverse engineering:

* Every superblock page starts with ``page_header`` (u32), ``version`` (u16),
  ``age`` (u16) and the two magics ``"EFSS"`` + ``"uper"`` (``EFSSuper`` at
  byte 8). Geometry follows: ``block_size`` (pages per block), ``page_size``
  (bytes), ``block_count``, ``log_head``, ``alloc_next[4]``, ``gc_next[4]``,
  ``upper_data[32]`` and a NAND-info block (``nodes_per_page``, ``page_depth``,
  ``super_nodes``, ``num_regions``, ``regions[]``, ``logr_badmap``, ``pad``,
  ``tables``).
* The superblock is rewritten to a new page as the log advances; the live one
  is the superblock with the highest ``age``.
* An "EFS info" block (magic ``a0 3e b9 a7``) carries the inode table pointers.

**There is no CRC or checksum field in any of these structures.** Integrity is
kept by the modem through the log sequence (``age``, ``page_header``) and by
the ``modemst1``/``modemst2`` mirror plus the ``fsg`` golden copy. So the only
"checksum fix" that exists for these partitions is to let the modem rebuild
its own metadata: erase the mirror pair and it re-creates them from ``fsg`` on
the next boot (:func:`mrt.modules.efs.rebuild_modemst`). What *can* be
verified offline is done here: find every superblock, validate the geometry,
pick the live one and compare the three images.
"""

from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional

SUPER_MAGIC = b"EFSSuper"  # magic1 "EFSS" + magic2 "uper" at superblock byte 8
SUPER_MAGIC_OFFSET = 8
EFS_INFO_MAGIC = bytes([0xA0, 0x3E, 0xB9, 0xA7])
SCAN_STRIDE = 512  # superblocks sit on page boundaries; every EFS2 page size is a multiple of 512
MIN_PAGE = 512
MAX_PAGE = 65536

# page_header u32, version u16, age u16, magic1, magic2, block_size, page_size,
# block_count, log_head, alloc_next[4], gc_next[4], upper_data[32]
_FIXED = struct.Struct("<IHH4s4sIIII4I4I32I")
# nand_info: nodes_per_page, page_depth, super_nodes, num_regions (u16 each)
_NAND_HEAD = struct.Struct("<HHHH")
_NAND_TAIL = struct.Struct("<III")  # logr_badmap, pad, tables (after regions[])
MAX_REGIONS = 64


def parse_superblock(page: bytes) -> Optional[Dict[str, Any]]:
    """Parse one superblock page. Returns ``None`` if the magic is absent or the
    geometry is nonsense (so a random page with the magic string is rejected)."""
    if len(page) < _FIXED.size + _NAND_HEAD.size:
        return None
    if page[SUPER_MAGIC_OFFSET:SUPER_MAGIC_OFFSET + 8] != SUPER_MAGIC:
        return None
    f = _FIXED.unpack_from(page, 0)
    page_header, version, age = f[0], f[1], f[2]
    block_size, page_size, block_count, log_head = f[5], f[6], f[7], f[8]
    alloc_next = list(f[9:13])
    gc_next = list(f[13:17])
    upper_data = list(f[17:49])
    if not (MIN_PAGE <= page_size <= MAX_PAGE) or page_size & (page_size - 1):
        return None
    if not (1 <= block_size <= 4096) or not (1 <= block_count <= 1 << 20):
        return None
    off = _FIXED.size
    nodes_per_page, page_depth, super_nodes, num_regions = _NAND_HEAD.unpack_from(page, off)
    off += _NAND_HEAD.size
    nand: Dict[str, Any] = {
        "nodes_per_page": nodes_per_page, "page_depth": page_depth,
        "super_nodes": super_nodes, "num_regions": num_regions,
    }
    if num_regions <= MAX_REGIONS and off + num_regions * 4 + _NAND_TAIL.size <= len(page):
        nand["regions"] = list(struct.unpack_from(f"<{num_regions}I", page, off))
        off += num_regions * 4
        nand["logr_badmap"], nand["pad"], nand["tables"] = _NAND_TAIL.unpack_from(page, off)
    return {
        "page_header": page_header, "version": version, "age": age,
        "block_size": block_size, "page_size": page_size, "block_count": block_count,
        "fs_bytes": block_size * page_size * block_count,
        "log_head": log_head, "alloc_next": alloc_next, "gc_next": gc_next,
        "upper_data_nonzero": sum(1 for x in upper_data if x),
        "nand_info": nand,
    }


def is_blank(data: bytes) -> bool:
    """True for an erased / never-written image (all 0x00 or all 0xFF)."""
    return bool(data) and (data.count(0) == len(data) or data.count(0xFF) == len(data))


def find_superblocks(data: bytes) -> List[Dict[str, Any]]:
    """Every valid superblock in the image, in offset order, each with ``offset``."""
    found: List[Dict[str, Any]] = []
    start = 0
    while True:
        idx = data.find(SUPER_MAGIC, start)
        if idx < 0:
            break
        start = idx + 1
        off = idx - SUPER_MAGIC_OFFSET
        if off < 0 or off % SCAN_STRIDE:
            continue
        sb = parse_superblock(data[off:off + 512])
        if sb is None:
            continue
        sb["offset"] = off
        found.append(sb)
    return found


def analyze(data: bytes) -> Dict[str, Any]:
    """Structural report of an EFS2 image (no device access).

    ``state``: ``blank`` (erased/never written), ``no-superblock`` (data but no
    EFS2 superblock), ``ok`` (consistent superblocks) or ``inconsistent``
    (superblocks disagree on geometry - a mixed/partial image).
    """
    report: Dict[str, Any] = {"format": "efs2", "bytes": len(data)}
    if not data or is_blank(data):
        report["state"] = "blank"
        report["superblocks"] = 0
        return report
    sbs = find_superblocks(data)
    report["superblocks"] = len(sbs)
    if not sbs:
        report["state"] = "no-superblock"
        return report
    newest = max(sbs, key=lambda s: (s["age"], s["offset"]))
    geometries = {(s["block_size"], s["page_size"], s["block_count"]) for s in sbs}
    report["live"] = {k: newest[k] for k in ("offset", "age", "version", "page_size", "block_size", "block_count", "fs_bytes", "log_head")}
    report["ages"] = sorted(s["age"] for s in sbs)
    report["age_rollover_possible"] = newest["age"] >= 0xFF00 or (min(s["age"] for s in sbs) < 0x0100 and newest["age"] > 0xFF00)
    report["geometry_consistent"] = len(geometries) == 1
    report["geometry_fits_image"] = newest["fs_bytes"] <= len(data)
    report["efs_info_blocks"] = _count_efs_info(data, newest["page_size"])
    report["state"] = "ok" if report["geometry_consistent"] else "inconsistent"
    return report


def _count_efs_info(data: bytes, page_size: int) -> int:
    n = 0
    start = 0
    while True:
        idx = data.find(EFS_INFO_MAGIC, start)
        if idx < 0:
            return n
        start = idx + 1
        if idx % page_size == 0:
            n += 1


def compare_mirror(reports: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Cross-check modemst1 / modemst2 / fsg reports produced by :func:`analyze`."""
    out: Dict[str, Any] = {"members": sorted(reports), "problems": []}
    live = {n: r.get("live") for n, r in reports.items() if r.get("live")}
    geoms = {(r["page_size"], r["block_size"], r["block_count"]) for r in live.values()}
    out["geometry_consistent"] = len(geoms) <= 1
    if len(geoms) > 1:
        out["problems"].append("images have different EFS2 geometry; they do not come from the same device/firmware")
    for n, r in reports.items():
        if r.get("state") in ("blank", "no-superblock"):
            out["problems"].append(f"{n}: {r.get('state')} (no EFS2 filesystem)")
        elif r.get("state") == "inconsistent":
            out["problems"].append(f"{n}: superblocks disagree on geometry")
    if live:
        newest = max(live.items(), key=lambda kv: kv[1]["age"])[0]
        out["newest"] = newest
        out["ages"] = {n: r["age"] for n, r in live.items()}
    fsg = reports.get("fsg")
    out["fsg_usable"] = bool(fsg and fsg.get("state") == "ok")
    if fsg is not None and not out["fsg_usable"]:
        out["problems"].append("fsg holds no valid EFS2 image: the modem cannot rebuild modemst from it")
    out["ok"] = not out["problems"]
    return out
