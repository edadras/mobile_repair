"""Fastboot operations: getvar, flash, erase, boot, unlock/lock, slots, oem."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..core.device import FastbootDevice
from ..core.errors import CommandFailed, MrtError
from ..core.safety import CRITICAL, DESTRUCTIVE, Safety

CRITICAL_PARTITIONS = {
    "bootloader", "abl", "xbl", "xbl_config", "tz", "rpm", "hyp", "aboot", "sbl1", "sbl", "pbl", "uefi",
    "modem", "radio", "bluetooth", "dsp", "cmnlib", "cmnlib64", "keymaster", "devcfg", "aop", "qupfw",
    "uefisecapp", "imagefv", "featenabler", "multiimgoem", "preloader", "lk", "logo", "persist", "efs",
    "modemst1", "modemst2", "fsg", "fsc", "ssd", "nvram", "nvdata", "nvcfg", "proinfo", "frp", "config",
    "sec", "param", "cp", "up_param", "bota", "keystore",
}


def getvar(fb: FastbootDevice, name: str = "all") -> Dict[str, str]:
    return fb.getvar(name)


def flash(fb: FastbootDevice, partition: str, image: str, safety: Safety, slot: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    if not os.path.isfile(image):
        raise MrtError(f"image not found: {image}")
    size = os.path.getsize(image)
    base = partition[:-2] if partition.endswith(("_a", "_b")) else partition
    level = CRITICAL if base in CRITICAL_PARTITIONS else DESTRUCTIVE
    safety.confirm(
        f"fastboot flash {partition} <- {image} ({size} bytes){' slot=' + slot if slot else ''}\n"
        f"Device: {fb.serial or '(auto)'}\n"
        "Flashing the wrong image to this partition can permanently brick the device.",
        level=level,
        token=partition,
    )
    res = fb.flash(partition, image, slot=slot)
    return {"partition": partition, "image": image, "size": size, "output": res.combined}


def erase(fb: FastbootDevice, partition: str, safety: Safety) -> str:
    safety.confirm(f"fastboot erase {partition}\nAll data in this partition will be lost.", level=CRITICAL, token=partition)
    return fb.erase(partition).combined


def format_partition(fb: FastbootDevice, partition: str, safety: Safety, fs: Optional[str] = None) -> str:
    safety.confirm(f"fastboot format {partition}{' as ' + fs if fs else ''}\nAll data in this partition will be lost.", level=CRITICAL, token=partition)
    return fb.format_partition(partition, fs).combined


def boot_image(fb: FastbootDevice, image: str, safety: Safety) -> str:
    if not os.path.isfile(image):
        raise MrtError(f"image not found: {image}")
    safety.confirm(f"Temporarily boot {image} (nothing is written to flash).", level=DESTRUCTIVE)
    return fb.boot(image).combined


def reboot(fb: FastbootDevice, target: Optional[str] = None) -> str:
    return fb.reboot(target).combined


def unlock(fb: FastbootDevice, safety: Safety, method: str = "auto", critical: bool = False) -> Dict[str, Any]:
    safety.confirm(
        "Bootloader UNLOCK.\n"
        "- The device will FACTORY RESET (all user data erased).\n"
        "- OEM unlocking must be enabled in Developer Options.\n"
        "- Some brands (Xiaomi, Huawei, Samsung Knox) need vendor tools / wait time.\n"
        "- Confirm on the device screen when asked.",
        level=CRITICAL,
        token="UNLOCK",
    )
    attempts: List[List[str]] = []
    if method in ("auto", "flashing"):
        attempts.append(["flashing", "unlock_critical" if critical else "unlock"])
    if method in ("auto", "oem"):
        attempts.append(["oem", "unlock"])
    outputs = []
    for args in attempts:
        res = fb.cmd(*args, timeout=300)
        outputs.append({"cmd": " ".join(args), "rc": res.returncode, "output": res.combined})
        if res.ok:
            return {"ok": True, "attempts": outputs}
    return {"ok": False, "attempts": outputs}


def lock(fb: FastbootDevice, safety: Safety, method: str = "auto", critical: bool = False) -> Dict[str, Any]:
    safety.confirm(
        "Bootloader LOCK.\n"
        "- Locking with a NON-STOCK or corrupted ROM will hard-brick the device.\n"
        "- The device will factory reset.\n"
        "Only lock after the full stock firmware has been flashed and boots.",
        level=CRITICAL,
        token="LOCK",
    )
    attempts: List[List[str]] = []
    if method in ("auto", "flashing"):
        attempts.append(["flashing", "lock_critical" if critical else "lock"])
    if method in ("auto", "oem"):
        attempts.append(["oem", "lock"])
    outputs = []
    for args in attempts:
        res = fb.cmd(*args, timeout=300)
        outputs.append({"cmd": " ".join(args), "rc": res.returncode, "output": res.combined})
        if res.ok:
            return {"ok": True, "attempts": outputs}
    return {"ok": False, "attempts": outputs}


def set_active(fb: FastbootDevice, slot: str, safety: Safety) -> str:
    if slot not in ("a", "b"):
        raise MrtError("slot must be 'a' or 'b'")
    safety.confirm(f"Switch active slot to '{slot}'. If that slot has no valid OS the device will not boot.", level=DESTRUCTIVE)
    return fb.set_active(slot).combined


def oem(fb: FastbootDevice, args: List[str], safety: Safety) -> str:
    safety.confirm(f"fastboot oem {' '.join(args)}\nOEM commands are vendor specific and unvalidated.", level=DESTRUCTIVE)
    return fb.oem(*args, timeout=300).combined


def wipe_userdata(fb: FastbootDevice, safety: Safety) -> str:
    safety.confirm("Erase userdata + cache (factory reset via fastboot -w).", level=CRITICAL, token="WIPE")
    return fb.cmd("-w", timeout=600).combined


def continue_boot(fb: FastbootDevice) -> str:
    return fb.cmd("continue", timeout=60).combined


def raw_command(fb: FastbootDevice, args: List[str], safety: Safety) -> str:
    safety.confirm(f"Raw fastboot command: fastboot {' '.join(args)}", level=DESTRUCTIVE)
    return fb.cmd(*args, timeout=600).combined
