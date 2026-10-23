"""MT5 协议加密层 / MT5 protocol crypto layer.

规范来源 / where this comes from
--------------------------------
本文件实现 MT5 协议在「Web 终端通道」上的加密方式，但同一套算法
（AES-256-CBC + 零 IV + PKCS7 + 先静态密钥握手再换会话密钥）也是
安卓客户端 `libcommon.so` 里网络层使用的方案。字段以实机采样为准，
参考文档见 `日志/参考/MT5_WebSocket协议参考_第三方.md`（第三方资料，
仅作对照，最终以本机对 Exness 网关的实测结果为准）。

The MT5 protocol encrypts every frame:

* handshake frame -> AES-256-CBC, key = AUTH_KEY (constant), IV = 16 zero bytes
* every later frame -> AES-256-CBC, key = session key handed out by the server

Padding is PKCS7.  Nothing here is secret-key management: the static key is a
protocol constant, the session key is sent by the server in the clear *inside*
the encrypted auth reply.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------- AES 后端
#
# 三种后端按可用性自动选择，保证「零第三方依赖也能跑」：
#   1. pycryptodome  （Crypto.Cipher.AES）
#   2. cryptography  （hazmat）
#   3. 纯标准库       （mt5api._aes_pure）
#
# 三者输出必须逐字节一致（见 90_selfcheck.py 的 AES 交叉校验用例）。

# 可用 MT5_AES_BACKEND=pure|cryptography|pycryptodome 强制指定（便于测试与排障）。

_FORCE = os.environ.get("MT5_AES_BACKEND", "").strip().lower()

_AES = None
_Cipher = None
_BACKEND = ""

if _FORCE in ("", "pycryptodome"):
    try:                                            # 1. pycryptodome
        from Crypto.Cipher import AES as _AES
        _BACKEND = "pycryptodome"
    except ImportError:
        _AES = None

if not _BACKEND and _FORCE in ("", "cryptography"):
    try:                                            # 2. cryptography
        from cryptography.hazmat.primitives.ciphers import (
            Cipher as _Cipher, algorithms as _algorithms, modes as _modes,
        )
        _BACKEND = "cryptography"
    except ImportError:
        _Cipher = None

if not _BACKEND:                                    # 3. 纯标准库
    _BACKEND = "pure"
    from . import _aes_pure



def aes_backend() -> str:
    """当前使用的 AES 后端名（``pycryptodome`` / ``cryptography`` / ``pure``）。"""
    return _BACKEND


def _aes_cbc_encrypt(key: bytes, data: bytes) -> bytes:
    if _BACKEND == "pycryptodome":
        return _AES.new(key, _AES.MODE_CBC, ZERO_IV).encrypt(data)
    if _BACKEND == "cryptography":
        enc = _Cipher(_algorithms.AES(key), _modes.CBC(ZERO_IV)).encryptor()
        return enc.update(data) + enc.finalize()
    return _aes_pure.cbc_encrypt(key, ZERO_IV, data)


def _aes_cbc_decrypt(key: bytes, data: bytes) -> bytes:
    if _BACKEND == "pycryptodome":
        return _AES.new(key, _AES.MODE_CBC, ZERO_IV).decrypt(data)
    if _BACKEND == "cryptography":
        dec = _Cipher(_algorithms.AES(key), _modes.CBC(ZERO_IV)).decryptor()
        return dec.update(data) + dec.finalize()
    return _aes_pure.cbc_decrypt(key, ZERO_IV, data)

#: 握手阶段（第一条报文）使用的固定密钥，32 字节。
#: Constant key used for the very first (handshake) frame.
AUTH_KEY = bytes.fromhex(
    "02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964"
)

#: CBC 模式的 IV——MT5 全协议固定为 16 个 0 字节。
#: The IV is fixed at 16 zero bytes for the whole protocol.
ZERO_IV = bytes(16)

BLOCK = 16


def pkcs7_pad(data: bytes) -> bytes:
    n = BLOCK - (len(data) % BLOCK)
    return data + bytes([n]) * n


def pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        return data
    n = data[-1]
    if 1 <= n <= BLOCK and len(data) >= n and data[-n:] == bytes([n]) * n:
        return data[:-n]
    return data


def encrypt(key: bytes, plaintext: bytes) -> bytes:
    """AES-256-CBC(零 IV) 加密 + 自动补位。"""
    return _aes_cbc_encrypt(key, pkcs7_pad(plaintext))


def decrypt(key: bytes, ciphertext: bytes) -> bytes:
    """AES-256-CBC(零 IV) 解密并去掉 PKCS7 补位。"""
    if len(ciphertext) % BLOCK:
        raise ValueError(f"ciphertext not block aligned: {len(ciphertext)}")
    return pkcs7_unpad(_aes_cbc_decrypt(key, ciphertext))


def random_bytes(n: int) -> bytes:
    return os.urandom(n)
