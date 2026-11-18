"""Tag definitions, S7 address parsing, decoding and read planning."""

from __future__ import annotations

import re
import struct
from collections import defaultdict
from dataclasses import dataclass

TYPE_SIZES = {"BOOL": 1, "BYTE": 1, "INT": 2, "WORD": 2, "DINT": 4, "DWORD": 4, "REAL": 4}
_STRUCT_FMT = {"BYTE": ">B", "INT": ">h", "WORD": ">H", "DINT": ">i", "DWORD": ">I", "REAL": ">f"}
# Size letter in an S7 address (DBX / DBB / DBW / DBD) -> width in bytes
_ADDRESS_WIDTH = {"X": 1, "B": 1, "W": 2, "D": 4}
_ADDRESS_RE = re.compile(r"^DB(\d+)\.DB([XBWD])(\d+)(?:\.([0-7]))?$", re.IGNORECASE)
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
# Absolute MQTT topic: no wildcards, no empty levels, no leading or trailing slash.
_TOPIC_RE = re.compile(r"^[^/+#\s]+(/[^/+#\s]+)*$")

# A single S7 PDU (240 bytes) carries ~222 bytes of payload; stay below it so every
# block is fetched in one request.
DEFAULT_MAX_BLOCK = 200
DEFAULT_MAX_GAP = 32

Value = bool | int | float


class TagError(ValueError):
    """Raised when a tag definition is invalid."""


def parse_address(address: str) -> tuple[int, int, str, int | None]:
    """Parse an absolute S7 address such as ``DB1.DBX0.3`` or ``DB10.DBD24``.

    Returns ``(db, offset, size_letter, bit)``.
    """
    match = _ADDRESS_RE.match(address.strip())
    if not match:
        raise TagError(f"invalid S7 address '{address}' (expected e.g. DB1.DBX0.0, DB1.DBW2, DB1.DBD4)")
    db, letter, offset, bit = match.groups()
    letter = letter.upper()
    if letter == "X" and bit is None:
        raise TagError(f"address '{address}' needs a bit number (e.g. DB1.DBX0.3)")
    if letter != "X" and bit is not None:
        raise TagError(f"address '{address}' only bit addresses (DBX) take a bit number")
    return int(db), int(offset), letter, int(bit) if bit is not None else None


@dataclass(frozen=True)
class Tag:
    """One PLC variable that the gateway publishes."""

    name: str
    db: int
    offset: int
    type: str
    bit: int | None = None
    deadband: float = 0.0
    unit: str | None = None
    topic: str | None = None  # absolute topic (e.g. a UNS path) instead of <base>/tags/<name>

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name):
            raise TagError(f"tag name '{self.name}' may only contain letters, digits, '_' and '-'")
        if self.type not in TYPE_SIZES:
            raise TagError(f"tag '{self.name}': unknown type '{self.type}' ({', '.join(TYPE_SIZES)})")
        if self.db < 1 or self.offset < 0:
            raise TagError(f"tag '{self.name}': DB number must be >= 1 and offset >= 0")
        if (self.type == "BOOL") != (self.bit is not None):
            raise TagError(f"tag '{self.name}': BOOL tags need a bit, other types must not have one")
        if self.bit is not None and not 0 <= self.bit <= 7:
            raise TagError(f"tag '{self.name}': bit must be between 0 and 7")
        if self.deadband < 0:
            raise TagError(f"tag '{self.name}': deadband must be >= 0")
        if self.topic is not None and not _TOPIC_RE.match(self.topic):
            raise TagError(f"tag '{self.name}': topic '{self.topic}' must be a path like area/device/value")

    @classmethod
    def from_address(cls, name: str, address: str, type: str, **extra) -> Tag:
        """Build a tag from an S7 address, checking that the address width matches the type."""
        db, offset, letter, bit = parse_address(address)
        type = type.upper()
        if type not in TYPE_SIZES:
            raise TagError(f"tag '{name}': unknown type '{type}' ({', '.join(TYPE_SIZES)})")
        if (letter == "X") != (type == "BOOL") or _ADDRESS_WIDTH[letter] != TYPE_SIZES[type]:
            raise TagError(f"tag '{name}': address '{address}' does not match type {type}")
        return cls(name=name, db=db, offset=offset, type=type, bit=bit, **extra)

    @property
    def size(self) -> int:
        return TYPE_SIZES[self.type]

    def decode(self, buffer: bytes | bytearray, base: int = 0) -> Value:
        """Decode this tag from ``buffer``, whose first byte is DB byte ``base``."""
        index = self.offset - base
        if index < 0 or index + self.size > len(buffer):
            raise TagError(f"tag '{self.name}' is outside the buffer that was read")
        if self.type == "BOOL":
            return bool((buffer[index] >> self.bit) & 1)
        value = struct.unpack_from(_STRUCT_FMT[self.type], buffer, index)[0]
        if self.type == "REAL":
            # float32 -> shortest decimal that round-trips (7 significant digits)
            return float(f"{value:.7g}")
        return value


@dataclass(frozen=True)
class ReadBlock:
    """A contiguous DB range fetched with one ``db_read`` call."""

    db: int
    start: int
    size: int
    tags: tuple[Tag, ...]


def plan_reads(
    tags: list[Tag], max_gap: int = DEFAULT_MAX_GAP, max_block: int = DEFAULT_MAX_BLOCK
) -> list[ReadBlock]:
    """Group tags into as few contiguous reads as possible.

    Neighbouring tags of the same DB are merged while the hole between them is at most
    ``max_gap`` bytes and the block stays within ``max_block`` bytes. Fewer, larger reads
    mean fewer round trips to the PLC and a lighter load on its communication processor.
    """
    by_db: dict[int, list[Tag]] = defaultdict(list)
    for tag in tags:
        by_db[tag.db].append(tag)

    blocks: list[ReadBlock] = []
    for db in sorted(by_db):
        current: list[Tag] = []
        start = end = 0
        for tag in sorted(by_db[db], key=lambda t: (t.offset, t.bit or 0)):
            tag_end = tag.offset + tag.size
            if current and tag.offset - end <= max_gap and max(end, tag_end) - start <= max_block:
                current.append(tag)
                end = max(end, tag_end)
                continue
            if current:
                blocks.append(ReadBlock(db, start, end - start, tuple(current)))
            current, start, end = [tag], tag.offset, tag_end
        if current:
            blocks.append(ReadBlock(db, start, end - start, tuple(current)))
    return blocks
