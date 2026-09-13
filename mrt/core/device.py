"""Device discovery and thin wrappers around an ADB or Fastboot device."""

from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .errors import CommandFailed, DeviceNotFound, RootRequired
from .runner import CmdResult, Runner

ADB_MODES = {"device", "recovery", "sideload", "rescue", "unauthorized", "offline", "bootloader", "host", "no permissions", "authorizing"}


@dataclass
class DeviceInfo:
    serial: str
    mode: str                 # device / recovery / sideload / fastboot / fastbootd / unauthorized / offline
    transport: str            # "adb" or "fastboot"
    details: Dict[str, str] = field(default_factory=dict)

    @property
    def description(self) -> str:
        parts = [self.details.get(k) for k in ("product", "model", "device") if self.details.get(k)]
        return " ".join(parts)

    def as_dict(self):
        return {"serial": self.serial, "mode": self.mode, "transport": self.transport, **self.details}


def parse_adb_devices(text: str) -> List[DeviceInfo]:
    devices = []
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, mode = parts[0], parts[1]
        if mode == "no" and len(parts) > 2 and parts[2] == "permissions":
            mode = "no permissions"
        details = {}
        for token in parts[2:]:
            if ":" in token:
                k, v = token.split(":", 1)
                details[k] = v
        devices.append(DeviceInfo(serial, mode, "adb", details))
    return devices


def parse_fastboot_devices(text: str) -> List[DeviceInfo]:
    devices = []
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ("fastboot", "fastbootd"):
            devices.append(DeviceInfo(parts[0], parts[1], "fastboot"))
        elif len(parts) >= 1 and "fastboot" in line:
            devices.append(DeviceInfo(parts[0], "fastboot", "fastboot"))
    return devices


def list_devices(runner: Runner, adb: bool = True, fastboot: bool = True) -> List[DeviceInfo]:
    found: List[DeviceInfo] = []
    if adb:
        try:
            res = runner.adb("devices", "-l", timeout=30)
            found += parse_adb_devices(res.text)
        except Exception as exc:  # tool missing -> skip transport
            runner.log.warn(f"adb devices failed: {exc}")
    if fastboot:
        try:
            res = runner.fastboot("devices", "-l", timeout=30)
            found += parse_fastboot_devices(res.combined)
        except Exception as exc:
            runner.log.warn(f"fastboot devices failed: {exc}")
    return found


def select_device(runner: Runner, serial: Optional[str] = None, want: str = "adb") -> DeviceInfo:
    """Pick exactly one device. ``want`` is 'adb', 'fastboot' or 'any'."""
    devices = list_devices(runner, adb=want in ("adb", "any"), fastboot=want in ("fastboot", "any"))
    if serial:
        for d in devices:
            if d.serial == serial:
                return d
        raise DeviceNotFound(f"device '{serial}' not found (connected: {[d.serial for d in devices] or 'none'})")
    usable = [d for d in devices if d.mode not in ("unauthorized", "offline", "no permissions", "authorizing")]
    if not usable:
        hints = []
        for d in devices:
            if d.mode == "unauthorized":
                hints.append(f"{d.serial}: unauthorized - accept the USB debugging prompt on the phone")
            elif d.mode == "no permissions":
                hints.append(f"{d.serial}: no permissions - fix udev rules / run with proper USB permissions")
            elif d.mode == "offline":
                hints.append(f"{d.serial}: offline - replug the cable or run 'adb kill-server'")
        raise DeviceNotFound("no usable device found in {} mode. {}".format(want, "; ".join(hints) if hints else "Connect a device with USB debugging or in fastboot."))
    if len(usable) > 1:
        raise DeviceNotFound("multiple devices connected, use -s SERIAL: " + ", ".join(f"{d.serial} ({d.mode})" for d in usable))
    return usable[0]


# --------------------------------------------------------------------------- ADB


class AdbDevice:
    """A device reachable with ``adb`` (system, recovery or sideload mode)."""

    def __init__(self, runner: Runner, serial: Optional[str] = None):
        self.runner = runner
        self.serial = serial
        self._root_mode: Optional[str] = None  # None=unknown, "" = no root, "adbd", "su", "su0"

    # ------------------------------------------------------------ raw calls
    def adb(self, *args, **kw) -> CmdResult:
        return self.runner.adb(*args, serial=self.serial, **kw)

    def shell(self, cmd: str, timeout: Optional[float] = None, check: bool = False) -> CmdResult:
        return self.adb("shell", cmd, timeout=timeout, check=check)

    def shell_text(self, cmd: str, timeout: Optional[float] = None) -> str:
        return self.shell(cmd, timeout=timeout).text.strip()

    def exec_out(self, cmd: str, dest_path: str, timeout: Optional[float] = None, progress=None) -> CmdResult:
        """Run ``cmd`` on the device and stream its raw stdout into ``dest_path``."""
        return self.adb("exec-out", cmd, stdout_file=dest_path, timeout=timeout, progress=progress)

    def push(self, local: str, remote: str, timeout: Optional[float] = 3600) -> CmdResult:
        return self.adb("push", local, remote, timeout=timeout, check=True)

    def pull(self, remote: str, local: str, timeout: Optional[float] = 3600, check: bool = True) -> CmdResult:
        return self.adb("pull", "-a", remote, local, timeout=timeout, check=check)

    def state(self) -> str:
        res = self.adb("get-state", timeout=20)
        return res.text.strip() or "unknown"

    def wait(self, state: str = "device", timeout: float = 120) -> bool:
        res = self.adb(f"wait-for-{state}", timeout=timeout)
        return res.ok

    def reboot(self, target: Optional[str] = None) -> CmdResult:
        args = ["reboot"] + ([target] if target else [])
        return self.adb(*args, timeout=60)

    # ------------------------------------------------------------ properties
    def getprop(self, name: Optional[str] = None) -> str:
        return self.shell_text(f"getprop {name}" if name else "getprop")

    def getprops(self) -> Dict[str, str]:
        return parse_getprop(self.getprop())

    # ------------------------------------------------------------------ root
    def ensure_root(self, try_adb_root: bool = True) -> str:
        """Return the root mode available ('adbd', 'su', 'su0') or raise RootRequired."""
        mode = self.root_mode(try_adb_root=try_adb_root)
        if not mode:
            raise RootRequired(
                "root access is required for this operation but neither 'adb root' nor 'su' works. "
                "Root the device (Magisk) or use fastboot-based operations instead."
            )
        return mode

    def root_mode(self, try_adb_root: bool = True) -> str:
        if self._root_mode is not None:
            return self._root_mode
        if _is_uid0(self.shell("id", timeout=20).text):
            self._root_mode = "adbd"
            return self._root_mode
        if try_adb_root:
            res = self.adb("root", timeout=30)
            if res.ok and "cannot run as root" not in res.combined and "adbd cannot" not in res.combined:
                self.wait("device", timeout=30)
                if _is_uid0(self.shell("id", timeout=20).text):
                    self._root_mode = "adbd"
                    return self._root_mode
        if _is_uid0(self.shell("su -c id", timeout=30).text):
            self._root_mode = "su"
            return self._root_mode
        if _is_uid0(self.shell("su 0 id", timeout=30).text):
            self._root_mode = "su0"
            return self._root_mode
        self._root_mode = ""
        return self._root_mode

    def has_root(self) -> bool:
        return bool(self.root_mode())

    def root_command(self, cmd: str) -> str:
        """Wrap ``cmd`` so that it executes as root (string usable with shell/exec-out)."""
        mode = self.ensure_root()
        if mode == "adbd":
            return cmd
        if mode == "su":
            return f"su -c {shlex.quote(cmd)}"
        return f"su 0 sh -c {shlex.quote(cmd)}"

    def root_shell(self, cmd: str, timeout: Optional[float] = None, check: bool = False) -> CmdResult:
        return self.shell(self.root_command(cmd), timeout=timeout, check=check)

    def root_shell_text(self, cmd: str, timeout: Optional[float] = None) -> str:
        return self.root_shell(cmd, timeout=timeout).text.strip()

    def root_exec_out(self, cmd: str, dest_path: str, timeout: Optional[float] = None, progress=None) -> CmdResult:
        return self.exec_out(self.root_command(cmd), dest_path, timeout=timeout, progress=progress)

    # --------------------------------------------------------------- helpers
    def which(self, binary: str, root: bool = False) -> Optional[str]:
        cmd = f"command -v {binary} 2>/dev/null || which {binary} 2>/dev/null"
        out = self.root_shell_text(cmd) if root else self.shell_text(cmd)
        out = out.split("\n")[0].strip()
        return out or None

    def file_exists(self, path: str, root: bool = False) -> bool:
        cmd = f"[ -e {shlex.quote(path)} ] && echo yes || echo no"
        out = self.root_shell_text(cmd) if root else self.shell_text(cmd)
        return out.endswith("yes")

    def remote_sha256(self, path: str, root: bool = True, length: Optional[int] = None) -> Optional[str]:
        """sha256 of a device file/block device (optionally only the first ``length`` bytes)."""
        tool = "sha256sum"
        q = shlex.quote(path)
        if length is not None:
            cmd = f"head -c {int(length)} {q} | {tool}"
        else:
            cmd = f"{tool} {q}"
        res = self.root_shell(cmd, timeout=3600) if root else self.shell(cmd, timeout=3600)
        m = re.search(r"\b([0-9a-fA-F]{64})\b", res.text)
        return m.group(1).lower() if m else None

    def remote_md5(self, path: str, root: bool = True, length: Optional[int] = None) -> Optional[str]:
        q = shlex.quote(path)
        cmd = f"head -c {int(length)} {q} | md5sum" if length is not None else f"md5sum {q}"
        res = self.root_shell(cmd, timeout=3600) if root else self.shell(cmd, timeout=3600)
        m = re.search(r"\b([0-9a-fA-F]{32})\b", res.text)
        return m.group(1).lower() if m else None

    def remote_size(self, path: str, root: bool = True) -> Optional[int]:
        q = shlex.quote(path)
        cmd = f"blockdev --getsize64 {q} 2>/dev/null || stat -c %s {q} 2>/dev/null || wc -c < {q}"
        out = self.root_shell_text(cmd) if root else self.shell_text(cmd)
        m = re.search(r"(\d+)\s*$", out)
        return int(m.group(1)) if m else None


def _is_uid0(text: str) -> bool:
    return bool(re.search(r"uid=0\(", text))


def parse_getprop(text: str) -> Dict[str, str]:
    props: Dict[str, str] = {}
    for m in re.finditer(r"^\[([^\]]+)\]:\s*\[(.*?)\]\s*$", text, re.M):
        props[m.group(1)] = m.group(2)
    return props


# ---------------------------------------------------------------------- Fastboot


class FastbootDevice:
    def __init__(self, runner: Runner, serial: Optional[str] = None):
        self.runner = runner
        self.serial = serial

    def cmd(self, *args, timeout: Optional[float] = 600, check: bool = False, stream: bool = False) -> CmdResult:
        return self.runner.fastboot(*args, serial=self.serial, timeout=timeout, check=check, stream=stream)

    def getvar(self, name: str = "all") -> Dict[str, str]:
        res = self.cmd("getvar", name, timeout=60)
        return parse_getvar(res.combined)

    def flash(self, partition: str, image: str, slot: Optional[str] = None, **kw) -> CmdResult:
        args = []
        if slot:
            args += ["--slot", slot]
        args += ["flash", partition, image]
        return self.cmd(*args, timeout=kw.pop("timeout", 3600), check=kw.pop("check", True), **kw)

    def erase(self, partition: str, **kw) -> CmdResult:
        return self.cmd("erase", partition, check=True, **kw)

    def format_partition(self, partition: str, fs: Optional[str] = None, **kw) -> CmdResult:
        target = f"format:{fs}" if fs else "format"
        return self.cmd(target, partition, check=True, **kw)

    def boot(self, image: str, **kw) -> CmdResult:
        return self.cmd("boot", image, check=True, **kw)

    def reboot(self, target: Optional[str] = None) -> CmdResult:
        if target in (None, "system"):
            return self.cmd("reboot", timeout=60)
        if target == "bootloader":
            return self.cmd("reboot-bootloader", timeout=60)
        return self.cmd("reboot", target, timeout=60)

    def oem(self, *args, **kw) -> CmdResult:
        return self.cmd("oem", *args, **kw)

    def set_active(self, slot: str) -> CmdResult:
        return self.cmd("set_active", slot, check=True)

    def is_unlocked(self) -> Optional[bool]:
        vars_ = self.getvar("all")
        val = vars_.get("unlocked") or vars_.get("secure")
        if "unlocked" in vars_:
            return vars_["unlocked"].lower() == "yes"
        if "secure" in vars_:
            return vars_["secure"].lower() == "no"
        return None


def parse_getvar(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        if line.startswith("(bootloader)"):
            line = line[len("(bootloader)"):].strip()
        if not line or line.lower().startswith(("finished", "okay", "total time", "all:", "waiting for")):
            continue
        if ": " in line:
            k, v = line.split(": ", 1)
        elif line.endswith(":"):
            k, v = line[:-1], ""
        elif ":" in line:
            k, v = line.split(":", 1)
        else:
            continue
        k = k.strip()
        if k and " " not in k:
            out[k] = v.strip()
    return out


def wait_for_fastboot(runner: Runner, serial: Optional[str], timeout: float = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for d in list_devices(runner, adb=False, fastboot=True):
            if not serial or d.serial == serial:
                return True
        time.sleep(2)
    return False
