"""Raw, bit-by-bit partition access over ADB (root) using ``dd``.

* ``list``      - enumerate block partitions with their sizes
* ``dump``      - read a whole partition into an image file (streamed, verified)
* ``dump-range``- read an arbitrary byte range of a partition
* ``write``     - write an image to a partition (size checked, hash verified before and after)
* ``compare``   - compare an image with the partition contents
* ``wipe``      - zero-fill a partition
* ``dump-all``  - image every partition into a directory with a manifest
"""

from __future__ import annotations

import json
import os
import re
import shlex
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.device import AdbDevice
from ..core.errors import MrtError, VerificationFailed
from ..core.output import Progress, human_size, info, warn
from ..core.safety import CRITICAL, DESTRUCTIVE, Safety
from ..utils.hashing import sha256_file

BY_NAME_DIRS = [
    "/dev/block/by-name",
    "/dev/block/bootdevice/by-name",
    "/dev/block/platform/*/by-name",
    "/dev/block/platform/*/*/by-name",
    "/dev/block/platform/*/*/*/by-name",
]

REMOTE_TMP = "/data/local/tmp"
DEFAULT_SKIP = ("userdata", "cache", "super", "system", "vendor", "product", "odm", "system_ext", "sdcard", "metadata", "swap")


@dataclass
class Partition:
    name: str
    device: str
    size: Optional[int]

    @property
    def size_h(self) -> str:
        return human_size(self.size)


_LIST_SCRIPT = (
    'for d in {dirs}; do if [ -d "$d" ]; then for f in "$d"/*; do n=$(basename "$f"); '
    't=$(readlink -f "$f" 2>/dev/null || readlink "$f" 2>/dev/null || echo "$f"); b=$(basename "$t"); '
    's=$(cat /sys/class/block/$b/size 2>/dev/null); [ -z "$s" ] && s=$(blockdev --getsize64 "$t" 2>/dev/null); '
    'echo "MRT|$n|$t|$s|$(cat /sys/class/block/$b/size 2>/dev/null)"; done; break; fi; done'
)


def find_by_name_dir(dev: AdbDevice, root: bool = False) -> Optional[str]:
    cmd = "for d in " + " ".join(BY_NAME_DIRS) + '; do [ -d "$d" ] && echo "$d" && break; done'
    out = dev.root_shell_text(cmd) if root else dev.shell_text(cmd)
    out = out.strip().split("\n")[0].strip()
    return out or None


def parse_partition_listing(text: str) -> List[Partition]:
    parts: List[Partition] = []
    for line in text.replace("\r", "").split("\n"):
        if not line.startswith("MRT|"):
            continue
        fields = line.split("|")
        if len(fields) < 5:
            continue
        _, name, target, size_field, sectors = fields[:5]
        size: Optional[int] = None
        sectors = sectors.strip()
        size_field = size_field.strip()
        if sectors.isdigit():
            size = int(sectors) * 512
        elif size_field.isdigit():
            size = int(size_field)
        parts.append(Partition(name.strip(), target.strip(), size))
    return sorted(parts, key=lambda p: p.name)


def list_partitions(dev: AdbDevice) -> List[Partition]:
    script = _LIST_SCRIPT.format(dirs=" ".join(BY_NAME_DIRS))
    res = dev.shell(script, timeout=120)
    parts = parse_partition_listing(res.text)
    if not parts and dev.has_root():
        res = dev.root_shell(script, timeout=120)
        parts = parse_partition_listing(res.text)
    return parts


def resolve(dev: AdbDevice, name: str) -> Partition:
    """Find a partition by name (accepts 'boot', 'boot_a', or a /dev/block path)."""
    if name.startswith("/dev/"):
        size = dev.remote_size(name)
        return Partition(os.path.basename(name), name, size)
    parts = list_partitions(dev)
    lookup = {p.name: p for p in parts}
    if name in lookup:
        return lookup[name]
    slot = dev.getprop("ro.boot.slot_suffix")
    if slot and name + slot in lookup:
        return lookup[name + slot]
    raise MrtError(f"partition '{name}' not found. Known partitions: {', '.join(sorted(lookup)) or 'none (root required?)'}")


# ------------------------------------------------------------------- dump


def dump_partition(dev: AdbDevice, name: str, out_path: str, verify: bool = True, bs: str = "4M", timeout: int = 7200) -> Dict[str, Any]:
    dev.ensure_root()
    part = resolve(dev, name)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    info(f"dumping {part.name} ({part.size_h}) from {part.device} -> {out_path}")
    progress = Progress(part.size, label=f"  {part.name}")
    cmd = f"dd if={shlex.quote(part.device)} bs={bs} 2>/dev/null"
    start = time.time()
    res = dev.root_exec_out(cmd, out_path, timeout=timeout, progress=progress)
    progress.finish()
    if not res.ok and not res.dry_run:
        raise MrtError(f"dd failed (rc={res.returncode}): {res.err[-500:]}")
    result: Dict[str, Any] = {"partition": part.name, "device": part.device, "expected_size": part.size, "file": out_path, "seconds": round(time.time() - start, 1)}
    if res.dry_run:
        return result
    actual = os.path.getsize(out_path)
    result["size"] = actual
    if part.size is not None and actual != part.size:
        warn(f"size mismatch: partition {part.size} bytes, dumped {actual} bytes")
        result["size_ok"] = False
    else:
        result["size_ok"] = True
    if verify:
        local = sha256_file(out_path)
        remote = dev.remote_sha256(part.device)
        result["sha256"] = local
        result["device_sha256"] = remote
        result["verified"] = (remote == local) if remote else None
        if remote is None:
            warn("device has no sha256sum; skipped remote verification")
        elif remote != local:
            warn("sha256 mismatch between device partition and dumped image (partition may be changing, e.g. userdata)")
    if result.get("verified") is False and result["size_ok"]:
        raise VerificationFailed(f"dump of {part.name} did not verify: device={result['device_sha256']} file={result['sha256']}")
    return result


def dump_range(dev: AdbDevice, name: str, out_path: str, offset: int, length: int, timeout: int = 3600) -> Dict[str, Any]:
    """Read ``length`` bytes starting at ``offset`` from the partition (byte granular)."""
    dev.ensure_root()
    part = resolve(dev, name)
    if length <= 0:
        raise MrtError("length must be > 0")
    if part.size is not None and offset + length > part.size:
        raise MrtError(f"range {offset}+{length} exceeds partition size {part.size}")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    bs = 4096
    if offset % bs == 0 and length % bs == 0:
        cmd = f"dd if={shlex.quote(part.device)} bs={bs} skip={offset // bs} count={length // bs} 2>/dev/null"
    else:
        # byte-granular fallback: dd bs=1 is slow, so use a 512-byte window plus tail/head
        block = 512
        skip_blocks = offset // block
        head_skip = offset - skip_blocks * block
        count = (head_skip + length + block - 1) // block
        cmd = f"dd if={shlex.quote(part.device)} bs={block} skip={skip_blocks} count={count} 2>/dev/null | tail -c +{head_skip + 1} | head -c {length}"
    progress = Progress(length, label=f"  {part.name}[{offset}:{offset + length}]")
    res = dev.root_exec_out(cmd, out_path, timeout=timeout, progress=progress)
    progress.finish()
    if not res.ok and not res.dry_run:
        raise MrtError(f"dd failed: {res.err[-500:]}")
    got = os.path.getsize(out_path) if not res.dry_run else length
    if got != length and not res.dry_run:
        raise VerificationFailed(f"expected {length} bytes, got {got}")
    return {"partition": part.name, "offset": offset, "length": length, "file": out_path, "sha256": sha256_file(out_path) if not res.dry_run else None}


def dump_all(dev: AdbDevice, out_dir: str, include: Optional[List[str]] = None, exclude: Optional[List[str]] = None,
             max_size: Optional[int] = None, verify: bool = True, skip_data: bool = True) -> Dict[str, Any]:
    dev.ensure_root()
    parts = list_partitions(dev)
    if not parts:
        raise MrtError("no partitions found")
    exclude = set(exclude or [])
    if skip_data:
        exclude |= set(DEFAULT_SKIP)
    selected = [p for p in parts if (not include or p.name in include or _base(p.name) in include) and p.name not in exclude and _base(p.name) not in exclude]
    if max_size:
        selected = [p for p in selected if p.size is None or p.size <= max_size]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest: Dict[str, Any] = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "serial": dev.serial,
        "props": {k: dev.getprop(k) for k in ("ro.product.model", "ro.product.device", "ro.build.fingerprint", "ro.boot.slot_suffix")},
        "partitions": [],
        "skipped": [p.name for p in parts if p not in selected],
    }
    total = sum(p.size or 0 for p in selected)
    info(f"dumping {len(selected)} partitions, ~{human_size(total)} total, skipping {len(manifest['skipped'])}")
    for p in selected:
        target = out / f"{p.name}.img"
        try:
            entry = dump_partition(dev, p.name, str(target), verify=verify)
            entry["status"] = "ok"
        except (MrtError, OSError) as exc:
            entry = {"partition": p.name, "status": "failed", "error": str(exc)}
            warn(f"{p.name}: {exc}")
        manifest["partitions"].append(entry)
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return manifest


def _base(name: str) -> str:
    return name[:-2] if name.endswith(("_a", "_b")) else name


# ------------------------------------------------------------------ write


def write_partition(dev: AdbDevice, name: str, image_path: str, safety: Safety, verify: bool = True, bs: str = "4M",
                    allow_smaller: bool = True, timeout: int = 7200) -> Dict[str, Any]:
    if not os.path.isfile(image_path):
        raise MrtError(f"image not found: {image_path}")
    dev.ensure_root()
    part = resolve(dev, name)
    img_size = os.path.getsize(image_path)
    if part.size is not None and img_size > part.size:
        raise MrtError(f"image ({img_size} bytes) is larger than partition {part.name} ({part.size} bytes)")
    if part.size is not None and img_size < part.size and not allow_smaller:
        raise MrtError(f"image ({img_size}) is smaller than partition ({part.size}); pass --allow-smaller to write anyway")
    local_hash = sha256_file(image_path)
    safety.confirm(
        f"RAW WRITE with dd\n"
        f"  partition : {part.name} ({part.device}, {part.size_h})\n"
        f"  image     : {image_path} ({human_size(img_size)})\n"
        f"  sha256    : {local_hash}\n"
        f"  device    : {dev.serial or '(auto)'}\n"
        "Writing a wrong or corrupt image can brick the device. Make sure you have a dump of this partition.",
        level=CRITICAL,
        token=part.name,
    )
    remote_tmp = f"{REMOTE_TMP}/mrt_{part.name}_{int(time.time())}.img"
    info(f"pushing image to {remote_tmp}")
    dev.push(image_path, remote_tmp)
    try:
        # verify upload integrity before touching flash
        remote_hash = dev.remote_sha256(remote_tmp, root=True)
        if remote_hash and remote_hash != local_hash:
            raise VerificationFailed(f"uploaded image hash mismatch (local {local_hash}, device {remote_hash}); aborting before write")
        info(f"writing {human_size(img_size)} to {part.device} ...")
        cmd = f"dd if={shlex.quote(remote_tmp)} of={shlex.quote(part.device)} bs={bs} conv=fsync 2>&1 || dd if={shlex.quote(remote_tmp)} of={shlex.quote(part.device)} bs={bs} 2>&1; sync"
        res = dev.root_shell(cmd, timeout=timeout)
        if not res.ok and not res.dry_run:
            raise MrtError(f"dd write failed: {res.combined[-800:]}")
        result: Dict[str, Any] = {"partition": part.name, "device": part.device, "image": image_path, "size": img_size, "sha256": local_hash}
        if verify and not res.dry_run:
            after = dev.remote_sha256(part.device, root=True, length=img_size)
            result["device_sha256_after"] = after
            result["verified"] = after == local_hash if after else None
            if after and after != local_hash:
                raise VerificationFailed(f"post-write verification FAILED for {part.name}: device={after} image={local_hash}")
            if after:
                info("post-write verification OK")
        return result
    finally:
        dev.root_shell(f"rm -f {shlex.quote(remote_tmp)}", timeout=60)


def compare_partition(dev: AdbDevice, name: str, image_path: str) -> Dict[str, Any]:
    if not os.path.isfile(image_path):
        raise MrtError(f"image not found: {image_path}")
    dev.ensure_root()
    part = resolve(dev, name)
    size = os.path.getsize(image_path)
    local = sha256_file(image_path)
    remote = dev.remote_sha256(part.device, root=True, length=size)
    return {"partition": part.name, "image": image_path, "compared_bytes": size, "image_sha256": local, "device_sha256": remote, "match": remote == local}


def wipe_partition(dev: AdbDevice, name: str, safety: Safety, bs: str = "4M", timeout: int = 7200) -> Dict[str, Any]:
    dev.ensure_root()
    part = resolve(dev, name)
    safety.confirm(
        f"ZERO-FILL partition {part.name} ({part.device}, {part.size_h}) with dd if=/dev/zero.\n"
        "This is irreversible.",
        level=CRITICAL,
        token=part.name,
    )
    cmd = f"dd if=/dev/zero of={shlex.quote(part.device)} bs={bs} 2>&1; sync"
    res = dev.root_shell(cmd, timeout=timeout)
    return {"partition": part.name, "device": part.device, "output": res.combined[-500:]}


def partition_table(dev: AdbDevice) -> Dict[str, Any]:
    """Extra layout info: block devices, GPT presence, slot suffix, super partition details."""
    dev.ensure_root()
    out: Dict[str, Any] = {}
    out["block_devices"] = [ln for ln in dev.root_shell_text("ls -l /dev/block/ 2>/dev/null | grep -E 'sd[a-z]|mmcblk|nvme|loop|dm-' | awk '{print $NF}' | head -80").split("\n") if ln]
    out["slot_suffix"] = dev.getprop("ro.boot.slot_suffix")
    out["by_name_dir"] = find_by_name_dir(dev, root=True)
    out["dynamic_partitions"] = dev.getprop("ro.boot.dynamic_partitions")
    lp = dev.which("lpdump", root=True)
    if lp:
        out["lpdump"] = [ln for ln in dev.root_shell_text("lpdump 2>&1 | head -120").split("\n") if ln.strip()]
    return out
