"""Complete device information (ADB and Fastboot)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..core.device import AdbDevice, FastbootDevice

KEY_PROPS = {
    "brand": "ro.product.brand",
    "manufacturer": "ro.product.manufacturer",
    "model": "ro.product.model",
    "device": "ro.product.device",
    "product": "ro.product.name",
    "board": "ro.product.board",
    "platform": "ro.board.platform",
    "hardware": "ro.hardware",
    "android_version": "ro.build.version.release",
    "sdk": "ro.build.version.sdk",
    "security_patch": "ro.build.version.security_patch",
    "build_id": "ro.build.id",
    "build_display": "ro.build.display.id",
    "build_fingerprint": "ro.build.fingerprint",
    "build_type": "ro.build.type",
    "build_tags": "ro.build.tags",
    "build_date": "ro.build.date",
    "bootloader": "ro.bootloader",
    "baseband": "gsm.version.baseband",
    "serialno": "ro.serialno",
    "boot_serialno": "ro.boot.serialno",
    "cpu_abi": "ro.product.cpu.abi",
    "cpu_abilist": "ro.product.cpu.abilist",
    "first_api_level": "ro.product.first_api_level",
    "vndk_version": "ro.vndk.version",
    "treble_enabled": "ro.treble.enabled",
    "dynamic_partitions": "ro.boot.dynamic_partitions",
    "slot_suffix": "ro.boot.slot_suffix",
    "ab_update": "ro.build.ab_update",
    "virtual_ab": "ro.virtual_ab.enabled",
    "verified_boot_state": "ro.boot.verifiedbootstate",
    "veritymode": "ro.boot.veritymode",
    "flash_locked": "ro.boot.flash.locked",
    "vbmeta_device_state": "ro.boot.vbmeta.device_state",
    "oem_unlock_supported": "ro.oem_unlock_supported",
    "secure": "ro.secure",
    "debuggable": "ro.debuggable",
    "adb_secure": "ro.adb.secure",
    "crypto_state": "ro.crypto.state",
    "crypto_type": "ro.crypto.type",
    "bootmode": "ro.bootmode",
    "boot_mode": "ro.boot.mode",
    "boot_reason": "ro.boot.bootreason",
    "boot_hardware": "ro.boot.hardware",
    "boot_baseband": "ro.boot.baseband",
    "kernel_qemu": "ro.kernel.qemu",
    "sim_state": "gsm.sim.state",
    "sim_operator": "gsm.sim.operator.alpha",
    "network_type": "gsm.network.type",
    "locale": "ro.product.locale",
    "timezone": "persist.sys.timezone",
    "boot_completed": "sys.boot_completed",
    "miui_version": "ro.miui.ui.version.name",
    "oneui_version": "ro.build.version.oneui",
    "emui_version": "ro.build.version.emui",
    "oxygen_version": "ro.oxygen.version",
    "coloros_version": "ro.build.version.opporom",
    "knox_version": "ro.config.knox",
}


def collect_adb(dev: AdbDevice, full: bool = False, sensitive: bool = False) -> Dict[str, Any]:
    """Collect information from a booted (or recovery) device via adb."""
    props = dev.getprops()
    info: Dict[str, Any] = {"transport": "adb", "serial": dev.serial, "state": dev.state()}
    info["identity"] = {k: props.get(p, "") for k, p in KEY_PROPS.items() if props.get(p)}

    info["kernel"] = {
        "uname": dev.shell_text("uname -a"),
        "version": dev.shell_text("cat /proc/version"),
        "cmdline": dev.shell_text("cat /proc/cmdline 2>/dev/null") or "(not readable)",
        "selinux": dev.shell_text("getenforce 2>/dev/null"),
    }
    info["cpu"] = parse_cpuinfo(dev.shell_text("cat /proc/cpuinfo"))
    info["cpu"]["cores_online"] = dev.shell_text("cat /sys/devices/system/cpu/online 2>/dev/null")
    info["memory"] = parse_meminfo(dev.shell_text("cat /proc/meminfo"))
    info["storage"] = parse_df(dev.shell_text("df -h 2>/dev/null"))
    info["battery"] = parse_dumpsys_kv(dev.shell_text("dumpsys battery 2>/dev/null"))
    info["display"] = {
        "size": dev.shell_text("wm size 2>/dev/null"),
        "density": dev.shell_text("wm density 2>/dev/null"),
    }
    info["network"] = {
        "wifi_mac": dev.shell_text("cat /sys/class/net/wlan0/address 2>/dev/null"),
        "interfaces": [ln.strip() for ln in dev.shell_text("ip -o -4 addr 2>/dev/null").split("\n") if ln.strip()],
        "bluetooth_name": props.get("persist.bluetooth.name") or props.get("ro.product.model", ""),
    }
    info["security"] = {
        "root_mode": dev.root_mode() or "none",
        "su_binary": dev.which("su") or "-",
        "magisk": dev.which("magisk") or "-",
        "selinux": info["kernel"]["selinux"],
        "verified_boot_state": props.get("ro.boot.verifiedbootstate", ""),
        "flash_locked": props.get("ro.boot.flash.locked", ""),
        "vbmeta_device_state": props.get("ro.boot.vbmeta.device_state", ""),
        "oem_unlock_supported": props.get("ro.oem_unlock_supported", ""),
        "oem_unlock_allowed": dev.shell_text("settings get global oem_unlock_allowed 2>/dev/null") or "?",
        "adb_enabled": dev.shell_text("settings get global adb_enabled 2>/dev/null") or "?",
        "developer_options": dev.shell_text("settings get global development_settings_enabled 2>/dev/null") or "?",
        "encryption": f"{props.get('ro.crypto.state', '')} {props.get('ro.crypto.type', '')}".strip(),
        "frp": _frp_state(dev),
    }
    info["slots"] = {
        "current": props.get("ro.boot.slot_suffix", "(non-A/B)"),
        "ab": props.get("ro.build.ab_update", "false"),
        "virtual_ab": props.get("ro.virtual_ab.enabled", "false"),
    }
    info["users"] = [ln.strip() for ln in dev.shell_text("pm list users 2>/dev/null").split("\n") if "UserInfo" in ln]
    info["sim"] = _sim_info(dev, props)
    if sensitive:
        info["sim"]["imei"] = get_imei(dev)
    if full:
        info["props"] = props
        info["mounts"] = [ln for ln in dev.shell_text("mount 2>/dev/null").split("\n") if ln.strip()]
        info["packages_count"] = {
            "all": _count(dev.shell_text("pm list packages 2>/dev/null")),
            "third_party": _count(dev.shell_text("pm list packages -3 2>/dev/null")),
            "disabled": _count(dev.shell_text("pm list packages -d 2>/dev/null")),
        }
        info["uptime"] = dev.shell_text("uptime 2>/dev/null")
        info["sensors"] = [ln.strip() for ln in dev.shell_text("dumpsys sensorservice 2>/dev/null | grep -E '^\\s+[A-Za-z].*\\|' | head -60").split("\n") if ln.strip()]
    return info


def collect_fastboot(fb: FastbootDevice) -> Dict[str, Any]:
    vars_ = fb.getvar("all")
    info: Dict[str, Any] = {"transport": "fastboot", "serial": fb.serial}
    keys = [
        "product", "variant", "serialno", "version", "version-bootloader", "version-baseband",
        "secure", "unlocked", "off-mode-charge", "battery-voltage", "battery-soc-ok", "charger-screen-enabled",
        "current-slot", "slot-count", "is-userspace", "max-download-size", "hw-revision", "kernel",
        "snapshot-update-status", "super-partition-name", "dynamic-partition", "treble-enabled",
        "first-api-level", "security-patch-level", "vendor-fingerprint", "system-fingerprint",
        "cpu-abi", "logical-block-size", "erase-block-size",
    ]
    info["summary"] = {k: vars_[k] for k in keys if k in vars_}
    info["unlocked"] = _unlocked(vars_)
    parts = []
    for k, v in vars_.items():
        m = re.match(r"partition-size:(.+)", k)
        if m:
            name = m.group(1)
            try:
                size = int(v, 16) if v.lower().startswith("0x") else int(v)
            except ValueError:
                size = None
            parts.append({"name": name, "size": size, "type": vars_.get(f"partition-type:{name}", ""),
                          "slot": vars_.get(f"has-slot:{name}", "")})
    info["partitions"] = sorted(parts, key=lambda p: p["name"])
    info["all_vars"] = vars_
    return info


def _unlocked(vars_: Dict[str, str]) -> Optional[bool]:
    if "unlocked" in vars_:
        return vars_["unlocked"].strip().lower() == "yes"
    if "secure" in vars_:
        return vars_["secure"].strip().lower() == "no"
    return None


# ------------------------------------------------------------------ parsers


def parse_cpuinfo(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    cores = 0
    for line in text.split("\n"):
        if ":" not in line:
            continue
        k, v = [x.strip() for x in line.split(":", 1)]
        if k == "processor":
            cores += 1
        elif k in ("Hardware", "model name", "Processor", "CPU implementer", "CPU architecture", "CPU part", "Features", "Revision", "Serial") and k not in out:
            out[k] = v
    out["cores"] = cores
    return out


def parse_meminfo(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.split("\n"):
        m = re.match(r"^(MemTotal|MemFree|MemAvailable|SwapTotal|SwapFree|Cached):\s*(\d+)\s*kB", line)
        if m:
            kb = int(m.group(2))
            out[m.group(1)] = f"{kb / 1024 / 1024:.2f} GiB"
    return out


def parse_df(text: str) -> List[Dict[str, str]]:
    rows = []
    for line in text.split("\n")[1:]:
        parts = line.split()
        if len(parts) >= 6:
            rows.append({"fs": parts[0], "size": parts[1], "used": parts[2], "avail": parts[3], "use%": parts[4], "mount": parts[5]})
    interesting = ("/data", "/system", "/vendor", "/product", "/storage/emulated", "/sdcard", "/cache", "/metadata", "/")
    return [r for r in rows if r["mount"] in interesting or r["mount"].startswith("/storage")]


def parse_dumpsys_kv(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.split("\n"):
        line = line.strip()
        if ":" in line and not line.endswith(":"):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _count(text: str) -> int:
    return len([ln for ln in text.split("\n") if ln.startswith("package:")])


def _frp_state(dev: AdbDevice) -> str:
    out = dev.shell_text("ls -l /dev/block/by-name/frp /dev/block/by-name/persistent /dev/block/by-name/config 2>/dev/null")
    if not out:
        return "unknown (no frp partition symlink visible)"
    if not dev.has_root():
        return "present (root required to inspect)"
    for p in ("frp", "persistent", "config"):
        if dev.file_exists(f"/dev/block/by-name/{p}", root=True):
            last = dev.root_shell_text(f"tail -c 1 /dev/block/by-name/{p} | od -An -tu1")
            return f"{p}: {'ENABLED (lock set)' if last.strip() == '1' else 'disabled/unset'}"
    return "unknown"


def _sim_info(dev: AdbDevice, props: Dict[str, str]) -> Dict[str, str]:
    return {
        "sim_state": props.get("gsm.sim.state", ""),
        "operator": props.get("gsm.sim.operator.alpha", "") or props.get("gsm.operator.alpha", ""),
        "operator_numeric": props.get("gsm.sim.operator.numeric", ""),
        "network_type": props.get("gsm.network.type", ""),
        "baseband": props.get("gsm.version.baseband", ""),
        "ril_impl": props.get("gsm.version.ril-impl", ""),
        "phone_type": dev.shell_text("getprop gsm.current.phone-type"),
    }


def parse_service_call(text: str) -> str:
    """Decode the UTF-16 string returned by ``service call`` parcel dumps."""
    chars = []
    for m in re.finditer(r"'([^']*)'", text):
        chars.append(m.group(1))
    raw = "".join(chars)
    return raw.replace(".", "").strip()


def get_imei(dev: AdbDevice) -> str:
    """Try to read the IMEI (needs root or shell permission on recent Android)."""
    for code in (1, 3, 4, 5, 6, 7, 8):
        for call in (f"service call iphonesubinfo {code} s16 com.android.shell", f"service call iphonesubinfo {code}"):
            try:
                out = dev.root_shell_text(call, timeout=30) if dev.has_root() else dev.shell_text(call, timeout=30)
            except Exception:
                continue
            value = parse_service_call(out)
            if re.fullmatch(r"\d{14,16}", value):
                return value
    return "unavailable (needs root on Android 10+)"
