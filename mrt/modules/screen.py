"""Screen and input helpers (useful to test touch/display during repair)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..core.device import AdbDevice
from ..core.output import info


def screenshot(dev: AdbDevice, out_path: str) -> str:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    dev.exec_out("screencap -p", out_path, timeout=60)
    return out_path


def record(dev: AdbDevice, out_path: str, seconds: int = 10, size: Optional[str] = None, bitrate: Optional[str] = None) -> str:
    remote = "/sdcard/mrt_record.mp4"
    args = ["screenrecord", "--time-limit", str(seconds)]
    if size:
        args += ["--size", size]
    if bitrate:
        args += ["--bit-rate", bitrate]
    args.append(remote)
    info(f"recording {seconds}s ...")
    dev.shell(" ".join(args), timeout=seconds + 60)
    dev.pull(remote, out_path)
    dev.shell(f"rm -f {remote}", timeout=30)
    return out_path


def tap(dev: AdbDevice, x: int, y: int) -> str:
    return dev.shell_text(f"input tap {x} {y}")


def swipe(dev: AdbDevice, x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> str:
    return dev.shell_text(f"input swipe {x1} {y1} {x2} {y2} {ms}")


def keyevent(dev: AdbDevice, key: str) -> str:
    return dev.shell_text(f"input keyevent {key}")


def text(dev: AdbDevice, value: str) -> str:
    escaped = value.replace(" ", "%s").replace("'", "\\'")
    return dev.shell_text(f"input text '{escaped}'")


def unlock_screen(dev: AdbDevice, pin: Optional[str] = None) -> str:
    keyevent(dev, "KEYCODE_WAKEUP")
    swipe(dev, 500, 1800, 500, 600, 200)
    if pin:
        text(dev, pin)
        keyevent(dev, "KEYCODE_ENTER")
    return "ok"


def touch_test(dev: AdbDevice, seconds: int = 10) -> None:
    info(f"printing raw touch events for {seconds}s, touch the screen...")
    dev.adb("shell", "timeout", str(seconds), "getevent", "-l", stream=True, timeout=seconds + 15)
