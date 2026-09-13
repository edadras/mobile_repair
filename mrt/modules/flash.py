"""ROM flashing: fastboot image directories, payload.bin, update zips, sideload."""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.device import FastbootDevice
from ..core.errors import MrtError
from ..core.output import Progress, human_size, info, warn
from ..core.safety import CRITICAL, DESTRUCTIVE, Safety
from ..utils import payload as payload_mod
from . import fastboot_ops

# Flash order matters: firmware first, then boot chain, then dynamic partitions.
FLASH_ORDER = [
    "bootloader", "abl", "xbl", "xbl_config", "aop", "tz", "hyp", "devcfg", "keymaster", "cmnlib", "cmnlib64",
    "qupfw", "uefisecapp", "imagefv", "featenabler", "multiimgoem", "storsec", "bluetooth", "dsp", "modem", "radio",
    "vbmeta", "vbmeta_system", "vbmeta_vendor", "boot", "init_boot", "vendor_boot", "dtbo", "recovery", "vendor_dlkm", "system_dlkm",
    "super", "system", "system_ext", "vendor", "product", "odm", "odm_dlkm", "cust", "metadata", "userdata", "cache", "persist",
]
FASTBOOTD_ONLY = {"super", "system", "system_ext", "vendor", "product", "odm", "vendor_dlkm", "system_dlkm", "odm_dlkm", "cust"}
NEVER_AUTO = {"persist", "userdata", "metadata", "efs", "modemst1", "modemst2", "fsg", "nvram", "nvdata", "frp", "config", "sec", "cache"}


def discover_images(directory: str) -> Dict[str, str]:
    """Map partition name -> image path for every ``*.img`` in a directory."""
    found: Dict[str, str] = {}
    for f in sorted(os.listdir(directory)):
        if f.lower().endswith(".img"):
            found[f[:-4]] = os.path.join(directory, f)
    return found


def plan_flash(images: Dict[str, str], only: Optional[List[str]] = None, skip: Optional[List[str]] = None, include_data: bool = False) -> List[str]:
    names = set(images)
    if only:
        names &= set(only)
    if skip:
        names -= set(skip)
    if not include_data:
        names -= NEVER_AUTO
    ordered = [n for n in FLASH_ORDER if n in names]
    rest = sorted(names - set(ordered))
    return ordered + rest


def flash_directory(fb: FastbootDevice, directory: str, safety: Safety, slot: Optional[str] = None, only: Optional[List[str]] = None,
                    skip: Optional[List[str]] = None, wipe: bool = False, include_data: bool = False, reboot: bool = False,
                    disable_verity: bool = False, disable_verification: bool = False) -> Dict[str, Any]:
    if not os.path.isdir(directory):
        raise MrtError(f"not a directory: {directory}")
    images = discover_images(directory)
    if not images:
        raise MrtError(f"no .img files found in {directory}")
    plan = plan_flash(images, only, skip, include_data)
    if not plan:
        raise MrtError("nothing to flash after applying filters")
    total = sum(os.path.getsize(images[n]) for n in plan)
    listing = "\n".join(f"  {n:<16} {human_size(os.path.getsize(images[n])):>12}  {images[n]}" for n in plan)
    safety.confirm(
        f"FLASH ROM from {directory} ({human_size(total)}):\n{listing}\n"
        f"slot={slot or 'current'} wipe={'YES' if wipe else 'no'}\n"
        "Make sure the images match this exact device model and the bootloader is unlocked.",
        level=CRITICAL,
        token="FLASH",
    )
    vars_ = fb.getvar("all")
    userspace = vars_.get("is-userspace", "no").lower() == "yes"
    results: List[Dict[str, Any]] = []
    for name in plan:
        needs_fbd = name in FASTBOOTD_ONLY and vars_.get("dynamic-partition", vars_.get("super-partition-name", "")) and name != "super"
        if needs_fbd and not userspace:
            info("switching to fastbootd for dynamic partition " + name)
            fb.cmd("reboot", "fastboot", timeout=120)
            from ..core.device import wait_for_fastboot
            wait_for_fastboot(fb.runner, fb.serial, 180)
            userspace = True
        extra = []
        if name.startswith("vbmeta"):
            if disable_verity:
                extra.append("--disable-verity")
            if disable_verification:
                extra.append("--disable-verification")
        args = list(extra)
        if slot:
            args += ["--slot", slot]
        args += ["flash", name, images[name]]
        info(f"flashing {name} ({human_size(os.path.getsize(images[name]))})")
        res = fb.cmd(*args, timeout=3600)
        results.append({"partition": name, "ok": res.ok, "output": res.combined[-400:]})
        if not res.ok:
            raise MrtError(f"flashing {name} failed:\n{res.combined[-800:]}\nCompleted: {[r['partition'] for r in results if r['ok']]}")
        if name in ("bootloader", "radio", "modem", "abl", "xbl"):
            info("rebooting bootloader after firmware partition")
            fb.cmd("reboot-bootloader", timeout=120)
            from ..core.device import wait_for_fastboot
            wait_for_fastboot(fb.runner, fb.serial, 180)
    if wipe:
        info("wiping userdata (fastboot -w)")
        fb.cmd("-w", timeout=600)
    if reboot:
        fb.reboot()
    return {"flashed": results, "wiped": wipe}


def flash_payload(fb: FastbootDevice, payload_path: str, workdir: str, safety: Safety, **kw) -> Dict[str, Any]:
    extracted = extract_payload(payload_path, workdir, kw.pop("only", None))
    result = flash_directory(fb, workdir, safety, only=[e["partition"] for e in extracted] if extracted else None, **kw)
    result["extracted"] = extracted
    return result


def extract_payload(source: str, out_dir: str, only: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Extract partition images from payload.bin (or an OTA zip containing one)."""
    payload_path = source
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as zf:
            if "payload.bin" not in zf.namelist():
                raise MrtError(f"{source} has no payload.bin (not an A/B OTA zip)")
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            info("extracting payload.bin from zip...")
            payload_path = zf.extract("payload.bin", out_dir)
    payload = payload_mod.parse_payload(payload_path)
    info(f"payload v{payload.version}: {len(payload.partitions)} partitions, block size {payload.block_size}")

    def factory(part):
        return Progress(part.size * payload.block_size, label=f"  {part.name}")

    results = payload_mod.extract_all(payload, out_dir, only=only, progress_factory=factory)
    Progress().finish()
    return results


def payload_info(source: str) -> Dict[str, Any]:
    path = source
    tmp = None
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as zf:
            if "payload.bin" not in zf.namelist():
                raise MrtError("no payload.bin in zip")
            import tempfile
            tmp = tempfile.mkdtemp(prefix="mrt-payload-")
            path = zf.extract("payload.bin", tmp)
    p = payload_mod.parse_payload(path)
    return {
        "version": p.version,
        "block_size": p.block_size,
        "partitions": [{"name": x.name, "size": x.size * p.block_size, "operations": len(x.operations), "full": x.is_full()} for x in p.partitions],
    }


def fastboot_update_zip(fb: FastbootDevice, zip_path: str, safety: Safety, wipe: bool = False) -> str:
    if not os.path.isfile(zip_path):
        raise MrtError(f"zip not found: {zip_path}")
    safety.confirm(f"fastboot update {zip_path}{' with wipe' if wipe else ''} (factory image zip)", level=CRITICAL, token="FLASH")
    args = (["-w"] if wipe else []) + ["update", zip_path]
    return fb.cmd(*args, timeout=7200).combined


def inspect_rom(path: str) -> Dict[str, Any]:
    """Tell the operator what kind of ROM package this is and how to flash it."""
    p = Path(path)
    if p.is_dir():
        images = discover_images(str(p))
        kind = "fastboot image directory"
        scripts = [f for f in os.listdir(p) if f.startswith("flash") and f.endswith((".sh", ".bat"))]
        return {"type": kind, "images": images, "scripts": scripts, "how": "mrt rom flash-dir " + str(p)}
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        if "payload.bin" in names:
            return {"type": "A/B OTA zip (payload.bin)", "files": len(names), "how": f"mrt rom payload {path}  (or: mrt recovery sideload {path})"}
        if "META-INF/com/google/android/update-binary" in names:
            return {"type": "recovery flashable zip (update-binary)", "files": len(names), "how": f"mrt recovery sideload {path}"}
        if any(n.endswith("android-info.txt") for n in names):
            return {"type": "fastboot factory zip", "files": len(names), "how": f"mrt rom update-zip {path}"}
        imgs = [n for n in names if n.endswith(".img")]
        return {"type": "zip with raw images" if imgs else "unknown zip", "images": imgs, "files": len(names), "how": "unzip and use mrt rom flash-dir"}
    if p.suffix.lower() == ".bin":
        try:
            return {"type": "payload.bin", **payload_info(path), "how": f"mrt rom payload {path}"}
        except Exception as exc:
            return {"type": "unknown .bin", "error": str(exc)}
    if p.suffix.lower() == ".img":
        return {"type": "raw image", "size": p.stat().st_size, "how": "mrt fastboot flash <partition> " + path + "  or  mrt part write <partition> " + path}
    if p.suffix.lower() in (".tar", ".md5"):
        return {"type": "Samsung Odin package", "how": "use Odin / Heimdall (mrt does not implement the Odin protocol)"}
    return {"type": "unknown"}
