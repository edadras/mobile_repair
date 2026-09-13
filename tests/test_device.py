import pytest

from mrt.core import device as dm
from mrt.core.device import AdbDevice, FastbootDevice, parse_adb_devices, parse_fastboot_devices, parse_getprop, parse_getvar
from mrt.core.errors import DeviceNotFound, RootRequired

from .conftest import FakeRunner


def test_parse_adb_devices_modes():
    text = (
        "List of devices attached\n"
        "ABC123\tdevice usb:1-2 product:sunfish model:Pixel_4a device:sunfish transport_id:3\n"
        "DEF456\tunauthorized usb:1-3 transport_id:4\n"
        "GHI789\trecovery transport_id:5\n"
        "JKL000\tno permissions (user in plugdev group?) transport_id:6\n"
        "MNO111\tsideload transport_id:7\n"
    )
    devs = parse_adb_devices(text)
    assert [d.serial for d in devs] == ["ABC123", "DEF456", "GHI789", "JKL000", "MNO111"]
    assert devs[0].mode == "device" and devs[0].details["model"] == "Pixel_4a"
    assert devs[0].description == "sunfish Pixel_4a sunfish"
    assert devs[1].mode == "unauthorized"
    assert devs[3].mode == "no permissions"
    assert devs[4].mode == "sideload"


def test_parse_fastboot_devices():
    devs = parse_fastboot_devices("1234ABCD\tfastboot\n\n9999\tfastbootd\n")
    assert [(d.serial, d.mode, d.transport) for d in devs] == [("1234ABCD", "fastboot", "fastboot"), ("9999", "fastbootd", "fastboot")]


def test_parse_getvar_bootloader_prefix():
    text = (
        "(bootloader) product: sunfish\n"
        "(bootloader) unlocked: yes\n"
        "(bootloader) partition-size:boot_a: 0x4000000\n"
        "(bootloader) current-slot: a\n"
        "all: \n"
        "Finished. Total time: 0.050s\n"
    )
    vars_ = parse_getvar(text)
    assert vars_["product"] == "sunfish"
    assert vars_["unlocked"] == "yes"
    assert vars_["partition-size:boot_a"] == "0x4000000"
    assert "Finished" not in vars_ and "all" not in vars_


def test_parse_getprop():
    props = parse_getprop("[ro.product.model]: [Pixel 4a]\n[ro.build.version.sdk]: [33]\n[empty]: []\n")
    assert props == {"ro.product.model": "Pixel 4a", "ro.build.version.sdk": "33", "empty": ""}


def test_select_device_single_and_errors(runner: FakeRunner):
    runner.add("adb devices -l", "List of devices attached\nA1\tdevice\n")
    runner.add("fastboot devices -l", "")
    assert dm.select_device(runner, want="adb").serial == "A1"
    with pytest.raises(DeviceNotFound):
        dm.select_device(runner, serial="NOPE", want="adb")


def test_select_device_multiple_requires_serial(runner: FakeRunner):
    runner.add("adb devices -l", "List of devices attached\nA1\tdevice\nB2\tdevice\n")
    with pytest.raises(DeviceNotFound) as exc:
        dm.select_device(runner, want="adb")
    assert "multiple" in str(exc.value)
    assert dm.select_device(runner, serial="B2", want="adb").serial == "B2"


def test_select_device_unauthorized_hint(runner: FakeRunner):
    runner.add("adb devices -l", "List of devices attached\nA1\tunauthorized\n")
    with pytest.raises(DeviceNotFound) as exc:
        dm.select_device(runner, want="adb")
    assert "USB debugging" in str(exc.value)


def test_root_mode_adbd(rooted_runner: FakeRunner):
    dev = AdbDevice(rooted_runner, "SER")
    assert dev.root_mode() == "adbd"
    assert dev.root_command("dd if=/dev/block/x") == "dd if=/dev/block/x"


def test_root_mode_su(su_runner: FakeRunner):
    dev = AdbDevice(su_runner, "SER")
    assert dev.root_mode() == "su"
    assert dev.root_command("cat /proc/cmdline") == "su -c 'cat /proc/cmdline'"
    # cached: further calls do not re-probe
    n = len(su_runner.calls)
    dev.root_mode()
    assert len(su_runner.calls) == n


def test_no_root_raises(runner: FakeRunner):
    runner.add("shell id", "uid=2000(shell)\n")
    runner.add("root", "adbd cannot run as root in production builds\n")
    dev = AdbDevice(runner, "SER")
    assert dev.root_mode() == ""
    with pytest.raises(RootRequired):
        dev.root_shell("id")


def test_remote_sha256_and_size(rooted_runner: FakeRunner):
    rooted_runner.add("sha256sum", "d" * 64 + "  /dev/block/by-name/boot\n")
    rooted_runner.add("blockdev --getsize64", "67108864\n")
    dev = AdbDevice(rooted_runner, "SER")
    assert dev.remote_sha256("/dev/block/by-name/boot") == "d" * 64
    assert dev.remote_size("/dev/block/by-name/boot") == 67108864
    assert any("head -c 4096" in c for c in rooted_runner.joined_calls()) is False
    dev.remote_sha256("/dev/block/by-name/boot", length=4096)
    assert any("head -c 4096 /dev/block/by-name/boot | sha256sum" in c for c in rooted_runner.joined_calls())


def test_fastboot_device_commands(runner: FakeRunner):
    runner.add("getvar all", stderr="(bootloader) unlocked: no\n(bootloader) secure: yes\n")
    fb = FastbootDevice(runner, "FB1")
    assert fb.is_unlocked() is False
    fb.flash("boot", "boot.img", slot="b")
    assert runner.calls[-1] == ["fastboot", "-s", "FB1", "--slot", "b", "flash", "boot", "boot.img"]
    fb.reboot("bootloader")
    assert runner.calls[-1][-1] == "reboot-bootloader"
    fb.reboot("recovery")
    assert runner.calls[-1][-2:] == ["reboot", "recovery"]
