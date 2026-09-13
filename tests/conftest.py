"""Shared test fixtures: a scripted fake command runner."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Union

import pytest

from mrt.core.context import Context
from mrt.core.device import AdbDevice, FastbootDevice
from mrt.core.errors import CommandFailed
from mrt.core.oplog import NullLog
from mrt.core.runner import CmdResult, Runner
from mrt.core.safety import Safety

Matcher = Union[str, Callable[[List[str]], bool]]


@dataclass
class Rule:
    match: Matcher
    stdout: str = ""
    stderr: str = ""
    rc: int = 0
    data: Optional[bytes] = None  # raw bytes for stdout_file mode


class FakeRunner(Runner):
    def __init__(self, rules: Optional[List[Rule]] = None, dry_run: bool = False):
        super().__init__(adb="adb", fastboot="fastboot", log=NullLog(), dry_run=dry_run)
        self._adb_path = "adb"
        self._fastboot_path = "fastboot"
        self.rules: List[Rule] = list(rules or [])
        self.calls: List[List[str]] = []

    def add(self, match: Matcher, stdout: str = "", stderr: str = "", rc: int = 0, data: Optional[bytes] = None) -> "FakeRunner":
        self.rules.append(Rule(match, stdout, stderr, rc, data))
        return self

    def joined_calls(self) -> List[str]:
        return [" ".join(c) for c in self.calls]

    def run(self, argv, timeout=None, check=False, input_bytes=None, stdout_file=None, stream=False, progress=None):
        argv = [str(a) for a in argv]
        joined = " ".join(argv)
        self.calls.append(argv)
        if self.dry_run:
            return CmdResult(argv, 0, dry_run=True)
        for rule in self.rules:
            ok = rule.match(argv) if callable(rule.match) else (rule.match in joined)
            if not ok:
                continue
            if stdout_file is not None:
                payload = rule.data if rule.data is not None else rule.stdout.encode()
                with open(stdout_file, "wb") as fh:
                    fh.write(payload)
                if progress:
                    progress(len(payload))
                res = CmdResult(argv, rule.rc, b"", rule.stderr.encode(), bytes_written=len(payload))
            else:
                res = CmdResult(argv, rule.rc, rule.stdout.encode(), rule.stderr.encode())
            if check and not res.ok:
                raise CommandFailed(f"failed: {joined}", res)
            return res
        if stdout_file is not None:
            open(stdout_file, "wb").close()
        return CmdResult(argv, 0)


class ScriptedSafety(Safety):
    """Safety that answers prompts from a queue (and records what was asked)."""

    def __init__(self, answers: Optional[List[str]] = None, assume_yes: bool = False):
        self.answers = list(answers or [])
        self.prompts: List[str] = []
        super().__init__(assume_yes=assume_yes, log=NullLog(), input_fn=self._answer, out=open(os.devnull, "w"))

    def _answer(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else ""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def rooted_runner() -> FakeRunner:
    """A runner where plain `adb shell id` already reports uid 0 (adbd root)."""
    r = FakeRunner()
    r.add("adb devices -l", "List of devices attached\nSER\tdevice\n")
    r.add("fastboot devices -l", "")
    r.add("shell id", "uid=0(root) gid=0(root)\n")
    return r


@pytest.fixture
def su_runner() -> FakeRunner:
    """A runner where root is only available through `su -c`."""
    r = FakeRunner()
    r.add("adb devices -l", "List of devices attached\nSER\tdevice\n")
    r.add("fastboot devices -l", "")
    r.add("shell id", "uid=2000(shell) gid=2000(shell)\n")
    r.add("adb -s SER root", "adbd cannot run as root in production builds\n")
    r.add("shell su -c id", "uid=0(root) gid=0(root)\n")
    return r


@pytest.fixture
def adb_dev(rooted_runner) -> AdbDevice:
    return AdbDevice(rooted_runner, "SER")


@pytest.fixture
def fb_dev(runner) -> FastbootDevice:
    return FastbootDevice(runner, "FBSER")


def make_ctx(runner: FakeRunner, safety: Optional[Safety] = None, serial: str = "SER", json_output: bool = False) -> Context:
    return Context(runner, NullLog(), safety or ScriptedSafety(assume_yes=True), serial=serial, json_output=json_output)
