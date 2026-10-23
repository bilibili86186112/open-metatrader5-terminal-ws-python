"""序列化层测试：字段宽度、定长打包/解析往返、变长字符串。"""

from __future__ import annotations

import struct
import unittest

from mt5ws import serial as S


def f(t: int, n: int | None = None) -> dict:
    d = {"propType": t}
    if n is not None:
        d["propLength"] = n
    return d


class TestWidths(unittest.TestCase):
    def test_fixed_widths(self) -> None:
        self.assertEqual(S.field_size(f(1)), 1)     # i8
        self.assertEqual(S.field_size(f(2)), 2)     # i16
        self.assertEqual(S.field_size(f(3)), 4)     # i32
        self.assertEqual(S.field_size(f(8)), 8)     # f64
        self.assertEqual(S.field_size(f(17)), 8)    # i64
        self.assertEqual(S.field_size(f(18)), 8)    # u64

    def test_var_types_use_prop_length(self) -> None:
        self.assertEqual(S.field_size(f(S.VAR_STRING, 64)), 64)
        self.assertEqual(S.field_size(f(S.VAR_RAW, 16)), 16)

    def test_var_without_length_is_zero(self) -> None:
        self.assertEqual(S.field_size(f(S.VAR_STRING)), 0)

    def test_series_size_sums(self) -> None:
        schema = [f(3), f(8), f(S.VAR_STRING, 32)]
        self.assertEqual(S.series_size(schema), 4 + 8 + 32)


class TestPackParseRoundtrip(unittest.TestCase):
    def test_fixed_roundtrip(self) -> None:
        schema = [f(3), f(8), f(18)]
        vals = [123456, 4131.81, 277985376]
        packed = S.pack_fixed(schema, vals)
        self.assertEqual(len(packed), S.series_size(schema))
        back, off = S.parse_fixed(packed, schema)
        self.assertEqual(len(back), 3)
        self.assertAlmostEqual(back[0], 123456)
        self.assertAlmostEqual(back[1], 4131.81, places=6)
        self.assertEqual(back[2], 277985376)
        self.assertEqual(off, len(packed))

    def test_negative_int(self) -> None:
        schema = [f(1), f(2), f(17)]
        vals = [-5, -300, -10 ** 12]
        back, _ = S.parse_fixed(S.pack_fixed(schema, vals), schema)
        self.assertEqual([int(v) for v in back], vals)

    def test_parse_with_offset(self) -> None:
        schema = [f(3)]
        buf = b"\x00\x00\x00\x00" + struct.pack("<i", 42)
        back, off = S.parse_fixed(buf, schema, offset=4)
        self.assertEqual(int(back[0]), 42)
        self.assertEqual(off, 8)

    def test_parse_series_fixed_string(self) -> None:
        schema = [f(3), f(S.VAR_STRING, 16)]
        buf = struct.pack("<i", 7) + "XAUUSDm".encode("utf-16-le").ljust(16, b"\x00")
        vals, _ = S.parse_series(buf, schema, raw_ok=True)
        self.assertEqual(int(vals[0]), 7)
        self.assertIn("XAUUSDm", str(vals[1]))


class TestWriter(unittest.TestCase):
    def test_writer_matches_struct(self) -> None:
        w = S.Writer()
        w.u8(1).u16(2).u32(3).i32(-4).u64(5).i64(-6).f64(1.5)
        expect = (struct.pack("<B", 1) + struct.pack("<H", 2)
                  + struct.pack("<I", 3) + struct.pack("<i", -4)
                  + struct.pack("<Q", 5) + struct.pack("<q", -6)
                  + struct.pack("<d", 1.5))
        self.assertEqual(bytes(w.buf), expect)

    def test_str_fixed_pads(self) -> None:
        w = S.Writer()
        w.str_fixed("XAU", 16)
        self.assertEqual(len(w.buf), 16)
        self.assertTrue(bytes(w.buf).startswith("XAU".encode("utf-16-le")))

    def test_str_fixed_too_long_truncates(self) -> None:
        w = S.Writer()
        w.str_fixed("ABCDEFGH", 8)
        self.assertEqual(len(w.buf), 8)


if __name__ == "__main__":
    unittest.main()
