import os

import pytest

from mrt.core.device import AdbDevice
from mrt.core.errors import MrtError, VerificationFailed
from mrt.modules import partitions as pm

from .conftest import FakeRunner, ScriptedSafety, sha256

LISTING = (
    "MRT|boot_a|/dev/block/sde11|134217728|262144\n"
    "MRT|boot_b|/dev/block/sde12|134217728|262144\n"
    "MRT|persist|/dev/block/sda2||65536\n"
    "MRT|userdata|/dev/block/sda13|||\n"
    "garbage line\n"
)


def test_parse_partition_listing():
    parts = pm.parse_partition_listing(LISTING)
    names = [p.name for p in parts]
    assert names == ["boot_a", "boot_b", "persist", "userdata"]
    by = {p.name: p for p in parts}
    assert by["boot_a"].size == 262144 * 512 and by["boot_a"].device == "/dev/block/sde11"
    assert by["persist"].size == 65536 * 512
    assert by["userdata"].size is None


def test_resolve_uses_slot_suffix(rooted_runner: FakeRunner):
    rooted_runner.add("MRT|", LISTING)
    rooted_runner.add("getprop ro.boot.slot_suffix", "_b\n")
    dev = AdbDevice(rooted_runner, "SER")
    assert pm.resolve(dev, "boot").name == "boot_b"
    assert pm.resolve(dev, "boot_a").device == "/dev/block/sde11"
    with pytest.raises(MrtError):
        pm.resolve(dev, "nope")


def test_dump_partition_verified(rooted_runner: FakeRunner, tmp_path):
    data = os.urandom(65536 * 512)
    rooted_runner.add("MRT|", LISTING)
    rooted_runner.add("dd if=/dev/block/sda2", data=data)
    rooted_runner.add("sha256sum /dev/block/sda2", sha256(data) + "  /dev/block/sda2\n")
    dev = AdbDevice(rooted_runner, "SER")
    out = tmp_path / "persist.img"
    res = pm.dump_partition(dev, "persist", str(out))
    assert out.read_bytes() == data
    assert res["verified"] is True and res["size_ok"] is True and res["sha256"] == sha256(data)
    # the dump must have been streamed with exec-out (no staging file on the device)
    assert any(c[:4] == ["adb", "-s", "SER", "exec-out"] for c in rooted_runner.calls)


def test_dump_partition_hash_mismatch_raises(rooted_runner: FakeRunner, tmp_path):
    data = b"\x01" * (65536 * 512)
    rooted_runner.add("MRT|", LISTING)
    rooted_runner.add("dd if=/dev/block/sda2", data=data)
    rooted_runner.add("sha256sum /dev/block/sda2", "f" * 64 + "  /dev/block/sda2\n")
    dev = AdbDevice(rooted_runner, "SER")
    with pytest.raises(VerificationFailed):
        pm.dump_partition(dev, "persist", str(tmp_path / "p.img"))


def test_dump_partition_uses_su_wrapper(su_runner: FakeRunner, tmp_path):
    su_runner.add("MRT|", LISTING)
    su_runner.add("dd if=", data=b"x" * 16)
    dev = AdbDevice(su_runner, "SER")
    pm.dump_partition(dev, "boot_a", str(tmp_path / "b.img"), verify=False)
    dd_call = [c for c in su_runner.calls if c[3] == "exec-out"][0]
    assert dd_call[4].startswith("su -c 'dd if=/dev/block/sde11 bs=4M")


def test_dump_range_aligned_and_unaligned(rooted_runner: FakeRunner, tmp_path):
    rooted_runner.add("MRT|", LISTING)
    rooted_runner.add("dd if=/dev/block/sde11 bs=4096 skip=1 count=2", data=b"a" * 8192)
    rooted_runner.add("dd if=/dev/block/sde11 bs=512 skip=1 count=2 2>/dev/null | tail -c +101 | head -c 500", data=b"b" * 500)
    dev = AdbDevice(rooted_runner, "SER")
    r = pm.dump_range(dev, "boot_a", str(tmp_path / "r1.bin"), 4096, 8192)
    assert r["length"] == 8192 and (tmp_path / "r1.bin").stat().st_size == 8192
    r = pm.dump_range(dev, "boot_a", str(tmp_path / "r2.bin"), 612, 500)
    assert (tmp_path / "r2.bin").read_bytes() == b"b" * 500
    with pytest.raises(MrtError):
        pm.dump_range(dev, "boot_a", str(tmp_path / "r3.bin"), 134217728 - 10, 100)


def test_write_partition_full_flow(rooted_runner: FakeRunner, tmp_path):
    image = tmp_path / "boot.img"
    data = os.urandom(4096)
    image.write_bytes(data)
    h = sha256(data)
    rooted_runner.add("MRT|", LISTING)
    rooted_runner.add("sha256sum /data/local/tmp/mrt_boot_a_", h + "  /data/local/tmp/x\n")
    rooted_runner.add("head -c 4096 /dev/block/sde11 | sha256sum", h + "  -\n")
    safety = ScriptedSafety(answers=["boot_a"])
    dev = AdbDevice(rooted_runner, "SER")
    res = pm.write_partition(dev, "boot_a", str(image), safety)
    assert res["verified"] is True
    calls = rooted_runner.joined_calls()
    push = [c for c in calls if " push " in c][0]
    assert push.startswith(f"adb -s SER push {image} /data/local/tmp/mrt_boot_a_")
    dd = [c for c in calls if "dd if=/data/local/tmp/mrt_boot_a_" in c][0]
    assert "of=/dev/block/sde11 bs=4M conv=fsync" in dd
    assert any("rm -f /data/local/tmp/mrt_boot_a_" in c for c in calls)
    # the confirmation prompt asked for the partition name token
    assert "boot_a" in safety.prompts[-1]


def test_write_partition_declined(rooted_runner: FakeRunner, tmp_path):
    image = tmp_path / "boot.img"
    image.write_bytes(b"z" * 10)
    rooted_runner.add("MRT|", LISTING)
    dev = AdbDevice(rooted_runner, "SER")
    from mrt.core.errors import Aborted

    with pytest.raises(Aborted):
        pm.write_partition(dev, "boot_a", str(image), ScriptedSafety(answers=["wrong"]))
    assert not any(" push " in c for c in rooted_runner.joined_calls())


def test_write_partition_rejects_oversized_image(rooted_runner: FakeRunner, tmp_path):
    image = tmp_path / "big.img"
    image.write_bytes(b"z" * (65536 * 512 + 1))
    rooted_runner.add("MRT|", LISTING)
    dev = AdbDevice(rooted_runner, "SER")
    with pytest.raises(MrtError) as exc:
        pm.write_partition(dev, "persist", str(image), ScriptedSafety(assume_yes=True))
    assert "larger than partition" in str(exc.value)


def test_write_partition_upload_corruption_aborts_before_dd(rooted_runner: FakeRunner, tmp_path):
    image = tmp_path / "boot.img"
    image.write_bytes(b"q" * 100)
    rooted_runner.add("MRT|", LISTING)
    rooted_runner.add("sha256sum /data/local/tmp/mrt_boot_a_", "0" * 64 + "  x\n")
    dev = AdbDevice(rooted_runner, "SER")
    with pytest.raises(VerificationFailed):
        pm.write_partition(dev, "boot_a", str(image), ScriptedSafety(assume_yes=True))
    assert not any("of=/dev/block/sde11" in c for c in rooted_runner.joined_calls())
    assert any("rm -f /data/local/tmp/mrt_boot_a_" in c for c in rooted_runner.joined_calls())


def test_dump_all_skips_data_and_writes_manifest(rooted_runner: FakeRunner, tmp_path):
    rooted_runner.add("MRT|", LISTING)
    rooted_runner.add("dd if=", data=b"\0" * 1024)
    dev = AdbDevice(rooted_runner, "SER")
    manifest = pm.dump_all(dev, str(tmp_path / "all"), verify=False)
    names = [p["partition"] for p in manifest["partitions"]]
    assert "userdata" not in names and "boot_a" in names and "persist" in names
    assert (tmp_path / "all" / "manifest.json").exists()
    assert all(p["status"] == "ok" for p in manifest["partitions"])
    # size mismatch was recorded but did not abort (verify=False)
    assert manifest["partitions"][0]["size_ok"] is False


def test_compare_partition(rooted_runner: FakeRunner, tmp_path):
    image = tmp_path / "boot.img"
    image.write_bytes(b"k" * 300)
    rooted_runner.add("MRT|", LISTING)
    rooted_runner.add("head -c 300 /dev/block/sde11 | sha256sum", sha256(b"k" * 300) + "  -\n")
    dev = AdbDevice(rooted_runner, "SER")
    assert pm.compare_partition(dev, "boot_a", str(image))["match"] is True
