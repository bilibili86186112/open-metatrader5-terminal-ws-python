"""``MetaTrader5`` 兼容层 —— 用 **WebSocket 通道**实现官方 Python 包的接口。

目标：把现有代码里的 ``import MetaTrader5 as mt5`` 换成这个模块，**其余代码不用改**。

    import MetaTrader5 as mt5

    if not mt5.initialize(login=123456, password="...", server="Exness-MT5Trial5"):
        print("连接失败", mt5.last_error()); raise SystemExit(1)

    print(mt5.account_info().balance)
    t = mt5.symbol_info_tick("XAUUSDm")
    rates = mt5.copy_rates_from_pos("XAUUSDm", mt5.TIMEFRAME_M1, 0, 10)
    r = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "symbol": "XAUUSDm",
                        "volume": 0.01, "type": mt5.ORDER_TYPE_BUY,
                        "price": t.ask, "deviation": 20,
                        "type_filling": mt5.ORDER_FILLING_IOC})
    print(r.retcode, r.comment)
    mt5.shutdown()

## 与官方包的差异（重要，请务必看）

| 项 | 官方包 | 本兼容层 |
|---|---|---|
| 传输 | 本地终端（IPC）| **WebSocket 直连券商网关**，不需要开 MT5 终端 |
| `copy_rates_*` 返回 | numpy 结构化数组（`rates['close']`）| **dict 列表**（`r['close']` 一样可用；但 `rates['close']` 整列取值不支持）|
| `initialize()` 参数 | `path`/`portable` 等终端参数 | 忽略 `path`；用 `login`/`password`/`server` 直连 |
| 交易后持仓 | 立即更新 | ⚠️ **同一会话内 `positions_get()` 可能返回旧快照**，需重连才准（见 `docs`）|
| 事件回调 | `mt5.*` 内建 | 用 `set_tick_callback()`（本层额外提供）|
| `symbol_info()` 字段 | 上百个 | 只填**已解析**的那些，其余为 None/0 |

其余语义（常量数值、字段名、返回类型、`retcode`）尽量与官方一致。
"""

from __future__ import annotations

import os
import time as _time
from collections import namedtuple
from typing import Any, Iterable, Optional

from mt5ws import MT5Client, MT5Error, protocol
from mt5ws.client import (AccountInfo as _AccInfo, TradeOrder as _TO,
                           TradePosition as _TP, TradeResult as _TR,
                           Tick as _Tick)

__all__ = [
    "initialize", "login", "shutdown", "last_error", "version", "terminal_info",
    "account_info", "symbols_get", "symbol_info", "symbol_info_tick",
    "symbol_select", "copy_rates_from_pos", "copy_rates_from", "copy_rates_range",
    "positions_get", "orders_get", "history_deals_get", "history_orders_get",
    "order_send", "order_check", "set_tick_callback", "last_error",
]

# ---------------------------------------------------------------- 常量（官方数值）

TIMEFRAME_M1 = 1
TIMEFRAME_M2 = 2
TIMEFRAME_M3 = 3
TIMEFRAME_M4 = 4
TIMEFRAME_M5 = 5
TIMEFRAME_M6 = 6
TIMEFRAME_M10 = 10
TIMEFRAME_M12 = 12
TIMEFRAME_M15 = 15
TIMEFRAME_M20 = 20
TIMEFRAME_M30 = 30
TIMEFRAME_H1 = 16385
TIMEFRAME_H2 = 16386
TIMEFRAME_H3 = 16387
TIMEFRAME_H4 = 16388
TIMEFRAME_H6 = 16390
TIMEFRAME_H8 = 16392
TIMEFRAME_H12 = 16396
TIMEFRAME_D1 = 16408
TIMEFRAME_W1 = 32769
TIMEFRAME_MN1 = 49153

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TYPE_BUY_STOP = 4
ORDER_TYPE_SELL_STOP = 5
ORDER_TYPE_BUY_STOP_LIMIT = 6
ORDER_TYPE_SELL_STOP_LIMIT = 7
ORDER_TYPE_CLOSE_BY = 8

ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2
ORDER_FILLING_BOC = 3

ORDER_TIME_GTC = 0
ORDER_TIME_DAY = 1
ORDER_TIME_SPECIFIED = 2
ORDER_TIME_SPECIFIED_DAY = 3

TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_SLTP = 6
TRADE_ACTION_MODIFY = 7
TRADE_ACTION_REMOVE = 8
TRADE_ACTION_CLOSE_BY = 10

TRADE_RETCODE_REQUOTE = 10004
TRADE_RETCODE_REJECT = 10006
TRADE_RETCODE_CANCEL = 10007
TRADE_RETCODE_PLACED = 10008
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_DONE_PARTIAL = 10010
TRADE_RETCODE_ERROR = 10011
TRADE_RETCODE_TIMEOUT = 10012
TRADE_RETCODE_INVALID = 10013
TRADE_RETCODE_INVALID_VOLUME = 10014
TRADE_RETCODE_INVALID_PRICE = 10015
TRADE_RETCODE_INVALID_STOPS = 10016
TRADE_RETCODE_TRADE_DISABLED = 10017
TRADE_RETCODE_MARKET_CLOSED = 10018
TRADE_RETCODE_NO_MONEY = 10019
TRADE_RETCODE_PRICE_CHANGED = 10020
TRADE_RETCODE_PRICE_OFF = 10021
TRADE_RETCODE_INVALID_FILL = 10030
TRADE_RETCODE_CONNECTION = 10031

DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1

SYMBOL_TRADE_MODE_DISABLED = 0
SYMBOL_TRADE_MODE_LONGONLY = 1
SYMBOL_TRADE_MODE_SHORTONLY = 2
SYMBOL_TRADE_MODE_CLOSEONLY = 3
SYMBOL_TRADE_MODE_FULL = 4

#: 官方包 ``account_info()`` 的字段顺序（尽量一致）
AccountInfo = namedtuple("AccountInfo", [
    "login", "trade_mode", "leverage", "limit_orders", "margin_so_mode",
    "trade_allowed", "trade_expert", "margin_mode", "currency_digits",
    "margin_so_call", "margin_so_so", "margin_initial", "margin_maintenance",
    "balance", "credit", "profit", "equity", "margin", "margin_free",
    "margin_level", "name", "server", "currency", "company",
])

SymbolInfo = namedtuple("SymbolInfo", [
    "name", "description", "path", "digits", "point", "spread", "trade_mode",
    "volume_min", "volume_max", "volume_step", "contract_size", "currency_base",
    "currency_profit", "bid", "ask", "select",
])

Tick = namedtuple("Tick", ["time", "bid", "ask", "last", "volume", "time_msc",
                           "flags", "volume_real"])

Position = namedtuple("Position", [
    "ticket", "time", "time_msc", "time_update", "time_update_msc", "type",
    "magic", "identifier", "reason", "volume", "price_open", "sl", "tp",
    "price_current", "swap", "profit", "symbol", "comment", "external_id",
])

Order = namedtuple("Order", [
    "ticket", "time_setup", "time_setup_msc", "time_done", "time_done_msc",
    "time_expiration", "type", "type_time", "type_filling", "state", "magic",
    "position_id", "position_by_id", "reason", "volume_initial",
    "volume_current", "price_open", "sl", "tp", "price_current",
    "price_stoplimit", "symbol", "comment", "external_id",
])

Deal = namedtuple("Deal", [
    "ticket", "order", "time", "time_msc", "type", "entry", "magic",
    "position_id", "reason", "volume", "price", "commission", "swap",
    "profit", "fee", "symbol", "comment", "external_id",
])

OrderSendResult = namedtuple("OrderSendResult", [
    "retcode", "deal", "order", "volume", "price", "bid", "ask", "comment",
    "request_id", "retcode_external", "request",
])

# ---------------------------------------------------------------- 内部状态

_client: Optional[MT5Client] = None
_last_error: tuple[int, str] = (0, "ok")
_login_args: dict[str, Any] = {}
_tick_cb = None


def _err(code: int, msg: str):
    global _last_error
    _last_error = (int(code), str(msg))
    return False


def _need() -> MT5Client:
    if _client is None:
        raise MT5Error("未初始化：请先调用 mt5.initialize(...)")
    return _client


# ---------------------------------------------------------------- 生命周期

def initialize(path: Optional[str] = None, *, login: Optional[int] = None,
               password: Optional[str] = None, server: Optional[str] = None,
               timeout: float = 20000, portable: bool = False,
               endpoints: Optional[list[str]] = None,
               **kwargs) -> bool:
    """连接并登录（对应官方的 ``initialize``）。

    官方包的参数是终端路径；这里**忽略** ``path``/``portable``，
    改为用 ``login``/``password``/``server`` **直连券商网关**。
    未显式给凭据时，读取环境变量 ``MT5_LOGIN`` / ``MT5_PASSWORD`` / ``MT5_SERVER``。
    """
    global _client
    lg = login or kwargs.get("login") or int(os.environ.get("MT5_LOGIN", "0") or 0)
    pw = password or os.environ.get("MT5_PASSWORD", "")
    sv = server or os.environ.get("MT5_SERVER", "Exness-MT5Trial5")
    if not lg or not pw:
        return _err(-1, "缺少 login/password（也可用 MT5_LOGIN/MT5_PASSWORD 环境变量）")
    try:
        _client = MT5Client.connect_auto(login=int(lg), password=pw, server=sv,
                                         endpoints=endpoints)
        if _tick_cb is not None:
            _client.set_tick_callback(_tick_cb)
        _login_args.update(login=int(lg), server=sv)
        _last_error = (0, "ok")
        return True
    except Exception as exc:                              # noqa: BLE001
        _client = None
        return _err(-2, f"{type(exc).__name__}: {exc}")


def login(login: int, password: str = "", server: str = "") -> bool:
    """重新登录（对应官方的 ``login``）。"""
    return initialize(None, login=login, password=password, server=server)


def shutdown() -> None:
    """断开连接（对应官方的 ``shutdown``）。"""
    global _client
    try:
        if _client is not None:
            _client.shutdown()
    except Exception:                                     # noqa: BLE001
        pass
    _client = None


def last_error() -> tuple[int, str]:
    """最近一次错误 ``(code, message)``。"""
    return _last_error


def version() -> tuple[int, int, str]:
    return (500, 0, "open-metatrader5-terminal-ws-python 1.0.0")


def terminal_info():
    """终端信息 —— 本层没有本地终端，返回 ``None``（与官方"没有终端"时的行为一致）。"""
    return None


# ---------------------------------------------------------------- 账户 / 品种

def account_info():
    """账户信息（字段名与官方一致）。"""
    try:
        a = _need().account_info()
    except Exception as exc:                              # noqa: BLE001
        _err(-3, f"{type(exc).__name__}: {exc}")
        return None
    return AccountInfo(
        login=a.login, trade_mode=a.trade_mode, leverage=a.leverage,
        limit_orders=0, margin_so_mode=0,
        trade_allowed=None,          # 服务器不下发该字段，无法可靠判断（官方为 bool）
        trade_expert=False,
        margin_mode=0, currency_digits=2, margin_so_call=0.0, margin_so_so=0.0,
        margin_initial=0.0, margin_maintenance=0.0,
        balance=float(a.balance or 0.0), credit=0.0,
        profit=float(a.profit or 0.0), equity=float(a.equity or 0.0),
        margin=float(a.margin or 0.0), margin_free=float(a.margin_free or 0.0),
        margin_level=0.0, name=a.name, server=a.server,
        currency=a.currency, company="",
    )


def _sym(s) -> SymbolInfo:
    return SymbolInfo(
        name=s.name, description=s.description, path=s.path, digits=s.digits,
        point=s.point, spread=s.spread, trade_mode=s.trade_mode,
        volume_min=s.volume_min, volume_max=s.volume_max,
        volume_step=s.volume_step,
        contract_size=getattr(s, "contract_size", None) or 0.0,
        currency_base="", currency_profit="",
        bid=s.bid, ask=s.ask, select=True,
    )


def symbols_get(group: Optional[str] = None) -> tuple:
    """品种列表（``group`` 支持官方那种 ``"*"`` 通配的简化匹配）。"""
    try:
        syms = _need().symbols_get()
    except Exception as exc:                              # noqa: BLE001
        _err(-4, f"{type(exc).__name__}: {exc}")
        return ()
    out = [_sym(s) for s in syms]
    if group and group != "*":
        import fnmatch
        pat = group.replace("*", "*").upper()
        out = [s for s in out if fnmatch.fnmatch(s.name.upper(), pat)]
    return tuple(out)


def symbol_info(symbol: str):
    """单个品种信息；不存在返回 ``None``（与官方一致）。"""
    try:
        s = _need().symbol_info(symbol)
    except Exception as exc:                              # noqa: BLE001
        _err(-5, f"{type(exc).__name__}: {exc}")
        return None
    if s is None:
        return None
    info = _sym(s)
    try:                     # 把 spec 里的手数档位/合约大小/交易模式补进来
        spec = _need().specs.get(symbol) or _need().symbol_spec(symbol)
        if spec:
            info = info._replace(
                contract_size=spec.get("contract_size") or info.contract_size,
                volume_min=spec.get("volume_min") or info.volume_min,
                volume_max=spec.get("volume_max") or info.volume_max,
                volume_step=spec.get("volume_step") or info.volume_step,
                trade_mode=spec.get("trade_mode") if spec.get("trade_mode") is not None else info.trade_mode,
                digits=spec.get("digits") or info.digits,
                point=spec.get("point") or info.point,
            )
    except Exception:                                     # noqa: BLE001
        pass
    return info


def symbol_info_tick(symbol: str):
    """最新报价；拿不到返回 ``None``。"""
    try:
        t = _need().symbol_info_tick(symbol, wait=8.0)
    except Exception as exc:                              # noqa: BLE001
        _err(-6, f"{type(exc).__name__}: {exc}")
        return None
    if t is None:
        return None
    return Tick(time=t.time, bid=t.bid, ask=t.ask, last=getattr(t, "last", 0.0),
                volume=int(getattr(t, "volume", 0) or 0), time_msc=t.time_msc,
                flags=getattr(t, "flags", 0), volume_real=0.0)


def symbol_select(symbol: str, enable: bool = True) -> bool:
    """订阅/取消订阅（本层用"订阅"实现，对应官方的 ``symbol_select``）。"""
    try:
        if enable:
            _need().subscribe([symbol])
        return True
    except Exception as exc:                              # noqa: BLE001
        return _err(-7, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- K 线

def _rows(bars) -> list[dict]:
    return [{"time": b.time, "open": b.open, "high": b.high, "low": b.low,
             "close": b.close, "tick_volume": b.tick_volume, "spread": 0,
             "real_volume": 0} for b in bars]


def copy_rates_from_pos(symbol: str, timeframe: int, start_pos: int,
                        count: int) -> list[dict]:
    """从 ``start_pos`` 起取 ``count`` 根 K 线（``0`` = 最新）。"""
    try:
        return _rows(_need().copy_rates_from_pos(symbol, timeframe, start_pos, count))
    except Exception as exc:                              # noqa: BLE001
        _err(-8, f"{type(exc).__name__}: {exc}")
        return []


def copy_rates_from(symbol: str, timeframe: int, date_from, count: int) -> list[dict]:
    """从某个时间点起取 ``count`` 根。"""
    try:
        ts = int(date_from) if not hasattr(date_from, "timestamp") \
            else int(date_from.timestamp())
        return _rows(_need().copy_rates_range(symbol, timeframe, ts,
                                              ts + 86400 * 3650))[:count]
    except Exception as exc:                              # noqa: BLE001
        _err(-9, f"{type(exc).__name__}: {exc}")
        return []


def copy_rates_range(symbol: str, timeframe: int, date_from, date_to) -> list[dict]:
    """按时间范围取 K 线。"""
    try:
        def _ts(d):
            return int(d) if not hasattr(d, "timestamp") else int(d.timestamp())
        return _rows(_need().copy_rates_range(symbol, timeframe,
                                              _ts(date_from), _ts(date_to)))
    except Exception as exc:                              # noqa: BLE001
        _err(-10, f"{type(exc).__name__}: {exc}")
        return []


# ---------------------------------------------------------------- 持仓 / 订单 / 历史

def _pos(p) -> Position:
    return Position(ticket=p.ticket, time=p.time, time_msc=0,
                    time_update=p.time_update, time_update_msc=0,
                    type=p.type, magic=p.magic, identifier=p.order, reason=0,
                    volume=p.volume, price_open=p.price_open, sl=p.sl, tp=p.tp,
                    price_current=p.price_current, swap=p.swap,
                    profit=p.profit, symbol=p.symbol, comment=p.comment,
                    external_id="")


def positions_get(symbol: Optional[str] = None, group: Optional[str] = None):
    """当前持仓（``symbol`` 过滤；无持仓返回 ``()``，与官方一致）。"""
    try:
        ps = _need().positions_get(symbol)
    except Exception as exc:                              # noqa: BLE001
        _err(-11, f"{type(exc).__name__}: {exc}")
        return None
    return tuple(_pos(p) for p in ps)


def _ord(o) -> Order:
    return Order(ticket=o.ticket, time_setup=o.time_setup, time_setup_msc=0,
                 time_done=o.time_done, time_done_msc=0, time_expiration=0,
                 type=o.type, type_time=0, type_filling=0, state=o.state,
                 magic=o.magic, position_id=o.position_id, position_by_id=0,
                 reason=0, volume_initial=o.volume_initial,
                 volume_current=o.volume_current, price_open=o.price_open,
                 sl=o.sl, tp=o.tp, price_current=o.price_current,
                 price_stoplimit=0.0, symbol=o.symbol, comment=o.comment,
                 external_id="")


def orders_get(symbol: Optional[str] = None, group: Optional[str] = None):
    """当前挂单。"""
    try:
        os_ = _need().orders_get(symbol)
    except Exception as exc:                              # noqa: BLE001
        _err(-12, f"{type(exc).__name__}: {exc}")
        return None
    return tuple(_ord(o) for o in os_)


def _deal(d) -> Deal:
    return Deal(ticket=d.ticket, order=d.order, time=d.time, time_msc=0,
                type=d.type, entry=d.entry, magic=d.magic,
                position_id=d.position_id, reason=0, volume=d.volume,
                price=d.price, commission=d.commission, swap=d.swap,
                profit=d.profit, fee=d.fee, symbol=d.symbol, comment=d.comment,
                external_id="")


def history_deals_get(date_from=None, date_to=None, *, position=None,
                      ticket=None, group: Optional[str] = None):
    """历史成交。``date_from``/``date_to`` 可传 ``datetime`` 或 Unix 秒。"""
    try:
        def _ts(d):
            if d is None:
                return int(_time.time()) - 86400 * 30
            return int(d) if not hasattr(d, "timestamp") else int(d.timestamp())
        # 底层返回的是 (deals, orders) 元组；只取 deals
        raw = _need().history_deals_get(_ts(date_from), _ts(date_to))
        deals = raw[0] if isinstance(raw, tuple) and len(raw) == 2 else raw
    except Exception as exc:                              # noqa: BLE001
        _err(-13, f"{type(exc).__name__}: {exc}")
        return None
    out = [_deal(d) for d in deals]
    if position is not None:
        out = [d for d in out if d.position_id == int(position)]
    return tuple(out)


def history_orders_get(date_from=None, date_to=None, **kwargs):
    """历史订单 —— 本层暂未实现，返回空元组。"""
    return ()


# ---------------------------------------------------------------- 交易

def _result(r: _TR, request: dict) -> OrderSendResult:
    return OrderSendResult(
        retcode=r.retcode, deal=r.deal, order=r.order, volume=r.volume,
        price=r.price, bid=getattr(r, "bid", 0.0), ask=getattr(r, "ask", 0.0),
        comment=r.comment, request_id=0, retcode_external=0, request=request)


def order_check(request: dict) -> dict:
    """下单前检查（对应官方的 ``order_check``）。

    这里用本库的**四道闸门**（可交易性 / 手数规整 / 保证金 / SL-TP 方向 / 风险）实现，
    返回官方的 ``OrderCheckResult`` 式字典：``retcode``/``balance``/``equity``/
    ``margin``/``margin_free``/``comment``。
    """
    try:
        c = _need()
        sym = request.get("symbol", "")
        vol = float(request.get("volume", 0.0) or 0.0)
        side = "buy" if int(request.get("type", 0)) in (0, 2, 4, 6) else "sell"
        price = float(request.get("price", 0.0) or 0.0) or None
        sl = float(request.get("sl", 0.0) or 0.0) or None
        spec = c.specs.get(sym) or c.symbol_spec(sym)
        from mt5ws.symbol_spec import check_sltp, margin_from_spec, snap_volume
        a = c.account_info()
        snapped = snap_volume(vol, spec)
        reasons = []
        if spec.get("trade_mode") is not None and spec["trade_mode"] != 4:
            reasons.append(f"trade_mode={spec['trade_mode']} 不允许自由交易")
        if abs(snapped - vol) > 1e-12:
            reasons.append(f"手数被规整 {vol} → {snapped}")
        m = 0.0
        if price and spec.get("contract_size"):
            m = margin_from_spec(snapped, spec, price, a.leverage or 1)
            if a.margin_free and m > float(a.margin_free):
                reasons.append("保证金不足")
        reasons += check_sltp(side, price, sl, None)
        fatal = [x for x in reasons if not x.startswith("手数被规整")]
        return {"retcode": 0 if not fatal else TRADE_RETCODE_INVALID,
                "balance": float(a.balance or 0.0),
                "equity": float(a.equity or 0.0),
                "margin": m, "margin_free": float(a.margin_free or 0.0),
                "margin_initial": m, "comment": "；".join(reasons)}
    except Exception as exc:                              # noqa: BLE001
        _err(-14, f"{type(exc).__name__}: {exc}")
        return {"retcode": TRADE_RETCODE_ERROR, "comment": str(exc)}


def order_send(request: dict) -> OrderSendResult:
    """发送交易请求（对应官方的 ``order_send``）。

    官方 ``action`` 与**本协议**数值不一致（官方 ``TRADE_ACTION_DEAL=1``，本协议能用的是
    ``TRADE_ACTION_MARKET=3``），所以这里做一层**转换**：

    * ``TRADE_ACTION_DEAL`` + ``position`` → **平仓**（直接构造最小持仓对象、
      **不做额外查询**；实测这样把平仓从 ~480ms 降到 ~280ms）
    * ``TRADE_ACTION_DEAL`` 不带 ``position`` → **市价开仓**
    * ``TRADE_ACTION_SLTP`` → 改止损/止盈
    * 其余 action 原样下发（未验证）

    ⚠️ 成功有两种 ``retcode``：**0** 和 **10009**（真机实测）。
    """
    c = _need()
    act = int(request.get("action", 0))
    sym = request.get("symbol", "")
    try:
        if act == TRADE_ACTION_DEAL and int(request.get("position", 0) or 0):
            from mt5ws.client import TradePosition as _TPos
            # 平仓单的 type 与持仓相反：发 SELL(1) 平的是 BUY(0) 持仓
            pos_type = 0 if int(request.get("type", 1)) == 1 else 1
            pseudo = _TPos(
                ticket=int(request["position"]), order=int(request["position"]),
                time=0, time_update=0, symbol=sym, type=pos_type,
                volume=float(request.get("volume", 0.0) or 0.0),
                price_open=0.0, price_current=0.0, sl=0.0, tp=0.0,
                profit=0.0, swap=0.0, commission=0.0, comment="", magic=0,
                raw=b"")
            res = c.close_position(pseudo,
                                   comment=str(request.get("comment", "")))
        elif act == TRADE_ACTION_DEAL:
            side = "buy" if int(request.get("type", 0)) in (0, 2, 4, 6) else "sell"
            res = c.market_order(
                sym, side, float(request.get("volume", 0.0)),
                sl=float(request.get("sl", 0.0) or 0.0),
                tp=float(request.get("tp", 0.0) or 0.0),
                comment=str(request.get("comment", "")))
        elif act == TRADE_ACTION_SLTP:
            pos = [p for p in c.positions_get(sym)
                   if p.ticket == int(request.get("position", 0) or 0)]
            if not pos:
                raise ValueError("找不到 position 对应的持仓")
            res = c.modify_position(pos[0],
                                    sl=float(request.get("sl", 0.0) or 0.0),
                                    tp=float(request.get("tp", 0.0) or 0.0))
        else:
            res = c.order_send(dict(request))
    except Exception as exc:                              # noqa: BLE001
        _err(-15, f"{type(exc).__name__}: {exc}")
        return OrderSendResult(retcode=TRADE_RETCODE_ERROR, deal=0, order=0,
                               volume=0.0, price=0.0, bid=0.0, ask=0.0,
                               comment=str(exc), request_id=0,
                               retcode_external=0, request=dict(request))
    _last_error = (0, "ok")
    return _result(res, dict(request))
def set_tick_callback(fn) -> None:
    """注册报价回调（本层额外提供；官方包没有这个函数）。"""
    global _tick_cb
    _tick_cb = fn
    if _client is not None:
        _client.set_tick_callback(fn)
