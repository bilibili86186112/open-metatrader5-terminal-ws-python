"""MT5 协议序列化层 / MT5 protocol serializer.

MT5 的报文体不是 protobuf，也不是定长 C 结构体，而是「带类型标签的紧凑序列」：
每个字段先声明类型（1 字节标签）与可选长度，再按类型取固定宽度；变长类型
（UTF-16 字符串 / 原始字节）长度另有 4 字节。解码必须严格按 schema 顺序走，
否则后面全部错位——这是分析 MT5 协议最容易踩的坑。

Schemas are lists of ``{"propType": int, "propLength": int (optional)}``.
Fixed-width types advance a known number of bytes; variable-width types
(11 = UTF-16LE string, 12 = raw bytes) read a uint32 length first.

Type table (verified against the 客户端's native command table and the public
reference; type 6 is uint32, *not* uint64):

===== ============== =======
type  meaning        width
===== ============== =======
1     int8           1
2     int16          2
3     int32          4
4     uint8          1
5     uint16         2
6     uint32         4
7     float32        4
8     float64        8
11    UTF-16LE str   var (uint32 length + payload)
12    raw bytes      var (uint32 length + payload)
17    int64          8
18    uint64         8
===== ============== =======
"""

from __future__ import annotations

import struct
from typing import Any, Iterable, Sequence

#: 固定宽度类型 → (struct 格式, 字节数)
FIXED: dict[int, tuple[str, int]] = {
    1: ("b", 1),
    2: ("h", 2),
    3: ("i", 4),
    4: ("B", 1),
    5: ("H", 2),
    6: ("I", 4),
    7: ("f", 4),
    8: ("d", 8),
    17: ("q", 8),
    18: ("Q", 8),
}

#: 变长类型：字符串（UTF-16LE）与原始字节
VAR_STRING = 11
VAR_RAW = 12

FLT_MAX = 1.7976931348623157e308


def field_size(field: dict) -> int:
    """字段的字节数：定长类型按类型宽度，带 propLength 的字符串/字节按 propLength。

    不带 propLength 的 11/12 是变长字段（长度写在报文里），返回 0。
    """
    t = field["propType"]
    if t in VAR_LEN_TYPES:
        return field.get("propLength", 0)
    return FIXED[t][1]


VAR_LEN_TYPES = (VAR_STRING, VAR_RAW)


def series_size(schema: Sequence[dict]) -> int:
    """整个序列的固定字节数（schema 中不能有变长字段）。"""
    return sum(field_size(f) for f in schema)


def _decode_str(raw: bytes) -> str:
    return raw.decode("utf-16-le", errors="replace").rstrip("\x00")


def _encode_str(value: str, length: int) -> bytes:
    raw = value.encode("utf-16-le")[:length]
    return raw + b"\x00" * (length - len(raw))


def parse_series(buf: bytes, schema: Sequence[dict], offset: int = 0, *, raw_ok: bool = False):
    """按 schema 解出一组字段，返回 ``(values, next_offset)``。

    两种字段写法（这是 MT5 序列化最容易搞错的地方）：

    * ``{"propType": 11, "propLength": 64}`` —— **定长**字符串，直接占 64 字节
      （UTF-16LE，尾部 \\0 补齐），**没有**长度前缀。
    * ``{"propType": 11}``（不给 propLength）—— 变长，先读 uint32 字节数再读内容。

    品种表、账户信息、持仓/订单/成交全部用定长写法。
    """
    values: list[Any] = []
    off = offset
    for f in schema:
        t = f["propType"]
        if t in VAR_LEN_TYPES:
            if "propLength" in f:
                n = f["propLength"]
                payload = buf[off:off + n]
                off += n
            else:
                n = struct.unpack_from("<I", buf, off)[0]
                off += 4
                payload = buf[off:off + n]
                off += n
            if t == VAR_STRING:
                values.append(_decode_str(payload))
            else:
                values.append(payload)
            continue
        fmt, size = FIXED[t]
        values.append(struct.unpack_from("<" + fmt, buf, off)[0])
        off += size
    return values, off


def parse_fixed(buf: bytes, schema: Sequence[dict], offset: int = 0):
    """解出定长字段；遇到变长类型会抛错（用于行情/持仓这类纯定长记录）。"""
    values: list[Any] = []
    off = offset
    for f in schema:
        t = f["propType"]
        if t in VAR_LEN_TYPES:
            raise ValueError("parse_fixed() cannot handle variable-length fields")
        fmt, size = FIXED[t]
        values.append(struct.unpack_from("<" + fmt, buf, off)[0])
        off += size
    return values, off


class Writer:
    """按 schema 顺序写报文体。"""

    def __init__(self) -> None:
        self.buf = bytearray()

    def u8(self, v: int) -> "Writer":
        self.buf += struct.pack("<B", v & 0xFF)
        return self

    def u16(self, v: int) -> "Writer":
        self.buf += struct.pack("<H", v & 0xFFFF)
        return self

    def u32(self, v: int) -> "Writer":
        self.buf += struct.pack("<I", v & 0xFFFFFFFF)
        return self

    def i32(self, v: int) -> "Writer":
        self.buf += struct.pack("<i", v)
        return self

    def u64(self, v: int) -> "Writer":
        self.buf += struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF)
        return self

    def i64(self, v: int) -> "Writer":
        self.buf += struct.pack("<q", v)
        return self

    def f64(self, v: float) -> "Writer":
        self.buf += struct.pack("<d", v)
        return self

    def string(self, v: str) -> "Writer":
        raw = v.encode("utf-16-le")
        self.u32(len(raw))
        self.buf += raw
        return self

    def str_fixed(self, v: str, length: int) -> "Writer":
        self.buf += _encode_str(v, length)
        return self

    def raw(self, data: bytes) -> "Writer":
        self.u32(len(data))
        self.buf += data
        return self

    def bytes(self) -> bytes:
        return bytes(self.buf)

    def __len__(self) -> int:
        return len(self.buf)


def pack_fixed(schema: Sequence[dict], values: Iterable[Any]) -> bytes:
    """把一组值按（纯定长）schema 打包。"""
    out = bytearray()
    for f, v in zip(schema, values):
        t = f["propType"]
        if t in VAR_LEN_TYPES:
            if t == VAR_STRING:
                out += _encode_str(str(v), f["propLength"])
            else:
                out += bytes(v)[:f["propLength"]].ljust(f["propLength"], b"\x00")
            continue
        fmt, _ = FIXED[t]
        out += struct.pack("<" + fmt, v)
    return bytes(out)
