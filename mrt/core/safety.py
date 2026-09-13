"""Interactive safety confirmations for destructive operations."""

from __future__ import annotations

import sys
from typing import Callable, Optional

from .errors import Aborted
from .oplog import NullLog, OperationLog

NOTICE = "notice"          # just print a warning
DESTRUCTIVE = "destructive"  # needs y/N
CRITICAL = "critical"      # needs typing a token (partition name / serial / YES)

_FA = {
    NOTICE: "توجه",
    DESTRUCTIVE: "هشدار: این عملیات مخرب است و قابل بازگشت نیست",
    CRITICAL: "هشدار جدی: این عملیات می‌تواند دستگاه را غیرقابل استفاده (brick) کند",
}


class Safety:
    def __init__(self, assume_yes: bool = False, log: Optional[OperationLog] = None, input_fn: Callable[[str], str] = input, out=None):
        self.assume_yes = assume_yes
        self.log = log or NullLog()
        self.input_fn = input_fn
        self.out = out or sys.stderr

    def confirm(self, message: str, level: str = DESTRUCTIVE, token: Optional[str] = None) -> bool:
        """Ask the operator to confirm. Raises :class:`Aborted` when declined."""
        banner = {NOTICE: "NOTICE", DESTRUCTIVE: "WARNING - DESTRUCTIVE", CRITICAL: "DANGER - MAY BRICK DEVICE"}[level]
        print("", file=self.out)
        print("=" * 70, file=self.out)
        print(f"  {banner}", file=self.out)
        print(f"  {_FA[level]}", file=self.out)
        print("-" * 70, file=self.out)
        for line in message.strip().split("\n"):
            print(f"  {line}", file=self.out)
        print("=" * 70, file=self.out)

        if level == NOTICE:
            self.log.info("notice shown", detail=message)
            return True
        if self.assume_yes:
            self.log.info("confirmation auto-accepted (--yes)", detail=message, level=level)
            print("  [--yes] proceeding without prompt", file=self.out)
            return True
        if self.input_fn is input and (sys.stdin is None or not sys.stdin.isatty()):
            raise Aborted("confirmation required but stdin is not interactive; re-run with --yes")

        if level == DESTRUCTIVE:
            answer = self.input_fn("  Continue? [y/N]: ").strip().lower()
            ok = answer in ("y", "yes")
        else:
            token = token or "YES"
            answer = self.input_fn(f"  Type '{token}' to continue: ").strip()
            ok = answer == token
        self.log.info("confirmation", detail=message, level=level, accepted=ok)
        if not ok:
            raise Aborted("operation cancelled by operator")
        return True
