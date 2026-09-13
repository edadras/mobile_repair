"""Recovery and boot-mode helpers."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from ..core.device import AdbDevice, FastbootDevice, list_devices, wait_for_fastboot
from ..core.errors import MrtError
from ..core.output import info, warn
from ..core.runner import Runner
from ..core.safety import CRITICAL, DESTRUCTIVE, Safety

REBOOT_TARGETS = ("system", "recovery", "bootloader", "fastboot", "fastbootd", "sideload", "sideload-auto-reboot", "edl", "download", "safemode")


def reboot(runner: Runner, serial: Optional[str], target: str) -> str:
    """Reboot from whichever mode the device is currently in."""
    devices = [d for d in list_devices(runner) if not serial or d.serial == serial]
    if not devices and not runner.dry_run:
        raise MrtError("no device found")
    dev = devices[0] if devices else None
    if dev and dev.transport == "fastboot":
        fb = FastbootDevice(runner, dev.serial)
        if target == "system":
            return fb.reboot().combined
        if target in ("fastboot", "fastbootd"):
            return fb.cmd("reboot", "fastboot", timeout=60).combined
        if target == "edl":
            return fb.cmd("oem", "edl", timeout=60).combined
        return fb.reboot(target).combined
    adb = AdbDevice(runner, dev.serial if dev else serial)
    if target == "system":
        return adb.reboot().combined
    if target == "fastbootd":
        return adb.reboot("fastboot").combined
    if target == "safemode":
        res = adb.shell("setprop persist.sys.safemode 1 && reboot", timeout=30)
        return res.combined
    return adb.reboot(target).combined


def wait_for(runner: Runner, serial: Optional[str], mode: str, timeout: int = 180) -> bool:
    """Wait until the device shows up in the given mode (device/recovery/sideload/fastboot)."""
    if mode in ("fastboot", "bootloader", "fastbootd"):
        return wait_for_fastboot(runner, serial, timeout)
    adb = AdbDevice(runner, serial)
    return adb.wait(mode, timeout=timeout)


def sideload(runner: Runner, serial: Optional[str], zip_path: str, safety: Safety, auto_reboot: bool = True) -> str:
    if not os.path.isfile(zip_path):
        raise MrtError(f"zip not found: {zip_path}")
    safety.confirm(f"Sideload {zip_path} through recovery.", level=DESTRUCTIVE)
    adb = AdbDevice(runner, serial)
    state = adb.state()
    if state != "sideload":
        info(f"device state is '{state}', rebooting into sideload mode")
        target = "sideload-auto-reboot" if auto_reboot else "sideload"
        adb.reboot(target)
        if not adb.wait("sideload", timeout=240):
            warn("device did not enter sideload automatically; in recovery choose 'Apply update from ADB' (stock) or Advanced > ADB Sideload (TWRP)")
            adb.wait("sideload", timeout=900)
    info("sideloading... (progress is printed by adb)")
    res = adb.adb("sideload", zip_path, stream=True, timeout=7200)
    return "sideload finished" if res.ok else f"sideload exited with rc={res.returncode}"


def wipe(runner: Runner, serial: Optional[str], what: str, safety: Safety) -> str:
    """Wipe cache / data / dalvik from recovery (TWRP) or system."""
    safety.confirm(f"Wipe {what}. User data may be lost.", level=CRITICAL, token="WIPE")
    adb = AdbDevice(runner, serial)
    state = adb.state()
    if state == "recovery":
        twrp = adb.shell("twrp wipe " + what, timeout=600)
        if twrp.ok and "not found" not in twrp.combined and "inaccessible" not in twrp.combined:
            return twrp.combined or f"twrp wipe {what} done"
        if what in ("data", "cache"):
            res = adb.shell(f"recovery --wipe_{what}", timeout=600)
            return res.combined
        raise MrtError(f"cannot wipe {what} in this recovery: {twrp.combined[-300:]}")
    if state == "device":
        if what == "data":
            return factory_reset(runner, serial, safety, confirmed=True)
        if what == "cache":
            adb.ensure_root()
            return adb.root_shell("rm -rf /cache/* /data/dalvik-cache/* 2>&1; echo done", timeout=300).combined
    raise MrtError(f"device must be in system or recovery mode (state={state})")


def factory_reset(runner: Runner, serial: Optional[str], safety: Safety, confirmed: bool = False) -> str:
    if not confirmed:
        safety.confirm("FACTORY RESET - all user data will be erased.", level=CRITICAL, token="RESET")
    adb = AdbDevice(runner, serial)
    state = adb.state()
    if state == "recovery":
        res = adb.shell("recovery --wipe_data", timeout=600)
        return res.combined or "wipe_data requested"
    if adb.has_root():
        res = adb.root_shell("echo '--wipe_data' > /cache/recovery/command && reboot recovery", timeout=60)
        if res.ok:
            return "rebooting to recovery to wipe data"
    res = adb.shell("am broadcast -a android.intent.action.MASTER_CLEAR -n android/com.android.server.MasterClearReceiver", timeout=60)
    if "Broadcast completed" in res.combined:
        return "MASTER_CLEAR broadcast sent"
    res = adb.shell("am broadcast -a android.intent.action.FACTORY_RESET -n android/com.android.server.MasterClearReceiver", timeout=60)
    return res.combined or "factory reset requested (device may need root)"


def flash_recovery(fb: FastbootDevice, image: str, safety: Safety, slot: Optional[str] = None, boot_after: bool = False) -> str:
    if not os.path.isfile(image):
        raise MrtError(f"image not found: {image}")
    safety.confirm(f"Flash custom recovery {image} to the recovery partition.", level=DESTRUCTIVE)
    out = fb.flash("recovery", image, slot=slot).combined
    if boot_after:
        out += "\n" + fb.cmd("reboot", "recovery", timeout=60).combined
    return out


def push_files(runner: Runner, serial: Optional[str], files, remote_dir: str = "/sdcard/") -> Dict[str, str]:
    """Push files (e.g. ROM zips, Magisk) to the device, works in TWRP too."""
    adb = AdbDevice(runner, serial)
    result = {}
    for f in files:
        if not os.path.isfile(f):
            raise MrtError(f"file not found: {f}")
        res = adb.push(f, remote_dir)
        result[f] = res.combined.split("\n")[-1] if res.combined else "ok"
    return result


def twrp_command(runner: Runner, serial: Optional[str], args) -> str:
    adb = AdbDevice(runner, serial)
    return adb.shell("twrp " + " ".join(args), timeout=3600).combined


def mode_status(runner: Runner, serial: Optional[str]) -> Dict[str, Any]:
    devices = [d.as_dict() for d in list_devices(runner) if not serial or d.serial == serial]
    return {"devices": devices}
