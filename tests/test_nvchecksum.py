import hashlib

from mrt.utils import nvchecksum


def test_crc16_x25_known_vector():
    # CRC-16/X-25 check value for "123456789" is 0x906E
    assert nvchecksum.crc16_x25(b"123456789") == 0x906E
    assert nvchecksum.crc16_x25_bytes(b"123456789") == (0x906E).to_bytes(2, "little")
    assert nvchecksum.crc16_x25(b"") == 0x0000


def test_samsung_md5_hex():
    assert nvchecksum.samsung_md5_hex(b"abc") == hashlib.md5(b"abc").hexdigest()


def test_samsung_md5_targets():
    names = ["nv_data.bin", "nv_data.bin.md5", ".nv_data.bak", ".nv_data.bak.md5", "carrier", "random.xml"]
    assert set(nvchecksum.samsung_md5_targets(names)) == {"nv_data.bin", ".nv_data.bak"}
    # a lone base file with no sidecar and not a known base is not a target
    assert nvchecksum.samsung_md5_targets(["foo.bin"]) == []
