"""Exception types used across the toolkit."""


class MrtError(Exception):
    """Base class for all toolkit errors."""

    exit_code = 1


class ToolNotFound(MrtError):
    """adb / fastboot binary could not be located."""

    exit_code = 2


class DeviceNotFound(MrtError):
    """No (or too many) matching devices."""

    exit_code = 3


class CommandFailed(MrtError):
    """A host command returned a non-zero exit code."""

    exit_code = 4

    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result


class RootRequired(MrtError):
    """The operation needs root on the device and none is available."""

    exit_code = 5


class Aborted(MrtError):
    """User declined a safety confirmation."""

    exit_code = 6


class VerificationFailed(MrtError):
    """A checksum / size verification did not match."""

    exit_code = 7


class UnsupportedFormat(MrtError):
    """An input file is not in a format the toolkit understands."""

    exit_code = 8
