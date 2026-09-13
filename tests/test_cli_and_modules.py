import json
import os

import pytest

from mrt import cli
from mrt.core.device import AdbDevice
from mrt.core.errors import Aborted
from mrt.core.safety import CRITICAL, DESTRUCTIVE, Safety
from mrt.menu import _fill
from mrt.modules import apps, info, logs, recovery

from .conftest import FakeRunner, ScriptedSafety, make_ctx


def test_cli_devices_json(capsys):
    runner = FakeRunner()
    runner.add("adb devices -l", "List of devices attached\nA1\tdevice product:x model:Y\n")
    runner.add("fastboot devices -l", "FB1\tfastboot\n")
    ctx = make_ctx(runner, json_output=True)
    rc = cli.dispatch(cli.build_parser(), ["--json", "devices"], ctx)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert [(d["serial"], d["transport"]) for d in data] == [("A1", "adb"), ("FB1", "fastboot")]


def test_cli_dry_run_prints_commands(capsys):
    runner = FakeRunner(dry_run=True)
    ctx = make_ctx(runner)
    rc = cli.dispatch(cli.build_parser(), ["-n", "fastboot", "getvar", "all"], ctx)
    assert rc == 0
    assert runner.calls[-1] == ["fastboot", "-s", "SER", "getvar", "all"]


def test_cli_error_exit_code(capsys):
    runner = FakeRunner()
    runner.add("adb devices -l", "List of devices attached\n")
    ctx = make_ctx(runner, serial=None)
    rc = cli.dispatch(cli.build_parser(), ["part", "list"], ctx)
    assert rc == 3  # DeviceNotFound
    assert "error:" in capsys.readouterr().err


def test_cli_part_list_table(capsys, rooted_runner: FakeRunner):
    rooted_runner.add("MRT|", "MRT|boot_a|/dev/block/sde11|134217728|262144\n")
    rc = cli.dispatch(cli.build_parser(), ["part", "list"], make_ctx(rooted_runner))
    assert rc == 0
    out = capsys.readouterr().out
    assert "boot_a" in out and "128.00 MiB" in out


def test_apps_list_parsing(runner: FakeRunner):
    runner.add("pm list packages -f -3 --show-versioncode", "package:/data/app/com.a-1/base.apk=com.a versionCode:12\npackage:/data/app/b/base.apk=org.b versionCode:3\n")
    rows = apps.list_packages(AdbDevice(runner, "SER"), "third", versions=True)
    assert rows == [{"package": "com.a", "path": "/data/app/com.a-1/base.apk", "versionCode": 12}, {"package": "org.b", "path": "/data/app/b/base.apk", "versionCode": 3}]
    assert apps.list_packages(AdbDevice(runner, "SER"), "third", versions=True, pattern="org")[0]["package"] == "org.b"


def test_apps_install_and_uninstall(runner: FakeRunner, tmp_path):
    apk = tmp_path / "a.apk"
    apk.write_bytes(b"PK")
    runner.add("install", "Success\n")
    runner.add("pm uninstall -k --user 0 com.x", "Success\n")
    dev = AdbDevice(runner, "SER")
    apps.install(dev, [str(apk)], downgrade=True)
    assert runner.calls[-1] == ["adb", "-s", "SER", "install", "-r", "-d", "-g", str(apk)]
    apps.uninstall(dev, "com.x", ScriptedSafety(answers=["y"]), keep_data=True, user=0)
    assert "pm uninstall -k --user 0 com.x" in runner.joined_calls()[-1]


def test_apps_debloat_disable(runner: FakeRunner):
    runner.add("pm disable-user --user 0 com.bloat", "Package com.bloat new state: disabled-user\n")
    runner.add("pm disable-user --user 0 com.missing", "Error: package not found\n")
    res = apps.debloat(AdbDevice(runner, "SER"), ["com.bloat", "com.missing"], ScriptedSafety(answers=["y"]))
    assert "disabled-user" in res["com.bloat"] and res["com.missing"].startswith("FAILED")


def test_info_collect_adb(runner: FakeRunner):
    runner.add("shell id", "uid=2000(shell)\n")
    runner.add("root", "adbd cannot run as root\n")
    runner.add("shell getprop", "[ro.product.model]: [Pixel 4a]\n[ro.build.version.release]: [13]\n[ro.boot.verifiedbootstate]: [green]\n[ro.boot.slot_suffix]: [_a]\n")
    runner.add("get-state", "device\n")
    runner.add("cat /proc/cpuinfo", "processor\t: 0\nHardware\t: Qualcomm SM7150\nprocessor\t: 1\n")
    runner.add("cat /proc/meminfo", "MemTotal:        5806048 kB\nMemFree:          200000 kB\n")
    runner.add("df -h", "Filesystem Size Used Avail Use% Mounted on\n/dev/block/dm-8 100G 50G 50G 50% /data\n")
    runner.add("dumpsys battery", "Current Battery Service state:\n  level: 87\n  health: 2\n")
    data = info.collect_adb(AdbDevice(runner, "SER"))
    assert data["identity"]["model"] == "Pixel 4a" and data["identity"]["android_version"] == "13"
    assert data["cpu"]["cores"] == 2 and data["cpu"]["Hardware"] == "Qualcomm SM7150"
    assert data["memory"]["MemTotal"] == "5.54 GiB"
    assert data["storage"][0]["mount"] == "/data"
    assert data["battery"]["level"] == "87"
    assert data["security"]["root_mode"] == "none" and data["slots"]["current"] == "_a"


def test_info_collect_fastboot(runner: FakeRunner):
    runner.add("getvar all", stderr="(bootloader) product: sunfish\n(bootloader) unlocked: yes\n(bootloader) partition-size:boot_a: 0x4000000\n(bootloader) partition-type:boot_a: raw\n(bootloader) has-slot:boot: yes\n")
    from mrt.core.device import FastbootDevice

    data = info.collect_fastboot(FastbootDevice(runner, "FB"))
    assert data["unlocked"] is True
    assert data["partitions"] == [{"name": "boot_a", "size": 0x4000000, "type": "raw", "slot": ""}]


def test_parse_service_call_imei():
    text = (
        "Result: Parcel(\n"
        "  0x00000000: 00000000 0000000f 00350033 00380036 '........3.5.8.6.'\n"
        "  0x00000010: 00380030 00310035 00320034 00370038 '8.0.1.5.2.4.7.8.'\n"
        "  0x00000020: 00350030 00000032                   '5.0.2...        ')\n"
    )
    assert info.parse_service_call(text) == "358680152478502"


def test_reboot_routes_by_transport(runner: FakeRunner):
    runner.add("adb devices -l", "List of devices attached\n")
    runner.add("fastboot devices -l", "FB\tfastboot\n")
    recovery.reboot(runner, None, "edl")
    assert runner.calls[-1] == ["fastboot", "-s", "FB", "oem", "edl"]
    runner2 = FakeRunner()
    runner2.add("adb devices -l", "List of devices attached\nA\tdevice\n")
    runner2.add("fastboot devices -l", "")
    recovery.reboot(runner2, None, "fastbootd")
    assert runner2.calls[-1] == ["adb", "-s", "A", "reboot", "fastboot"]


def test_sideload_from_system(runner: FakeRunner, tmp_path):
    z = tmp_path / "ota.zip"
    z.write_bytes(b"PK")
    runner.add("get-state", "device\n")
    runner.add("sideload", "Total xfer: 1.00x\n")
    out = recovery.sideload(runner, "SER", str(z), ScriptedSafety(answers=["y"]))
    calls = runner.joined_calls()
    assert any(c.endswith("reboot sideload-auto-reboot") for c in calls)
    assert any(c.endswith("wait-for-sideload") for c in calls)
    assert calls[-1].endswith(f"sideload {z}") and out == "sideload finished"


def test_logs_logcat_to_file(runner: FakeRunner, tmp_path):
    runner.add("logcat -b all -v threadtime -d", "01-01 00:00:00.000 1 1 E Tag: boom\n")
    out = logs.logcat(AdbDevice(runner, "SER"), str(tmp_path / "lc.txt"), filters=["*:E"], clear=True)
    assert (tmp_path / "lc.txt").read_text().startswith("01-01")
    assert runner.joined_calls()[0].endswith("logcat -b all -c")
    assert runner.joined_calls()[-1].endswith("logcat -b all -v threadtime -d *:E")


def test_safety_levels():
    s = ScriptedSafety(answers=["n"])
    with pytest.raises(Aborted):
        s.confirm("x", DESTRUCTIVE)
    s = ScriptedSafety(answers=["yes"])
    assert s.confirm("x", DESTRUCTIVE)
    s = ScriptedSafety(answers=["YES"])
    assert s.confirm("x", CRITICAL)
    s = ScriptedSafety(answers=["boot"])
    with pytest.raises(Aborted):
        s.confirm("x", CRITICAL, token="boot_a")
    assert ScriptedSafety(assume_yes=True).confirm("x", CRITICAL, token="anything")


def test_menu_fill_template():
    answers = iter(["boot_a", "out.img"])
    argv = _fill("part dump {partition} {output}", lambda prompt: next(answers))
    assert argv == ["part", "dump", "boot_a", "out.img"]
    argv = _fill("fastboot oem {args}", lambda prompt: "device-info")
    assert argv == ["fastboot", "oem", "device-info"]
    assert _fill("apps info {package}", lambda prompt: "") is None


def test_menu_runs_command(capsys):
    from mrt.menu import run_menu

    runner = FakeRunner()
    runner.add("adb devices -l", "List of devices attached\nA1\tdevice\n")
    runner.add("fastboot devices -l", "")
    answers = iter(["1", "1", "q"])
    run_menu(make_ctx(runner), cli.build_parser(), input_fn=lambda p: next(answers))
    out = capsys.readouterr().out
    assert "A1" in out and "[exit code 0]" in out


def test_every_subcommand_has_handler():
    parser = cli.build_parser()
    groups = parser._subparsers._group_actions[0].choices
    for name, sub in groups.items():
        actions = [a for a in sub._actions if isinstance(a, type(parser._subparsers._group_actions[0]))]
        if not actions:
            assert sub.get_default("func") is not None, name
            continue
        for cname, csub in actions[0].choices.items():
            assert csub.get_default("func") is not None, f"{name} {cname}"
