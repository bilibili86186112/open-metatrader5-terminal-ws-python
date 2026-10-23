"""``MetaTrader5`` 兼容层测试（离线：常量、签名、未初始化行为）。"""

from __future__ import annotations

import unittest

import MetaTrader5 as mt5


class TestConstants(unittest.TestCase):
    """常量数值必须与官方包一致，否则换库就出错。"""

    def test_timeframes(self) -> None:
        self.assertEqual(mt5.TIMEFRAME_M1, 1)
        self.assertEqual(mt5.TIMEFRAME_M5, 5)
        self.assertEqual(mt5.TIMEFRAME_M15, 15)
        self.assertEqual(mt5.TIMEFRAME_M30, 30)
        self.assertEqual(mt5.TIMEFRAME_H1, 16385)
        self.assertEqual(mt5.TIMEFRAME_H4, 16388)
        self.assertEqual(mt5.TIMEFRAME_D1, 16408)
        self.assertEqual(mt5.TIMEFRAME_W1, 32769)
        self.assertEqual(mt5.TIMEFRAME_MN1, 49153)

    def test_order_types(self) -> None:
        self.assertEqual(mt5.ORDER_TYPE_BUY, 0)
        self.assertEqual(mt5.ORDER_TYPE_SELL, 1)
        self.assertEqual(mt5.ORDER_TYPE_BUY_LIMIT, 2)
        self.assertEqual(mt5.ORDER_TYPE_SELL_STOP, 5)

    def test_filling_and_time(self) -> None:
        self.assertEqual(mt5.ORDER_FILLING_FOK, 0)
        self.assertEqual(mt5.ORDER_FILLING_IOC, 1)
        self.assertEqual(mt5.ORDER_FILLING_RETURN, 2)
        self.assertEqual(mt5.ORDER_TIME_GTC, 0)

    def test_trade_actions(self) -> None:
        self.assertEqual(mt5.TRADE_ACTION_DEAL, 1)
        self.assertEqual(mt5.TRADE_ACTION_PENDING, 5)
        self.assertEqual(mt5.TRADE_ACTION_SLTP, 6)

    def test_retcodes(self) -> None:
        self.assertEqual(mt5.TRADE_RETCODE_DONE, 10009)
        self.assertEqual(mt5.TRADE_RETCODE_INVALID, 10013)
        self.assertEqual(mt5.TRADE_RETCODE_NO_MONEY, 10019)
        self.assertEqual(mt5.TRADE_RETCODE_MARKET_CLOSED, 10018)


class TestSurface(unittest.TestCase):
    """官方包的函数都要在，签名要能接受常见调用。"""

    FUNCS = ("initialize", "login", "shutdown", "last_error", "version",
             "account_info", "symbols_get", "symbol_info", "symbol_info_tick",
             "symbol_select", "copy_rates_from_pos", "copy_rates_from",
             "copy_rates_range", "positions_get", "orders_get",
             "history_deals_get", "order_send", "order_check")

    def test_all_functions_exist(self) -> None:
        missing = [f for f in self.FUNCS if not hasattr(mt5, f)]
        self.assertEqual(missing, [], f"缺少: {missing}")

    def test_namedtuple_fields(self) -> None:
        self.assertIn("balance", mt5.AccountInfo._fields)
        self.assertIn("equity", mt5.AccountInfo._fields)
        self.assertIn("leverage", mt5.AccountInfo._fields)
        self.assertIn("bid", mt5.Tick._fields)
        self.assertIn("ask", mt5.Tick._fields)
        self.assertIn("ticket", mt5.Position._fields)
        self.assertIn("price_open", mt5.Position._fields)
        self.assertIn("retcode", mt5.OrderSendResult._fields)
        self.assertIn("volume_initial", mt5.Order._fields)


class TestUninitialized(unittest.TestCase):
    """未初始化时的行为要与官方一致：返回 None/空，并设置 last_error。"""

    def test_returns_none_and_sets_error(self) -> None:
        mt5.shutdown()
        self.assertIsNone(mt5.account_info())
        code, msg = mt5.last_error()
        self.assertNotEqual(code, 0)
        self.assertIn("未初始化", msg)

    def test_initialize_without_credentials_fails(self) -> None:
        import os
        old = os.environ.pop("MT5_PASSWORD", None)
        try:
            self.assertFalse(mt5.initialize())
            code, _ = mt5.last_error()
            self.assertNotEqual(code, 0)
        finally:
            if old is not None:
                os.environ["MT5_PASSWORD"] = old


if __name__ == "__main__":
    unittest.main()
