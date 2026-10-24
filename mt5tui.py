#!/usr/bin/env python3
"""MT5 TUI —— 全屏终端界面（curses，纯标准库，零依赖）。

    export MT5_LOGIN=... MT5_PASSWORD=... MT5_SERVER=Exness-MT5Trial5
    python3 mt5tui.py                  # 全屏界面
    python3 mt5tui.py --render         # 只渲染一帧到 stdout（无 TTY 也能看布局）
    python3 mt5tui.py --watch XAUUSDm,GBPUSDm --interval 2

布局：
    标题栏 │ 账户行（余额/净值/可用/杠杆）
    左：自选（实时报价，涨跌箭头）   右：持仓（选中可平仓）
    日志区 │ 命令行/确认栏

按键：
    q 退出   r 刷新   Tab 切换左右   j/k ↑/↓ 移动
    b 买入(选中品种)  s 卖出  c 平掉选中持仓  : 命令行
    命令：buy SYM [VOL] / sell SYM [VOL] / close TICKET / add SYM / del SYM / refresh

安全：买入/卖出/平仓**都要二次确认**（底部 y/N），不会误触。
交易后自动重连刷新（同会话持仓快照会滞后）。
"""

from __future__ import annotations

import argparse
import curses
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import MetaTrader5 as mt5                # noqa: E402

RETCODES = {
    0: "成功", 10004: "重新报价", 10006: "请求被拒", 10008: "已下单", 10009: "成交完成",
    10010: "部分成交", 10011: "处理错误", 10012: "超时", 10013: "无效请求",
    10014: "无效手数", 10015: "无效价格", 10016: "无效止损止盈", 10017: "禁止交易",
    10018: "市场关闭", 10019: "资金不足", 10020: "价格已变", 10021: "无报价",
    10030: "不支持该填充方式", 10031: "无连接",
}


def ok_code(rc: int) -> bool:
    return rc in (0, 10009)


def rc_str(rc: int) -> str:
    return f"{rc} {RETCODES.get(rc, '未知')}"


class App:
    def __init__(self, symbols, interval, volume):
        self.symbols = list(symbols)
        self.interval = interval
        self.volume = volume
        self.ticks = {}
        self.prev = {}
        self.positions = []
        self.account = None
        self.log = []
        self.focus = "watch"
        self.cursor = 0
        self.cmd_buf = ""
        self.mode = "normal"
        self.confirm_text = ""
        self.confirm_action = None
        self.last_full = 0.0

    def say(self, msg):
        self.log.append(f"{time.strftime('%H:%M:%S')} {msg}")
        del self.log[:-200]

    def refresh_ticks(self):
        missing = []
        for s in self.symbols:
            try:
                t = mt5.symbol_info_tick(s)
                if t and (t.bid or t.ask):
                    self.prev[s] = self.ticks.get(s, (0.0, 0.0, 0))[0]
                    self.ticks[s] = (t.bid, t.ask, t.time)
                else:
                    missing.append(s)
            except Exception:
                missing.append(s)
        # 报价推送可能很稀疏：等不到 tick 就用最后一根 M1 收盘价兜底（并标记来源）
        for s in missing:
            try:
                r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M1, 0, 1)
                if r:
                    c = r[-1]["close"]
                    self.prev[s] = self.ticks.get(s, (0.0, 0.0, 0))[0]
                    self.ticks[s] = (c, c, r[-1]["time"])
            except Exception:
                pass
    def refresh_full(self):
        try:
            self.account = mt5.account_info()
        except Exception as exc:
            self.say(f"账户查询失败: {type(exc).__name__}")
        try:
            self.positions = list(mt5.positions_get() or ())
        except Exception as exc:
            self.say(f"持仓查询失败: {type(exc).__name__}")
        self.last_full = time.time()

    def refresh(self, full=False):
        self.refresh_ticks()
        if full or time.time() - self.last_full > 10:
            self.refresh_full()

    def current_symbol(self):
        if self.focus == "watch" and self.cursor < len(self.symbols):
            return self.symbols[self.cursor]
        return None

    def current_position(self):
        if self.focus == "positions" and self.positions:
            return self.positions[min(self.cursor, len(self.positions) - 1)]
        return None

    def ask_confirm(self, text, action):
        self.mode = "confirm"
        self.confirm_text = text
        self.confirm_action = action

    def do_trade(self, side):
        sym = self.current_symbol()
        if not sym:
            self.say("请先在左侧选一个品种")
            return
        t = self.ticks.get(sym)
        price = (t[1] if side == "buy" else t[0]) if t else 0.0
        self.ask_confirm(f"确认 {side.upper()} {sym} {self.volume} 手 @ {price:.5f} ?",
                         lambda: self._send_trade(side, sym, price))

    def _send_trade(self, side, sym, price, vol=None):
        vol = vol or self.volume
        r = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "symbol": sym,
                            "volume": vol,
                            "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
                            "price": price, "deviation": 20,
                            "type_filling": mt5.ORDER_FILLING_IOC,
                            "comment": "tui"})
        if ok_code(r.retcode):
            self.say(f"{side.upper()} {sym} {vol} → {rc_str(r.retcode)}")
            self.reconnect_reload()
        else:
            self.say(f"{side.upper()} {sym} 失败: {rc_str(r.retcode)} {r.comment}")

    def do_close(self):
        p = self.current_position()
        if not p:
            self.say("请先在右侧选一笔持仓")
            return
        self.ask_confirm(f"确认平掉 #{p.ticket} {p.symbol} {p.volume} 手 ?",
                         lambda: self._send_close(p))

    def _send_close(self, p):
        t = mt5.symbol_info_tick(p.symbol)
        bid = t.bid if t else 0.0
        ask = t.ask if t else 0.0
        r = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol,
                            "volume": p.volume,
                            "type": mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY,
                            "position": p.ticket,
                            "price": bid if p.type == 0 else ask,
                            "deviation": 20, "type_filling": mt5.ORDER_FILLING_IOC,
                            "comment": "tui-close"})
        if ok_code(r.retcode):
            self.say(f"平仓 #{p.ticket} → {rc_str(r.retcode)}")
            self.reconnect_reload()
        else:
            self.say(f"平仓 #{p.ticket} 失败: {rc_str(r.retcode)}")

    def reconnect_reload(self):
        try:
            mt5.shutdown()
            mt5.initialize()
            for s in self.symbols:
                mt5.symbol_select(s, True)
            self.refresh_full()
            self.refresh_ticks()
            self.say("已重连并刷新")
        except Exception as exc:
            self.say(f"重连失败: {type(exc).__name__}")

    def run_command(self, line):
        parts = line.split()
        if not parts:
            return
        cmd = parts[0].lower()
        try:
            if cmd in ("buy", "sell") and len(parts) >= 2:
                sym = parts[1]
                vol = float(parts[2]) if len(parts) > 2 else self.volume
                if sym not in self.symbols:
                    mt5.symbol_select(sym, True)
                    self.symbols.append(sym)
                t = mt5.symbol_info_tick(sym)
                if not t:
                    self.say(f"{sym} 无报价")
                    return
                price = t.ask if cmd == "buy" else t.bid
                self.ask_confirm(f"确认 {cmd.upper()} {sym} {vol} 手 @ {price:.5f} ?",
                                 lambda: self._send_trade(cmd, sym, price, vol))
            elif cmd == "close" and len(parts) >= 2:
                ticket = int(parts[1].lstrip("#"))
                p = next((x for x in self.positions if x.ticket == ticket), None)
                if p:
                    self.ask_confirm(f"确认平掉 #{p.ticket} ?", lambda: self._send_close(p))
                else:
                    self.say(f"找不到持仓 #{ticket}")
            elif cmd == "add" and len(parts) >= 2:
                if parts[1] not in self.symbols:
                    mt5.symbol_select(parts[1], True)
                    self.symbols.append(parts[1])
                    self.say(f"已加入自选: {parts[1]}")
            elif cmd == "del" and len(parts) >= 2 and parts[1] in self.symbols:
                self.symbols.remove(parts[1])
                self.say(f"已移除: {parts[1]}")
            elif cmd == "refresh":
                self.refresh(full=True)
                self.say("已刷新")
            else:
                self.say(f"未知命令: {line}")
        except Exception as exc:
            self.say(f"命令出错: {type(exc).__name__}: {exc}")


def _put(scr, y, x, text, maxw, attr=0):
    if maxw <= 0:
        return
    try:
        scr.addnstr(y, x, text, maxw, attr)
    except curses.error:
        pass


def _box(scr, y, x, height, width, title="", attr=0):
    if height < 3 or width < 4:
        return
    try:
        scr.attron(attr)
        for i in range(height):
            scr.addnstr(y + i, x, " " * (width - 1), width - 1)
        scr.attrset(curses.A_NORMAL)
        scr.addstr(y, x, "┌" + "─" * (width - 2) + "┐")
        for i in range(1, height - 1):
            scr.addstr(y + i, x, "│")
            scr.addstr(y + i, x + width - 2, "│")
        scr.addstr(y + height - 1, x, "└" + "─" * (width - 2) + "┘")
        if title:
            scr.addnstr(y, x + 2, title, max(1, width - 4), curses.A_BOLD)
        scr.attrset(curses.A_NORMAL)
    except curses.error:
        pass


def draw(scr, app):
    scr.erase()
    h, w = scr.getmaxyx()
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    _put(scr, 0, 0, f" MT5 TUI │ {now} UTC ".ljust(w - 1), w - 1, curses.A_REVERSE)

    a = app.account
    line = (f"账户 {a.login}  {a.server}   余额 {a.balance:,.2f} {a.currency}   "
            f"净值 {a.equity:,.2f}   可用 {a.margin_free:,.2f}   杠杆 1:{a.leverage}"
            if a else "账户信息未就绪…")
    _put(scr, 1, 0, line, w - 1, curses.A_BOLD)

    body_top = 4
    log_h = 7
    body_h = max(4, h - body_top - log_h - 2)
    half = max(26, (w - 1) // 2)

    _box(scr, body_top - 1, 0, body_h + 2, half, f" 自选 ({len(app.symbols)}) ",
         curses.A_BOLD if app.focus == "watch" else curses.A_NORMAL)
    for i, s in enumerate(app.symbols[:body_h]):
        bid, ask, _ts = app.ticks.get(s, (0.0, 0.0, 0))
        prev = app.prev.get(s, 0.0)
        arrow = "▲" if bid > prev else ("▼" if bid and prev and bid < prev else " ")
        mark = ">" if (app.focus == "watch" and i == app.cursor) else " "
        txt = f"{mark}{s:<12} {bid:>13.5f} {ask:>13.5f} {arrow}"
        attr = curses.A_REVERSE if (app.focus == "watch" and i == app.cursor) else curses.A_NORMAL
        _put(scr, body_top + i, 1, txt, half - 2, attr)

    rx = half
    rw = w - half - 1
    _box(scr, body_top - 1, rx, body_h + 2, rw, f" 持仓 ({len(app.positions)}) ",
         curses.A_BOLD if app.focus == "positions" else curses.A_NORMAL)
    for i, p in enumerate(app.positions[:body_h]):
        side = "BUY " if p.type == 0 else "SELL"
        mark = ">" if (app.focus == "positions" and i == app.cursor) else " "
        txt = (f"{mark}#{p.ticket} {p.symbol:<10} {side} {p.volume:>5.2f} "
               f"{p.price_open:>11.5f} {p.price_current:>11.5f} {p.profit:>+9.2f}")
        attr = curses.A_REVERSE if (app.focus == "positions" and i == app.cursor) else curses.A_NORMAL
        _put(scr, body_top + i, rx + 1, txt, rw - 2, attr)

    log_y = body_top + body_h + 1
    _box(scr, log_y, 0, log_h, w - 1, " 日志 ")
    for i, ln in enumerate(app.log[-(log_h - 2):]):
        _put(scr, log_y + 1 + i, 1, ln, w - 3)

    bar = h - 2
    if app.mode == "confirm":
        _put(scr, bar, 0, f" {app.confirm_text}  [y/N] ", w - 1, curses.A_REVERSE)
    elif app.mode == "command":
        _put(scr, bar, 0, f" :{app.cmd_buf}_", w - 1)
    else:
        _put(scr, bar, 0,
             " q退出  r刷新  Tab切换  j/k移动  b买 s卖 c平仓  :命令 ", w - 1, curses.A_DIM)
    scr.refresh()


def loop(scr, app):
    scr.nodelay(True)
    scr.keypad(True)
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    while True:
        app.refresh()
        draw(scr, app)
        deadline = time.time() + app.interval
        while time.time() < deadline:
            ch = scr.getch()
            if ch == -1:
                time.sleep(0.05)
                continue
            if app.mode == "confirm":
                if ch in (ord("y"), ord("Y")):
                    act = app.confirm_action
                    app.mode, app.confirm_action = "normal", None
                    if act:
                        act()
                elif ch in (ord("n"), ord("N"), 27):
                    app.mode, app.confirm_action = "normal", None
                    app.say("已取消")
                continue
            if app.mode == "command":
                if ch in (10, 13):
                    line = app.cmd_buf
                    app.mode, app.cmd_buf = "normal", ""
                    app.run_command(line)
                elif ch == 27:
                    app.mode, app.cmd_buf = "normal", ""
                elif ch in (127, 8):
                    app.cmd_buf = app.cmd_buf[:-1]
                elif 32 <= ch < 127:
                    app.cmd_buf += chr(ch)
                continue
            if ch in (ord("q"), ord("Q")):
                return
            if ch == ord("r"):
                app.refresh(full=True)
                app.say("已刷新")
            elif ch in (9, ord("h"), ord("l")):
                app.focus = "positions" if app.focus == "watch" else "watch"
                app.cursor = 0
            elif ch in (ord("j"), curses.KEY_DOWN):
                app.cursor += 1
            elif ch in (ord("k"), curses.KEY_UP):
                app.cursor = max(0, app.cursor - 1)
            elif ch == ord("b"):
                app.do_trade("buy")
            elif ch == ord("s"):
                app.do_trade("sell")
            elif ch == ord("c"):
                app.do_close()
            elif ch in (ord(":"), ord("/")):
                app.mode, app.cmd_buf = "command", ""
            n = len(app.symbols) if app.focus == "watch" else len(app.positions)
            app.cursor = max(0, min(app.cursor, max(0, n - 1)))


def render_once(app, width=104):
    """无 TTY 时渲染一帧为纯文本（便于测试）。"""
    time.sleep(1.5)   # 订阅后稍等，让报价/K线到位
    app.refresh(full=True)
    a = app.account
    out = [f" MT5 TUI │ {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC ".ljust(width)]
    out.append((f"账户 {a.login}  {a.server}   余额 {a.balance:,.2f} {a.currency}   "
                f"净值 {a.equity:,.2f}   可用 {a.margin_free:,.2f}   杠杆 1:{a.leverage}"
                if a else "账户信息未就绪…"))
    out.append("")
    out.append(f" 自选 ({len(app.symbols)})".ljust(46) + f" 持仓 ({len(app.positions)})")
    rows = max(len(app.symbols), len(app.positions), 3)
    for i in range(rows):
        left = ""
        if i < len(app.symbols):
            s = app.symbols[i]
            bid, ask, _ = app.ticks.get(s, (0.0, 0.0, 0))
            mark = ">" if (i == app.cursor and app.focus == "watch") else " "
            left = f"{mark}{s:<12} {bid:>13.5f} {ask:>13.5f}"
        right = ""
        if i < len(app.positions):
            p = app.positions[i]
            mark = ">" if (i == app.cursor and app.focus == "positions") else " "
            right = (f"{mark}#{p.ticket} {p.symbol:<9} "
                     f"{'BUY' if p.type == 0 else 'SELL'} {p.volume:>5.2f} "
                     f"{p.price_open:>11.5f} {p.profit:>+9.2f}")
        out.append(left.ljust(46) + right)
    out.append("")
    out.append(" 日志")
    for ln in app.log[-5:]:
        out.append("  " + ln)
    out.append("")
    out.append(" q退出  r刷新  Tab切换  j/k移动  b买 s卖 c平仓  :命令 ")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(prog="mt5tui", description="MetaTrader 5 全屏终端界面")
    ap.add_argument("--login", type=int, default=int(os.environ.get("MT5_LOGIN", "0") or 0))
    ap.add_argument("--password", default=os.environ.get("MT5_PASSWORD", ""))
    ap.add_argument("--server", default=os.environ.get("MT5_SERVER", "Exness-MT5Trial5"))
    ap.add_argument("--watch", default="XAUUSDm,EURUSDm,GBPUSDm", help="自选品种（逗号分隔）")
    ap.add_argument("--interval", type=float, default=1.0, help="刷新间隔秒")
    ap.add_argument("--volume", type=float, default=0.01, help="默认手数")
    ap.add_argument("--render", action="store_true", help="渲染一帧到 stdout 后退出")
    args = ap.parse_args()

    if not args.login or not args.password:
        print("需要账号密码：--login/--password 或 MT5_LOGIN/MT5_PASSWORD")
        return 2
    print(f"连接 {args.login} @ {args.server} …", file=sys.stderr)
    if not mt5.initialize(login=args.login, password=args.password, server=args.server):
        print("连接失败:", mt5.last_error(), file=sys.stderr)
        return 2
    symbols = [s.strip() for s in args.watch.split(",") if s.strip()]
    app = App(symbols, args.interval, args.volume)
    for s in symbols:
        mt5.symbol_select(s, True)
    app.say(f"已连接 {args.login} @ {args.server}")
    try:
        if args.render:
            print(render_once(app))
            return 0
        curses.wrapper(loop, app)
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
