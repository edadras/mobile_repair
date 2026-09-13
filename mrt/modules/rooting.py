"""Rooting with Magisk: status, boot-image patching, flashing, unrooting.

The workflow mirrors what Magisk's own app does, but driven from the host so
it works on a phone that cannot install / run the app yet:

1. ``status``  - what the device offers: ABI, A/B slot, ``init_boot`` vs
   ``boot``, bootloader lock state, existing root, Magisk app.
2. ``patch``   - take the *stock* image of the target partition (from a file,
   from an OTA ``payload.bin``, or dumped from an already-rooted device), push
   Magisk's own ``magiskboot``/``magiskinit``/``boot_patch.sh`` (extracted from
   the Magisk APK you provide) to ``/data/local/tmp`` and run
   ``boot_patch.sh`` there exactly like the app does, then pull
   ``new-boot.img`` back. The stock image is kept next to it for un-rooting.
3. ``flash``   - reboot to the bootloader, verify it is unlocked, flash the
   patched image to ``boot``/``init_boot`` (or ``fastboot boot`` it for a
   one-time, temporary root) and reboot.
4. ``unroot``  - flash the saved stock image back.

Rooting needs an **unlocked bootloader** (``mrt fastboot unlock`` - wipes
data) and the stock image of the *installed* firmware build. Samsung devices
flash through Odin, not fastboot: ``patch`` still works there but the flash
step must be done with the Magisk app / Odin (AP tar).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.device import AdbDevice, FastbootDevice, list_devices, wait_for_fastboot
from ..core.errors import MrtError
from ..core.output import info, warn
from ..core.runner import Runner
from ..core.safety import CRITICAL, DESTRUCTIVE, Safety
from ..utils.hashing import sha256_file
from . import fastboot_ops
from . import partitions as part_mod

MAGISK_PKG = "com.topjohnwu.magisk"
REMOTE_DIR = "/data/local/tmp/mrt_magisk"
PATCHED_NAME = "new-boot.img"  # what boot_patch.sh writes
MANIFEST = "root-manifest.json"

# device ABI -> (64-bit lib dir, 32-bit lib dir)
ABI_DIRS = {
    "arm64-v8a": ("arm64-v8a", "armeabi-v7a"),
    "armeabi-v7a": (None, "armeabi-v7a"),
    "x86_64": ("x86_64", "x86"),
    "x86": (None, "x86"),
}
# lib<name>.so files boot_patch.sh expects next to it, renamed the way the app does
LIB_NAMES = ("magiskboot", "magiskinit", "magisk", "magisk64", "magisk32", "magiskpolicy", "init-ld", "busybox")
ASSETS = ("boot_patch.sh", "util_functions.sh", "stub.apk", "addon.d.sh")
REQUIRED = ("magiskboot", "magiskinit", "boot_patch.sh", "util_functions.sh")


# ------------------------------------------------------------------ status


def status(dev: AdbDevice) -> Dict[str, Any]:
    props = dev.getprops()
    abi = props.get("ro.product.cpu.abi", "")
    manuf = props.get("ro.product.manufacturer", "").lower()
    root_mode = dev.root_mode(try_adb_root=False)
    rep: Dict[str, Any] = {
        "serial": dev.serial,
        "model": props.get("ro.product.model", ""),
        "device": props.get("ro.product.device", ""),
        "android": props.get("ro.build.version.release", ""),
        "sdk": props.get("ro.build.version.sdk", ""),
        "abi": abi,
        "slot": props.get("ro.boot.slot_suffix", "") or None,
        "manufacturer": manuf,
        "root": root_mode or None,
        "bootloader": _lock_state(props),
        "oem_unlock_allowed": props.get("sys.oem_unlock_allowed") == "1" if "sys.oem_unlock_allowed" in props else None,
        "flash_method": "odin" if manuf == "samsung" else "fastboot",
    }
    rep["target_partition"] = detect_target(dev, "auto", quiet=True)
    app = dev.shell_text(f"pm path {MAGISK_PKG} 2>/dev/null")
    rep["magisk_app"] = bool(app.startswith("package:"))
    if root_mode in ("su", "su0"):
        rep["magisk_version"] = dev.root_shell_text("magisk -v 2>/dev/null") or None
    rep["ready_to_flash"] = rep["bootloader"] == "unlocked" and rep["flash_method"] == "fastboot"
    rep["notes"] = []
    if rep["bootloader"] == "locked":
        rep["notes"].append("bootloader is locked: enable OEM unlocking in Developer options, then 'mrt fastboot unlock' (erases all data)")
    if rep["flash_method"] == "odin":
        rep["notes"].append("Samsung: 'root patch' can produce the patched image, but flashing goes through Odin / the Magisk app (AP tar), not fastboot")
    if rep["root"]:
        rep["notes"].append(f"device already has root ({rep['root']}); 'root patch' can dump the stock image directly")
    return rep


def _lock_state(props: Dict[str, str]) -> str:
    vb = props.get("ro.boot.verifiedbootstate", "").lower()
    if vb == "orange":
        return "unlocked"
    if props.get("ro.boot.flash.locked") == "0" or props.get("ro.boot.vbmeta.device_state", "").lower() == "unlocked":
        return "unlocked"
    if props.get("ro.boot.flash.locked") == "1" or props.get("ro.boot.vbmeta.device_state", "").lower() == "locked" or vb in ("green", "yellow"):
        return "locked"
    return "unknown"


def detect_target(dev: AdbDevice, target: str = "auto", quiet: bool = False) -> str:
    """``init_boot`` (GKI devices with a separate ramdisk partition) or ``boot``."""
    if target != "auto":
        return target
    names = {p.name for p in part_mod.list_partitions(dev)}
    slot = dev.getprop("ro.boot.slot_suffix")
    if "init_boot" in names or (slot and "init_boot" + slot in names) or "init_boot_a" in names:
        return "init_boot"
    if "boot" in names or (slot and "boot" + slot in names) or "boot_a" in names:
        return "boot"
    if not quiet:
        warn("could not list partitions (no root?); assuming target 'boot'. Use --target init_boot on Android 13+ GKI devices.")
    return "boot"


# ------------------------------------------------------ magisk apk handling


def magisk_version(apk_path: str) -> Dict[str, Any]:
    with zipfile.ZipFile(apk_path) as zf:
        try:
            text = zf.read("assets/util_functions.sh").decode("utf-8", "replace")
        except KeyError:
            raise MrtError(f"{apk_path} is not a Magisk APK (no assets/util_functions.sh)")
    ver = re.search(r"MAGISK_VER=['\"]?([^'\"\n]+)", text)
    code = re.search(r"MAGISK_VER_CODE=(\d+)", text)
    return {"version": ver.group(1) if ver else None, "code": int(code.group(1)) if code else None}


def extract_magisk(apk_path: str, out_dir: str, abi: str) -> Dict[str, Any]:
    """Unpack the binaries and scripts ``boot_patch.sh`` needs, renamed as the
    Magisk app renames them (``lib/<abi>/libmagiskboot.so`` -> ``magiskboot``)."""
    if not os.path.isfile(apk_path):
        raise MrtError(f"Magisk APK not found: {apk_path}")
    if abi not in ABI_DIRS:
        raise MrtError(f"unsupported device ABI '{abi}'")
    dir64, dir32 = ABI_DIRS[abi]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    extracted: List[str] = []
    with zipfile.ZipFile(apk_path) as zf:
        names = set(zf.namelist())

        def take(member: str, dest: str) -> bool:
            if member not in names:
                return False
            (out / dest).write_bytes(zf.read(member))
            extracted.append(dest)
            return True

        primary = dir64 or dir32
        for lib in LIB_NAMES:
            take(f"lib/{primary}/lib{lib}.so", lib)
        if dir64 and dir32:
            # 64-bit devices also carry the 32-bit daemon for 32-bit apps
            if not take(f"lib/{dir32}/libmagisk32.so", "magisk32"):
                take(f"lib/{dir32}/libmagisk.so", "magisk32")
        for asset in ASSETS:
            take(f"assets/{asset}", asset)
    missing = [r for r in REQUIRED if r not in extracted]
    if missing or not any(x in extracted for x in ("magisk", "magisk64", "magisk32")):
        raise MrtError(f"{apk_path} does not look like a Magisk APK for {abi}: missing {', '.join(missing) or 'magisk daemon library'}")
    ver = magisk_version(apk_path)
    return {"dir": str(out), "files": extracted, "abi": primary, **ver}


# ------------------------------------------------------------- stock image


def obtain_stock_image(dev: Optional[AdbDevice], target: str, out_path: str, boot_image: Optional[str] = None,
                       payload: Optional[str] = None) -> Dict[str, Any]:
    """Get the stock ``target`` image into ``out_path`` from a file, an OTA payload,
    or (rooted device only) the partition itself."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    if boot_image:
        if not os.path.isfile(boot_image):
            raise MrtError(f"boot image not found: {boot_image}")
        shutil.copyfile(boot_image, out_path)
        return {"source": "file", "path": boot_image}
    if payload:
        from .flash import extract_payload
        with tempfile.TemporaryDirectory(prefix="mrt-payload-") as tmp:
            results = extract_payload(payload, tmp, only=[target])
            got = [r for r in results if r.get("name") == target or os.path.basename(str(r.get("file", ""))) == f"{target}.img"]
            candidate = os.path.join(tmp, f"{target}.img")
            if not os.path.isfile(candidate):
                raise MrtError(f"payload has no '{target}' partition (extracted: {[r.get('name') for r in results]})")
            shutil.copyfile(candidate, out_path)
        return {"source": "payload", "path": payload, "entries": len(got)}
    if dev is not None and dev.has_root():
        res = part_mod.dump_partition(dev, target, out_path, verify=True)
        return {"source": "device", "partition": res.get("partition"), "sha256": res.get("sha256")}
    raise MrtError(
        f"no stock {target} image: pass --boot-image FILE (from the firmware of the INSTALLED build) or "
        f"--payload OTA.zip/payload.bin; dumping from the device needs root, which this device has not got yet.")


# ------------------------------------------------------------------- patch


def patch(dev: AdbDevice, safety: Safety, magisk_apk: str, out_dir: str, boot_image: Optional[str] = None,
          payload: Optional[str] = None, target: str = "auto", keep_verity: bool = True,
          keep_forceencrypt: bool = True, patch_vbmeta: bool = False, recovery_mode: bool = False,
          install_app: bool = False) -> Dict[str, Any]:
    """Produce ``magisk_patched-<target>.img`` in ``out_dir`` plus the stock image and a manifest."""
    props = dev.getprops()
    abi = props.get("ro.product.cpu.abi", "")
    target = detect_target(dev, target)
    if boot_image and target == "boot" and "init_boot" in os.path.basename(boot_image).lower():
        target = "init_boot"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stock = out / f"stock-{target}.img"
    patched = out / f"magisk_patched-{target}.img"

    src = obtain_stock_image(dev, target, str(stock), boot_image=boot_image, payload=payload)
    with tempfile.TemporaryDirectory(prefix="mrt-magisk-") as tmp:
        magisk = extract_magisk(magisk_apk, tmp, abi)
        safety.confirm(
            f"Patch {target} with Magisk {magisk.get('version') or '?'} on {dev.serial or '(auto)'} ({props.get('ro.product.model', '')}, {abi})\n"
            f"  stock image: {stock} (from {src['source']})\n"
            f"  KEEPVERITY={str(keep_verity).lower()} KEEPFORCEENCRYPT={str(keep_forceencrypt).lower()} PATCHVBMETAFLAG={str(patch_vbmeta).lower()}\n"
            "Nothing is flashed in this step; the patched image is pulled to the host.",
            level=DESTRUCTIVE)
        info(f"pushing Magisk {magisk.get('version') or ''} ({len(magisk['files'])} files) to {REMOTE_DIR}")
        dev.shell(f"rm -rf {REMOTE_DIR}; mkdir -p {REMOTE_DIR}", timeout=30)
        dev.push(tmp + "/.", REMOTE_DIR)
        dev.push(str(stock), f"{REMOTE_DIR}/boot.img")
        env = (f"KEEPVERITY={str(keep_verity).lower()} KEEPFORCEENCRYPT={str(keep_forceencrypt).lower()} "
               f"PATCHVBMETAFLAG={str(patch_vbmeta).lower()} RECOVERYMODE={str(recovery_mode).lower()}")
        info("running boot_patch.sh on the device")
        res = dev.shell(f"cd {REMOTE_DIR} && chmod 755 * && {env} sh ./boot_patch.sh ./boot.img", timeout=600)
        log = res.combined
        exists = dev.shell_text(f"[ -f {REMOTE_DIR}/{PATCHED_NAME} ] && echo yes || echo no")
        if not res.ok or exists != "yes" or re.search(r"^! ", log, re.M):
            dev.shell(f"rm -rf {REMOTE_DIR}", timeout=30)
            raise MrtError(f"boot_patch.sh failed (rc={res.returncode}):\n{log[-1500:]}")
        dev.pull(f"{REMOTE_DIR}/{PATCHED_NAME}", str(patched))
        dev.shell(f"rm -rf {REMOTE_DIR}", timeout=30)
    if not patched.is_file() or patched.stat().st_size == 0:
        raise MrtError("patched image was not pulled back from the device")

    manifest: Dict[str, Any] = {
        "type": "mrt-root", "created": time.strftime("%Y-%m-%d %H:%M:%S"), "serial": dev.serial,
        "device": {k: props.get(k, "") for k in ("ro.product.device", "ro.product.model", "ro.build.fingerprint", "ro.boot.slot_suffix", "ro.product.cpu.abi")},
        "target": target, "stock_source": src,
        "stock_image": stock.name, "stock_sha256": sha256_file(str(stock)),
        "patched_image": patched.name, "patched_sha256": sha256_file(str(patched)),
        "magisk": {"apk": os.path.abspath(magisk_apk), "version": magisk.get("version"), "code": magisk.get("code")},
        "options": {"keep_verity": keep_verity, "keep_forceencrypt": keep_forceencrypt, "patch_vbmeta": patch_vbmeta, "recovery_mode": recovery_mode},
        "patch_log": log[-4000:],
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if install_app:
        r = dev.adb("install", "-r", magisk_apk, timeout=600)
        manifest["app_installed"] = r.ok
        if not r.ok:
            warn(f"Magisk app install failed: {r.combined[-300:]}")
    info(f"patched image: {patched}  (stock kept at {stock}); next: 'mrt root flash {out}'")
    return manifest


# ------------------------------------------------------------------- flash


def load_manifest(path: str) -> Dict[str, Any]:
    p = Path(path)
    man = p / MANIFEST if p.is_dir() else p
    if not man.is_file():
        raise MrtError(f"not an mrt root directory (missing {MANIFEST}): {path}")
    data = json.loads(man.read_text(encoding="utf-8"))
    data["_dir"] = str(man.parent)
    return data


def _resolve_image(source: str, want: str, target: Optional[str]) -> Dict[str, Any]:
    """``source`` is a root directory (manifest) or a bare image; ``want`` is 'patched' or 'stock'."""
    p = Path(source)
    if p.is_dir() or p.name == MANIFEST:
        man = load_manifest(source)
        img = os.path.join(man["_dir"], man[f"{want}_image"])
        if sha256_file(img) != man.get(f"{want}_sha256"):
            raise MrtError(f"{img} does not match the sha256 recorded in {MANIFEST}; refusing to flash a modified image")
        return {"image": img, "target": target or man["target"], "manifest": man}
    if not p.is_file():
        raise MrtError(f"no such image or root directory: {source}")
    if not target:
        target = "init_boot" if "init_boot" in p.name.lower() else "boot"
        warn(f"bare image given; assuming target partition '{target}' (use --target to override)")
    return {"image": str(p), "target": target, "manifest": None}


def _to_fastboot(runner: Runner, serial: Optional[str], wait: int = 180) -> FastbootDevice:
    devices = [d for d in list_devices(runner) if not serial or d.serial == serial]
    fb_devs = [d for d in devices if d.transport == "fastboot"]
    if fb_devs:
        return FastbootDevice(runner, fb_devs[0].serial)
    adb_devs = [d for d in devices if d.transport == "adb"]
    if not adb_devs and not runner.dry_run:
        raise MrtError("no device found over adb or fastboot")
    adb_serial = adb_devs[0].serial if adb_devs else serial
    dev = AdbDevice(runner, adb_serial)
    if dev.getprop("ro.product.manufacturer").lower() == "samsung":
        raise MrtError("Samsung devices have no fastboot: flash the patched image with Odin (put it in an AP tar) or use the Magisk app. 'root patch' output is still valid.")
    info("rebooting to bootloader...")
    dev.reboot("bootloader")
    if not wait_for_fastboot(runner, adb_serial, timeout=wait) and not runner.dry_run:
        raise MrtError("device did not show up in fastboot mode (check drivers / USB cable)")
    return FastbootDevice(runner, adb_serial)


def flash(runner: Runner, serial: Optional[str], source: str, safety: Safety, target: Optional[str] = None,
          temporary: bool = False, slot: Optional[str] = None, reboot: bool = True, wait: int = 180) -> Dict[str, Any]:
    """Flash (or temporarily boot) the patched image. ``source`` is the directory
    written by :func:`patch` or a bare image file."""
    sel = _resolve_image(source, "patched", target)
    image, target = sel["image"], sel["target"]
    fb = _to_fastboot(runner, serial, wait)
    unlocked = fb.is_unlocked()
    if unlocked is False:
        raise MrtError("bootloader is LOCKED: a patched boot image will not boot (and may not flash). "
                       "Run 'mrt fastboot unlock' first (this wipes user data), then retry.")
    if unlocked is None:
        warn("cannot read the bootloader lock state; continuing")
    product = fb.getvar("product").get("product")
    man = sel["manifest"]
    if man and product and man["device"].get("ro.product.device") and product.lower() not in (man["device"]["ro.product.device"].lower(), ""):
        safety.confirm(f"Patched image was made for '{man['device']['ro.product.device']}' but the connected bootloader reports product '{product}'.",
                       level=CRITICAL, token="MISMATCH")
    result: Dict[str, Any] = {"image": image, "target": target, "serial": fb.serial, "temporary": temporary}
    if temporary:
        result["output"] = fastboot_ops.boot_image(fb, image, safety)
        info("booted the patched image once; root is lost on the next reboot unless you 'Direct Install' from the Magisk app")
        return result
    result["flash"] = fastboot_ops.flash(fb, target, image, safety, slot=slot)
    if reboot:
        fb.reboot()
        result["rebooted"] = True
    info(f"{target} flashed; after boot open the Magisk app (install it if needed) to finish setup")
    return result


def install(runner: Runner, dev: AdbDevice, safety: Safety, magisk_apk: str, out_dir: str, **patch_kw) -> Dict[str, Any]:
    """``patch`` followed by ``flash`` in one go."""
    temporary = patch_kw.pop("temporary", False)
    slot = patch_kw.pop("slot", None)
    man = patch(dev, safety, magisk_apk, out_dir, **patch_kw)
    res = flash(runner, dev.serial, out_dir, safety, temporary=temporary, slot=slot)
    return {"patch": {k: man[k] for k in ("target", "patched_image", "stock_image", "magisk")}, "flash": res}


def unroot(runner: Runner, serial: Optional[str], source: str, safety: Safety, slot: Optional[str] = None,
           reboot: bool = True, wait: int = 180) -> Dict[str, Any]:
    """Flash the stock image saved by :func:`patch` back to the target partition."""
    sel = _resolve_image(source, "stock", None)
    fb = _to_fastboot(runner, serial, wait)
    result: Dict[str, Any] = {"image": sel["image"], "target": sel["target"], "serial": fb.serial}
    result["flash"] = fastboot_ops.flash(fb, sel["target"], sel["image"], safety, slot=slot)
    if reboot:
        fb.reboot()
        result["rebooted"] = True
    info(f"stock {sel['target']} restored; uninstall the Magisk app from the device if it is still there")
    return result
