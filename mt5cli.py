#!/usr/bin/env python3
"""交易命令行（基于 open-metatrader5-terminal-ws-python）。

两种用法::

    # 一次性执行
    MT5_LOGIN=... MT5_PASSWORD=... python3 mt5cli.py account
    python3 mt5cli.py quote XAUUSDm
    python3 mt5cli.py buy XAUUSDm 0.01 --yes

    # 交互模式（不传命令时进入 REPL）
    MT5_LOGIN=... MT5_PASSWORD=... python3 mt5cli.py

安全设计
--------
* **下单/平仓必须显式确认** —— 交互模式问 ``y/N``；非交互必须加 ``--yes``，
  否则只打印"将要做什么"然后退出（dry-run）。
* 金额类操作前会先跑 ``order_check``（四道闸门），不通过直接拒绝。
* 所有交易成功后**自动重连复核**（因为同会话的持仓快照会滞后，见 README）。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import MetaTrader5 as mt5            # noqa: E402

#: retcode → 中文说明（只列常见的；其余显示数字）
RETCODES = {
    0: "成功",
    10004: "重新报价", 10006: "请求被拒", 10007: "已取消", 10008: "已下单",
    10009: "成交完成", 10010: "部分成交", 10011: "请求处理错误", 10012: "请求超时",
    10013: "无效请求", 10014: "无效手数", 10015: "无效价格", 10016: "无效止损止盈",
    10017: "禁止交易", 10018: "市场关闭", 10019: "资金不足", 10020: "价格已变",
    10021: "无报价", 10030: "不支持的填充方式", 10031: "无连接",
}


def ok_code(rc: int) -> bool:
    """**成功有两种码：0 和 10009**（实测）。"""
    return rc in (0, 10009)


def rc_str(rc: int) -> str:
    return f"{rc} ({RETCODES.get(rc, '未知')})"


def table(rows: list[list[str]], header: list[str]) -> str:
    """极简等宽表格（中文按 2 列宽估算）。"""
    def w(s: str) -> int:
        return sum(2 if ord(c) > 0x2E80 else 1 for c in s)
    widths = [w(h) for h in header]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], w(c))
    out = ["  ".join(h + " " * (widths[i] - w(h)) for i, h in enumerate(header))]
    out.append("  ".join("-" * x for x in widths))
    for r in rows:
        out.append("  ".join(c + " " * (widths[i] - w(c)) for i, c in enumerate(r)))
    return "\n".join(out)


# ---------------------------------------------------------------- 命令实现

def cmd_account(_args) -> int:
    a = mt5.account_info()
    if not a:
        print("拿不到账户信息:", mt5.last_error()); return 1
    print(f"登录号  : {a.login}")
    print(f"服务器  : {a.server}   组: {a.name or '-'}")
    print(f"币种    : {a.currency}")
    print(f"余额    : {a.balance:,.2f}")
    print(f"净值    : {a.equity:,.2f}")
    print(f"可用保证: {a.margin_free:,.2f}   已用: {a.margin:,.2f}")
    print(f"杠杆    : 1:{a.leverage}")
    print(f"可交易  : {('是' if a.trade_allowed else '否') if a.trade_allowed is not None else '未知（服务器不下发）'}")
    return 0


def cmd_symbols(args) -> int:
    syms = mt5.symbols_get(args.pattern or "*")
    print(f"共 {len(syms)} 个品种" + (f"（匹配 {args.pattern}）" if args.pattern else ""))
    for s in syms[:args.limit]:
        print(f"  {s.name:<14} {s.description[:34]:<34} digits={s.digits} spread={s.spread}")
    if len(syms) > args.limit:
        print(f"  …还有 {len(syms) - args.limit} 个（用 --limit 看更多）")
    return 0


def _last_price(symbol: str):
    """取最新价：优先 tick；拿不到就退回最新 M1 收盘价（报价推送可能很稀疏）。"""
    t = mt5.symbol_info_tick(symbol)
    if t and (t.bid or t.ask):
        return t, "tick"
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1)
    if rates:
        c = rates[-1]["close"]
        return mt5.Tick(time=rates[-1]["time"], bid=c, ask=c, last=0.0,
                        volume=0, time_msc=0, flags=0, volume_real=0.0), "kline"
    return None, "none"


def cmd_quote(args) -> int:
    mt5.symbol_select(args.symbol, True)   # 报价只来自订阅推送，先订阅
    t, src = _last_price(args.symbol)
    if not t:
        print(f"{args.symbol}: 无报价（订阅后仍未收到推送，且无 K 线）", mt5.last_error())
        return 1
    si = mt5.symbol_info(args.symbol)
    sp = (t.ask - t.bid) if (t.bid and t.ask) else 0
    note = "" if src == "tick" else "  ← 无实时推送，取自最后一根 M1 收盘价"
    print(f"{args.symbol:<12} bid={t.bid}  ask={t.ask}  点差={sp:.5f}"
          + (f"  digits={si.digits}" if si else "") + note)
    return 0



def cmd_kline(args) -> int:
    mt5.symbol_select(args.symbol, True)   # 同理：先订阅再取 K 线
    tf = getattr(mt5, f"TIMEFRAME_{args.tf.upper()}", None)
    if tf is None:
        print(f"未知周期 {args.tf}（如 M1/M5/M15/H1/D1）"); return 1
    rates = mt5.copy_rates_from_pos(args.symbol, tf, 0, args.count)
    if not rates:
        print("没有 K 线数据", mt5.last_error()); return 1
    rows = [[time.strftime("%m-%d %H:%M", time.gmtime(r["time"])),
             f"{r['open']:.5f}", f"{r['high']:.5f}", f"{r['low']:.5f}",
             f"{r['close']:.5f}", str(r["tick_volume"])] for r in rates]
    print(f"{args.symbol} {args.tf.upper()} — 最近 {len(rows)} 根")
    print(table(rows, ["时间(UTC)", "开", "高", "低", "收", "量"]))
    return 0


def cmd_positions(args) -> int:
    pos = mt5.positions_get(args.symbol)
    if not pos:
        print("当前无持仓"); return 0
    rows = [[f"#{p.ticket}", p.symbol, "BUY" if p.type == 0 else "SELL",
             f"{p.volume:.2f}", f"{p.price_open:.5f}", f"{p.price_current:.5f}",
             f"{p.profit:+.2f}", f"{p.sl:.5f}" if p.sl else "-",
             f"{p.tp:.5f}" if p.tp else "-", (p.comment or "")[:14]] for p in pos]
    total = sum(p.profit for p in pos)
    print(f"持仓 {len(pos)} 笔，浮动盈亏合计 {total:+.2f}")
    print(table(rows, ["票号", "品种", "方向", "手数", "开仓价", "现价", "盈亏", "SL", "TP", "备注"]))
    return 0


def cmd_orders(args) -> int:
    os_ = mt5.orders_get(args.symbol)
    if not os_:
        print("当前无挂单"); return 0
    rows = [[f"#{o.ticket}", o.symbol,
             {2: "BUY LIMIT", 3: "SELL LIMIT", 4: "BUY STOP", 5: "SELL STOP"}.get(o.type, str(o.type)),
             f"{o.volume_initial:.2f}", f"{o.price_open:.5f}", str(o.state)] for o in os_]
    print(f"挂单 {len(os_)} 张")
    print(table(rows, ["票号", "品种", "类型", "手数", "价格", "状态"]))
    return 0


def cmd_history(args) -> int:
    now = time.time()
    deals = mt5.history_deals_get(now - args.days * 86400, now + 60) or ()
    if args.symbol:
        deals = [d for d in deals if d.symbol == args.symbol]
    if not deals:
        print(f"近 {args.days} 天没有成交记录"); return 0
    rows = [[time.strftime("%m-%d %H:%M", time.gmtime(d.time)), d.symbol,
             "BUY" if d.type == 0 else "SELL", f"{d.volume:.2f}",
             f"{d.price:.5f}", f"{d.profit:+.2f}", (d.comment or "")[:16]]
            for d in list(deals)[-args.limit:]]
    print(f"近 {args.days} 天成交 {len(deals)} 笔（显示最后 {len(rows)} 笔）")
    print(table(rows, ["时间(UTC)", "品种", "方向", "手数", "价格", "盈亏", "备注"]))
    return 0


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        print(f"{prompt}  → 已用 --yes 跳过确认")
        return True
    try:
        ans = input(f"{prompt}\n确认执行？[y/N] ").strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes")


def _trade(args, side: str) -> int:
    sym = args.symbol
    mt5.symbol_select(sym, True)
    t = mt5.symbol_info_tick(sym)
    if not t:
        print(f"{sym}: 无报价", mt5.last_error()); return 1
    si = mt5.symbol_info(sym)
    vol = args.volume
    price = t.ask if side == "buy" else t.bid
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": vol,
           "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
           "price": price, "deviation": args.deviation,
           "type_filling": mt5.ORDER_FILLING_IOC,
           "comment": args.comment or "cli",
           "sl": args.sl or 0.0, "tp": args.tp or 0.0}

    print(f"即将 {side.upper()}  {sym}  {vol} 手 @ {price:.5f}  "
          f"(sl={args.sl or '-'} tp={args.tp or '-'} dev={args.deviation} 点)")
    chk = mt5.order_check(req)
    if chk["retcode"] != 0:
        print(f"下单前校验不通过：{chk['comment']}")
        return 1
    print(f"校验通过：预估保证金 {chk['margin']:.2f}，可用 {chk['margin_free']:.2f}")
    if not _confirm("", args.yes):
        print("已取消（未下单）")
        return 0

    t0 = time.perf_counter()
    r = mt5.order_send(req)
    dt = (time.perf_counter() - t0) * 1000
    print(f"结果：retcode={rc_str(r.retcode)}  {dt:.0f} ms")
    if not ok_code(r.retcode):
        return 1

    # 同会话持仓快照会滞后 → 重连复核
    time.sleep(1.0)
    mt5.shutdown(); mt5.initialize()
    mine = [p for p in (mt5.positions_get(sym) or ()) if p.comment == req["comment"]]
    if mine:
        p = mine[-1]
        print(f"已确认为持仓：#{p.ticket} {p.symbol} {p.volume} 手 @ {p.price_open:.5f}")
    else:
        print("⚠ 未能立刻在持仓里看到这笔单（快照滞后），请稍后 positions 查看")
    return 0


def cmd_buy(args) -> int:
    return _trade(args, "buy")


def cmd_sell(args) -> int:
    return _trade(args, "sell")


def cmd_close(args) -> int:
    pos = mt5.positions_get(args.symbol)
    if args.ticket:
        pos = [p for p in (pos or ()) if p.ticket == args.ticket]
    if not pos:
        print("没有可平的持仓"); return 0
    if args.all and not args.symbol:
        pos = mt5.positions_get()
    rows = [[f"#{p.ticket}", p.symbol, "BUY" if p.type == 0 else "SELL",
             f"{p.volume:.2f}", f"{p.price_open:.5f}", f"{p.profit:+.2f}"] for p in pos]
    print(f"将平掉 {len(pos)} 笔：")
    print(table(rows, ["票号", "品种", "方向", "手数", "开仓价", "盈亏"]))
    if not _confirm("", args.yes):
        print("已取消（未平仓）")
        return 0
    done = 0
    for p in pos:
        t = mt5.symbol_info_tick(p.symbol)
        if not t:
            print(f"  #{p.ticket} {p.symbol} 无报价，跳过"); continue
        r = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol,
                            "volume": p.volume,
                            "type": mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY,
                            "position": p.ticket,
                            "price": t.bid if p.type == 0 else t.ask,
                            "deviation": args.deviation,
                            "type_filling": mt5.ORDER_FILLING_IOC,
                            "comment": "cli-close"})
        print(f"  #{p.ticket} → retcode={rc_str(r.retcode)}")
        if ok_code(r.retcode):
            done += 1
        time.sleep(0.4)
    print(f"完成 {done}/{len(pos)} 笔")
    return 0 if done == len(pos) else 1


def cmd_modify(args) -> int:
    pos = [p for p in (mt5.positions_get(args.symbol) or ()) if p.ticket == args.ticket]
    if not pos:
        print(f"找不到持仓 #{args.ticket}"); return 1
    p = pos[0]
    print(f"#{p.ticket} {p.symbol} 当前 SL={p.sl or '-'} TP={p.tp or '-'}")
    print(f"改为 SL={args.sl or '-'} TP={args.tp or '-'}")
    if not _confirm("", args.yes):
        print("已取消"); return 0
    r = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "symbol": p.symbol,
                        "position": p.ticket,
                        "sl": args.sl or 0.0, "tp": args.tp or 0.0})
    print(f"结果：retcode={rc_str(r.retcode)}")
    return 0 if ok_code(r.retcode) else 1


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="mt5cli", description="MetaTrader 5 交易命令行（WebSocket 通道）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="不传子命令时进入交互模式。")
    ap.add_argument("--login", type=int, default=int(os.environ.get("MT5_LOGIN", "0") or 0))
    ap.add_argument("--password", default=os.environ.get("MT5_PASSWORD", ""))
    ap.add_argument("--server", default=os.environ.get("MT5_SERVER", "Exness-MT5Trial5"))
    ap.add_argument("--endpoint", action="append", default=None,
                    help="手动指定接入点 host:port，可重复")
    ap.add_argument("-y", "--yes", action="store_true", help="交易时跳过确认（危险）")

    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("account", help="账户信息").set_defaults(fn=cmd_account)

    s = sub.add_parser("symbols", help="品种列表")
    s.add_argument("pattern", nargs="?", default=None)
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=cmd_symbols)

    for name in ("quote", "kline", "positions", "orders"):
        s = sub.add_parser(name, help={"quote": "报价", "kline": "K 线",
                                       "positions": "持仓", "orders": "挂单"}[name])
        s.add_argument("symbol", nargs="?", default=None)
        if name == "kline":
            s.add_argument("--tf", default="M1")
            s.add_argument("--count", type=int, default=20)
        s.set_defaults(fn={"quote": cmd_quote, "kline": cmd_kline,
                           "positions": cmd_positions, "orders": cmd_orders}[name])

    s = sub.add_parser("history", help="成交历史")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--symbol", default=None)
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_history)

    for name in ("buy", "sell"):
        s = sub.add_parser(name, help=f"{name} 市价开仓")
        s.add_argument("symbol")
        s.add_argument("volume", type=float)
        s.add_argument("--sl", type=float, default=None)
        s.add_argument("--tp", type=float, default=None)
        s.add_argument("--deviation", type=int, default=20)
        s.add_argument("--comment", default=None)
        s.set_defaults(fn=cmd_buy if name == "buy" else cmd_sell)

    s = sub.add_parser("close", help="平仓")
    s.add_argument("symbol", nargs="?", default=None)
    s.add_argument("--ticket", type=int, default=None)
    s.add_argument("--all", action="store_true")
    s.add_argument("--deviation", type=int, default=20)
    s.set_defaults(fn=cmd_close)

    s = sub.add_parser("modify", help="改止损止盈")
    s.add_argument("ticket", type=int)
    s.add_argument("symbol")
    s.add_argument("--sl", type=float, default=None)
    s.add_argument("--tp", type=float, default=None)
    s.set_defaults(fn=cmd_modify)
    return ap


def repl(args) -> int:
    print("mt5cli 交互模式 —— 输入 help 看命令，quit 退出")
    while True:
        try:
            line = input("mt5> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return 0
        if not line:
            continue
        if line in ("quit", "exit", "q"):
            return 0
        if line in ("help", "?"):
            print("命令: account | symbols [匹配] | quote SYMBOL | kline SYMBOL [--tf M5] |"
                  "\n      positions | orders | history | buy SYMBOL VOL | sell SYMBOL VOL |"
                  "\n      close [SYMBOL] [--all] | modify TICKET SYMBOL --sl X --tp Y | quit")
            continue
        argv = line.split()
        try:
            ns = build_parser().parse_args(argv)
            fn = getattr(ns, "fn", None)
            if fn is None:
                print("未知命令，输入 help 查看"); continue
            ns.yes = args.yes
            fn(ns)
        except SystemExit:
            pass
        except Exception as exc:                          # noqa: BLE001
            print(f"出错: {type(exc).__name__}: {exc}")


def main() -> int:
    args = build_parser().parse_args()
    if not args.login or not args.password:
        print("需要账号与密码：用 --login/--password，或设 MT5_LOGIN / MT5_PASSWORD")
        return 2
    print(f"连接 {args.login} @ {args.server} …")
    # 接入点可显式指定（否则用 mt5ws/endpoints.py 的内置表）
    _eps = args.endpoint

    if not mt5.initialize(login=args.login, password=args.password, server=args.server,
                          endpoints=_eps):
        print("连接失败:", mt5.last_error()); return 2
    a = mt5.account_info()
    print(f"已连接：{a.login} | 余额 {a.balance:,.2f} {a.currency} | 杠杆 1:{a.leverage}")
    try:
        fn = getattr(args, "fn", None)
        if fn is None:
            return repl(args)
        return fn(args)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
