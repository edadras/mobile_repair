"""IMEI service layer.

A single entry point that picks the right chipset backend and, per backend,
knows its transport and its on-device storage - exactly the layering in the
project README::

                         IMEI Service Layer
                                │
              ┌─────────────────┼─────────────────┐
              ↓                 ↓                 ↓
          Qualcomm            MTK              Samsung
            DIAG              META            Service/AT
              │                 │                 │
              ↓                 ↓                 ↓
           NV/QCN         NVRAM/NVDATA          EFS/NV

What is real over adb and what is not:

* **Reading** the live IMEI works through the Android telephony layer
  (``service call iphonesubinfo``) on any chipset, and **offline** from a QCN
  (Qualcomm NV item 550) or a MediaTek ``MP0B_001`` blob.
* **Writing** the IMEI into the modem needs the chipset's proprietary port -
  Qualcomm DIAG, MediaTek META, or the modem's AT channel - none of which is
  reachable through adb. So a write is handled in two honest halves: `mrt` can
  rewrite the IMEI **offline inside a QCN** (to restore a device's own number
  after a board repair, then flash it back with QPST), and for a live write it
  emits the exact transport payload (DIAG frame / AT command) plus how to send
  it, rather than pretending adb can.

Writing an IMEI is a licensed repair operation; the number is always supplied
by the operator and validated (Luhn) - this module never fabricates one.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..core.device import AdbDevice
from ..core.errors import MrtError
from ..core.output import info
from ..core.safety import CRITICAL, Safety
from ..utils import imei as imei_util
from ..utils import qcn as qcn_mod
from . import efs as efs_mod
from .info import get_imei

QUALCOMM = efs_mod.QUALCOMM
MEDIATEK = efs_mod.MEDIATEK
SAMSUNG = efs_mod.SAMSUNG
UNKNOWN = efs_mod.UNKNOWN


class ImeiBackend:
    """One chipset path: its transport and storage names plus the concrete ops."""

    chipset = UNKNOWN
    transport = "-"
    storage = "-"

    def read_live(self, dev: AdbDevice) -> Dict[str, Any]:
        """Best-effort live read via the Android telephony layer (works anywhere)."""
        value = get_imei(dev)
        ok = value.isdigit()
        return {"imei": value if ok else None, "source": "android-telephony" if ok else None,
                "raw": value, "valid": imei_util.is_valid(value) if ok else False}

    def plan_write(self, imei: str, sim: int = 1) -> Dict[str, Any]:
        raise NotImplementedError


class QualcommBackend(ImeiBackend):
    chipset = QUALCOMM
    transport = "DIAG"
    storage = "NV/QCN"

    def plan_write(self, imei: str, sim: int = 1) -> Dict[str, Any]:
        imei = imei_util.validate(imei, allow_14=True)
        frame = imei_util.build_diag_imei_write(imei)
        return {
            "chipset": self.chipset, "transport": self.transport, "storage": self.storage,
            "imei": imei, "nv_item": imei_util.NV_UE_IMEI,
            "nv_value_hex": imei_util.encode_nv_imei(imei).hex(),
            "diag_frame_hex": frame.hex(),
            "how": "send diag_frame to a Qualcomm DIAG port (/dev/diag, QPST/QFIL) - not over adb. "
                   "Offline: 'mrt imei qcn-write BACKUP.qcn <imei>' then restore the QCN with QPST.",
        }


class MediatekBackend(ImeiBackend):
    chipset = MEDIATEK
    transport = "META"
    storage = "NVRAM/NVDATA"

    def plan_write(self, imei: str, sim: int = 1) -> Dict[str, Any]:
        imei = imei_util.validate(imei, allow_14=True)
        return {
            "chipset": self.chipset, "transport": self.transport, "storage": self.storage,
            "imei": imei, "nvram_file": imei_util.MTK_IMEI_FILE,
            "mp0b_value_hex": imei_util.nv_imei_value(imei).hex(),
            "at_command": imei_util.build_at_egmr(imei, sim=sim),
            "how": "write via SP Flash Tool / Maui META (write NVRAM MP0B_001) in META mode, or send at_command "
                   "on the modem AT port - neither is reachable over adb.",
        }


class SamsungBackend(ImeiBackend):
    chipset = SAMSUNG
    transport = "Service/AT"
    storage = "EFS/NV"

    def plan_write(self, imei: str, sim: int = 1) -> Dict[str, Any]:
        imei = imei_util.validate(imei, allow_14=True)
        return {
            "chipset": self.chipset, "transport": self.transport, "storage": self.storage,
            "imei": imei,
            "at_command": imei_util.build_at_egmr(imei, sim=sim),
            "how": "send at_command on the Samsung modem AT port (service mode), or use a licensed service box. "
                   "adb cannot reach the modem AT channel.",
        }


BACKENDS: Dict[str, ImeiBackend] = {
    QUALCOMM: QualcommBackend(), MEDIATEK: MediatekBackend(), SAMSUNG: SamsungBackend(),
}


def backend_for(chipset: str) -> ImeiBackend:
    if chipset not in BACKENDS:
        raise MrtError(f"no IMEI backend for chipset '{chipset}'. Known: {', '.join(BACKENDS)}")
    return BACKENDS[chipset]


def layer_map(chipset: str = UNKNOWN) -> Dict[str, Any]:
    """The service-layer table (all chipsets, or the selected one highlighted)."""
    rows = []
    for name, be in BACKENDS.items():
        rows.append({"chipset": name, "transport": be.transport, "storage": be.storage,
                     "selected": name == chipset})
    return {"service": "IMEI Service Layer", "selected_chipset": chipset if chipset in BACKENDS else None, "backends": rows}


# --------------------------------------------------------------- service ops


def resolve_chipset(dev: Optional[AdbDevice], chipset: str = "auto") -> str:
    if chipset != "auto":
        return chipset
    if dev is None:
        return UNKNOWN
    return efs_mod.detect_chipset(dev)


def read(dev: AdbDevice, chipset: str = "auto") -> Dict[str, Any]:
    """Read the IMEI through the chipset's layer, with offline sources tried too."""
    chipset = resolve_chipset(dev, chipset)
    be = BACKENDS.get(chipset, ImeiBackend())
    result: Dict[str, Any] = {
        "chipset": chipset, "transport": getattr(be, "transport", "-"),
        "storage": getattr(be, "storage", "-"),
    }
    result.update(be.read_live(dev))
    return result


def plan_write(dev: Optional[AdbDevice], imei: str, chipset: str = "auto", sim: int = 1) -> Dict[str, Any]:
    """Produce the transport payload for a live IMEI write (does not send it)."""
    chipset = resolve_chipset(dev, chipset)
    be = backend_for(chipset)
    plan = be.plan_write(imei, sim=sim)
    plan["note"] = "This does NOT write anything. It gives the exact payload for the chipset's service port."
    return plan


# ---------------------------------------------------------- offline QCN write


def qcn_read(path: str, item: int = imei_util.NV_UE_IMEI, storage: Optional[str] = None) -> Dict[str, Any]:
    q = qcn_mod.Qcn.load(path)
    it = q.get(item, storage=storage)
    if it is None:
        raise MrtError(f"NV item {item} not found in {path}")
    imei = imei_util.decode_nv_imei(it.value)
    return {"file": path, "storage": it.storage, "nv_item": item, "value_hex": it.value.hex(),
            "imei": imei, "valid": imei_util.is_valid(imei) if imei else False}


def qcn_write(path: str, imei: str, safety: Safety, item: int = imei_util.NV_UE_IMEI,
              storage: Optional[str] = None, out: Optional[str] = None) -> Dict[str, Any]:
    """Rewrite NV item 550 inside a QCN so it carries ``imei`` (offline, real)."""
    imei = imei_util.validate(imei, allow_14=True)
    q = qcn_mod.Qcn.load(path)
    existing = q.get(item, storage=storage)
    old = imei_util.decode_nv_imei(existing.value) if existing else None
    length = len(existing.value) if existing else 9
    value = imei_util.nv_imei_value(imei, length)
    target_storage = (existing.storage if existing else None) or storage or "NV_ITEM_ARRAY"
    safety.confirm(
        f"Write IMEI {imei} into NV item {item} ({target_storage}) of {path}\n"
        f"  current: {old or '(absent)'}  ->  new: {imei}\n"
        "This edits the QCN file only; flash it back to the modem with QPST/QFIL. "
        "Only program a device's own original IMEI (e.g. after a board repair).",
        level=CRITICAL, token="IMEI")
    q.set(item, value, storage=target_storage)
    out_path = out or path
    q.save(out_path)
    info(f"IMEI {imei} written to {out_path} (NV {item}); restore this QCN with QPST to apply on the modem")
    return {"file": out_path, "storage": target_storage, "nv_item": item, "old_imei": old,
            "imei": imei, "value_hex": value.hex(), "method": "qcn-rebuild"}
