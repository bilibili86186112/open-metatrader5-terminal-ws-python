"""纯标准库 AES-256（ECB 核心 + CBC 封装）。

用途：当环境里既没有 ``pycryptodome`` 也没有 ``cryptography`` 时，
:mod:`mt5api.crypto` 自动退回到本模块，使整个包保持**零第三方依赖**。

只实现 MT5 协议需要的部分：

* AES-**256** 单块加/解密（14 轮）
* CBC 模式（IV 固定 16 字节 0，由调用方传入）

正确性由 ``90_selfcheck.py`` 的 AES 交叉校验用例保证
（与 ``cryptography`` / ``pycryptodome`` 逐字节对比）。
"""

from __future__ import annotations

__all__ = ["AES256", "cbc_encrypt", "cbc_decrypt"]

# ---------------------------------------------------------------- 常量表

_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76"
    "ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d83115"
    "04c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f84"
    "53d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa8"
    "51a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d1973"
    "60814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479"
    "e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a"
    "703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df"
    "8ca1890dbfe6426841992d0fb054bb16"
)

_INV_SBOX = bytearray(256)
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i
_INV_SBOX = bytes(_INV_SBOX)

_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _xtime(a: int) -> int:
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x11B) & 0xFF
    return a


def _mul(a: int, b: int) -> int:
    """GF(2^8) 乘法（0x11B 多项式）。"""
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        b >>= 1
        a = _xtime(a)
    return r


_M2 = bytes(_mul(i, 2) for i in range(256))
_M3 = bytes(_mul(i, 3) for i in range(256))
_M9 = bytes(_mul(i, 9) for i in range(256))
_M11 = bytes(_mul(i, 11) for i in range(256))
_M13 = bytes(_mul(i, 13) for i in range(256))
_M14 = bytes(_mul(i, 14) for i in range(256))


# ---------------------------------------------------------------- 核心


class AES256:
    """AES-256 单块加解密（key 必须 32 字节）。"""

    __slots__ = ("_rk", "_rnd")

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("AES-256 需要 32 字节密钥")
        self._rk = self._expand(key)
        self._rnd = 14

    @staticmethod
    def _expand(key: bytes) -> list[list[int]]:
        """密钥扩展 → 15 组轮密钥（每组 4 个 32-bit 字，用 4 字节列表表示）。"""
        nk = 8                      # AES-256: 8 个 32-bit 字
        total = 4 * (14 + 1)        # 60 字
        w: list[list[int]] = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
        for i in range(nk, total):
            temp = list(w[i - 1])
            if i % nk == 0:
                temp = temp[1:] + temp[:1]                  # RotWord
                temp = [_SBOX[b] for b in temp]             # SubWord
                temp[0] ^= _RCON[i // nk - 1]
            elif i % nk == 4:
                temp = [_SBOX[b] for b in temp]             # SubWord
            w.append([w[i - nk][j] ^ temp[j] for j in range(4)])
        # 重排成按字节的轮密钥（每轮 16 字节）
        rk = []
        for r in range(15):
            blk = []
            for c in range(4):
                blk.extend(w[4 * r + c])
            rk.append(blk)
        return rk

    def encrypt_block(self, block: bytes) -> bytes:
        if len(block) != 16:
            raise ValueError("块必须 16 字节")
        s = [block[i] ^ self._rk[0][i] for i in range(16)]
        for rnd in range(1, self._rnd):
            s = self._sub_shift(s)
            s = self._mix(s)
            s = [s[i] ^ self._rk[rnd][i] for i in range(16)]
        s = self._sub_shift(s)
        s = [s[i] ^ self._rk[self._rnd][i] for i in range(16)]
        return bytes(s)

    def decrypt_block(self, block: bytes) -> bytes:
        if len(block) != 16:
            raise ValueError("块必须 16 字节")
        s = [block[i] ^ self._rk[self._rnd][i] for i in range(16)]
        for rnd in range(self._rnd - 1, 0, -1):
            s = self._inv_shift(s)
            s = [_INV_SBOX[b] for b in s]
            s = [s[i] ^ self._rk[rnd][i] for i in range(16)]
            s = self._inv_mix(s)
        s = self._inv_shift(s)
        s = [_INV_SBOX[b] for b in s]
        s = [s[i] ^ self._rk[0][i] for i in range(16)]
        return bytes(s)

    # -- 轮变换（状态按列优先：s[4*c + r]） --

    @staticmethod
    def _sub_shift(s: list[int]) -> list[int]:
        s = [_SBOX[b] for b in s]
        out = [0] * 16
        for c in range(4):
            for r in range(4):
                out[4 * c + r] = s[4 * ((c + r) % 4) + r]
        return out

    @staticmethod
    def _inv_shift(s: list[int]) -> list[int]:
        out = [0] * 16
        for c in range(4):
            for r in range(4):
                out[4 * ((c + r) % 4) + r] = s[4 * c + r]
        return out

    @staticmethod
    def _mix(s: list[int]) -> list[int]:
        out = [0] * 16
        for c in range(4):
            a0, a1, a2, a3 = s[4 * c:4 * c + 4]
            out[4 * c + 0] = _M2[a0] ^ _M3[a1] ^ a2 ^ a3
            out[4 * c + 1] = a0 ^ _M2[a1] ^ _M3[a2] ^ a3
            out[4 * c + 2] = a0 ^ a1 ^ _M2[a2] ^ _M3[a3]
            out[4 * c + 3] = _M3[a0] ^ a1 ^ a2 ^ _M2[a3]
        return out

    @staticmethod
    def _inv_mix(s: list[int]) -> list[int]:
        out = [0] * 16
        for c in range(4):
            a0, a1, a2, a3 = s[4 * c:4 * c + 4]
            out[4 * c + 0] = _M14[a0] ^ _M11[a1] ^ _M13[a2] ^ _M9[a3]
            out[4 * c + 1] = _M9[a0] ^ _M14[a1] ^ _M11[a2] ^ _M13[a3]
            out[4 * c + 2] = _M13[a0] ^ _M9[a1] ^ _M14[a2] ^ _M11[a3]
            out[4 * c + 3] = _M11[a0] ^ _M13[a1] ^ _M9[a2] ^ _M14[a3]
        return out


# ---------------------------------------------------------------- CBC


def cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-256-CBC 加密（data 长度须为 16 的整数倍，补位由调用方负责）。"""
    if len(data) % 16:
        raise ValueError("CBC 数据长度须为 16 的倍数")
    aes = AES256(key)
    prev = bytearray(iv)
    out = bytearray()
    for off in range(0, len(data), 16):
        blk = bytes(a ^ b for a, b in zip(data[off:off + 16], prev))
        prev = bytearray(aes.encrypt_block(blk))
        out += prev
    return bytes(out)


def cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-256-CBC 解密（不做去补位）。"""
    if len(data) % 16:
        raise ValueError("CBC 数据长度须为 16 的倍数")
    aes = AES256(key)
    prev = bytes(iv)
    out = bytearray()
    for off in range(0, len(data), 16):
        blk = data[off:off + 16]
        plain = aes.decrypt_block(blk)
        out += bytes(a ^ b for a, b in zip(plain, prev))
        prev = blk
    return bytes(out)
