"""Application management via package manager."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.device import AdbDevice
from ..core.errors import MrtError
from ..core.output import info, warn
from ..core.safety import DESTRUCTIVE, Safety


def list_packages(dev: AdbDevice, filter_: str = "all", user: Optional[int] = None, versions: bool = False, pattern: Optional[str] = None) -> List[Dict[str, Any]]:
    flags = {"all": "", "third": "-3", "system": "-s", "disabled": "-d", "enabled": "-e", "uninstalled": "-u"}[filter_]
    cmd = f"pm list packages -f {flags}"
    if versions:
        cmd += " --show-versioncode"
    if user is not None:
        cmd += f" --user {user}"
    out = dev.shell_text(cmd, timeout=120)
    rows = []
    for line in out.split("\n"):
        line = line.strip()
        if not line.startswith("package:"):
            continue
        line = line[len("package:"):]
        version = None
        m = re.search(r"\s+versionCode:(\d+)$", line)
        if m:
            version = int(m.group(1))
            line = line[:m.start()]
        path, _, pkg = line.rpartition("=")
        if pattern and pattern.lower() not in pkg.lower():
            continue
        rows.append({"package": pkg, "path": path, "versionCode": version})
    return sorted(rows, key=lambda r: r["package"])


def package_info(dev: AdbDevice, pkg: str) -> Dict[str, Any]:
    out = dev.shell_text(f"dumpsys package {shlex.quote(pkg)}", timeout=120)
    if "Unable to find package" in out or not out:
        raise MrtError(f"package {pkg} not found")
    fields = {}
    for key in ("versionName", "versionCode", "userId", "firstInstallTime", "lastUpdateTime", "installerPackageName", "targetSdk", "minSdk", "codePath", "dataDir", "primaryCpuAbi", "flags", "pkgFlags"):
        m = re.search(rf"^\s*{key}=(.+?)\s*$", out, re.M)
        if m:
            fields[key] = m.group(1).strip()
    enabled = re.search(r"enabled=(\d)", out)
    fields["enabled_state"] = {"0": "default", "1": "enabled", "2": "disabled", "3": "disabled-user", "4": "disabled-until-used"}.get(enabled.group(1), enabled.group(1)) if enabled else "?"
    perms = re.findall(r"^\s+(android\.permission\.[A-Z_.]+): granted=(true|false)", out, re.M)
    fields["runtime_permissions"] = {p: g == "true" for p, g in perms}
    fields["apk_paths"] = apk_paths(dev, pkg)
    return {"package": pkg, **fields}


def apk_paths(dev: AdbDevice, pkg: str) -> List[str]:
    out = dev.shell_text(f"pm path {shlex.quote(pkg)}", timeout=60)
    return [ln[len("package:"):].strip() for ln in out.split("\n") if ln.startswith("package:")]


def install(dev: AdbDevice, files: List[str], reinstall: bool = True, downgrade: bool = False, grant: bool = True, test: bool = False, user: Optional[int] = None) -> str:
    for f in files:
        if not os.path.isfile(f):
            raise MrtError(f"file not found: {f}")
    args = ["install-multiple" if len(files) > 1 else "install"]
    if reinstall:
        args.append("-r")
    if downgrade:
        args.append("-d")
    if grant:
        args.append("-g")
    if test:
        args.append("-t")
    if user is not None:
        args += ["--user", str(user)]
    res = dev.adb(*args, *files, timeout=1800)
    if not res.ok or "Failure" in res.combined:
        raise MrtError(f"install failed: {res.combined[-800:]}")
    return res.combined


def uninstall(dev: AdbDevice, pkg: str, safety: Safety, keep_data: bool = False, user: Optional[int] = 0, system: bool = False) -> str:
    safety.confirm(f"Uninstall {pkg}{' (keep data)' if keep_data else ''}{' for user ' + str(user) if user is not None else ''}", level=DESTRUCTIVE)
    if system or user is not None:
        cmd = f"pm uninstall {'-k ' if keep_data else ''}--user {user if user is not None else 0} {shlex.quote(pkg)}"
        res = dev.shell(cmd, timeout=120)
    else:
        args = ["uninstall"] + (["-k"] if keep_data else []) + [pkg]
        res = dev.adb(*args, timeout=120)
    if "Success" not in res.combined:
        raise MrtError(f"uninstall failed: {res.combined[-400:]}")
    return res.combined


def set_enabled(dev: AdbDevice, pkg: str, enabled: bool, user: int = 0) -> str:
    verb = "enable" if enabled else "disable-user"
    res = dev.shell(f"pm {verb} --user {user} {shlex.quote(pkg)}", timeout=60)
    if "new state" not in res.combined:
        raise MrtError(f"pm {verb} failed: {res.combined[-400:]}")
    return res.combined.strip()


def clear_data(dev: AdbDevice, pkg: str, safety: Safety) -> str:
    safety.confirm(f"Clear all app data and cache of {pkg}", level=DESTRUCTIVE)
    res = dev.shell(f"pm clear {shlex.quote(pkg)}", timeout=120)
    if "Success" not in res.combined:
        raise MrtError(f"pm clear failed: {res.combined[-400:]}")
    return res.combined.strip()


def force_stop(dev: AdbDevice, pkg: str) -> str:
    return dev.shell_text(f"am force-stop {shlex.quote(pkg)}")


def pull_apk(dev: AdbDevice, pkg: str, out_dir: str) -> List[str]:
    paths = apk_paths(dev, pkg)
    if not paths:
        raise MrtError(f"no APK path for {pkg}")
    target = Path(out_dir) / pkg
    target.mkdir(parents=True, exist_ok=True)
    saved = []
    for p in paths:
        local = target / os.path.basename(p)
        dev.pull(p, str(local))
        saved.append(str(local))
    info(f"pulled {len(saved)} apk file(s) for {pkg} into {target}")
    return saved


def backup_app_data(dev: AdbDevice, pkg: str, out_file: str, apk: bool = True) -> str:
    """Use the (deprecated but still available) adb backup mechanism for one app."""
    args = ["backup", "-f", out_file, "-apk" if apk else "-noapk", pkg]
    warn("confirm the backup on the device screen (do not set a password unless you can supply it on restore)")
    res = dev.adb(*args, timeout=3600)
    if not res.ok:
        raise MrtError(f"adb backup failed: {res.combined[-400:]}")
    return out_file


def restore_app_data(dev: AdbDevice, ab_file: str) -> str:
    if not os.path.isfile(ab_file):
        raise MrtError(f"file not found: {ab_file}")
    warn("confirm the restore on the device screen")
    res = dev.adb("restore", ab_file, timeout=3600)
    return res.combined


def grant_permission(dev: AdbDevice, pkg: str, permission: str, grant: bool = True) -> str:
    verb = "grant" if grant else "revoke"
    res = dev.shell(f"pm {verb} {shlex.quote(pkg)} {shlex.quote(permission)}", timeout=60)
    if res.combined.strip():
        raise MrtError(res.combined.strip())
    return f"{verb}ed {permission} for {pkg}"


def debloat(dev: AdbDevice, packages: List[str], safety: Safety, mode: str = "disable", user: int = 0) -> Dict[str, str]:
    """Disable or uninstall-for-user a list of packages (no root required)."""
    safety.confirm(f"{mode} {len(packages)} package(s) for user {user}:\n  " + "\n  ".join(packages), level=DESTRUCTIVE)
    results: Dict[str, str] = {}
    for pkg in packages:
        try:
            if mode == "disable":
                results[pkg] = set_enabled(dev, pkg, False, user)
            elif mode == "enable":
                results[pkg] = set_enabled(dev, pkg, True, user)
            elif mode == "uninstall":
                res = dev.shell(f"pm uninstall -k --user {user} {shlex.quote(pkg)}", timeout=120)
                results[pkg] = res.combined.strip()
            elif mode == "reinstall":
                res = dev.shell(f"cmd package install-existing --user {user} {shlex.quote(pkg)}", timeout=120)
                results[pkg] = res.combined.strip()
            else:
                raise MrtError(f"unknown debloat mode {mode}")
        except MrtError as exc:
            results[pkg] = f"FAILED: {exc}"
    return results


def running_processes(dev: AdbDevice, top: int = 40) -> List[str]:
    out = dev.shell_text(f"ps -A -o PID,USER,RSS,NAME 2>/dev/null | head -{top + 1}")
    return [ln for ln in out.split("\n") if ln.strip()]
