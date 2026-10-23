"""WebSocket 传输层测试：组帧/拆帧/加解密往返（纯函数，离线）。"""

from __future__ import annotations

import struct
import unittest

from mt5ws import crypto
from mt5ws.transport import (FRAME_PREFIX, BaseTransport, Frame, MT5Error,
                              MT5Disconnected)


class TestExceptions(unittest.TestCase):
    def test_hierarchy(self) -> None:
        self.assertTrue(issubclass(MT5Disconnected, MT5Error))
        self.assertTrue(issubclass(MT5Error, RuntimeError))


class TestFrame(unittest.TestCase):
    def test_fields(self) -> None:
        fr = Frame(tag=7, cmd=0x12, res_code=0, body=b"abc", raw=b"xyz")
        self.assertEqual((fr.tag, fr.cmd, fr.res_code, fr.body), (7, 0x12, 0, b"abc"))

    def test_repr_contains_cmd(self) -> None:
        fr = Frame(1, 0x12, 0, b"", b"")
        r = repr(fr).lower()
        self.assertIn("cmd=18", r)          # repr 用十进制
        self.assertIn("tag=1", r)


class TestEncodeDecodeRoundtrip(unittest.TestCase):
    KEY = bytes(range(32))

    def _t(self) -> BaseTransport:
        t = BaseTransport()
        t.session_key = self.KEY
        return t

    def test_encode_produces_8byte_prefix(self) -> None:
        raw = self._t().encode(0x12, b"hello")
        length, version = struct.unpack_from("<II", raw, 0)
        self.assertEqual(version, 1)
        self.assertEqual(length, len(raw) - FRAME_PREFIX)
        self.assertEqual(length % 16, 0, "密文必须 16 字节对齐")

    def test_roundtrip_short_header(self) -> None:
        t = self._t()
        raw = t.encode(0x12, b"payload-here")
        plain = t.decode_plain(raw)
        # short 头：2 字节随机 + 2 字节 cmd
        self.assertEqual(len(plain), 4 + len(b"payload-here"))
        self.assertEqual(struct.unpack_from("<H", plain, 2)[0], 0x12)
        self.assertEqual(plain[4:], b"payload-here")

    def test_roundtrip_tagged_header(self) -> None:
        t = self._t()
        raw = t.encode(0x34, b"abc", header="tagged")
        plain = t.decode_plain(raw)
        self.assertEqual(struct.unpack_from("<H", plain, 2)[0], 0x34)
        self.assertEqual(plain[4:], b"abc")

    def test_no_session_key_raises(self) -> None:
        t = BaseTransport()
        with self.assertRaises(MT5Error):
            t.encode(1, b"")
        with self.assertRaises(MT5Error):
            t.decode_plain(b"\x00" * 16)

    def test_bad_header_name(self) -> None:
        with self.assertRaises(ValueError):
            self._t().encode(1, b"", header="nope")


class TestStripPrefix(unittest.TestCase):
    def test_strips_when_consistent(self) -> None:
        t = BaseTransport()
        ct = crypto.encrypt(bytes(range(32)), b"1234567890abcdef")
        raw = struct.pack("<II", len(ct), 1) + ct
        self.assertEqual(t.strip_prefix(raw), ct)

    def test_accepts_server_style_version_zero(self) -> None:
        """服务器回的 version=0 也要能剥（实测 Exness 网关如此）。"""
        t = BaseTransport()
        ct = crypto.encrypt(bytes(range(32)), b"abcdefghijklmnop")
        raw = struct.pack("<II", len(ct), 0) + ct
        self.assertEqual(t.strip_prefix(raw), ct)

    def test_no_prefix_16_aligned_returned_as_is(self) -> None:
        t = BaseTransport()
        ct = crypto.encrypt(bytes(range(32)), b"abcdefghijklmnop")
        self.assertEqual(t.strip_prefix(ct), ct)


if __name__ == "__main__":
    unittest.main()
