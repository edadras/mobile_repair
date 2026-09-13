"""Structure parsers for Qualcomm EFS2 and MediaTek nvram/nvdata images."""

import struct

from mrt.utils import efs2, mtknv


# ---------------------------------------------------------------- helpers


def make_superblock(age=1, page_size=512, block_size=8, block_count=8, version=0x24, num_regions=2):
    fixed = struct.pack("<IHH4s4sIIII4I4I32I", 0x53000000 | age, version, age, b"EFSS", b"uper",
                        block_size, page_size, block_count, 5, *([1, 2, 3, 4]), *([5, 6, 7, 8]), *([0] * 32))
    nand = struct.pack("<HHHH", 32, 2, 4, num_regions) + struct.pack(f"<{num_regions}I", *range(num_regions)) + struct.pack("<III", 0, 0, 9)
    page = fixed + nand
    return page + b"\0" * (page_size - len(page))


def make_efs2_image(ages=(1, 2, 3), page_size=512, pages=64, **kw):
    img = bytearray(b"\xff" * (page_size * pages))
    for i, age in enumerate(ages):
        off = (i * 4 + 1) * page_size  # superblocks on page boundaries, not page 0
        img[off:off + page_size] = make_superblock(age=age, page_size=page_size, **kw)
    info = efs2.EFS_INFO_MAGIC + b"\0" * (page_size - 4)
    img[20 * page_size:21 * page_size] = info
    return bytes(img)


def make_ext4(volume=b"nvdata", blocks=1024, csum=False, state=1, error_count=0):
    sb = bytearray(1024)
    struct.pack_into("<II", sb, 0, 100, blocks)
    struct.pack_into("<I", sb, 0x18, 2)  # 4096-byte blocks
    struct.pack_into("<HH", sb, 0x34, 3, 20)
    struct.pack_into("<H", sb, 0x38, 0xEF53)
    struct.pack_into("<HH", sb, 0x3A, state, 1)
    sb[0x78:0x78 + len(volume)] = volume
    struct.pack_into("<I", sb, 0x194, error_count)
    if csum:
        struct.pack_into("<I", sb, 0x64, mtknv.EXT4_FEATURE_RO_COMPAT_METADATA_CSUM)
        struct.pack_into("<I", sb, 0x3FC, mtknv.crc32c(bytes(sb[:0x3FC])) ^ 0xFFFFFFFF)
    return b"\0" * 1024 + bytes(sb) + b"\0" * 4096


def make_mtk_nvram(names=("APCFG/APRDCL/FILE_VER", "md/NVRAM/NVD_IMEI/MP0B_001"), offset=0, flag=mtknv.DATA_FLAG, total_size=64 * 1024):
    titles = b""
    pos = 0
    bodies = b""
    for n in names:
        body = n.encode() + b"\0" * 16
        titles += struct.pack("<iii", len(n) + 1, pos, len(body)) + n.encode().ljust(128, b"\0")
        bodies += body
        pos += len(body)
    counts = [len(names), 0, 0, 0, 0, 0, 0, 0]
    header = struct.pack("<8H", *counts) + struct.pack("<II", len(bodies), flag)
    region = header + titles + bodies
    img = bytearray(b"\0" * total_size)
    img[offset:offset + len(region)] = region
    return bytes(img)


# ------------------------------------------------------------------ EFS2


def test_superblock_roundtrip():
    sb = efs2.parse_superblock(make_superblock(age=7, page_size=2048, block_size=64, block_count=100))
    assert sb["age"] == 7 and sb["page_size"] == 2048 and sb["block_size"] == 64 and sb["block_count"] == 100
    assert sb["fs_bytes"] == 2048 * 64 * 100
    assert sb["nand_info"]["regions"] == [0, 1] and sb["nand_info"]["tables"] == 9


def test_superblock_rejects_bad_geometry_and_magic():
    assert efs2.parse_superblock(make_superblock(page_size=1000)) is None  # not a power of two
    assert efs2.parse_superblock(make_superblock(block_count=0)) is None
    page = bytearray(make_superblock()); page[8] = ord("X")
    assert efs2.parse_superblock(bytes(page)) is None


def test_analyze_picks_newest_superblock():
    rep = efs2.analyze(make_efs2_image(ages=(4, 9, 2)))
    assert rep["state"] == "ok" and rep["superblocks"] == 3
    assert rep["live"]["age"] == 9 and rep["live"]["offset"] == 5 * 512
    assert rep["ages"] == [2, 4, 9] and rep["geometry_consistent"] and rep["geometry_fits_image"]
    assert rep["efs_info_blocks"] == 1


def test_analyze_blank_and_no_superblock():
    assert efs2.analyze(b"\xff" * 4096)["state"] == "blank"
    assert efs2.analyze(b"\x00" * 4096)["state"] == "blank"
    assert efs2.analyze(b"")["state"] == "blank"
    rep = efs2.analyze(b"\x12\x34" * 2048)
    assert rep["state"] == "no-superblock" and rep["superblocks"] == 0


def test_analyze_magic_off_page_boundary_ignored():
    img = bytearray(b"\xff" * 8192)
    img[100:100 + 512] = make_superblock()
    assert efs2.analyze(bytes(img))["state"] == "no-superblock"


def test_analyze_inconsistent_geometry():
    img = bytearray(make_efs2_image(ages=(1, 2)))
    img[9 * 512:10 * 512] = make_superblock(age=3, block_count=999)
    rep = efs2.analyze(bytes(img))
    assert rep["state"] == "inconsistent" and rep["geometry_consistent"] is False


def test_compare_mirror():
    good = efs2.analyze(make_efs2_image(ages=(1, 5)))
    newer = efs2.analyze(make_efs2_image(ages=(1, 8)))
    cmp = efs2.compare_mirror({"modemst1": good, "modemst2": newer, "fsg": good})
    assert cmp["ok"] and cmp["newest"] == "modemst2" and cmp["fsg_usable"]
    blank = efs2.analyze(b"\xff" * 4096)
    cmp = efs2.compare_mirror({"modemst1": blank, "modemst2": good, "fsg": blank})
    assert not cmp["ok"] and not cmp["fsg_usable"]
    assert any("fsg" in p for p in cmp["problems"]) and any("modemst1" in p for p in cmp["problems"])
    other = efs2.analyze(make_efs2_image(ages=(1,), page_size=2048, block_count=10))
    cmp = efs2.compare_mirror({"modemst1": good, "fsg": other})
    assert cmp["geometry_consistent"] is False


# ------------------------------------------------------------- MediaTek


def test_crc32c_check_value():
    assert mtknv.crc32c(b"123456789") == 0xE3069283
    assert mtknv.crc32c(b"") == 0


def test_ext4_superblock_and_csum():
    rep = mtknv.analyze_nvdata(make_ext4(csum=True))
    assert rep["state"] == "ok" and rep["volume_name"] == "nvdata" and rep["block_size"] == 4096
    assert rep["metadata_csum"] and rep["superblock_csum_ok"] is True
    img = bytearray(make_ext4(csum=True)); img[1024 + 0x78] ^= 0xFF  # corrupt the volume name
    rep = mtknv.analyze_nvdata(bytes(img))
    assert rep["superblock_csum_ok"] is False and rep["state"] == "errors"
    rep = mtknv.analyze_nvdata(make_ext4(state=2, error_count=3))
    assert rep["state"] == "errors" and rep["state_errors"] and rep["error_count"] == 3
    assert mtknv.analyze_nvdata(b"\0" * 8192)["state"] == "blank"
    assert mtknv.analyze_nvdata(b"\x55" * 8192)["state"] == "no-ext4-superblock"


def test_mtk_nvram_backup_region():
    rep = mtknv.analyze_nvram(make_mtk_nvram())
    assert rep["state"] == "ok" and rep["files"] == 2 and rep["has_imei_file"] and rep["offset"] == 0
    assert rep["counts"]["ap_boot"] == 2 and "md/NVRAM/NVD_IMEI/MP0B_001" in rep["file_names"]
    rep = mtknv.analyze_nvram(make_mtk_nvram(offset=4096))
    assert rep["state"] == "ok" and rep["offset"] == 4096 and rep["found_by_scan"]
    assert mtknv.analyze_nvram(make_mtk_nvram(flag=0x1234))["state"] == "no-backup-header"
    assert mtknv.analyze_nvram(b"\0" * 4096)["state"] == "blank"


def test_mtk_nvram_rejects_garbage_names():
    img = bytearray(make_mtk_nvram())
    img[24 + 12] = 0x01  # non-printable first char of the first file name
    assert mtknv.analyze_nvram(bytes(img))["state"] == "no-backup-header"
