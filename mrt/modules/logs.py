"""Log collection: logcat, kernel log, bugreport, dumpsys, ANR/tombstones, pstore."""

from __future__ import annotations

import os
import shlex
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.device import AdbDevice
from ..core.errors import MrtError
from ..core.output import info, warn


def logcat(dev: AdbDevice, out_file: Optional[str] = None, buffers: str = "all", follow: bool = False, filters: Optional[List[str]] = None,
           clear: bool = False, since_boot: bool = False, timeout: int = 3600) -> Optional[str]:
    if clear:
        dev.adb("logcat", "-b", buffers, "-c", timeout=30)
    args = ["logcat", "-b", buffers, "-v", "threadtime"]
    if not follow:
        args.append("-d")
    if filters:
        args += filters
    if follow:
        info("streaming logcat, Ctrl-C to stop")
        dev.adb(*args, stream=True, timeout=timeout * 24)
        return None
    if out_file:
        Path(out_file).parent.mkdir(parents=True, exist_ok=True)
        res = dev.adb(*args, stdout_file=out_file, timeout=timeout)
        return out_file
    res = dev.adb(*args, timeout=timeout)
    return res.text


def dmesg(dev: AdbDevice, out_file: Optional[str] = None) -> str:
    cmd = "dmesg 2>/dev/null || cat /proc/kmsg 2>/dev/null | head -5000"
    text = dev.root_shell_text(cmd, timeout=120) if dev.has_root() else dev.shell_text(cmd, timeout=120)
    if not text:
        warn("kernel log is empty (root usually required)")
    if out_file:
        Path(out_file).write_text(text, encoding="utf-8")
    return text


def last_kmsg(dev: AdbDevice, out_dir: str) -> List[str]:
    """Pull crash-time kernel logs (pstore / last_kmsg) - key for boot-loop diagnosis."""
    dev.ensure_root()
    candidates = ["/sys/fs/pstore/console-ramoops", "/sys/fs/pstore/console-ramoops-0", "/sys/fs/pstore/dmesg-ramoops-0",
                  "/sys/fs/pstore/pmsg-ramoops-0", "/proc/last_kmsg", "/sys/fs/pstore/ftrace-ramoops-0"]
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    saved = []
    for path in candidates:
        if dev.file_exists(path, root=True):
            local = Path(out_dir) / os.path.basename(path).replace("/", "_")
            dev.root_exec_out(f"cat {shlex.quote(path)}", str(local), timeout=300)
            if local.exists() and local.stat().st_size:
                saved.append(str(local))
    if not saved:
        warn("no pstore/last_kmsg data found on this device")
    return saved


def bugreport(dev: AdbDevice, out_path: str) -> str:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    info("collecting bugreport (takes 1-3 minutes)...")
    res = dev.adb("bugreport", out_path, timeout=900)
    if not res.ok:
        raise MrtError(f"bugreport failed: {res.combined[-400:]}")
    return out_path


def dumpsys(dev: AdbDevice, service: Optional[str] = None, args: Optional[List[str]] = None, out_file: Optional[str] = None) -> str:
    cmd = "dumpsys" + (f" {shlex.quote(service)}" if service else "") + ((" " + " ".join(shlex.quote(a) for a in args)) if args else "")
    text = dev.shell_text(cmd, timeout=600)
    if out_file:
        Path(out_file).write_text(text, encoding="utf-8")
    return text


def crash_dumps(dev: AdbDevice, out_dir: str) -> Dict[str, Any]:
    """Pull ANR traces, tombstones and dropbox entries."""
    dev.ensure_root()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Any] = {}
    for name, remote in (("anr", "/data/anr"), ("tombstones", "/data/tombstones"), ("dropbox", "/data/system/dropbox")):
        listing = dev.root_shell_text(f"ls -1 {remote} 2>/dev/null | head -200")
        files = [f for f in listing.split("\n") if f.strip()]
        result[name] = {"count": len(files)}
        if not files:
            continue
        target = out / name
        target.mkdir(exist_ok=True)
        tar_remote = f"/data/local/tmp/mrt_{name}.tar"
        dev.root_shell(f"tar -cf {tar_remote} -C {remote} . 2>/dev/null && chmod 644 {tar_remote}", timeout=300)
        dev.pull(tar_remote, str(target / f"{name}.tar"), check=False)
        dev.root_shell(f"rm -f {tar_remote}", timeout=30)
        result[name]["archive"] = str(target / f"{name}.tar")
    return result


def collect_all(dev: AdbDevice, out_dir: str) -> Dict[str, Any]:
    """One-shot diagnostic bundle."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Any] = {"dir": str(out)}
    result["logcat"] = logcat(dev, str(out / "logcat.txt"))
    result["dmesg"] = str(out / "dmesg.txt")
    dmesg(dev, result["dmesg"])
    for svc in ("battery", "batterystats", "meminfo", "cpuinfo", "activity", "package", "window", "power", "thermalservice", "telephony.registry", "wifi", "connectivity", "usb", "diskstats"):
        try:
            dumpsys(dev, svc, out_file=str(out / f"dumpsys_{svc}.txt"))
        except MrtError as exc:
            warn(f"dumpsys {svc}: {exc}")
    result["getprop"] = str(out / "getprop.txt")
    Path(result["getprop"]).write_text(dev.getprop(), encoding="utf-8")
    if dev.has_root():
        result["pstore"] = last_kmsg(dev, str(out / "pstore"))
        result["crashes"] = crash_dumps(dev, str(out / "crashes"))
    else:
        warn("no root: skipping pstore and crash dumps")
    try:
        result["bugreport"] = bugreport(dev, str(out / "bugreport.zip"))
    except MrtError as exc:
        warn(str(exc))
    return result


def battery_history(dev: AdbDevice) -> str:
    return dev.shell_text("dumpsys batterystats --charged | head -200", timeout=120)


def boot_reason(dev: AdbDevice) -> Dict[str, str]:
    return {
        "bootreason": dev.getprop("ro.boot.bootreason"),
        "shutdown_reason": dev.getprop("sys.shutdown.requested"),
        "last_boot_completed": dev.getprop("sys.boot_completed"),
        "uptime": dev.shell_text("uptime"),
        "boot_count": dev.shell_text("settings get global boot_count 2>/dev/null"),
    }
