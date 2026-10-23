"""交易 CLI 的离线测试（帮助、参数解析、无凭证时退出码）。"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(args, timeout=90):
    return subprocess.run([sys.executable, os.path.join(HERE, "mt5cli.py")] + args,
                          cwd=HERE, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "MT5_LOGIN": "", "MT5_PASSWORD": ""})


class TestCli(unittest.TestCase):
    def test_help(self) -> None:
        r = run(["--help"])
        self.assertEqual(r.returncode, 0)
        for cmd in ("account", "quote", "kline", "positions", "buy", "sell",
                    "close", "modify", "history", "orders", "symbols"):
            self.assertIn(cmd, r.stdout)

    def test_missing_credentials_exits_2(self) -> None:
        r = run(["account"])
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("需要账号与密码", r.stdout)

    def test_trade_cmd_requires_symbol_and_volume(self) -> None:
        r = run(["buy"])
        self.assertNotEqual(r.returncode, 0)

    def test_retcode_helper(self) -> None:
        sys.path.insert(0, HERE)
        import mt5cli
        self.assertTrue(mt5cli.ok_code(0))
        self.assertTrue(mt5cli.ok_code(10009))
        self.assertFalse(mt5cli.ok_code(10013))
        self.assertIn("成功", mt5cli.rc_str(0))
        self.assertIn("无效请求", mt5cli.rc_str(10013))

    def test_table_renders(self) -> None:
        sys.path.insert(0, HERE)
        import mt5cli
        out = mt5cli.table([["a", "1"], ["bb", "22"]], ["k", "v"])
        self.assertIn("k", out)
        self.assertIn("bb", out)
        self.assertEqual(len(out.splitlines()), 4)


if __name__ == "__main__":
    unittest.main()
