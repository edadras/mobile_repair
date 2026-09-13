import os
import json

import pytest

from mrt.core.device import AdbDevice
from mrt.core.errors import Aborted, MrtError
from mrt.modules import efs

from .conftest import FakeRunner, ScriptedSafety, sha256

LISTING = (
    "MRT|modemst1|/dev/block/sda5||4096\n"
    "MRT|modemst2|/dev/block/sda6||4096\n"
    "MRT|fsg|/dev/block/sda7||4096\n"
    "MRT|fsc|/dev/block/sda8||512\n"
    "MRT|persist|/dev/block/sda9||65536\n"
)


def _rooted_with_parts(props=None):
    r = FakeRunner()
    r.add("adb devices -l", "List of devices attached\nSER\tdevice\n")
    r.add("fastboot devices -l", "")
    r.add("shell id", "uid=0(root) gid=0(root)\n")
    r.add("MRT|", LISTING)
    for k, v in (props or {}).items():
        r.add(f"getprop {k}", v + "\n")
    return r


def test_detect_chipset_qualcomm():
    r = _rooted_with_parts()
    r.add("shell getprop", "[ro.board.platform]: [kona]\n[ro.hardware]: [qcom]\n")
    assert efs.detect_chipset(AdbDevice(r, "SER")) == efs.QUALCOMM


def test_detect_chipset_mediatek_and_samsung():
    r = FakeRunner(); r.add("shell getprop", "[ro.board.platform]: [mt6768]\n")
    assert efs.detect_chipset(AdbDevice(r, "SER")) == efs.MEDIATEK
    r = FakeRunner(); r.add("shell getprop", "[ro.product.manufacturer]: [samsung]\n[ro.board.platform]: [exynos9820]\n")
    assert efs.detect_chipset(AdbDevice(r, "SER")) == efs.SAMSUNG
    r = FakeRunner(); r.add("shell getprop", "[ro.board.platform]: [something]\n")
    assert efs.detect_chipset(AdbDevice(r, "SER")) == efs.UNKNOWN


def test_resolve_group_only_existing():
    r = _rooted_with_parts()
    r.add("getprop ro.boot.slot_suffix", "\n")
    parts = efs.resolve_group(AdbDevice(r, "SER"), efs.QUALCOMM, "modem-nv")
    assert [p.name for p in parts] == ["modemst1", "modemst2", "fsg", "fsc"]


def test_backup_group_atomic(tmp_path):
    r = _rooted_with_parts()
    r.add("shell getprop", "[ro.board.platform]: [kona]\n")
    r.add("getprop ro.boot.slot_suffix", "\n")
    for name, dev in (("modemst1", "sda5"), ("modemst2", "sda6"), ("fsg", "sda7"), ("fsc", "sda8")):
        r.add(f"dd if=/dev/block/{dev}", data=b"\x01" * 2048)
        r.add(f"sha256sum /dev/block/{dev}", sha256(b"\x01" * 2048) + f"  /dev/block/{dev}\n")
    man = efs.backup(AdbDevice(r, "SER"), str(tmp_path / "efs"), ScriptedSafety(assume_yes=True), chipset="qualcomm")
    assert man["chipset"] == "qualcomm"
    names = [p["partition"] for p in man["partitions"]]
    assert names == ["modemst1", "modemst2", "fsg", "fsc"]
    assert all(p["status"] == "ok" for p in man["partitions"])
    assert (tmp_path / "efs" / "efs-manifest.json").exists()
    assert (tmp_path / "efs" / "modemst1.img").read_bytes() == b"\x01" * 2048


def _make_backup(tmp_path, device="raphael"):
    d = tmp_path / "bk"
    d.mkdir()
    for name in ("modemst1", "modemst2", "fsg"):
        (d / f"{name}.img").write_bytes(bytes([1]) * 2048)
    manifest = {
        "type": "mrt-efs-backup", "chipset": "qualcomm", "group": "modem-nv",
        "device": {"ro.product.device": device},
        "partitions": [{"partition": n, "status": "ok", "sha256": sha256(bytes([1]) * 2048)} for n in ("modemst1", "modemst2", "fsg")],
    }
    (d / "efs-manifest.json").write_text(json.dumps(manifest))
    return d


def test_restore_writes_group_with_rollback(tmp_path):
    d = _make_backup(tmp_path, device="raphael")
    r = _rooted_with_parts()
    r.add("getprop ro.product.device", "raphael\n")
    r.add("getprop ro.boot.slot_suffix", "\n")
    for name, dev in (("modemst1", "sda5"), ("modemst2", "sda6"), ("fsg", "sda7")):
        r.add(f"dd if=/dev/block/{dev}", data=b"\x00" * 2048)         # rollback dump
        r.add(f"sha256sum /data/local/tmp/mrt_{name}", sha256(bytes([1]) * 2048) + "  x\n")  # upload check
        r.add(f"head -c 2048 /dev/block/{dev} | sha256sum", sha256(bytes([1]) * 2048) + "  -\n")  # post-write
    res = efs.restore(AdbDevice(r, "SER"), str(d), ScriptedSafety(assume_yes=True))
    assert set(res["restored"]) == {"modemst1", "modemst2", "fsg"}
    assert all(isinstance(v, dict) and v.get("verified") is True for v in res["restored"].values())
    assert res["rollback_dir"] and os.path.isdir(res["rollback_dir"])
    assert os.path.exists(os.path.join(res["rollback_dir"], "modemst1.img"))
    # each partition was written with dd
    for dev in ("sda5", "sda6", "sda7"):
        assert any(f"of=/dev/block/{dev}" in c for c in r.joined_calls())


def test_restore_device_mismatch_needs_token(tmp_path):
    d = _make_backup(tmp_path, device="OTHER")
    r = _rooted_with_parts()
    r.add("getprop ro.product.device", "raphael\n")
    r.add("getprop ro.boot.slot_suffix", "\n")
    with pytest.raises(Aborted):
        efs.restore(AdbDevice(r, "SER"), str(d), ScriptedSafety(answers=["wrong"]))


def test_restore_partial_mirror_warns(tmp_path, capsys):
    d = _make_backup(tmp_path)
    r = _rooted_with_parts()
    r.add("getprop ro.product.device", "raphael\n")
    r.add("getprop ro.boot.slot_suffix", "\n")
    r.add("dd if=/dev/block/sda5", data=b"\x00" * 2048)
    r.add("sha256sum /data/local/tmp/mrt_modemst1", sha256(bytes([1]) * 2048) + "  x\n")
    r.add("head -c 2048 /dev/block/sda5 | sha256sum", sha256(bytes([1]) * 2048) + "  -\n")
    efs.restore(AdbDevice(r, "SER"), str(d), ScriptedSafety(assume_yes=True), partitions=["modemst1"], rollback=False)
    err = capsys.readouterr().err
    assert "modemst1/modemst2" in err  # partial-mirror warning fired


def test_validate_reports_mirror_and_notes():
    r = _rooted_with_parts()
    r.add("shell getprop", "[ro.board.platform]: [kona]\n")
    r.add("getprop ro.boot.slot_suffix", "\n")
    r.add("sha256sum /dev/block/sda5", "a" * 64 + "  x\n")
    r.add("sha256sum /dev/block/sda6", "a" * 64 + "  x\n")
    r.add("sha256sum /dev/block/sda7", "b" * 64 + "  x\n")
    r.add("sha256sum /dev/block/sda8", "c" * 64 + "  x\n")
    r.add("head -c 1048576", "0011223344\n")
    rep = efs.validate(AdbDevice(r, "SER"), chipset="qualcomm")
    assert rep["mirror_modemst"]["identical"] is True
    assert any("EFS2" in n or "NVRAM" in n for n in rep["notes"])


def test_samsung_fix_md5_local(tmp_path):
    d = tmp_path / "efs"
    d.mkdir()
    (d / "nv_data.bin").write_bytes(b"hello world")
    (d / "nv_data.bin.md5").write_text("deadbeef")  # stale
    (d / ".nv_data.bak").write_bytes(b"backup")
    (d / ".nv_data.bak.md5").write_text("00")
    res = efs.samsung_fix_md5(None, ScriptedSafety(assume_yes=True), local_dir=str(d))
    from mrt.utils.nvchecksum import samsung_md5_hex
    assert (d / "nv_data.bin.md5").read_text() == samsung_md5_hex(b"hello world")
    assert (d / ".nv_data.bak.md5").read_text() == samsung_md5_hex(b"backup")
    assert {c["file"] for c in res["changed"]} == {"nv_data.bin", ".nv_data.bak"}


def test_samsung_fix_md5_local_skips_correct(tmp_path):
    from mrt.utils.nvchecksum import samsung_md5_hex
    d = tmp_path / "efs"; d.mkdir()
    (d / "nv_data.bin").write_bytes(b"data")
    (d / "nv_data.bin.md5").write_text(samsung_md5_hex(b"data"))
    res = efs.samsung_fix_md5(None, ScriptedSafety(assume_yes=True), local_dir=str(d))
    assert res["changed"] == []
