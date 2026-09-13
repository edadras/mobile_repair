"""Shared runtime context passed to every command handler."""

from __future__ import annotations

from typing import Optional

from .device import AdbDevice, DeviceInfo, FastbootDevice, select_device
from .oplog import OperationLog
from .runner import Runner
from .safety import Safety


class Context:
    def __init__(
        self,
        runner: Runner,
        log: OperationLog,
        safety: Safety,
        serial: Optional[str] = None,
        json_output: bool = False,
        no_adb_root: bool = False,
    ):
        self.runner = runner
        self.log = log
        self.safety = safety
        self.serial = serial
        self.json = json_output
        self.no_adb_root = no_adb_root
        self._adb: Optional[AdbDevice] = None
        self._fastboot: Optional[FastbootDevice] = None

    def device(self, want: str = "adb") -> DeviceInfo:
        return select_device(self.runner, self.serial, want=want)

    def adb(self, require: bool = True) -> AdbDevice:
        if self._adb is None:
            if require and not self.runner.dry_run:
                info = self.device("adb")
                self._adb = AdbDevice(self.runner, info.serial)
            else:
                self._adb = AdbDevice(self.runner, self.serial)
        return self._adb

    def fastboot(self, require: bool = True) -> FastbootDevice:
        if self._fastboot is None:
            if require and not self.runner.dry_run:
                info = self.device("fastboot")
                self._fastboot = FastbootDevice(self.runner, info.serial)
            else:
                self._fastboot = FastbootDevice(self.runner, self.serial)
        return self._fastboot
