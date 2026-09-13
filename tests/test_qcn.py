import os

import pytest

from mrt.core.errors import UnsupportedFormat
from mrt.utils import qcn


def sample_tree():
    return {
        "Version": bytes([1, 0, 0, 0]),
        "Mobile_Property": bytes(range(20)),
        "NV_ITEM_ARRAY": {"550": bytes(range(16)), "10": b"\x01", "441": os.urandom(64)},
        "Provisioning_Item_Files": {"00000042": b"\x07", "00000199": os.urandom(300)},
    }


def test_roundtrip_small_streams():
    tree = sample_tree()
    raw = qcn.serialize(tree)
    assert raw[:8] == qcn.SIGNATURE
    assert qcn.CompoundFile(raw).to_tree() == tree


def test_roundtrip_with_big_stream():
    tree = sample_tree()
    tree["NV_ITEM_ARRAY"]["4998"] = os.urandom(5000)  # crosses the 4096 mini cutoff
    tree["NV_ITEM_ARRAY"]["4999"] = os.urandom(4096)   # exactly the cutoff -> big
    raw = qcn.serialize(tree)
    assert qcn.CompoundFile(raw).to_tree() == tree


def test_roundtrip_empty_and_odd_sizes():
    tree = {"A": {"1": b"", "2": b"\x00", "3": b"\xff" * 63, "4": b"\xaa" * 65}}
    assert qcn.CompoundFile(qcn.serialize(tree)).to_tree() == tree


def test_bad_signature():
    with pytest.raises(UnsupportedFormat):
        qcn.CompoundFile(b"not a compound file" * 40)


def test_name_too_long_rejected():
    with pytest.raises(UnsupportedFormat):
        qcn.serialize({"x" * 40: b"y"})


def test_qcn_helpers(tmp_path):
    p = tmp_path / "t.qcn"
    qcn.Qcn(sample_tree()).save(str(p))
    q = qcn.Qcn.load(str(p))
    nv = {it.item for it in q.nv_items() if it.storage == "NV_ITEM_ARRAY"}
    assert nv == {"550", "10", "441"}
    assert q.get(550).value == bytes(range(16))
    assert q.get(550, storage="NV_ITEM_ARRAY").storage == "NV_ITEM_ARRAY"
    assert q.get(99999) is None
    meta = q.metadata()
    assert meta["nv_item_count"] == 5 and "NV_ITEM_ARRAY" in meta["storages"]


def test_edit_inplace_same_length_mini_and_big(tmp_path):
    tree = sample_tree()
    tree["NV_ITEM_ARRAY"]["4998"] = b"\x11" * 5000
    p = tmp_path / "t.qcn"
    qcn.Qcn(tree).save(str(p))
    before = p.read_bytes()
    qcn.edit_item_inplace(str(p), 550, b"\x02" * 16)
    qcn.edit_item_inplace(str(p), 4998, b"\x22" * 5000)
    q = qcn.Qcn.load(str(p))
    assert q.get(550).value == b"\x02" * 16
    assert q.get(4998).value == b"\x22" * 5000
    # other items untouched
    assert q.get(10).value == b"\x01"
    assert len(p.read_bytes()) == len(before)  # container size unchanged


def test_edit_inplace_length_mismatch_refused(tmp_path):
    p = tmp_path / "t.qcn"
    qcn.Qcn(sample_tree()).save(str(p))
    with pytest.raises(UnsupportedFormat):
        qcn.edit_item_inplace(str(p), 10, b"\x01\x02")


def test_edit_inplace_missing_item(tmp_path):
    p = tmp_path / "t.qcn"
    qcn.Qcn(sample_tree()).save(str(p))
    with pytest.raises(UnsupportedFormat):
        qcn.edit_item_inplace(str(p), 12345, b"\x00")


def test_rebuild_edit_changes_length(tmp_path):
    p = tmp_path / "t.qcn"
    q = qcn.Qcn(sample_tree())
    q.save(str(p))
    q2 = qcn.Qcn.load(str(p))
    q2.set(10, b"\xaa\xbb\xcc")
    q2.save(str(p))
    assert qcn.Qcn.load(str(p)).get(10).value == b"\xaa\xbb\xcc"
