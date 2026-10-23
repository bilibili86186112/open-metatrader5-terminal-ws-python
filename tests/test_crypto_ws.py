"""AES 与纯标准库 WebSocket 的测试（离线、无网络）。"""

from __future__ import annotations

import base64
import hashlib
import socket
import struct
import threading
import unittest

from mt5ws import _aes_pure, _ws_pure, crypto


class TestAES(unittest.TestCase):
    def test_available_backends(self) -> None:
        self.assertTrue(crypto.aes_backend())

    def test_roundtrip_cbc(self) -> None:
        """纯标准库后端的 CBC 往返（自补/去 PKCS#7 填充）。"""
        key = bytes(range(32))
        iv = bytes(range(16))
        plain = b"hello mt5 protocol" * 3
        enc = _aes_pure.cbc_encrypt(key, iv, crypto.pkcs7_pad(plain))
        dec = crypto.pkcs7_unpad(_aes_pure.cbc_decrypt(key, iv, enc))
        self.assertEqual(dec, plain)

    def test_pkcs7(self) -> None:
        for n in (0, 1, 15, 16, 17, 32):
            data = b"x" * n
            self.assertEqual(crypto.pkcs7_unpad(crypto.pkcs7_pad(data)), data)

    def test_nist_vector(self) -> None:
        """FIPS-197 附录 C.3：AES-256 单块加密已知答案。"""
        key = bytes.fromhex(
            "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
        plain = bytes.fromhex("00112233445566778899aabbccddeeff")
        expect = bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")
        self.assertEqual(_aes_pure.AES256(key).encrypt_block(plain), expect)

    def test_blocksize(self) -> None:
        self.assertEqual(crypto.BLOCK, 16)
        self.assertEqual(len(crypto.ZERO_IV), 16)


class TestPureWebSocket(unittest.TestCase):
    """socketpair 回环验证帧编解码（不联网）。"""

    def test_masked_frame_and_reassembly(self) -> None:
        a, b = socket.socketpair()
        got: dict = {}

        def server() -> None:
            head = b.recv(2)
            masked = bool(head[1] & 0x80)
            ln = head[1] & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", b.recv(2))[0]
            key = b.recv(4) if masked else b""
            payload = b.recv(ln)
            if masked:
                payload = bytes(x ^ key[i & 3] for i, x in enumerate(payload))
            got["masked"] = masked
            got["payload"] = payload
            b.sendall(bytes([0x89, 0x02]) + b"hi")           # ping
            b.sendall(bytes([0x02, 3]) + b"abc")             # 分片 1（FIN=0）
            b.sendall(bytes([0x80, 4]) + b"defg")            # 分片 2（FIN=1）

        t = threading.Thread(target=server, daemon=True)
        t.start()
        ws = _ws_pure.WebSocket(a, recv_timeout=5.0)
        ws.send_binary(b"order-frame")
        msg = ws.recv()
        t.join(timeout=5)
        ws.close()
        a.close()
        b.close()
        self.assertTrue(got["masked"], "客户端帧必须带掩码")
        self.assertEqual(got["payload"], b"order-frame")
        self.assertEqual(msg, b"abcdefg", "分片应自动重组且 ping 自动回 pong")

    def test_accept_key_matches_rfc_example(self) -> None:
        """RFC 6455 §1.3 的示例密钥应当算出同一个 accept 值。"""
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        acc = base64.b64encode(
            hashlib.sha1((key + _ws_pure._GUID).encode()).digest()).decode()
        self.assertEqual(acc, "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")


if __name__ == "__main__":
    unittest.main()
