"""Console output helpers: tables, key/value trees, sizes, progress."""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence


def human_size(n: Optional[float]) -> str:
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TiB"


def parse_size(text: str) -> int:
    """'4M' -> 4194304, '512' -> 512, '1G' -> 1073741824, '0x1000' -> 4096."""
    s = str(text).strip().lower()
    if s.startswith("0x"):
        return int(s, 16)
    mult = 1
    for suffix, m in (("k", 1024), ("m", 1024 ** 2), ("g", 1024 ** 3), ("t", 1024 ** 4)):
        if s.endswith(suffix) or s.endswith(suffix + "b") or s.endswith(suffix + "ib"):
            mult = m
            s = s.rstrip("ib").rstrip(suffix)
            break
    return int(float(s) * mult)


def print_table(rows: Iterable[Sequence[Any]], headers: Optional[Sequence[str]] = None, file=None) -> None:
    file = file or sys.stdout
    rows = [[("" if c is None else str(c)) for c in r] for r in rows]
    if headers:
        rows.insert(0, [str(h) for h in headers])
    if not rows:
        return
    widths = [max(len(r[i]) if i < len(r) else 0 for r in rows) for i in range(max(len(r) for r in rows))]
    for idx, r in enumerate(rows):
        print("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip(), file=file)
        if headers and idx == 0:
            print("  ".join("-" * w for w in widths), file=file)


def print_tree(data: Any, indent: int = 0, file=None) -> None:
    file = file or sys.stdout
    pad = "  " * indent
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)) and v:
                print(f"{pad}{k}:", file=file)
                print_tree(v, indent + 1, file)
            else:
                print(f"{pad}{k}: {_scalar(v)}", file=file)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                print(f"{pad}-", file=file)
                print_tree(item, indent + 1, file)
            else:
                print(f"{pad}- {_scalar(item)}", file=file)
    else:
        print(f"{pad}{_scalar(data)}", file=file)


def _scalar(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (list, dict)) and not v:
        return "-"
    return str(v)


def emit(data: Any, as_json: bool = False, file=None) -> None:
    file = file or sys.stdout
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str), file=file)
    else:
        print_tree(data, file=file)


class Progress:
    """Minimal byte-progress reporter for stderr."""

    def __init__(self, total: Optional[int] = None, label: str = "", enabled: bool = True):
        self.total = total
        self.label = label
        self.enabled = enabled and sys.stderr.isatty()
        self.start = time.time()
        self._last = 0.0

    def __call__(self, done: int) -> None:
        if not self.enabled:
            return
        now = time.time()
        if now - self._last < 0.2:
            return
        self._last = now
        elapsed = max(now - self.start, 1e-6)
        rate = done / elapsed
        if self.total:
            pct = min(100.0, done * 100.0 / self.total)
            msg = f"\r{self.label} {human_size(done)} / {human_size(self.total)} ({pct:5.1f}%) {human_size(rate)}/s"
        else:
            msg = f"\r{self.label} {human_size(done)} {human_size(rate)}/s"
        sys.stderr.write(msg.ljust(79))
        sys.stderr.flush()

    def finish(self) -> None:
        if self.enabled:
            sys.stderr.write("\n")
            sys.stderr.flush()


def warn(msg: str) -> None:
    print(f"[warn] {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(f"[mrt] {msg}", file=sys.stderr)
