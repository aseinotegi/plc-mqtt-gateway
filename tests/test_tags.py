import struct

import pytest

from plc_gateway.tags import Tag, TagError, parse_address, plan_reads


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("DB1.DBX0.0", (1, 0, "X", 0)),
        ("db10.dbx3.7", (10, 3, "X", 7)),
        ("DB1.DBB2", (1, 2, "B", None)),
        ("DB5.DBW120", (5, 120, "W", None)),
        ("DB1.DBD8", (1, 8, "D", None)),
    ],
)
def test_parse_address(address, expected):
    assert parse_address(address) == expected


@pytest.mark.parametrize("address", ["DB1.DBX0", "DB1.DBW2.1", "M0.0", "DB1.DBX0.8", "DB.DBD0", ""])
def test_parse_address_rejects_invalid(address):
    with pytest.raises(TagError):
        parse_address(address)


@pytest.mark.parametrize(
    ("address", "type_"),
    [("DB1.DBX0.0", "INT"), ("DB1.DBW2", "REAL"), ("DB1.DBD4", "BOOL"), ("DB1.DBB0", "WORD")],
)
def test_address_must_match_type(address, type_):
    with pytest.raises(TagError):
        Tag.from_address("x", address, type_)


def test_tag_validation():
    with pytest.raises(TagError):
        Tag("bad name", 1, 0, "INT")
    with pytest.raises(TagError):
        Tag("x", 1, 0, "BOOL")  # BOOL without bit
    with pytest.raises(TagError):
        Tag("x", 1, 0, "INT", bit=1)
    with pytest.raises(TagError):
        Tag("x", 1, 0, "REAL", deadband=-1)


def test_decode_every_type():
    buf = bytearray(20)
    buf[0] = 0b0000_0101
    struct.pack_into(">B", buf, 1, 200)
    struct.pack_into(">h", buf, 2, -1234)
    struct.pack_into(">H", buf, 4, 65000)
    struct.pack_into(">i", buf, 6, -70000)
    struct.pack_into(">I", buf, 10, 4_000_000_000)
    struct.pack_into(">f", buf, 14, 6.8)

    assert Tag("b0", 1, 0, "BOOL", bit=0).decode(buf) is True
    assert Tag("b1", 1, 0, "BOOL", bit=1).decode(buf) is False
    assert Tag("b2", 1, 0, "BOOL", bit=2).decode(buf) is True
    assert Tag("byte", 1, 1, "BYTE").decode(buf) == 200
    assert Tag("int", 1, 2, "INT").decode(buf) == -1234
    assert Tag("word", 1, 4, "WORD").decode(buf) == 65000
    assert Tag("dint", 1, 6, "DINT").decode(buf) == -70000
    assert Tag("dword", 1, 10, "DWORD").decode(buf) == 4_000_000_000
    assert Tag("real", 1, 14, "REAL").decode(buf) == 6.8  # float32 noise removed


def test_decode_with_block_base():
    buf = struct.pack(">f", 4.2)
    assert Tag("p", 1, 100, "REAL").decode(buf, base=100) == 4.2
    with pytest.raises(TagError):
        Tag("p", 1, 102, "REAL").decode(buf, base=100)


def test_plan_reads_merges_neighbours_and_splits_far_tags():
    tags = [
        Tag("a", 1, 0, "BOOL", bit=0),
        Tag("b", 1, 0, "BOOL", bit=1),
        Tag("c", 1, 4, "REAL"),
        Tag("far", 1, 500, "INT"),
        Tag("other_db", 2, 0, "INT"),
    ]
    blocks = plan_reads(tags)
    assert [(b.db, b.start, b.size) for b in blocks] == [(1, 0, 8), (1, 500, 2), (2, 0, 2)]
    assert {t.name for t in blocks[0].tags} == {"a", "b", "c"}


def test_plan_reads_respects_block_limit():
    tags = [Tag(f"t{i}", 1, i * 4, "REAL") for i in range(100)]  # 400 contiguous bytes
    blocks = plan_reads(tags, max_block=200)
    assert all(b.size <= 200 for b in blocks)
    assert sum(len(b.tags) for b in blocks) == 100
