"""品种规格 / 风控测试：spec 解析、手数规整、保证金、按风险算手数、货币折算。"""

from __future__ import annotations

import os
import unittest

from mt5ws.symbol_spec import (SPEC_SIZE, check_sltp, compute_margin,
                                margin_from_spec, parse_spec, quote_currency,
                                selftest_conv, selftest_margin, selftest_sltp,
                                selftest_size_by_risk, size_by_risk,
                                size_by_risk_conv, snap_volume)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(HERE, "mt5ws", "frames", "spec_xauusdm.bin")
SPEC = {"digits": 3, "point": 0.001, "contract_size": 100.0,
        "volume_min": 0.01, "volume_max": 200.0, "volume_step": 0.01}


class TestParseSpec(unittest.TestCase):
    def setUp(self) -> None:
        if not os.path.isfile(FIXTURE):
            self.skipTest("缺少真机样本 spec_xauusdm.bin")
        with open(FIXTURE, "rb") as fh:
            self.raw = fh.read()

    def test_size(self) -> None:
        self.assertEqual(len(self.raw), SPEC_SIZE)

    def test_fields(self) -> None:
        info = parse_spec(self.raw)
        self.assertEqual(info["name"], "XAUUSDm")
        self.assertEqual(info["digits"], 3)
        self.assertAlmostEqual(info["point"], 0.001, places=12)
        self.assertAlmostEqual(info["contract_size"], 100.0, places=9)
        self.assertAlmostEqual(info["volume_min"], 0.01, places=12)
        self.assertAlmostEqual(info["volume_max"], 200.0, places=9)
        self.assertAlmostEqual(info["volume_step"], 0.01, places=12)
        self.assertEqual(info["symbol_id"], 442)
        self.assertEqual(info["trade_mode"], 4)

    def test_too_short_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_spec(self.raw[:100])


class TestVolume(unittest.TestCase):
    def test_snap_step(self) -> None:
        self.assertAlmostEqual(snap_volume(0.019, SPEC), 0.02, places=12)

    def test_snap_clamp_high(self) -> None:
        self.assertAlmostEqual(snap_volume(250, SPEC), 200.0, places=9)

    def test_snap_clamp_low(self) -> None:
        self.assertAlmostEqual(snap_volume(0.001, SPEC), 0.01, places=12)

    def test_no_spec_returns_unchanged(self) -> None:
        self.assertAlmostEqual(snap_volume(0.037, {}), 0.037, places=12)


class TestMargin(unittest.TestCase):
    def test_xau(self) -> None:
        self.assertAlmostEqual(compute_margin(0.72, 100.0, 4131.81, 2000),
                               148.745, delta=0.01)

    def test_leverage_halves_margin(self) -> None:
        a = compute_margin(1, 100, 4000, 4000)
        b = compute_margin(1, 100, 4000, 2000)
        self.assertAlmostEqual(a, b / 2, places=12)

    def test_zero_leverage_raises(self) -> None:
        with self.assertRaises(ValueError):
            compute_margin(1, 100, 4000, 0)

    def test_from_spec(self) -> None:
        self.assertAlmostEqual(margin_from_spec(0.01, SPEC, 4131.81, 2000),
                               2.065905, delta=0.01)


class TestRiskSizing(unittest.TestCase):
    def test_basic(self) -> None:
        r = size_by_risk(10000, 1.0, 4100.0, 4090.0, SPEC)
        self.assertAlmostEqual(r["volume"], 0.1, places=12)
        self.assertAlmostEqual(r["risk_amount"], 100.0, places=9)
        self.assertAlmostEqual(r["risk_per_lot"], 1000.0, places=9)
        self.assertFalse(r["overshoot"])

    def test_half_stop_doubles_volume(self) -> None:
        r = size_by_risk(10000, 1.0, 4100.0, 4095.0, SPEC)
        self.assertAlmostEqual(r["volume"], 0.2, places=12)

    def test_min_lot_overshoot(self) -> None:
        r = size_by_risk(100, 0.1, 4100.0, 4099.0, SPEC)
        self.assertAlmostEqual(r["volume"], 0.01, places=12)
        self.assertTrue(r["overshoot"])

    def test_zero_stop_raises(self) -> None:
        with self.assertRaises(ValueError):
            size_by_risk(10000, 1.0, 4100.0, 4100.0, SPEC)


class TestCurrency(unittest.TestCase):
    def test_quote_detection(self) -> None:
        for sym, exp in (("XAUUSDm", "USD"), ("EURUSDm", "USD"),
                         ("USDJPYm", "JPY"), ("GBPJPYm", "JPY"),
                         ("AUDCADm", "CAD"), ("BTCUSDm", "USD"),
                         ("XAGUSDm", "USD")):
            self.assertEqual(quote_currency(sym), exp, sym)

    def test_conversion(self) -> None:
        spec = {"contract_size": 100000.0, "volume_min": 0.01,
                "volume_step": 0.01, "volume_max": 200.0}
        r = size_by_risk_conv(10020, 0.5, 158.158, 157.858, spec,
                              quote_to_account=1 / 158.0)
        self.assertAlmostEqual(r["risk_in_account"], 300 / 158, delta=0.02)
        self.assertFalse(r["overshoot_account"])


class TestSltp(unittest.TestCase):
    def test_long_ok(self) -> None:
        self.assertEqual(check_sltp("buy", 100.0, 95.0, 110.0), [])

    def test_long_bad_sl(self) -> None:
        self.assertEqual(len(check_sltp("buy", 100.0, 105.0)), 1)

    def test_short_ok(self) -> None:
        self.assertEqual(check_sltp("sell", 100.0, 110.0, 90.0), [])

    def test_short_bad_tp(self) -> None:
        self.assertEqual(len(check_sltp("sell", 100.0, None, 110.0)), 1)


class TestBuiltinSelftests(unittest.TestCase):
    def test_all_selftests_pass(self) -> None:
        for fn in (selftest_margin, selftest_sltp, selftest_size_by_risk,
                   selftest_conv):
            bad = [n for n, ok, _ in fn() if not ok]
            self.assertEqual(bad, [], f"{fn.__name__} 未通过: {bad}")


if __name__ == "__main__":
    unittest.main()
