"""IMEI helpers and the chipset service layer."""

import pytest

from mrt.core.device import AdbDevice
from mrt.core.errors import Aborted, MrtError
from mrt.modules import imei as svc
from mrt.utils import imei as u
from mrt.utils import qcn as qcn_mod

from .conftest import FakeRunner, ScriptedSafety

VALID = u.complete("35328311000000")  # a Luhn-valid IMEI


# ------------------------------------------------------------- pure helpers


def test_luhn_and_validation():
    assert u.is_valid(VALID) and u.luhn_checksum(VALID) == 0
    assert u.is_valid("490154203237518")  # classic valid IMEI
    assert not u.is_valid("490154203237519")
    assert u.complete("49015420323751") == "490154203237518"
    assert u.luhn_check_digit("49015420323751") == 8
    assert u.validate("49 015420-323751", allow_14=True) == "490154203237518"
    with pytest.raises(ValueError):
        u.validate("123")
    with pytest.raises(ValueError):
        u.validate("490154203237519")  # bad check digit, 15 digits


def test_nv_imei_roundtrip_and_layout():
    raw = u.encode_nv_imei(VALID)
    assert len(raw) == 9 and raw[0] == 0x08 and (raw[1] & 0x0F) == 0x0A
    assert (raw[1] >> 4) == int(VALID[0])
    assert u.decode_nv_imei(raw) == VALID
    assert u.decode_nv_imei(raw + b"\x00" * 119) == VALID  # trailing padding tolerated
    assert u.decode_nv_imei(b"\x00" * 9) is None
    assert u.decode_nv_imei(b"\x08\xff\x00\x00\x00\x00\x00\x00\x00") is None  # bad marker nibble
    assert u.nv_imei_value(VALID, 12) == raw + b"\x00\x00\x00"


def test_diag_frame_crc_and_hdlc():
    frame = u.build_diag_imei_write(VALID)
    assert frame[0] == u.DIAG_NV_WRITE_F and frame[-1] == 0x7E
    # unescape and check the trailing CRC matches crc16_x25 of the payload
    body = bytearray()
    esc = False
    for b in frame[:-1]:
        if esc:
            body.append(b ^ 0x20); esc = False
        elif b == 0x7D:
            esc = True
        else:
            body.append(b)
    payload, crc = bytes(body[:-2]), int.from_bytes(body[-2:], "little")
    from mrt.utils.nvchecksum import crc16_x25
    assert crc == crc16_x25(payload)
    assert payload[0] == u.DIAG_NV_WRITE_F and payload[1:3] == (550).to_bytes(2, "little")
    assert len(payload) == 1 + 2 + u.NV_ITEM_PAYLOAD + 2


def test_at_egmr_and_mp0b():
    assert u.build_at_egmr(VALID) == f'AT+EGMR=1,7,"{VALID}"'
    assert u.build_at_egmr(VALID, sim=2) == f'AT+EGMR=1,10,"{VALID}"'
    assert u.build_at_egmr() == "AT+EGMR=0,7"
    with pytest.raises(ValueError):
        u.build_at_egmr(sim=3)
    blob = u.encode_mtk_mp0b([VALID, "490154203237518"])
    assert u.decode_mtk_mp0b(blob) == [VALID, "490154203237518"]


# ------------------------------------------------------------- service layer


def test_layer_map_matches_diagram():
    m = svc.layer_map("mediatek")
    rows = {r["chipset"]: (r["transport"], r["storage"], r["selected"]) for r in m["backends"]}
    assert rows["qualcomm"] == ("DIAG", "NV/QCN", False)
    assert rows["mediatek"] == ("META", "NVRAM/NVDATA", True)
    assert rows["samsung"] == ("Service/AT", "EFS/NV", False)


def _adb_qualcomm():
    r = FakeRunner()
    r.add("adb devices -l", "List of devices attached\nSER\tdevice\n")
    r.add("fastboot devices -l", "")
    r.add("shell id", "uid=2000(shell) gid=2000(shell)\n")
    r.add("adb -s SER root", "adbd cannot run as root in production builds\n")
    r.add("shell su -c id", "uid=2000(shell)\n")  # no root
    r.add("shell getprop", "[ro.board.platform]: [kona]\n")
    return r


def test_read_selects_backend_and_reads_telephony():
    r = _adb_qualcomm()
    r.add("service call iphonesubinfo", "Result: Parcel(\n  0x00000000: '3' '5' '3' '2' '8' '3' '1' '1'\n  '0' '0' '0' '0' '0' '0' '2')\n")
    rep = svc.read(AdbDevice(r, "SER"))
    assert rep["chipset"] == "qualcomm" and rep["transport"] == "DIAG" and rep["storage"] == "NV/QCN"
    assert rep["imei"] == VALID and rep["valid"] is True and rep["source"] == "android-telephony"


def test_plan_write_per_chipset():
    q = svc.plan_write(None, VALID, chipset="qualcomm")
    assert q["transport"] == "DIAG" and q["nv_item"] == 550
    assert q["diag_frame_hex"] == u.build_diag_imei_write(VALID).hex()
    assert "does NOT write" in q["note"]
    mtk = svc.plan_write(None, VALID, chipset="mediatek", sim=2)
    assert mtk["transport"] == "META" and mtk["at_command"] == f'AT+EGMR=1,10,"{VALID}"' and mtk["nvram_file"] == "MP0B_001"
    sam = svc.plan_write(None, VALID, chipset="samsung")
    assert sam["transport"] == "Service/AT" and sam["storage"] == "EFS/NV"
    with pytest.raises(MrtError):
        svc.plan_write(None, VALID, chipset="unknown")
    with pytest.raises(ValueError):
        svc.plan_write(None, "123", chipset="qualcomm")


def _qcn_with_imei(path, imei, item=550, length=9):
    tree = {"NV_ITEM_ARRAY": {str(item): u.nv_imei_value(imei, length)}, "Version": b"\x01"}
    qcn_mod.Qcn(tree).save(str(path))


def test_qcn_read(tmp_path):
    f = tmp_path / "b.qcn"; _qcn_with_imei(f, VALID)
    rep = svc.qcn_read(str(f))
    assert rep["imei"] == VALID and rep["valid"] and rep["nv_item"] == 550
    with pytest.raises(MrtError):
        svc.qcn_read(str(f), item=999)


def test_qcn_write_roundtrip_and_token(tmp_path):
    f = tmp_path / "b.qcn"; _qcn_with_imei(f, "490154203237518")
    out = tmp_path / "patched.qcn"
    rep = svc.qcn_write(str(f), VALID, ScriptedSafety(answers=["IMEI"]), out=str(out))
    assert rep["old_imei"] == "490154203237518" and rep["imei"] == VALID
    assert svc.qcn_read(str(out))["imei"] == VALID
    assert svc.qcn_read(str(f))["imei"] == "490154203237518"  # original untouched (--out)
    # preserves the stored stream length
    assert len(qcn_mod.Qcn.load(str(out)).get(550).value) == 9


def test_qcn_write_needs_token(tmp_path):
    f = tmp_path / "b.qcn"; _qcn_with_imei(f, "490154203237518")
    with pytest.raises(Aborted):
        svc.qcn_write(str(f), VALID, ScriptedSafety(answers=["y"]))


def test_qcn_write_pads_to_existing_length(tmp_path):
    f = tmp_path / "b.qcn"; _qcn_with_imei(f, "490154203237518", length=16)
    svc.qcn_write(str(f), VALID, ScriptedSafety(answers=["IMEI"]))
    assert len(qcn_mod.Qcn.load(str(f)).get(550).value) == 16
    assert svc.qcn_read(str(f))["imei"] == VALID
