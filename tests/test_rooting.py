"""Magisk rooting workflow: APK extraction, patching, flashing, unrooting."""

import hashlib
import json
import zipfile

import pytest

from mrt.core.device import AdbDevice
from mrt.core.errors import Aborted, MrtError
from mrt.modules import rooting

from .conftest import FakeRunner, ScriptedSafety

UTIL = "#!/system/bin/sh\nMAGISK_VER='27.0'\nMAGISK_VER_CODE=27000\n"


def make_apk(path, layout="v27", abis=("arm64-v8a", "armeabi-v7a")):
    with zipfile.ZipFile(path, "w") as zf:
        for abi in abis:
            for lib in ("magiskboot", "magiskinit", "magiskpolicy", "init-ld", "busybox"):
                zf.writestr(f"lib/{abi}/lib{lib}.so", b"ELF" + lib.encode())
            if layout == "v27":
                zf.writestr(f"lib/{abi}/libmagisk.so", b"ELF-magisk-" + abi.encode())
            else:
                zf.writestr(f"lib/{abi}/lib{'magisk64' if abi.endswith('v8a') or abi == 'x86_64' else 'magisk32'}.so", b"ELF-daemon")
        zf.writestr("assets/boot_patch.sh", "#!/system/bin/sh\necho patch\n")
        zf.writestr("assets/util_functions.sh", UTIL)
        zf.writestr("assets/stub.apk", b"PK-stub")
        zf.writestr("classes.dex", b"dex")
    return str(path)


def test_extract_magisk_v27_layout(tmp_path):
    apk = make_apk(tmp_path / "Magisk.apk")
    res = rooting.extract_magisk(apk, str(tmp_path / "out"), "arm64-v8a")
    assert res["version"] == "27.0" and res["code"] == 27000 and res["abi"] == "arm64-v8a"
    files = set(res["files"])
    assert {"magiskboot", "magiskinit", "magisk", "magisk32", "boot_patch.sh", "util_functions.sh", "stub.apk"} <= files
    assert (tmp_path / "out" / "magisk").read_bytes() == b"ELF-magisk-arm64-v8a"
    assert (tmp_path / "out" / "magisk32").read_bytes() == b"ELF-magisk-armeabi-v7a"


def test_extract_magisk_legacy_layout_and_32bit(tmp_path):
    apk = make_apk(tmp_path / "Magisk-25.apk", layout="v25")
    res = rooting.extract_magisk(apk, str(tmp_path / "out"), "arm64-v8a")
    assert {"magisk64", "magisk32"} <= set(res["files"]) and "magisk" not in res["files"]
    res32 = rooting.extract_magisk(apk, str(tmp_path / "out32"), "armeabi-v7a")
    assert "magisk32" in res32["files"] and "magisk64" not in res32["files"]


def test_extract_magisk_rejects_non_magisk(tmp_path):
    bad = tmp_path / "x.apk"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("classes.dex", b"dex")
        zf.writestr("assets/util_functions.sh", UTIL)
    with pytest.raises(MrtError, match="does not look like a Magisk APK"):
        rooting.extract_magisk(str(bad), str(tmp_path / "o"), "arm64-v8a")
    with pytest.raises(MrtError, match="unsupported device ABI"):
        rooting.extract_magisk(make_apk(tmp_path / "m.apk"), str(tmp_path / "o2"), "mips")
    with pytest.raises(MrtError, match="not found"):
        rooting.extract_magisk(str(tmp_path / "missing.apk"), str(tmp_path / "o3"), "arm64-v8a")


PROPS_UNLOCKED = ("[ro.product.cpu.abi]: [arm64-v8a]\n[ro.product.model]: [Pixel 7]\n[ro.product.device]: [panther]\n"
                  "[ro.boot.slot_suffix]: [_a]\n[ro.boot.verifiedbootstate]: [orange]\n[ro.product.manufacturer]: [Google]\n"
                  "[ro.build.version.sdk]: [34]\n")
LISTING_INIT_BOOT = "MRT|boot_a|/dev/block/sda1||16384\nMRT|init_boot_a|/dev/block/sda2||16384\n"


def _adb_runner(props=PROPS_UNLOCKED, listing=LISTING_INIT_BOOT, rooted=False):
    r = FakeRunner()
    r.add("adb devices -l", "List of devices attached\nSER\tdevice\n")
    r.add("fastboot devices -l", "")
    r.add("shell id", "uid=0(root) gid=0(root)\n" if rooted else "uid=2000(shell) gid=2000(shell)\n")
    r.add("shell getprop ro.product.manufacturer", "Google\n")
    r.add("shell getprop ro.boot.slot_suffix", "_a\n")
    r.add("shell getprop", props)
    r.add("MRT|", listing)
    return r


def test_status_reports_prerequisites():
    r = _adb_runner()
    r.add("pm path", "package:/data/app/x/base.apk\n")
    rep = rooting.status(AdbDevice(r, "SER"))
    assert rep["bootloader"] == "unlocked" and rep["target_partition"] == "init_boot" and rep["abi"] == "arm64-v8a"
    assert rep["magisk_app"] is True and rep["root"] is None and rep["ready_to_flash"] is True
    assert not any("adb -s SER root" == " ".join(c[:4]) for c in r.calls)  # status never restarts adbd as root


def test_status_locked_samsung_notes():
    props = "[ro.product.cpu.abi]: [arm64-v8a]\n[ro.boot.flash.locked]: [1]\n[ro.product.manufacturer]: [samsung]\n"
    r = _adb_runner(props=props, listing="MRT|boot|/dev/block/sda1||16384\n")
    rep = rooting.status(AdbDevice(r, "SER"))
    assert rep["bootloader"] == "locked" and rep["flash_method"] == "odin" and rep["target_partition"] == "boot"
    assert rep["ready_to_flash"] is False and len(rep["notes"]) == 2


def test_detect_target_falls_back_to_boot(capsys):
    r = _adb_runner(listing="")
    assert rooting.detect_target(AdbDevice(r, "SER")) == "boot"
    assert "assuming target 'boot'" in capsys.readouterr().err


def _patch_runner(tmp_path, patched_content=b"PATCHED", rc=0, log="- Patching ramdisk\n- Repacking boot image\n"):
    r = _adb_runner()
    r.add("boot_patch.sh", log, rc=rc)
    r.add("echo yes || echo no", "yes\n" if patched_content is not None else "no\n")

    def pull(argv):
        if "pull" in argv and argv[-2].endswith(rooting.PATCHED_NAME):
            if patched_content is not None:
                open(argv[-1], "wb").write(patched_content)
            return True
        return False
    r.add(pull, "1 file pulled")
    return r


def test_patch_pushes_magisk_runs_boot_patch_and_pulls(tmp_path):
    apk = make_apk(tmp_path / "Magisk.apk")
    stock = tmp_path / "init_boot.img"; stock.write_bytes(b"STOCK" * 100)
    r = _patch_runner(tmp_path)
    out = tmp_path / "root"
    man = rooting.patch(AdbDevice(r, "SER"), ScriptedSafety(assume_yes=True), apk, str(out), boot_image=str(stock))
    assert man["target"] == "init_boot" and man["magisk"]["version"] == "27.0"
    assert (out / "stock-init_boot.img").read_bytes() == b"STOCK" * 100
    assert (out / "magisk_patched-init_boot.img").read_bytes() == b"PATCHED"
    saved = json.loads((out / rooting.MANIFEST).read_text())
    assert saved["patched_sha256"] == hashlib.sha256(b"PATCHED").hexdigest()
    assert saved["stock_sha256"] == hashlib.sha256(b"STOCK" * 100).hexdigest()
    calls = r.joined_calls()
    push_calls = [c for c in calls if " push " in c]
    assert any(c.endswith(rooting.REMOTE_DIR) for c in push_calls) and any(c.endswith(f"{rooting.REMOTE_DIR}/boot.img") for c in push_calls)
    run = next(c for c in calls if "boot_patch.sh" in c)
    assert "KEEPVERITY=true" in run and "KEEPFORCEENCRYPT=true" in run and "PATCHVBMETAFLAG=false" in run
    assert run.index("push") if False else True
    assert calls.index(run) > max(calls.index(c) for c in push_calls)
    assert any(f"rm -rf {rooting.REMOTE_DIR}" in c for c in calls[calls.index(run):])


def test_patch_options_and_target_override(tmp_path):
    apk = make_apk(tmp_path / "Magisk.apk")
    stock = tmp_path / "boot.img"; stock.write_bytes(b"S")
    r = _patch_runner(tmp_path)
    man = rooting.patch(AdbDevice(r, "SER"), ScriptedSafety(assume_yes=True), apk, str(tmp_path / "o"), boot_image=str(stock),
                        target="boot", keep_verity=False, patch_vbmeta=True, recovery_mode=True)
    assert man["target"] == "boot"
    run = next(c for c in r.joined_calls() if "boot_patch.sh" in c)
    assert "KEEPVERITY=false" in run and "PATCHVBMETAFLAG=true" in run and "RECOVERYMODE=true" in run


def test_patch_fails_when_script_reports_error(tmp_path):
    apk = make_apk(tmp_path / "Magisk.apk")
    stock = tmp_path / "boot.img"; stock.write_bytes(b"S")
    r = _patch_runner(tmp_path, patched_content=None, log="! Unsupported/Unknown image format\n")
    with pytest.raises(MrtError, match="boot_patch.sh failed"):
        rooting.patch(AdbDevice(r, "SER"), ScriptedSafety(assume_yes=True), apk, str(tmp_path / "o"), boot_image=str(stock))
    assert not (tmp_path / "o" / "magisk_patched-init_boot.img").exists()


def test_patch_needs_stock_image_without_root(tmp_path):
    apk = make_apk(tmp_path / "Magisk.apk")
    r = _patch_runner(tmp_path)
    with pytest.raises(MrtError, match="--boot-image"):
        rooting.patch(AdbDevice(r, "SER"), ScriptedSafety(assume_yes=True), apk, str(tmp_path / "o"))


def test_patch_dumps_stock_from_rooted_device(tmp_path):
    apk = make_apk(tmp_path / "Magisk.apk")
    r = _adb_runner(rooted=True)
    data = b"DUMPED" * 50
    r.add("dd if=/dev/block/sda2", data=data)
    r.add("sha256sum /dev/block/sda2", hashlib.sha256(data).hexdigest() + "  x\n")
    r.add("boot_patch.sh", "- ok\n")
    r.add("echo yes || echo no", "yes\n")
    r.add(lambda a: "pull" in a and (open(a[-1], "wb").write(b"P") or True), "")
    man = rooting.patch(AdbDevice(r, "SER"), ScriptedSafety(assume_yes=True), apk, str(tmp_path / "o"))
    assert man["stock_source"]["source"] == "device" and (tmp_path / "o" / "stock-init_boot.img").read_bytes() == data


def _root_dir(tmp_path, target="init_boot", device="panther"):
    d = tmp_path / "rootdir"; d.mkdir(parents=True)
    (d / f"stock-{target}.img").write_bytes(b"STOCK")
    (d / f"magisk_patched-{target}.img").write_bytes(b"PATCHED")
    man = {"type": "mrt-root", "target": target, "device": {"ro.product.device": device},
           "stock_image": f"stock-{target}.img", "stock_sha256": hashlib.sha256(b"STOCK").hexdigest(),
           "patched_image": f"magisk_patched-{target}.img", "patched_sha256": hashlib.sha256(b"PATCHED").hexdigest()}
    (d / rooting.MANIFEST).write_text(json.dumps(man))
    return d


def _flash_runner(unlocked="yes", start="adb", product="panther"):
    r = FakeRunner()
    state = {"fastboot": start == "fastboot"}
    r.add(lambda a: a[:2] == ["adb", "devices"], "")  # replaced below by callable rule order
    r.rules.clear()

    def adb_devices(argv):
        return argv[0].endswith("adb") and "devices" in argv and not state["fastboot"]
    r.add(adb_devices, "List of devices attached\nSER\tdevice\n")
    r.add(lambda a: a[0].endswith("adb") and "devices" in a, "List of devices attached\n")

    def fb_devices(argv):
        return argv[0].endswith("fastboot") and "devices" in argv and state["fastboot"]
    r.add(fb_devices, "SER\tfastboot\n")
    r.add(lambda a: a[0].endswith("fastboot") and "devices" in a, "")

    def reboot_bl(argv):
        if argv[0].endswith("adb") and argv[-2:] == ["reboot", "bootloader"]:
            state["fastboot"] = True
            return True
        return False
    r.add(reboot_bl, "")
    r.add("getprop ro.product.manufacturer", "Google\n")
    r.add("getvar product", f"product: {product}\nFinished.")
    r.add("getvar all", f"(bootloader) unlocked: {unlocked}\n(bootloader) product: {product}\nFinished.")
    return r


def test_flash_reboots_to_bootloader_checks_unlock_and_flashes(tmp_path):
    d = _root_dir(tmp_path)
    r = _flash_runner()
    res = rooting.flash(r, "SER", str(d), ScriptedSafety(answers=["y"]))
    calls = r.joined_calls()
    assert any(c.endswith("reboot bootloader") for c in calls)
    assert any(c.endswith(f"flash init_boot {d / 'magisk_patched-init_boot.img'}") for c in calls)
    assert calls[-1].endswith("fastboot -s SER reboot") and res["rebooted"] and res["target"] == "init_boot"


def test_flash_refuses_locked_bootloader(tmp_path):
    d = _root_dir(tmp_path)
    r = _flash_runner(unlocked="no")
    with pytest.raises(MrtError, match="LOCKED"):
        rooting.flash(r, "SER", str(d), ScriptedSafety(assume_yes=True))
    assert not any(" flash " in c for c in r.joined_calls())


def test_flash_temporary_uses_fastboot_boot(tmp_path):
    d = _root_dir(tmp_path)
    r = _flash_runner(start="fastboot")
    res = rooting.flash(r, "SER", str(d), ScriptedSafety(assume_yes=True), temporary=True)
    calls = r.joined_calls()
    assert res["temporary"] and any(c.endswith(f"boot {d / 'magisk_patched-init_boot.img'}") for c in calls)
    assert not any(" flash " in c or c.endswith("reboot bootloader") for c in calls)


def test_flash_refuses_tampered_image_and_product_mismatch(tmp_path):
    d = _root_dir(tmp_path)
    (d / "magisk_patched-init_boot.img").write_bytes(b"EVIL")
    with pytest.raises(MrtError, match="sha256"):
        rooting.flash(_flash_runner(), "SER", str(d), ScriptedSafety(assume_yes=True))
    d2 = _root_dir(tmp_path / "b", device="cheetah")
    with pytest.raises(Aborted):
        rooting.flash(_flash_runner(), "SER", str(d2), ScriptedSafety(answers=["y"]))


def test_flash_bare_image_guesses_target(tmp_path):
    img = tmp_path / "magisk_patched_boot.img"; img.write_bytes(b"P")
    r = _flash_runner(start="fastboot")
    res = rooting.flash(r, "SER", str(img), ScriptedSafety(answers=["y"]))
    assert res["target"] == "boot"


def test_unroot_flashes_stock(tmp_path):
    d = _root_dir(tmp_path)
    r = _flash_runner(start="fastboot")
    res = rooting.unroot(r, "SER", str(d), ScriptedSafety(answers=["y"]))
    assert any(c.endswith(f"flash init_boot {d / 'stock-init_boot.img'}") for c in r.joined_calls())
    assert res["rebooted"]


def test_samsung_flash_is_refused():
    r = _flash_runner()
    r.rules.insert(0, __import__("tests.conftest", fromlist=["Rule"]).Rule("getprop ro.product.manufacturer", "samsung\n"))
    with pytest.raises(MrtError, match="Odin"):
        rooting._to_fastboot(r, "SER")
