"""Command-line interface for mrt (Mobile Repair Toolkit)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, List, Optional

from . import __version__
from .core import device as device_mod
from .core.context import Context
from .core.errors import MrtError
from .core.oplog import OperationLog
from .core.output import emit, human_size, info, parse_size, print_table, warn
from .core.runner import Runner
from .core.safety import CRITICAL, DESTRUCTIVE, NOTICE, Safety
from .modules import apps as apps_mod
from .modules import backup as backup_mod
from .modules import fastboot_ops
from .modules import flash as flash_mod
from .modules import info as info_mod
from .modules import logs as logs_mod
from .modules import partitions as part_mod
from .modules import recovery as recovery_mod
from .modules import screen as screen_mod
from .modules import efs as efs_mod
from .utils import qcn as qcn_mod
from .utils import nvchecksum as nvchecksum_mod


def _out(ctx: Context, data: Any) -> None:
    if isinstance(data, str):
        print(data)
    else:
        emit(data, as_json=ctx.json)


def _csv(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


# ============================================================ top-level handlers


def cmd_devices(ctx: Context, args) -> None:
    devices = device_mod.list_devices(ctx.runner)
    if ctx.json:
        _out(ctx, [d.as_dict() for d in devices])
        return
    if not devices:
        print("no devices found (adb or fastboot)")
        return
    print_table([(d.serial, d.transport, d.mode, d.description) for d in devices], headers=["serial", "transport", "mode", "description"])


def cmd_doctor(ctx: Context, args) -> None:
    report: dict = {"platform": sys.platform, "python": sys.version.split()[0], "mrt": __version__}
    for tool in ("adb", "fastboot"):
        try:
            path = ctx.runner.adb_path if tool == "adb" else ctx.runner.fastboot_path
            report[tool] = {"path": path, "version": ctx.runner.version(tool)}
        except MrtError as exc:
            report[tool] = {"error": str(exc)}
    try:
        report["devices"] = [d.as_dict() for d in device_mod.list_devices(ctx.runner)]
    except MrtError as exc:
        report["devices"] = str(exc)
    hints = []
    if sys.platform.startswith("linux"):
        if not Path("/etc/udev/rules.d/51-android.rules").exists():
            hints.append("Linux: install udev rules -> sudo cp scripts/51-android.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules")
    if sys.platform.startswith("win"):
        hints.append("Windows: install Google USB driver / vendor driver; check Device Manager for 'Android Bootloader Interface' in fastboot mode")
    if any(isinstance(d, dict) and d.get("mode") == "unauthorized" for d in report.get("devices", []) if isinstance(report.get("devices"), list)):
        hints.append("A device is unauthorized: accept the 'Allow USB debugging' dialog on the phone")
    report["hints"] = hints
    report["log_dir"] = str(ctx.log.log_dir)
    _out(ctx, report)


def cmd_info(ctx: Context, args) -> None:
    want = "any"
    dev_info = ctx.device(want) if not ctx.runner.dry_run else None
    if dev_info and dev_info.transport == "fastboot":
        data = info_mod.collect_fastboot(device_mod.FastbootDevice(ctx.runner, dev_info.serial))
        if not args.full:
            data.pop("all_vars", None)
    else:
        adb = device_mod.AdbDevice(ctx.runner, dev_info.serial if dev_info else ctx.serial)
        data = info_mod.collect_adb(adb, full=args.full, sensitive=args.imei)
    if args.save:
        Path(args.save).write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        info(f"saved to {args.save}")
    _out(ctx, data)


def cmd_props(ctx: Context, args) -> None:
    props = ctx.adb().getprops()
    if args.pattern:
        props = {k: v for k, v in props.items() if args.pattern.lower() in k.lower() or args.pattern.lower() in v.lower()}
    _out(ctx, props)


def cmd_shell(ctx: Context, args) -> None:
    adb = ctx.adb()
    cmd = " ".join(args.command)
    if not cmd:
        adb.adb("shell", stream=True, timeout=24 * 3600)
        return
    res = adb.root_shell(cmd, timeout=args.timeout) if args.root else adb.shell(cmd, timeout=args.timeout)
    sys.stdout.write(res.combined + ("\n" if res.combined else ""))
    if not res.ok:
        sys.exit(res.returncode)


def cmd_reboot(ctx: Context, args) -> None:
    _out(ctx, recovery_mod.reboot(ctx.runner, ctx.serial, args.target) or f"reboot {args.target} sent")


def cmd_wait(ctx: Context, args) -> None:
    ok = recovery_mod.wait_for(ctx.runner, ctx.serial, args.mode, timeout=args.timeout)
    _out(ctx, {"mode": args.mode, "ready": ok})
    if not ok:
        sys.exit(1)


# ====================================================================== fastboot


def fb_getvar(ctx, args):
    _out(ctx, fastboot_ops.getvar(ctx.fastboot(), args.name))


def fb_flash(ctx, args):
    _out(ctx, fastboot_ops.flash(ctx.fastboot(), args.partition, args.image, ctx.safety, slot=args.slot))


def fb_erase(ctx, args):
    _out(ctx, fastboot_ops.erase(ctx.fastboot(), args.partition, ctx.safety))


def fb_format(ctx, args):
    _out(ctx, fastboot_ops.format_partition(ctx.fastboot(), args.partition, ctx.safety, fs=args.fs))


def fb_boot(ctx, args):
    _out(ctx, fastboot_ops.boot_image(ctx.fastboot(), args.image, ctx.safety))


def fb_reboot(ctx, args):
    _out(ctx, fastboot_ops.reboot(ctx.fastboot(), args.target) or "ok")


def fb_unlock(ctx, args):
    _out(ctx, fastboot_ops.unlock(ctx.fastboot(), ctx.safety, method=args.method, critical=args.critical))


def fb_lock(ctx, args):
    _out(ctx, fastboot_ops.lock(ctx.fastboot(), ctx.safety, method=args.method, critical=args.critical))


def fb_set_active(ctx, args):
    _out(ctx, fastboot_ops.set_active(ctx.fastboot(), args.slot, ctx.safety))


def fb_oem(ctx, args):
    _out(ctx, fastboot_ops.oem(ctx.fastboot(), args.args, ctx.safety))


def fb_wipe(ctx, args):
    _out(ctx, fastboot_ops.wipe_userdata(ctx.fastboot(), ctx.safety))


def fb_continue(ctx, args):
    _out(ctx, fastboot_ops.continue_boot(ctx.fastboot()))


def fb_raw(ctx, args):
    _out(ctx, fastboot_ops.raw_command(ctx.fastboot(), args.args, ctx.safety))


def fb_partitions(ctx, args):
    data = info_mod.collect_fastboot(ctx.fastboot())["partitions"]
    if ctx.json:
        _out(ctx, data)
    else:
        print_table([(p["name"], human_size(p["size"]), p["type"], p["slot"]) for p in data], headers=["partition", "size", "type", "has-slot"])


# ==================================================================== partitions


def part_list(ctx, args):
    parts = part_mod.list_partitions(ctx.adb())
    if ctx.json:
        _out(ctx, [p.__dict__ for p in parts])
        return
    if not parts:
        warn("no partitions visible (device may need root, or is not in system/recovery mode)")
        return
    print_table([(p.name, p.size_h, p.size, p.device) for p in parts], headers=["partition", "size", "bytes", "block device"])
    print(f"\n{len(parts)} partitions, total {human_size(sum(p.size or 0 for p in parts))}")


def part_table(ctx, args):
    _out(ctx, part_mod.partition_table(ctx.adb()))


def part_dump(ctx, args):
    out = args.output or f"{args.name}.img"
    _out(ctx, part_mod.dump_partition(ctx.adb(), args.name, out, verify=not args.no_verify, bs=args.bs))


def part_dump_range(ctx, args):
    _out(ctx, part_mod.dump_range(ctx.adb(), args.name, args.output, parse_size(args.offset), parse_size(args.length)))


def part_dump_all(ctx, args):
    manifest = part_mod.dump_all(ctx.adb(), args.directory, include=_csv(args.include), exclude=_csv(args.exclude),
                                 max_size=parse_size(args.max_size) if args.max_size else None, verify=not args.no_verify, skip_data=not args.with_data)
    if ctx.json:
        _out(ctx, manifest)
    else:
        print_table([(p.get("partition"), p.get("status"), human_size(p.get("size")), (p.get("sha256") or p.get("error") or "")[:64]) for p in manifest["partitions"]],
                    headers=["partition", "status", "size", "sha256 / error"])
        print(f"\nskipped: {', '.join(manifest['skipped']) or '-'}")


def part_write(ctx, args):
    _out(ctx, part_mod.write_partition(ctx.adb(), args.name, args.image, ctx.safety, verify=not args.no_verify, bs=args.bs, allow_smaller=not args.exact_size))


def part_compare(ctx, args):
    res = part_mod.compare_partition(ctx.adb(), args.name, args.image)
    _out(ctx, res)
    if not res["match"]:
        sys.exit(1)


def part_wipe(ctx, args):
    _out(ctx, part_mod.wipe_partition(ctx.adb(), args.name, ctx.safety))


# ======================================================================== backup


def backup_create(ctx, args):
    out_dir = args.directory or os.path.join("backups", time.strftime("%Y%m%d-%H%M%S"))
    _out(ctx, backup_mod.create(ctx.adb(), out_dir, ctx.safety, apps=not args.no_apps, app_data=args.app_data, sdcard=args.sdcard,
                                partitions=_csv(args.partitions) or (backup_mod.DEFAULT_PARTITIONS if args.critical_partitions else None),
                                all_partitions=args.all_partitions, include_userdata=args.with_userdata, sdcard_path=args.sdcard_path,
                                only_third_party=not args.all_apps))


def backup_restore(ctx, args):
    _out(ctx, backup_mod.restore(ctx.adb(), args.directory, ctx.safety, apps=args.apps, app_data=args.app_data, sdcard=args.sdcard,
                                 partitions=_csv(args.partitions), sdcard_path=args.sdcard_path))


def backup_verify(ctx, args):
    _out(ctx, backup_mod.verify(args.directory))


# =========================================================================== rom


def rom_inspect(ctx, args):
    _out(ctx, flash_mod.inspect_rom(args.path))


def rom_flash_dir(ctx, args):
    _out(ctx, flash_mod.flash_directory(ctx.fastboot(), args.directory, ctx.safety, slot=args.slot, only=_csv(args.only), skip=_csv(args.skip),
                                        wipe=args.wipe, include_data=args.with_data, reboot=args.reboot,
                                        disable_verity=args.disable_verity, disable_verification=args.disable_verification))


def rom_payload(ctx, args):
    workdir = args.workdir or os.path.join(os.path.dirname(os.path.abspath(args.source)), "extracted")
    if args.extract_only:
        _out(ctx, flash_mod.extract_payload(args.source, workdir, only=_csv(args.only)))
        return
    _out(ctx, flash_mod.flash_payload(ctx.fastboot(), args.source, workdir, ctx.safety, only=_csv(args.only), slot=args.slot, skip=_csv(args.skip),
                                      wipe=args.wipe, include_data=args.with_data, reboot=args.reboot,
                                      disable_verity=args.disable_verity, disable_verification=args.disable_verification))


def rom_payload_info(ctx, args):
    data = flash_mod.payload_info(args.source)
    if ctx.json:
        _out(ctx, data)
    else:
        print(f"payload version {data['version']}, block size {data['block_size']}")
        print_table([(p["name"], human_size(p["size"]), p["operations"], "full" if p["full"] else "differential") for p in data["partitions"]],
                    headers=["partition", "size", "ops", "type"])


def rom_update_zip(ctx, args):
    _out(ctx, flash_mod.fastboot_update_zip(ctx.fastboot(), args.zip, ctx.safety, wipe=args.wipe))


# ====================================================================== recovery


def rec_sideload(ctx, args):
    _out(ctx, recovery_mod.sideload(ctx.runner, ctx.serial, args.zip, ctx.safety, auto_reboot=not args.no_auto_reboot))


def rec_wipe(ctx, args):
    _out(ctx, recovery_mod.wipe(ctx.runner, ctx.serial, args.what, ctx.safety))


def rec_factory_reset(ctx, args):
    _out(ctx, recovery_mod.factory_reset(ctx.runner, ctx.serial, ctx.safety))


def rec_flash(ctx, args):
    _out(ctx, recovery_mod.flash_recovery(ctx.fastboot(), args.image, ctx.safety, slot=args.slot, boot_after=args.boot))


def rec_push(ctx, args):
    _out(ctx, recovery_mod.push_files(ctx.runner, ctx.serial, args.files, args.to))


def rec_twrp(ctx, args):
    _out(ctx, recovery_mod.twrp_command(ctx.runner, ctx.serial, args.args))


def rec_status(ctx, args):
    _out(ctx, recovery_mod.mode_status(ctx.runner, ctx.serial))


# ========================================================================== apps


def apps_list(ctx, args):
    rows = apps_mod.list_packages(ctx.adb(), args.filter, user=args.user, versions=args.versions, pattern=args.grep)
    if ctx.json:
        _out(ctx, rows)
    else:
        print_table([(r["package"], r["versionCode"] if args.versions else "", r["path"]) for r in rows], headers=["package", "version", "path"])
        print(f"\n{len(rows)} packages")


def apps_info(ctx, args):
    _out(ctx, apps_mod.package_info(ctx.adb(), args.package))


def apps_install(ctx, args):
    _out(ctx, apps_mod.install(ctx.adb(), args.files, reinstall=not args.no_reinstall, downgrade=args.downgrade, grant=not args.no_grant, test=args.test, user=args.user))


def apps_uninstall(ctx, args):
    _out(ctx, apps_mod.uninstall(ctx.adb(), args.package, ctx.safety, keep_data=args.keep_data, user=args.user, system=args.system))


def apps_disable(ctx, args):
    _out(ctx, apps_mod.set_enabled(ctx.adb(), args.package, False, args.user))


def apps_enable(ctx, args):
    _out(ctx, apps_mod.set_enabled(ctx.adb(), args.package, True, args.user))


def apps_clear(ctx, args):
    _out(ctx, apps_mod.clear_data(ctx.adb(), args.package, ctx.safety))


def apps_stop(ctx, args):
    _out(ctx, apps_mod.force_stop(ctx.adb(), args.package) or "stopped")


def apps_pull(ctx, args):
    _out(ctx, apps_mod.pull_apk(ctx.adb(), args.package, args.out))


def apps_backup(ctx, args):
    _out(ctx, apps_mod.backup_app_data(ctx.adb(), args.package, args.file, apk=not args.no_apk))


def apps_restore(ctx, args):
    _out(ctx, apps_mod.restore_app_data(ctx.adb(), args.file))


def apps_grant(ctx, args):
    _out(ctx, apps_mod.grant_permission(ctx.adb(), args.package, args.permission, grant=True))


def apps_revoke(ctx, args):
    _out(ctx, apps_mod.grant_permission(ctx.adb(), args.package, args.permission, grant=False))


def apps_debloat(ctx, args):
    packages: List[str] = []
    for item in args.packages:
        if os.path.isfile(item):
            for line in Path(item).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    packages.append(line)
        else:
            packages.append(item)
    _out(ctx, apps_mod.debloat(ctx.adb(), packages, ctx.safety, mode=args.mode, user=args.user))


def apps_ps(ctx, args):
    print("\n".join(apps_mod.running_processes(ctx.adb(), args.top)))


# ========================================================================== logs


def logs_logcat(ctx, args):
    out = logs_mod.logcat(ctx.adb(), out_file=args.output, buffers=args.buffers, follow=args.follow, filters=args.filters or None, clear=args.clear)
    if out is None:
        return
    if args.output:
        info(f"saved to {out}")
    else:
        print(out)


def logs_dmesg(ctx, args):
    text = logs_mod.dmesg(ctx.adb(), args.output)
    if args.output:
        info(f"saved to {args.output}")
    else:
        print(text)


def logs_last_kmsg(ctx, args):
    _out(ctx, logs_mod.last_kmsg(ctx.adb(), args.directory))


def logs_bugreport(ctx, args):
    _out(ctx, logs_mod.bugreport(ctx.adb(), args.output))


def logs_dumpsys(ctx, args):
    text = logs_mod.dumpsys(ctx.adb(), args.service, args.args, args.output)
    if args.output:
        info(f"saved to {args.output}")
    else:
        print(text)


def logs_crashes(ctx, args):
    _out(ctx, logs_mod.crash_dumps(ctx.adb(), args.directory))


def logs_collect(ctx, args):
    _out(ctx, logs_mod.collect_all(ctx.adb(), args.directory or os.path.join("logs", time.strftime("%Y%m%d-%H%M%S"))))


def logs_battery(ctx, args):
    print(logs_mod.battery_history(ctx.adb()))


def logs_boot_reason(ctx, args):
    _out(ctx, logs_mod.boot_reason(ctx.adb()))


# ======================================================================== screen


def screen_shot(ctx, args):
    _out(ctx, screen_mod.screenshot(ctx.adb(), args.output))


def screen_record(ctx, args):
    _out(ctx, screen_mod.record(ctx.adb(), args.output, seconds=args.seconds, size=args.size, bitrate=args.bitrate))


def screen_tap(ctx, args):
    _out(ctx, screen_mod.tap(ctx.adb(), args.x, args.y) or "ok")


def screen_swipe(ctx, args):
    _out(ctx, screen_mod.swipe(ctx.adb(), args.x1, args.y1, args.x2, args.y2, args.ms) or "ok")


def screen_key(ctx, args):
    _out(ctx, screen_mod.keyevent(ctx.adb(), args.key) or "ok")


def screen_text(ctx, args):
    _out(ctx, screen_mod.text(ctx.adb(), " ".join(args.text)) or "ok")


def screen_touch_test(ctx, args):
    screen_mod.touch_test(ctx.adb(), args.seconds)


def screen_unlock(ctx, args):
    _out(ctx, screen_mod.unlock_screen(ctx.adb(), args.pin))


# =========================================================================== efs


def efs_detect(ctx, args):
    dev = ctx.adb()
    chipset = efs_mod.detect_chipset(dev)
    data = {"chipset": chipset, "groups": {}}
    for group in efs_mod.GROUPS.get(chipset, efs_mod.GROUPS[efs_mod.UNKNOWN]):
        parts = efs_mod.resolve_group(dev, chipset, group)
        data["groups"][group] = [{"name": p.name, "size": p.size_h, "device": p.device} for p in parts]
    _out(ctx, data)


def efs_backup(ctx, args):
    out_dir = args.directory or os.path.join("backups", "efs-" + time.strftime("%Y%m%d-%H%M%S"))
    _out(ctx, efs_mod.backup(ctx.adb(), out_dir, ctx.safety, group=args.group, chipset=args.chipset,
                             include_efs_fs=not args.no_efs_fs))


def efs_restore(ctx, args):
    _out(ctx, efs_mod.restore(ctx.adb(), args.directory, ctx.safety, group=args.group,
                              partitions=_csv(args.partitions), rollback=not args.no_rollback))


def efs_validate(ctx, args):
    _out(ctx, efs_mod.validate(ctx.adb(), chipset=args.chipset, group=args.group, deep=not args.no_deep))


def efs_check_image(ctx, args):
    _out(ctx, efs_mod.check_images(args.paths))


def efs_rebuild_modemst(ctx, args):
    _out(ctx, efs_mod.rebuild_modemst(ctx.adb(), ctx.safety, backup_dir=args.backup_dir, reboot=args.reboot))


def efs_mtk_rebuild_nvdata(ctx, args):
    _out(ctx, efs_mod.mtk_rebuild_nvdata(ctx.adb(), ctx.safety, backup_dir=args.backup_dir, reboot=args.reboot))


def efs_samsung_fix_md5(ctx, args):
    dev = None if args.local else ctx.adb()
    _out(ctx, efs_mod.samsung_fix_md5(dev, ctx.safety, efs_dir=args.efs_dir, local_dir=args.local, create=args.create))


def efs_qcn_info(ctx, args):
    q = qcn_mod.Qcn.load(args.file)
    data = {"file": args.file}
    data.update(q.metadata())
    data["nv_items"] = sorted((it.storage + "/" + it.item) for it in q.nv_items())
    if ctx.json:
        _out(ctx, data)
        return
    print(f"file: {args.file}")
    print(f"storages: {', '.join(data['storages'])}")
    print(f"NV item count: {data['nv_item_count']}")
    for k in ("Version", "File_Version", "Mobile_Property"):
        if k in data:
            print(f"{k}: {data[k]}")
    print("items:")
    for it in q.items():
        print(f"  {it.storage}/{it.item}  ({len(it.value)} bytes)")


def efs_qcn_extract(ctx, args):
    q = qcn_mod.Qcn.load(args.file)
    if args.item is not None:
        it = q.get(args.item, storage=args.storage)
        if not it:
            raise MrtError(f"item {args.item} not found")
        if args.out:
            Path(args.out).write_bytes(it.value)
            _out(ctx, {"item": it.item, "storage": it.storage, "bytes": len(it.value), "file": args.out})
        else:
            _out(ctx, {"item": it.item, "storage": it.storage, "hex": it.value.hex(),
                       "ascii": it.value.decode("latin-1")})
        return
    out_dir = Path(args.out or "qcn_items")
    out_dir.mkdir(parents=True, exist_ok=True)
    exported = []
    for it in q.items():
        sub = out_dir / it.storage
        sub.mkdir(exist_ok=True)
        (sub / it.item).write_bytes(it.value)
        exported.append(f"{it.storage}/{it.item}")
    (out_dir / "index.json").write_text(json.dumps({"file": args.file, "items": exported, "meta": q.metadata()}, indent=2, default=str), encoding="utf-8")
    _out(ctx, {"exported": len(exported), "dir": str(out_dir)})


def efs_qcn_edit(ctx, args):
    if args.value is not None:
        value = bytes.fromhex(args.value.replace(" ", ""))
    elif args.value_ascii is not None:
        value = args.value_ascii.encode("latin-1")
    elif args.value_file is not None:
        value = Path(args.value_file).read_bytes()
    else:
        raise MrtError("provide --value HEX, --value-ascii STR or --value-file FILE")
    preview = value.hex()[:48]
    ctx.safety.confirm(
        f"Edit NV item {args.item} in {args.file} to {len(value)} bytes ({preview})\n"
        "This edits the QCN file only. Writing it back to the modem needs QPST/QFIL over a DIAG port.",
        level=DESTRUCTIVE)
    if args.rebuild:
        q = qcn_mod.Qcn.load(args.file)
        q.set(args.item, value, storage=args.storage or "NV_ITEM_ARRAY")
        q.save(args.out or args.file)
        _out(ctx, {"item": str(args.item), "bytes": len(value), "file": args.out or args.file, "method": "rebuild"})
    else:
        if args.out and args.out != args.file:
            import shutil
            shutil.copyfile(args.file, args.out)
        qcn_mod.edit_item_inplace(args.out or args.file, args.item, value, storage=args.storage)
        _out(ctx, {"item": str(args.item), "bytes": len(value), "file": args.out or args.file, "method": "in-place"})


def efs_qcn_diff(ctx, args):
    a = {f"{i.storage}/{i.item}": i.value for i in qcn_mod.Qcn.load(args.a).items()}
    b = {f"{i.storage}/{i.item}": i.value for i in qcn_mod.Qcn.load(args.b).items()}
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    _out(ctx, {"only_in_a": only_a, "only_in_b": only_b, "changed": changed,
               "changed_detail": {k: {"a": a[k].hex(), "b": b[k].hex()} for k in changed[:50]}})


def efs_nvcrc(ctx, args):
    data = Path(args.file).read_bytes() if args.file else bytes.fromhex(args.hex.replace(" ", ""))
    _out(ctx, {"bytes": len(data), "crc16_x25": f"0x{nvchecksum_mod.crc16_x25(data):04x}",
               "md5": nvchecksum_mod.samsung_md5_hex(data)})


def _build_efs(sub):
    g = sub.add_parser("efs", aliases=["nv"], help="EFS / NV (IMEI, calibration) backup, restore, QCN, checksums")
    s = g.add_subparsers(dest="command", metavar="<command>")
    s.required = True
    s.add_parser("detect", help="detect chipset and list EFS/NV partitions that exist").set_defaults(func=efs_detect)
    x = s.add_parser("backup", help="atomic backup of the EFS/NV partition group (root)")
    x.add_argument("directory", nargs="?", help="output dir (default backups/efs-<timestamp>)")
    x.add_argument("--group", default="modem-nv", help="modem-nv (default), persist, modem-fw")
    x.add_argument("--chipset", default="auto", choices=["auto", "qualcomm", "mediatek", "samsung", "unknown"])
    x.add_argument("--no-efs-fs", action="store_true", help="skip pulling the Samsung /efs filesystem")
    x.set_defaults(func=efs_backup)
    x = s.add_parser("restore", help="atomic restore of the EFS/NV group with mirror handling (root)")
    x.add_argument("directory")
    x.add_argument("--group", help="limit to this group")
    x.add_argument("--partitions", help="comma separated subset to restore")
    x.add_argument("--no-rollback", action="store_true", help="do not dump current content before writing")
    x.set_defaults(func=efs_restore)
    x = s.add_parser("validate", help="check erased/mirror state, EFS2/ext4/nvram structure and Samsung md5 sidecars (root)")
    x.add_argument("--group", default="modem-nv")
    x.add_argument("--chipset", default="auto", choices=["auto", "qualcomm", "mediatek", "samsung", "unknown"])
    x.add_argument("--no-deep", action="store_true", help="skip dumping partitions for the internal structure check")
    x.set_defaults(func=efs_validate)
    x = s.add_parser("check-image", help="offline structure check of dumped modemst/fsg (EFS2), nvdata (ext4), nvram images")
    x.add_argument("paths", nargs="+", help="image files and/or mrt EFS backup directories")
    x.set_defaults(func=efs_check_image)
    x = s.add_parser("rebuild-modemst", help="Qualcomm: erase modemst1/2 so the modem rebuilds them from fsg (backup + fsg check first, root)")
    x.add_argument("--backup-dir", help="where to store the mandatory pre-rebuild backup")
    x.add_argument("--reboot", action="store_true", help="reboot immediately after erasing")
    x.set_defaults(func=efs_rebuild_modemst)
    x = s.add_parser("mtk-rebuild-nvdata", help="MediaTek: empty nvdata so nvram_daemon restores it from the nvram backup (backup + check first, root)")
    x.add_argument("--backup-dir", help="where to store the mandatory pre-rebuild backup")
    x.add_argument("--reboot", action="store_true", help="reboot immediately after emptying nvdata")
    x.set_defaults(func=efs_mtk_rebuild_nvdata)
    x = s.add_parser("samsung-fix-md5", help="recompute Samsung nv_data.bin.md5 sidecars")
    x.add_argument("--efs-dir", help="device /efs dir (auto-detected)")
    x.add_argument("--local", help="fix a pulled copy in this local directory instead of the device")
    x.add_argument("--create", action="store_true", help="also create missing sidecars for known files")
    x.set_defaults(func=efs_samsung_fix_md5)
    x = s.add_parser("nv-crc", help="compute DIAG CRC-16/X-25 and MD5 of a value")
    x.add_argument("--hex", help="value as hex")
    x.add_argument("--file", help="value from a file")
    x.set_defaults(func=efs_nvcrc)

    q = s.add_parser("qcn", help="offline QCN (QPST backup) inspect/extract/edit")
    qs = q.add_subparsers(dest="qcn_command", metavar="<command>")
    qs.required = True
    x = qs.add_parser("info", help="list storages and NV items in a QCN")
    x.add_argument("file")
    x.set_defaults(func=efs_qcn_info)
    x = qs.add_parser("extract", help="extract one item or all items")
    x.add_argument("file")
    x.add_argument("--item", help="a single NV item number")
    x.add_argument("--storage", help="restrict to a storage name")
    x.add_argument("--out", help="output file (single item) or directory (all)")
    x.set_defaults(func=efs_qcn_extract)
    x = qs.add_parser("edit", help="edit an NV item value inside the QCN (offline)")
    x.add_argument("file")
    x.add_argument("item")
    x.add_argument("--value", help="new value as hex")
    x.add_argument("--value-ascii", help="new value as ascii text")
    x.add_argument("--value-file", help="new value from a file")
    x.add_argument("--storage", help="storage name (default NV_ITEM_ARRAY on rebuild)")
    x.add_argument("--out", help="write to a new file instead of in place")
    x.add_argument("--rebuild", action="store_true", help="rebuild the container (needed if length changes)")
    x.set_defaults(func=efs_qcn_edit)
    x = qs.add_parser("diff", help="compare NV items of two QCN files")
    x.add_argument("a")
    x.add_argument("b")
    x.set_defaults(func=efs_qcn_diff)



def cmd_menu(ctx, args):
    from .menu import run_menu

    run_menu(ctx, build_parser())


# ======================================================================== parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mrt",
        description="Mobile Repair Toolkit - full-access Android repair & recovery (ADB / Fastboot / raw partitions).",
        epilog="Run 'mrt <group> -h' for group help, 'mrt menu' for the interactive menu. Logs: ~/.mrt/logs",
    )
    p.add_argument("--version", action="version", version=f"mrt {__version__}")
    p.add_argument("-s", "--serial", help="target device serial (adb or fastboot)")
    p.add_argument("--adb", help="path to adb binary")
    p.add_argument("--fastboot", help="path to fastboot binary")
    p.add_argument("-y", "--yes", action="store_true", help="answer all safety prompts automatically (dangerous)")
    p.add_argument("-n", "--dry-run", action="store_true", help="print host commands instead of executing them")
    p.add_argument("--json", action="store_true", help="machine-readable JSON output")
    p.add_argument("-v", "--verbose", action="store_true", help="echo every host command")
    p.add_argument("--log-dir", help="operation log directory (default ~/.mrt/logs or $MRT_LOG_DIR)")
    p.add_argument("--no-log", action="store_true", help="disable the operation log")
    p.add_argument("--no-adb-root", action="store_true", help="never try 'adb root' (use su only)")

    sub = p.add_subparsers(dest="group", metavar="<group>")
    sub.required = True

    sub.add_parser("devices", help="list connected devices (adb + fastboot)").set_defaults(func=cmd_devices)
    sub.add_parser("doctor", help="check adb/fastboot installation, drivers and devices").set_defaults(func=cmd_doctor)
    sub.add_parser("menu", help="interactive menu (bilingual)").set_defaults(func=cmd_menu)

    sp = sub.add_parser("info", help="read complete device information")
    sp.add_argument("--full", action="store_true", help="include all properties, mounts, packages")
    sp.add_argument("--imei", action="store_true", help="try to read IMEI (root needed on Android 10+)")
    sp.add_argument("--save", metavar="FILE", help="also save JSON report to FILE")
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("props", help="dump system properties (getprop)")
    sp.add_argument("pattern", nargs="?", help="substring filter")
    sp.set_defaults(func=cmd_props)

    sp = sub.add_parser("shell", help="run a shell command on the device (interactive if empty)")
    sp.add_argument("--root", action="store_true", help="run as root (adb root / su)")
    sp.add_argument("--timeout", type=int, default=600)
    sp.add_argument("command", nargs=argparse.REMAINDER)
    sp.set_defaults(func=cmd_shell)

    sp = sub.add_parser("reboot", help="reboot into a mode from any current mode")
    sp.add_argument("target", nargs="?", default="system", choices=recovery_mod.REBOOT_TARGETS)
    sp.set_defaults(func=cmd_reboot)

    sp = sub.add_parser("wait", help="wait for the device to appear in a mode")
    sp.add_argument("mode", choices=["device", "recovery", "sideload", "fastboot", "bootloader"])
    sp.add_argument("--timeout", type=int, default=180)
    sp.set_defaults(func=cmd_wait)

    _build_fastboot(sub)
    _build_partitions(sub)
    _build_backup(sub)
    _build_rom(sub)
    _build_recovery(sub)
    _build_apps(sub)
    _build_logs(sub)
    _build_screen(sub)
    _build_efs(sub)
    return p


def _build_fastboot(sub):
    g = sub.add_parser("fastboot", aliases=["fb"], help="fastboot operations")
    s = g.add_subparsers(dest="command", metavar="<command>")
    s.required = True
    x = s.add_parser("getvar", help="read bootloader variables")
    x.add_argument("name", nargs="?", default="all")
    x.set_defaults(func=fb_getvar)
    s.add_parser("partitions", help="partition table as reported by the bootloader").set_defaults(func=fb_partitions)
    x = s.add_parser("flash", help="flash an image to a partition")
    x.add_argument("partition")
    x.add_argument("image")
    x.add_argument("--slot", choices=["a", "b", "all"])
    x.set_defaults(func=fb_flash)
    x = s.add_parser("erase", help="erase a partition")
    x.add_argument("partition")
    x.set_defaults(func=fb_erase)
    x = s.add_parser("format", help="format a partition")
    x.add_argument("partition")
    x.add_argument("--fs", help="filesystem type, e.g. ext4 / f2fs")
    x.set_defaults(func=fb_format)
    x = s.add_parser("boot", help="boot an image without flashing (test kernels / TWRP)")
    x.add_argument("image")
    x.set_defaults(func=fb_boot)
    x = s.add_parser("reboot", help="reboot from fastboot")
    x.add_argument("target", nargs="?", default="system", choices=["system", "bootloader", "recovery", "fastboot"])
    x.set_defaults(func=fb_reboot)
    for name, fn in (("unlock", fb_unlock), ("lock", fb_lock)):
        x = s.add_parser(name, help=f"{name} the bootloader")
        x.add_argument("--method", choices=["auto", "flashing", "oem"], default="auto")
        x.add_argument("--critical", action="store_true", help=f"use flashing {name}_critical")
        x.set_defaults(func=fn)
    x = s.add_parser("set-active", help="set active A/B slot")
    x.add_argument("slot", choices=["a", "b"])
    x.set_defaults(func=fb_set_active)
    x = s.add_parser("oem", help="run 'fastboot oem ...'")
    x.add_argument("args", nargs="+")
    x.set_defaults(func=fb_oem)
    s.add_parser("wipe", help="erase userdata and cache (fastboot -w)").set_defaults(func=fb_wipe)
    s.add_parser("continue", help="continue booting").set_defaults(func=fb_continue)
    x = s.add_parser("raw", help="run any raw fastboot command")
    x.add_argument("args", nargs="+")
    x.set_defaults(func=fb_raw)


def _build_partitions(sub):
    g = sub.add_parser("part", aliases=["partition", "partitions"], help="raw partition read/write with dd (root)")
    s = g.add_subparsers(dest="command", metavar="<command>")
    s.required = True
    s.add_parser("list", help="list partitions with sizes").set_defaults(func=part_list)
    s.add_parser("table", help="block device / super partition layout").set_defaults(func=part_table)
    x = s.add_parser("dump", help="bit-by-bit dump of a partition to a file")
    x.add_argument("name", help="partition name (boot, boot_a, /dev/block/...)")
    x.add_argument("output", nargs="?", help="output file (default <name>.img)")
    x.add_argument("--no-verify", action="store_true")
    x.add_argument("--bs", default="4M", help="dd block size")
    x.set_defaults(func=part_dump)
    x = s.add_parser("dump-range", help="dump a byte range of a partition")
    x.add_argument("name")
    x.add_argument("output")
    x.add_argument("--offset", required=True, help="byte offset (supports K/M/G, 0x..)")
    x.add_argument("--length", required=True, help="byte length")
    x.set_defaults(func=part_dump_range)
    x = s.add_parser("dump-all", help="dump every partition into a directory (manifest + sha256)")
    x.add_argument("directory")
    x.add_argument("--include", help="comma separated names to include")
    x.add_argument("--exclude", help="comma separated names to exclude")
    x.add_argument("--max-size", help="skip partitions larger than this (e.g. 512M)")
    x.add_argument("--with-data", action="store_true", help="also dump userdata/super/system (very large)")
    x.add_argument("--no-verify", action="store_true")
    x.set_defaults(func=part_dump_all)
    x = s.add_parser("write", help="write an image to a partition with dd (verified)")
    x.add_argument("name")
    x.add_argument("image")
    x.add_argument("--no-verify", action="store_true")
    x.add_argument("--exact-size", action="store_true", help="refuse images smaller than the partition")
    x.add_argument("--bs", default="4M")
    x.set_defaults(func=part_write)
    x = s.add_parser("compare", help="compare an image with the partition content")
    x.add_argument("name")
    x.add_argument("image")
    x.set_defaults(func=part_compare)
    x = s.add_parser("wipe", help="zero-fill a partition")
    x.add_argument("name")
    x.set_defaults(func=part_wipe)


def _build_backup(sub):
    g = sub.add_parser("backup", help="full backup / restore")
    s = g.add_subparsers(dest="command", metavar="<command>")
    s.required = True
    x = s.add_parser("create", help="create a backup directory")
    x.add_argument("directory", nargs="?", help="output directory (default backups/<timestamp>)")
    x.add_argument("--no-apps", action="store_true", help="skip APK backup")
    x.add_argument("--all-apps", action="store_true", help="include system APKs too")
    x.add_argument("--app-data", action="store_true", help="adb backup of app data (needs on-device confirmation)")
    x.add_argument("--sdcard", action="store_true", help="pull internal storage")
    x.add_argument("--sdcard-path", default="/sdcard")
    x.add_argument("--partitions", help="comma separated partitions to image (root)")
    x.add_argument("--critical-partitions", action="store_true", help="image boot/efs/modem/persist/nv... (root)")
    x.add_argument("--all-partitions", action="store_true", help="image every partition except userdata (root)")
    x.add_argument("--with-userdata", action="store_true", help="include userdata in --all-partitions")
    x.set_defaults(func=backup_create)
    x = s.add_parser("restore", help="restore from a backup directory")
    x.add_argument("directory")
    x.add_argument("--apps", action="store_true")
    x.add_argument("--app-data", action="store_true")
    x.add_argument("--sdcard", action="store_true")
    x.add_argument("--sdcard-path", default="/sdcard")
    x.add_argument("--partitions", help="comma separated partitions to write back (root, dangerous)")
    x.set_defaults(func=backup_restore)
    x = s.add_parser("verify", help="verify partition image hashes in a backup")
    x.add_argument("directory")
    x.set_defaults(func=backup_verify)


def _add_flash_opts(x):
    x.add_argument("--slot", choices=["a", "b", "all"])
    x.add_argument("--only", help="comma separated partitions to flash")
    x.add_argument("--skip", help="comma separated partitions to skip")
    x.add_argument("--wipe", action="store_true", help="fastboot -w after flashing")
    x.add_argument("--with-data", action="store_true", help="allow flashing userdata/persist/etc. if present")
    x.add_argument("--reboot", action="store_true", help="reboot to system when done")
    x.add_argument("--disable-verity", action="store_true")
    x.add_argument("--disable-verification", action="store_true")


def _build_rom(sub):
    g = sub.add_parser("rom", help="ROM flashing (fastboot images, payload.bin, factory zips)")
    s = g.add_subparsers(dest="command", metavar="<command>")
    s.required = True
    x = s.add_parser("inspect", help="identify a ROM package and how to flash it")
    x.add_argument("path")
    x.set_defaults(func=rom_inspect)
    x = s.add_parser("flash-dir", help="flash all *.img in a directory via fastboot")
    x.add_argument("directory")
    _add_flash_opts(x)
    x.set_defaults(func=rom_flash_dir)
    x = s.add_parser("payload", help="extract payload.bin / OTA zip and flash via fastboot")
    x.add_argument("source", help="payload.bin or OTA zip")
    x.add_argument("--workdir", help="where to extract images")
    x.add_argument("--extract-only", action="store_true")
    _add_flash_opts(x)
    x.set_defaults(func=rom_payload)
    x = s.add_parser("payload-info", help="list partitions inside a payload.bin / OTA zip")
    x.add_argument("source")
    x.set_defaults(func=rom_payload_info)
    x = s.add_parser("update-zip", help="fastboot update <factory zip>")
    x.add_argument("zip")
    x.add_argument("--wipe", action="store_true")
    x.set_defaults(func=rom_update_zip)
    x = s.add_parser("sideload", help="adb sideload a zip through recovery")
    x.add_argument("zip")
    x.add_argument("--no-auto-reboot", action="store_true")
    x.set_defaults(func=rec_sideload)


def _build_recovery(sub):
    g = sub.add_parser("recovery", aliases=["rec"], help="recovery mode helpers")
    s = g.add_subparsers(dest="command", metavar="<command>")
    s.required = True
    x = s.add_parser("sideload", help="adb sideload a zip")
    x.add_argument("zip")
    x.add_argument("--no-auto-reboot", action="store_true")
    x.set_defaults(func=rec_sideload)
    x = s.add_parser("wipe", help="wipe cache / data / dalvik (TWRP or stock)")
    x.add_argument("what", choices=["cache", "data", "dalvik", "system"])
    x.set_defaults(func=rec_wipe)
    s.add_parser("factory-reset", help="factory reset").set_defaults(func=rec_factory_reset)
    x = s.add_parser("flash", help="flash a recovery image via fastboot")
    x.add_argument("image")
    x.add_argument("--slot", choices=["a", "b", "all"])
    x.add_argument("--boot", action="store_true", help="reboot into recovery afterwards")
    x.set_defaults(func=rec_flash)
    x = s.add_parser("push", help="push files (ROM zip, Magisk) to the device")
    x.add_argument("files", nargs="+")
    x.add_argument("--to", default="/sdcard/")
    x.set_defaults(func=rec_push)
    x = s.add_parser("twrp", help="run a TWRP command (twrp install|wipe|backup|restore ...)")
    x.add_argument("args", nargs="+")
    x.set_defaults(func=rec_twrp)
    s.add_parser("status", help="show current mode of connected devices").set_defaults(func=rec_status)


def _build_apps(sub):
    g = sub.add_parser("apps", aliases=["app", "pm"], help="application management")
    s = g.add_subparsers(dest="command", metavar="<command>")
    s.required = True
    x = s.add_parser("list", help="list packages")
    x.add_argument("--filter", choices=["all", "third", "system", "disabled", "enabled", "uninstalled"], default="all")
    x.add_argument("--user", type=int)
    x.add_argument("--versions", action="store_true")
    x.add_argument("--grep", help="substring filter")
    x.set_defaults(func=apps_list)
    x = s.add_parser("info", help="detailed package info")
    x.add_argument("package")
    x.set_defaults(func=apps_info)
    x = s.add_parser("install", help="install APK(s) (split APKs supported)")
    x.add_argument("files", nargs="+")
    x.add_argument("--no-reinstall", action="store_true")
    x.add_argument("--downgrade", action="store_true")
    x.add_argument("--no-grant", action="store_true", help="do not auto-grant runtime permissions")
    x.add_argument("--test", action="store_true", help="allow test APKs")
    x.add_argument("--user", type=int)
    x.set_defaults(func=apps_install)
    x = s.add_parser("uninstall", help="uninstall a package")
    x.add_argument("package")
    x.add_argument("--keep-data", action="store_true")
    x.add_argument("--user", type=int, default=None, help="uninstall only for this user (0 = remove bloat without root)")
    x.add_argument("--system", action="store_true", help="system app: uninstall for user 0")
    x.set_defaults(func=apps_uninstall)
    for name, fn in (("disable", apps_disable), ("enable", apps_enable)):
        x = s.add_parser(name, help=f"{name} a package")
        x.add_argument("package")
        x.add_argument("--user", type=int, default=0)
        x.set_defaults(func=fn)
    x = s.add_parser("clear", help="clear app data")
    x.add_argument("package")
    x.set_defaults(func=apps_clear)
    x = s.add_parser("stop", help="force stop")
    x.add_argument("package")
    x.set_defaults(func=apps_stop)
    x = s.add_parser("pull", help="pull APK (+ splits)")
    x.add_argument("package")
    x.add_argument("--out", default="apks")
    x.set_defaults(func=apps_pull)
    x = s.add_parser("backup", help="adb backup of one app's data")
    x.add_argument("package")
    x.add_argument("file")
    x.add_argument("--no-apk", action="store_true")
    x.set_defaults(func=apps_backup)
    x = s.add_parser("restore", help="adb restore from .ab file")
    x.add_argument("file")
    x.set_defaults(func=apps_restore)
    for name, fn in (("grant", apps_grant), ("revoke", apps_revoke)):
        x = s.add_parser(name, help=f"{name} a runtime permission")
        x.add_argument("package")
        x.add_argument("permission")
        x.set_defaults(func=fn)
    x = s.add_parser("debloat", help="disable/uninstall many packages (names or a list file)")
    x.add_argument("packages", nargs="+")
    x.add_argument("--mode", choices=["disable", "uninstall", "enable", "reinstall"], default="disable")
    x.add_argument("--user", type=int, default=0)
    x.set_defaults(func=apps_debloat)
    x = s.add_parser("ps", help="running processes")
    x.add_argument("--top", type=int, default=40)
    x.set_defaults(func=apps_ps)


def _build_logs(sub):
    g = sub.add_parser("logs", aliases=["log"], help="logs and diagnostics")
    s = g.add_subparsers(dest="command", metavar="<command>")
    s.required = True
    x = s.add_parser("logcat", help="dump or follow logcat")
    x.add_argument("-o", "--output", help="save to file")
    x.add_argument("-b", "--buffers", default="all", help="main,system,radio,events,crash,all")
    x.add_argument("-f", "--follow", action="store_true")
    x.add_argument("-c", "--clear", action="store_true", help="clear buffers first")
    x.add_argument("filters", nargs="*", help="logcat filter specs, e.g. '*:E' 'ActivityManager:I'")
    x.set_defaults(func=logs_logcat)
    x = s.add_parser("dmesg", help="kernel log")
    x.add_argument("-o", "--output")
    x.set_defaults(func=logs_dmesg)
    x = s.add_parser("last-kmsg", help="pull pstore / last_kmsg (previous boot crash logs)")
    x.add_argument("directory")
    x.set_defaults(func=logs_last_kmsg)
    x = s.add_parser("bugreport", help="full bugreport zip")
    x.add_argument("output", nargs="?", default="bugreport.zip")
    x.set_defaults(func=logs_bugreport)
    x = s.add_parser("dumpsys", help="dumpsys [service] [args]")
    x.add_argument("service", nargs="?")
    x.add_argument("args", nargs="*")
    x.add_argument("-o", "--output")
    x.set_defaults(func=logs_dumpsys)
    x = s.add_parser("crashes", help="pull ANR traces, tombstones, dropbox (root)")
    x.add_argument("directory")
    x.set_defaults(func=logs_crashes)
    x = s.add_parser("collect", help="collect everything into a diagnostics bundle")
    x.add_argument("directory", nargs="?")
    x.set_defaults(func=logs_collect)
    s.add_parser("battery", help="battery statistics").set_defaults(func=logs_battery)
    s.add_parser("boot-reason", help="last boot / shutdown reason").set_defaults(func=logs_boot_reason)


def _build_screen(sub):
    g = sub.add_parser("screen", help="screenshot, screen record, input injection, touch test")
    s = g.add_subparsers(dest="command", metavar="<command>")
    s.required = True
    x = s.add_parser("shot", help="screenshot to PNG")
    x.add_argument("output", nargs="?", default="screenshot.png")
    x.set_defaults(func=screen_shot)
    x = s.add_parser("record", help="record the screen")
    x.add_argument("output", nargs="?", default="record.mp4")
    x.add_argument("--seconds", type=int, default=10)
    x.add_argument("--size")
    x.add_argument("--bitrate")
    x.set_defaults(func=screen_record)
    x = s.add_parser("tap")
    x.add_argument("x", type=int)
    x.add_argument("y", type=int)
    x.set_defaults(func=screen_tap)
    x = s.add_parser("swipe")
    for a in ("x1", "y1", "x2", "y2"):
        x.add_argument(a, type=int)
    x.add_argument("--ms", type=int, default=300)
    x.set_defaults(func=screen_swipe)
    x = s.add_parser("key", help="send a key event (e.g. KEYCODE_POWER, 26)")
    x.add_argument("key")
    x.set_defaults(func=screen_key)
    x = s.add_parser("text", help="type text")
    x.add_argument("text", nargs="+")
    x.set_defaults(func=screen_text)
    x = s.add_parser("touch-test", help="print raw touch events")
    x.add_argument("--seconds", type=int, default=10)
    x.set_defaults(func=screen_touch_test)
    x = s.add_parser("unlock", help="wake + swipe + optional PIN")
    x.add_argument("--pin")
    x.set_defaults(func=screen_unlock)


# ========================================================================== main


def make_context(args) -> Context:
    log = OperationLog(log_dir=args.log_dir, verbose=args.verbose, enabled=not args.no_log)
    runner = Runner(adb=args.adb, fastboot=args.fastboot, log=log, dry_run=args.dry_run, verbose=args.verbose)
    safety = Safety(assume_yes=args.yes, log=log)
    return Context(runner, log, safety, serial=args.serial, json_output=args.json, no_adb_root=args.no_adb_root)


def dispatch(parser: argparse.ArgumentParser, argv: List[str], ctx: Optional[Context] = None) -> int:
    args = parser.parse_args(argv)
    own_ctx = ctx is None
    ctx = ctx or make_context(args)
    if not own_ctx:
        ctx.json = args.json or ctx.json
    try:
        args.func(ctx, args)
        return 0
    except MrtError as exc:
        ctx.log.error(str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    finally:
        if own_ctx:
            ctx.log.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    return dispatch(parser, list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
