"""Operation log.

Every host command, every result and every checksum is written to a
per-session plain-text log and a machine-readable JSONL log so that a
repair job can be audited afterwards.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class OperationLog:
    def __init__(self, log_dir: Optional[str] = None, verbose: bool = False, enabled: bool = True):
        self.verbose = verbose
        self.enabled = enabled
        self.session = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = log_dir or os.environ.get("MRT_LOG_DIR") or str(Path.home() / ".mrt" / "logs")
        self.log_dir = Path(base)
        self.text_path = self.log_dir / f"mrt-{self.session}.log"
        self.jsonl_path = self.log_dir / f"mrt-{self.session}.jsonl"
        self._text = None
        self._jsonl = None
        if enabled:
            try:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                self._text = open(self.text_path, "a", encoding="utf-8")
                self._jsonl = open(self.jsonl_path, "a", encoding="utf-8")
            except OSError as exc:  # logging must never break the tool
                self.enabled = False
                print(f"[mrt] warning: cannot open log files in {self.log_dir}: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------ core
    def event(self, kind: str, **data: Any) -> None:
        record = {"ts": time.time(), "time": datetime.now().isoformat(timespec="seconds"), "kind": kind}
        record.update(data)
        if self._jsonl:
            self._jsonl.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self._jsonl.flush()
        if self._text:
            summary = " ".join(f"{k}={_short(v)}" for k, v in data.items())
            self._text.write(f"{record['time']} [{kind}] {summary}\n")
            self._text.flush()

    def command(self, argv, returncode, duration, stdout_len=0, stderr=""):
        self.event(
            "command",
            argv=argv,
            returncode=returncode,
            duration=round(duration, 3),
            stdout_len=stdout_len,
            stderr=(stderr or "")[:2000],
        )
        if self.verbose:
            print(f"[cmd] {' '.join(argv)} -> rc={returncode} ({duration:.2f}s)", file=sys.stderr)

    def info(self, message: str, **data: Any) -> None:
        self.event("info", message=message, **data)
        if self.verbose:
            print(f"[info] {message}", file=sys.stderr)

    def warn(self, message: str, **data: Any) -> None:
        self.event("warn", message=message, **data)
        print(f"[warn] {message}", file=sys.stderr)

    def error(self, message: str, **data: Any) -> None:
        self.event("error", message=message, **data)
        print(f"[error] {message}", file=sys.stderr)

    def close(self) -> None:
        for fh in (self._text, self._jsonl):
            if fh:
                try:
                    fh.close()
                except OSError:
                    pass
        self._text = self._jsonl = None


def _short(value: Any, limit: int = 300) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


class NullLog(OperationLog):
    """Log that discards everything (used by tests)."""

    def __init__(self, verbose: bool = False):
        super().__init__(enabled=False, verbose=verbose)
