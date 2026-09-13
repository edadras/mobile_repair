"""Checksum helpers for NV / EFS data.

What is a *real, recomputable* checksum and what is not:

* **Samsung** ``nv_data.bin`` uses a sidecar file (``nv_data.bin.md5``) whose
  content is the ASCII MD5 hex digest of the binary. This IS recomputable and
  is implemented here (:func:`samsung_md5_hex`, :func:`samsung_md5_targets`).

* **Qualcomm DIAG** wraps each NV item in a 16-bit CRC (CRC-CCITT / X.25) when
  the item travels over the diagnostic port. :func:`crc16_x25` computes it, for
  callers that build DIAG frames. Note this CRC is a *wire* checksum: it is not
  stored inside a QCN stream nor inside the ``modemst`` partitions.

* **Qualcomm ``modemst1``/``modemst2``/``fsg``** hold an EFS2 log-structured
  filesystem with internal page sequence numbers and per-page integrity data.
  There is no single partition-level checksum a user can recompute; hand-editing
  the image and "fixing the CRC" is not a real operation. The correct repair is
  an atomic restore of the mirror group from a known-good dump of the same
  device (see :mod:`mrt.modules.efs`).

* **MediaTek ``nvram``/``nvdata``** are filesystems too; same story - restore
  the group atomically, do not try to recompute a partition checksum.
"""

from __future__ import annotations

import hashlib
from typing import Dict, List


def crc16_x25(data: bytes) -> int:
    """CRC-16-CCITT (X.25 / HDLC), reflected, poly 0x1021, init 0xFFFF, xorout 0xFFFF.

    This is the CRC Qualcomm's DIAG protocol uses to frame NV read/write commands.
    """
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return (~crc) & 0xFFFF


def crc16_x25_bytes(data: bytes) -> bytes:
    return crc16_x25(data).to_bytes(2, "little")


def samsung_md5_hex(data: bytes) -> str:
    """The 32-char lowercase hex MD5 digest Samsung stores in ``*.md5`` sidecars."""
    return hashlib.md5(data).hexdigest()


# Files inside a Samsung /efs whose content is protected by an MD5 sidecar.
SAMSUNG_MD5_PAIRS = [
    ("nv_data.bin", "nv_data.bin.md5"),
    (".nv_data.bak", ".nv_data.bak.md5"),
    ("nv.log", "nv.log.md5"),
    (".nv_core.bak", ".nv_core.bak.md5"),
    (".nv_core.bak.md5", None),
]


def samsung_md5_targets(names: List[str]) -> List[str]:
    """Given the file names present in an /efs dir, return the base files that
    should have an MD5 sidecar recomputed (a base file X for which X.md5 exists,
    or a known base file)."""
    present = set(names)
    known_bases = {b for b, _ in SAMSUNG_MD5_PAIRS if b and not b.endswith(".md5")}
    targets = []
    for name in names:
        if name.endswith(".md5"):
            continue
        if name in known_bases or (name + ".md5") in present:
            targets.append(name)
    return targets
