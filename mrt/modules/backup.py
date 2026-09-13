"""Full-device backup and restore."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.device import AdbDevice
from ..core.errors import MrtError
from ..core.output import info, warn
from ..core.safety import CRITICAL, DESTRUCTIVE, Safety
from . import apps as apps_mod
from . import info as info_mod
from . import partitions as part_mod

DEFAULT_PARTITIONS = ["boot", "recovery", "dtbo", "vbmeta", "vbmeta_system", "vendor_boot", "init_boot", "persist", "efs", "modemst1", "modemst2", "fsg", "fsc", "nvram", "nvdata", "nvcfg", "proinfo", "sec_efs", "frp", "config", "misc", "modem", "bluetooth", "dsp", "abl", "xbl", "xbl_config"]


def create(dev: AdbDevice, out_dir: str, safety: Safety, apps: bool = True, app_data: bool = False, sdcard: bool = False,
           partitions: Optional[List[str]] = None, all_partitions: bool = False, include_userdata: bool = False,
           sdcard_path: str = "/sdcard", only_third_party: bool = True) -> Dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest: Dict[str, Any] = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "serial": dev.serial, "sections": {}}

    info("collecting device info")
    device_info = info_mod.collect_adb(dev, full=True)
    (out / "device-info.json").write_text(json.dumps(device_info, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    manifest["device"] = device_info.get("identity", {})

    if apps:
        info("backing up APKs")
        pkgs = apps_mod.list_packages(dev, "third" if only_third_party else "all", versions=True)
        apk_dir = out / "apps"
        saved = []
        for row in pkgs:
            try:
                files = apps_mod.pull_apk(dev, row["package"], str(apk_dir))
                saved.append({"package": row["package"], "versionCode": row["versionCode"], "files": files})
            except MrtError as exc:
                warn(f"{row['package']}: {exc}")
        (out / "apps.json").write_text(json.dumps(saved, indent=2), encoding="utf-8")
        manifest["sections"]["apps"] = {"count": len(saved), "dir": str(apk_dir)}
        manifest["sections"]["package_list"] = {
            "all": [r["package"] for r in apps_mod.list_packages(dev, "all")],
            "disabled": [r["package"] for r in apps_mod.list_packages(dev, "disabled")],
        }

    if app_data:
        info("running adb backup (confirm on the device screen)")
        ab = out / "appdata.ab"
        res = dev.adb("backup", "-f", str(ab), "-all", "-apk", "-shared", "-system", timeout=7200)
        manifest["sections"]["adb_backup"] = {"file": str(ab), "ok": res.ok and ab.exists() and ab.stat().st_size > 0}

    if sdcard:
        info(f"pulling {sdcard_path} (this can take a long time)")
        sd_dir = out / "sdcard"
        sd_dir.mkdir(exist_ok=True)
        res = dev.pull(sdcard_path, str(sd_dir), timeout=24 * 3600, check=False)
        manifest["sections"]["sdcard"] = {"dir": str(sd_dir), "ok": res.ok, "summary": res.combined.split("\n")[-1] if res.combined else ""}

    if partitions or all_partitions:
        if not dev.has_root():
            warn("no root: skipping partition images")
            manifest["sections"]["partitions"] = {"skipped": "no root"}
        else:
            info("dumping partitions")
            part_dir = out / "partitions"
            if all_partitions:
                pm = part_mod.dump_all(dev, str(part_dir), skip_data=False, exclude=None if include_userdata else ["userdata", "sdcard", "swap"])
            else:
                pm = part_mod.dump_all(dev, str(part_dir), include=partitions, skip_data=False)
            manifest["sections"]["partitions"] = {"dir": str(part_dir), "count": len([p for p in pm["partitions"] if p.get("status") == "ok"]), "failed": [p["partition"] for p in pm["partitions"] if p.get("status") != "ok"]}

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    info(f"backup written to {out}")
    return manifest


def restore(dev: AdbDevice, backup_dir: str, safety: Safety, apps: bool = True, app_data: bool = False, sdcard: bool = False,
            partitions: Optional[List[str]] = None, sdcard_path: str = "/sdcard") -> Dict[str, Any]:
    src = Path(backup_dir)
    manifest_path = src / "manifest.json"
    if not manifest_path.exists():
        raise MrtError(f"not a mrt backup directory (missing manifest.json): {backup_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result: Dict[str, Any] = {"restored": {}}
    current_model = dev.getprop("ro.product.device")
    backup_model = (manifest.get("device") or {}).get("device")
    if backup_model and current_model and backup_model != current_model:
        safety.confirm(f"Backup was made on '{backup_model}' but the connected device is '{current_model}'.", level=CRITICAL, token="MISMATCH")

    if apps and (src / "apps.json").exists():
        entries = json.loads((src / "apps.json").read_text(encoding="utf-8"))
        safety.confirm(f"Install {len(entries)} app(s) from backup", level=DESTRUCTIVE)
        ok, failed = [], []
        for entry in entries:
            files = [f for f in entry["files"] if os.path.isfile(f)]
            if not files:
                failed.append(entry["package"])
                continue
            try:
                apps_mod.install(dev, files, reinstall=True, downgrade=True)
                ok.append(entry["package"])
            except MrtError as exc:
                warn(f"{entry['package']}: {exc}")
                failed.append(entry["package"])
        result["restored"]["apps"] = {"ok": ok, "failed": failed}

    if app_data and (src / "appdata.ab").exists():
        safety.confirm("Run adb restore of appdata.ab (confirm on device)", level=DESTRUCTIVE)
        res = dev.adb("restore", str(src / "appdata.ab"), timeout=7200)
        result["restored"]["adb_restore"] = res.ok

    if sdcard and (src / "sdcard").is_dir():
        safety.confirm(f"Push backed-up sdcard content to {sdcard_path}", level=DESTRUCTIVE)
        res = dev.adb("push", str(src / "sdcard") + "/.", sdcard_path, timeout=24 * 3600)
        result["restored"]["sdcard"] = res.ok

    if partitions:
        part_dir = src / "partitions"
        done = {}
        for name in partitions:
            img = part_dir / f"{name}.img"
            if not img.exists():
                warn(f"no image for partition {name} in backup")
                done[name] = "missing"
                continue
            done[name] = part_mod.write_partition(dev, name, str(img), safety)
        result["restored"]["partitions"] = done
    return result


def verify(backup_dir: str) -> Dict[str, Any]:
    """Re-hash partition images against the manifest recorded at dump time."""
    from ..utils.hashing import sha256_file

    src = Path(backup_dir) / "partitions" / "manifest.json"
    if not src.exists():
        raise MrtError("no partitions/manifest.json in backup")
    manifest = json.loads(src.read_text(encoding="utf-8"))
    report = {}
    for entry in manifest.get("partitions", []):
        f = entry.get("file")
        if entry.get("status") != "ok" or not f:
            report[entry.get("partition")] = "skipped"
            continue
        if not os.path.isfile(f):
            report[entry["partition"]] = "missing"
            continue
        report[entry["partition"]] = "ok" if sha256_file(f) == entry.get("sha256") else "CORRUPT"
    return report
