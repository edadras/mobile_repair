"""Host command execution for adb / fastboot.

All process execution goes through :class:`Runner` so that every call is
logged, honours ``--dry-run`` and locates the platform-tools binaries in
a consistent way.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, List, Optional, Sequence

from .errors import CommandFailed, ToolNotFound
from .oplog import NullLog, OperationLog


@dataclass
class CmdResult:
    argv: List[str]
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""
    duration: float = 0.0
    bytes_written: int = 0
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")

    @property
    def err(self) -> str:
        return self.stderr.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")

    @property
    def combined(self) -> str:
        """stdout followed by stderr (fastboot prints almost everything to stderr)."""
        return (self.text + "\n" + self.err).strip("\n")

    def lines(self):
        return [ln for ln in self.text.split("\n") if ln.strip()]

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{' '.join(self.argv)} rc={self.returncode}>"


_COMMON_TOOL_DIRS = [
    "~/platform-tools",
    "~/Android/Sdk/platform-tools",
    "~/Library/Android/sdk/platform-tools",
    "~/AppData/Local/Android/Sdk/platform-tools",
    "/opt/platform-tools",
    "/opt/android-sdk/platform-tools",
    "/usr/local/platform-tools",
    "C:/platform-tools",
    "C:/adb",
]


def find_tool(name: str, explicit: Optional[str] = None) -> str:
    """Locate ``adb`` or ``fastboot``.

    Order: explicit path, ``MRT_ADB``/``MRT_FASTBOOT`` env var, ``PATH``,
    then a list of well-known platform-tools locations.
    """
    candidates = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get(f"MRT_{name.upper()}")
    if env:
        candidates.append(env)
    for cand in candidates:
        if Path(cand).is_file():
            return str(Path(cand))
        found = shutil.which(cand)
        if found:
            return found
    found = shutil.which(name)
    if found:
        return found
    exe_names = [name, name + ".exe"]
    for d in _COMMON_TOOL_DIRS:
        base = Path(os.path.expanduser(d))
        for exe in exe_names:
            p = base / exe
            if p.is_file():
                return str(p)
    raise ToolNotFound(
        f"'{name}' not found. Install Android platform-tools and put it on PATH, "
        f"or set MRT_{name.upper()}=/path/to/{name} (or use --{name} PATH)."
    )


class Runner:
    """Executes host commands with logging, timeouts and dry-run support."""

    def __init__(
        self,
        adb: Optional[str] = None,
        fastboot: Optional[str] = None,
        log: Optional[OperationLog] = None,
        dry_run: bool = False,
        verbose: bool = False,
        default_timeout: int = 300,
    ):
        self._adb_hint = adb
        self._fastboot_hint = fastboot
        self._adb_path: Optional[str] = None
        self._fastboot_path: Optional[str] = None
        self.log = log or NullLog(verbose=verbose)
        self.dry_run = dry_run
        self.verbose = verbose
        self.default_timeout = default_timeout

    # ------------------------------------------------------------ tool paths
    @property
    def adb_path(self) -> str:
        if self._adb_path is None:
            self._adb_path = self._locate("adb", self._adb_hint)
        return self._adb_path

    @property
    def fastboot_path(self) -> str:
        if self._fastboot_path is None:
            self._fastboot_path = self._locate("fastboot", self._fastboot_hint)
        return self._fastboot_path

    def _locate(self, name: str, hint: Optional[str]) -> str:
        try:
            return find_tool(name, hint)
        except ToolNotFound:
            if self.dry_run:  # allow rehearsing commands on a host without platform-tools
                return hint or name
            raise

    # --------------------------------------------------------------- execute
    def run(
        self,
        argv: Sequence[str],
        timeout: Optional[float] = None,
        check: bool = False,
        input_bytes: Optional[bytes] = None,
        stdout_file: Optional[str] = None,
        stream: bool = False,
        progress=None,
    ) -> CmdResult:
        """Run ``argv``.

        ``stdout_file``: write raw stdout to this path (used for ``adb exec-out dd``).
        ``stream``: let the child inherit the terminal (interactive / live output).
        ``progress``: optional callable(bytes_so_far) invoked while writing ``stdout_file``.
        """
        argv = [str(a) for a in argv]
        timeout = timeout or self.default_timeout
        if self.verbose:
            print(f"[run] {' '.join(argv)}", file=sys.stderr)
        if self.dry_run:
            print(f"[dry-run] {' '.join(argv)}", file=sys.stderr)
            self.log.command(argv, 0, 0.0)
            return CmdResult(argv, 0, dry_run=True)

        start = time.time()
        try:
            if stdout_file is not None:
                result = self._run_to_file(argv, stdout_file, timeout, progress)
            elif stream:
                proc = subprocess.run(argv, timeout=timeout, stdin=None if input_bytes is None else subprocess.PIPE, input=input_bytes)
                result = CmdResult(argv, proc.returncode)
            else:
                proc = subprocess.run(argv, capture_output=True, timeout=timeout, input=input_bytes)
                result = CmdResult(argv, proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired:
            result = CmdResult(argv, 124, b"", f"timeout after {timeout}s".encode())
        except FileNotFoundError as exc:
            raise ToolNotFound(f"cannot execute {argv[0]}: {exc}") from exc
        result.duration = time.time() - start
        self.log.command(argv, result.returncode, result.duration, len(result.stdout), result.err)
        if check and not result.ok:
            raise CommandFailed(
                f"command failed (rc={result.returncode}): {' '.join(argv)}\n{result.combined[-2000:]}",
                result,
            )
        return result

    def _run_to_file(self, argv, path, timeout, progress) -> CmdResult:
        import threading

        state = {"written": 0}
        stderr_buf: List[bytes] = []
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdout is not None and proc.stderr is not None

        def pump_stdout():
            with open(path, "wb") as fh:
                while True:
                    chunk = proc.stdout.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    state["written"] += len(chunk)
                    if progress:
                        progress(state["written"])

        t_out = threading.Thread(target=pump_stdout, daemon=True)
        t_err = threading.Thread(target=lambda: stderr_buf.append(proc.stderr.read()), daemon=True)
        t_out.start()
        t_err.start()
        timed_out = False
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            rc = proc.wait()
        t_out.join(timeout=30)
        t_err.join(timeout=5)
        stderr = b"".join(stderr_buf)
        if timed_out:
            return CmdResult(argv, 124, b"", stderr + b"\ntimeout", bytes_written=state["written"])
        return CmdResult(argv, rc, b"", stderr, bytes_written=state["written"])

    # --------------------------------------------------------------- helpers
    def adb(self, *args, serial: Optional[str] = None, **kw) -> CmdResult:
        argv = [self.adb_path]
        if serial:
            argv += ["-s", serial]
        argv += list(args)
        return self.run(argv, **kw)

    def fastboot(self, *args, serial: Optional[str] = None, **kw) -> CmdResult:
        argv = [self.fastboot_path]
        if serial:
            argv += ["-s", serial]
        argv += list(args)
        return self.run(argv, **kw)

    def version(self, tool: str) -> str:
        path = self.adb_path if tool == "adb" else self.fastboot_path
        res = self.run([path, "--version"], timeout=20)
        return res.combined.split("\n")[0] if res.combined else "unknown"
