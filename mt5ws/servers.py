"""`servers.dat` 解析 / MT5 server-list parsing.

MT5 终端把「服务器 → 接入点 → 地址」这张表存在 `config/servers.dat` 里。
**这张表是纯 Python 客户端唯一的接入点来源**：MT5 不需要 DNS，直接连表里的
IP:port（本例 Exness-MT5Trial5 的 8 个接入点就是从这张表里拿到的）。

文件格式（实测 + 与公开资料互校）：

* 头部 428 字节**不加密**，其中偏移 0 是结构版本（503/504/505/506），
  偏移 172 是服务器条数；
* 之后是**连续记录**，每条记录用「XOR 链式流密码」加密，
  每个字段块单独解密、链式初值 prev=0；
* 各种计数字段（count）**不加密**，直接小端 int32。

XOR 链式解密（`prev` 用**密文**字节，这是 MT5 这套 crypt 的关键）::

    plain[i] = cipher[i] ^ ((prev + KEY[i & 15]) & 0xFF)
    prev     = cipher[i]
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Optional

#: servers.dat 的固定 XOR 密钥（16 字节）
XOR_KEY = bytes([65, 182, 127, 88, 56, 12, 240, 45,
                 123, 57, 8, 254, 33, 187, 65, 88])

HEADER_SIZE = 428


def xor_chain(data: bytes) -> bytes:
    """XOR 链式解密一个块（每块各自从 prev=0 开始）。"""
    out = bytearray(len(data))
    prev = 0
    for i, cur in enumerate(data):
        out[i] = cur ^ ((prev + XOR_KEY[i & 0xF]) & 0xFF)
        prev = cur
    return bytes(out)


def _u16str(raw: bytes) -> str:
    end = len(raw)
    for i in range(0, len(raw) - 1, 2):
        if raw[i] == 0 and raw[i + 1] == 0:
            end = i
            break
    return raw[:end].decode("utf-16-le", errors="replace")


@dataclass
class Address:
    address: str
    port: int = 0
    kind: int = 0
    description: str = ""


@dataclass
class AccessPoint:
    name: str
    addresses: list[Address] = field(default_factory=list)


@dataclass
class ServerEntry:
    index: int
    name: str
    company: str
    address: str = ""
    dst: int = 0
    timezone: int = 0
    ping_time: int = 0
    accesses: list[AccessPoint] = field(default_factory=list)

    @property
    def endpoints(self) -> list[str]:
        """所有接入点的 ``host:port``（去重、保序）。

        注意 AddressRec 里的地址**通常自带端口**（如 ``18.163.85.196:443``），
        所以这里只在地址本身没有端口时才补 port。
        """
        out: list[str] = []
        for ap in self.accesses:
            for a in ap.addresses:
                host = a.address.strip()
                if not host:
                    continue
                port = a.port or getattr(ap, "port", 0) or 443
                item = host if ":" in host else f"{host}:{port}"
                if item not in out:
                    out.append(item)
        return out


def ap_port(ap: AccessPoint) -> int:
    return getattr(ap, "port", 0)


class _Reader:
    def __init__(self, data: bytes, pos: int = HEADER_SIZE) -> None:
        self.data = data
        self.pos = pos

    def raw(self, n: int) -> bytes:
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def dec(self, n: int) -> bytes:
        return xor_chain(self.raw(n))

    def int32(self) -> int:
        v = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def dec_int32(self, base: bytes, off: int) -> int:
        return struct.unpack_from("<i", base, off)[0]


#: servers.dat 的常见位置（按顺序找第一个存在的）
CANDIDATE_PATHS = [
    os.environ.get("MT5_SERVERS_DAT", ""),
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "附件", "servers.dat"),
    "/home/abc/.wine/drive_c/Program Files/MetaTrader 5/Config/servers.dat",
    os.path.expanduser("~/.wine/drive_c/Program Files/MetaTrader 5/Config/servers.dat"),
]


def default_servers_dat() -> Optional[str]:
    """挑一个存在的 servers.dat 路径（可用 MT5_SERVERS_DAT 覆盖）。"""
    for p in CANDIDATE_PATHS:
        if p and os.path.exists(p):
            return os.path.abspath(p)
    return None


def parse_servers_dat(path: str) -> tuple[int, list[ServerEntry]]:
    """解析 servers.dat，返回 ``(结构版本, 服务器列表)``。"""
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < HEADER_SIZE:
        raise ValueError("文件太小，不是 servers.dat / too small")
    version = struct.unpack_from("<i", data, 0)[0]
    count = struct.unpack_from("<i", data, 172)[0]
    r = _Reader(data, HEADER_SIZE)
    servers: list[ServerEntry] = []
    for i in range(count):
        if version in (505, 506):
            info = r.dec(1716)
            name_sz, comp_off = 128, 128
        elif version in (503, 504):
            info = r.dec(660)
            name_sz, comp_off = 128, 128
        else:
            raise ValueError(f"未知的 servers.dat 版本 / unknown version: {version}")
        entry = ServerEntry(
            index=i,
            name=_u16str(info[0:name_sz]),
            company=_u16str(info[comp_off:comp_off + 256]),
            dst=struct.unpack_from("<i", info, 392)[0],
            timezone=struct.unpack_from("<i", info, 396)[0],
            address=_u16str(info[404:532]),
            ping_time=struct.unpack_from("<i", info, 532)[0],
        )
        access_count = r.int32()
        for _ in range(access_count):
            adata = r.dec(356)
            ap = AccessPoint(name=_u16str(adata[0:64]))
            port = struct.unpack_from("<i", adata, 192)[0]
            setattr(ap, "port", port)
            addr_count = r.int32()
            for _a in range(addr_count):
                addr = r.dec(148)
                ap.addresses.append(Address(
                    address=_u16str(addr[0:128]),
                    port=port,
                ))
            entry.accesses.append(ap)
        if version in (505, 506):
            ex_count = r.int32()
            for _ in range(ex_count):
                ex = r.dec(3160)
                ap = AccessPoint(name=_u16str(ex[512:576]) or _u16str(ex[0:128]))
                host = _u16str(ex[600:856])
                if host:
                    setattr(ap, "port", 443)
                    ap.addresses.append(Address(address=host, port=443))
                addrex_count = r.int32()
                for _a in range(addrex_count):
                    addr = r.dec(1284)
                    kind = struct.unpack_from("<i", addr, 0)[0]
                    ap.addresses.append(Address(
                        address=_u16str(addr[4:516]),
                        description=_u16str(addr[516:1028]),
                        kind=kind,
                        port=443,
                    ))
                entry.accesses.append(ap)
        servers.append(entry)
    return version, servers


def find_server(servers: list[ServerEntry], name: str) -> Optional[ServerEntry]:
    """按名字找服务器（大小写不敏感）。"""
    low = name.lower()
    for s in servers:
        if s.name.lower() == low:
            return s
    for s in servers:
        if low in s.name.lower():
            return s
    return None


def access_points(path: str, server_name: str) -> list[str]:
    """便捷函数：直接返回某个服务器的 ``host:port`` 列表。"""
    _v, servers = parse_servers_dat(path)
    entry = find_server(servers, server_name)
    if entry is None:
        raise KeyError(f"服务器不在表里 / server not found: {server_name}")
    return entry.endpoints
