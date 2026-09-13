"""Streaming file hashing."""

from __future__ import annotations

import hashlib
from typing import Optional


def sha256_file(path: str, length: Optional[int] = None, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    remaining = length
    with open(path, "rb") as fh:
        while True:
            size = chunk if remaining is None else min(chunk, remaining)
            if size <= 0:
                break
            data = fh.read(size)
            if not data:
                break
            h.update(data)
            if remaining is not None:
                remaining -= len(data)
    return h.hexdigest()


def md5_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()
