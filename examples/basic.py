#!/usr/bin/env python3
"""最小示例：登录 → 账户 → 报价 → K 线 → 持仓 → 平仓演示（默认只读）。

    MT5_LOGIN=... MT5_PASSWORD=... MT5_SERVER=Exness-MT5Trial5 python3 examples/basic.py
    ... python3 examples/basic.py --trade      # 额外做一笔 0.01 手的开+平测试
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5   # noqa: E402

SYMBOL = os.environ.get("MT5_SYMBOL", "XAUUSDm")


def main() -> int:
    if not mt5.initialize():
        print("连接失败:", mt5.last_error())
        return 2
    try:
        a = mt5.account_info()
        print(f"账户 {a.login} | 余额 {a.balance:.2f} {a.currency} | 杠杆 1:{a.leverage} | {a.server}")

        mt5.symbol_select(SYMBOL, True)
        t = mt5.symbol_info_tick(SYMBOL)
        si = mt5.symbol_info(SYMBOL)
        print(f"{SYMBOL}: bid={t.bid} ask={t.ask} digits={si.digits} 合约={si.contract_size} "
              f"手数 {si.volume_min}~{si.volume_max} 步长 {si.volume_step}")

        rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 0, 5)
        print(f"最近 {len(rates)} 根 M1：", end="")
        for r in rates[-3:]:
            print(f"[{time.strftime('%H:%M', time.gmtime(r['time']))} {r['close']}]", end=" ")
        print()

        pos = mt5.positions_get()
        print(f"持仓 {len(pos)} 笔")

        if "--trade" in sys.argv:
            r = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL,
                                "volume": si.volume_min, "type": mt5.ORDER_TYPE_BUY,
                                "price": t.ask, "deviation": 20,
                                "type_filling": mt5.ORDER_FILLING_IOC,
                                "comment": "example"})
            print(f"下单 retcode={r.retcode} comment={r.comment!r}")
            time.sleep(1.5)
            mt5.shutdown(); mt5.initialize(); mt5.symbol_select(SYMBOL, True)
            mine = [p for p in mt5.positions_get() if p.symbol == SYMBOL and p.comment == "example"]
            for p in mine:
                tt = mt5.symbol_info_tick(SYMBOL)
                rr = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL,
                                     "volume": p.volume, "type": mt5.ORDER_TYPE_SELL,
                                     "position": p.ticket, "price": tt.bid,
                                     "deviation": 20,
                                     "type_filling": mt5.ORDER_FILLING_IOC,
                                     "comment": "example-close"})
                print(f"平仓 retcode={rr.retcode}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
