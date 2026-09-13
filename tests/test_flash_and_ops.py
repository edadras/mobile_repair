import os
import zipfile

import pytest

from mrt.core.errors import Aborted, MrtError
from mrt.modules import fastboot_ops, flash

from .conftest import FakeRunner, ScriptedSafety


def test_plan_flash_order_and_filters(tmp_path):
    for name in ("system", "boot", "userdata", "vbmeta", "bootloader", "persist", "dtbo", "zzz_custom"):
        (tmp_path / f"{name}.img").write_bytes(b"x")
    images = flash.discover_images(str(tmp_path))
    plan = flash.plan_flash(images)
    assert plan == ["bootloader", "vbmeta", "boot", "dtbo", "system", "zzz_custom"]
    assert flash.plan_flash(images, include_data=True)[-2:] == ["userdata", "zzz_custom"] or "userdata" in flash.plan_flash(images, include_data=True)
    assert flash.plan_flash(images, only=["boot", "dtbo"]) == ["boot", "dtbo"]
    assert "boot" not in flash.plan_flash(images, skip=["boot"])


def test_flash_directory_sequence(runner: FakeRunner, tmp_path):
    for name in ("boot", "vbmeta", "dtbo"):
        (tmp_path / f"{name}.img").write_bytes(b"img")
    runner.add("getvar all", stderr="(bootloader) is-userspace: no\n(bootloader) current-slot: a\n")
    safety = ScriptedSafety(answers=["FLASH"])
    from mrt.core.device import FastbootDevice

    fb = FastbootDevice(runner, "FB")
    res = flash.flash_directory(fb, str(tmp_path), safety, slot="a", disable_verification=True, reboot=True)
    flashed = [r["partition"] for r in res["flashed"]]
    assert flashed == ["vbmeta", "boot", "dtbo"]
    calls = runner.joined_calls()
    vb = [c for c in calls if "flash vbmeta" in c][0]
    assert "--disable-verification" in vb and "--slot a" in vb
    assert calls[-1].endswith("reboot")


def test_flash_directory_stops_on_failure(runner: FakeRunner, tmp_path):
    for name in ("boot", "dtbo"):
        (tmp_path / f"{name}.img").write_bytes(b"img")
    runner.add("flash boot", stderr="FAILED (remote: 'Not allowed in locked state')", rc=1)
    from mrt.core.device import FastbootDevice

    with pytest.raises(MrtError) as exc:
        flash.flash_directory(FastbootDevice(runner, "FB"), str(tmp_path), ScriptedSafety(assume_yes=True))
    assert "locked state" in str(exc.value)
    assert not any("flash dtbo" in c for c in runner.joined_calls())


def test_inspect_rom_kinds(tmp_path):
    z = tmp_path / "rec.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("META-INF/com/google/android/update-binary", "x")
    assert "recovery flashable" in flash.inspect_rom(str(z))["type"]
    d = tmp_path / "imgs"
    d.mkdir()
    (d / "boot.img").write_bytes(b"1")
    (d / "flash-all.sh").write_text("#!/bin/sh\n")
    info = flash.inspect_rom(str(d))
    assert info["type"].startswith("fastboot image directory") and info["scripts"] == ["flash-all.sh"]
    assert flash.inspect_rom(str(tmp_path / "AP_X.tar.md5"))["type"].startswith("Samsung")


def test_fastboot_flash_critical_partition_needs_token(runner: FakeRunner, tmp_path):
    img = tmp_path / "abl.img"
    img.write_bytes(b"a")
    from mrt.core.device import FastbootDevice

    fb = FastbootDevice(runner, "FB")
    with pytest.raises(Aborted):
        fastboot_ops.flash(fb, "abl_a", str(img), ScriptedSafety(answers=["y"]))
    fastboot_ops.flash(fb, "abl_a", str(img), ScriptedSafety(answers=["abl_a"]))
    assert runner.calls[-1][-3:] == ["flash", "abl_a", str(img)]


def test_unlock_tries_flashing_then_oem(runner: FakeRunner):
    runner.add("flashing unlock", stderr="FAILED (remote: unknown command)", rc=1)
    runner.add("oem unlock", stderr="OKAY")
    from mrt.core.device import FastbootDevice

    res = fastboot_ops.unlock(FastbootDevice(runner, "FB"), ScriptedSafety(answers=["UNLOCK"]))
    assert res["ok"] is True and [a["cmd"] for a in res["attempts"]] == ["flashing unlock", "oem unlock"]
