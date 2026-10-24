"""高层 API / high level MT5 client.

对标官方 `MetaTrader5` Python 包（那个只能在 Windows 上通过终端 DLL 跑），
这里用纯 Python 直接讲 MT5 协议，Linux/容器里也能用。

    from mt5api import MT5Client

    c = MT5Client.connect("wss://15.206.31.153:443/terminal",
                          origin="https://15.206.31.153:443")
    c.login(12345678, "password", server="Exness-MT5Trial17")
    print(c.account_info())
    print(c.copy_rates_from_pos("XAUUSDm", "M5", 0, 100))
"""

from __future__ import annotations

import gzip
import socket
import struct
import threading
import time
import zlib
from collections import namedtuple
from typing import Iterable, Optional, Sequence

from . import crypto, protocol, serial
from .transport import Frame, MT5Disconnected, MT5Error, WebSocketTransport

# --------------------------------------------------------------------------- #
# 返回结构（与官方包同名的命名元组）
# --------------------------------------------------------------------------- #
AccountInfo = namedtuple("AccountInfo", [
    "login", "trade_mode", "leverage", "balance", "equity", "profit", "margin",
    "margin_free", "currency", "server", "name", "group", "credit", "bonus",
    "flags", "rights", "raw",
])
SymbolInfo = namedtuple("SymbolInfo", [
    "name", "description", "path", "digits", "symbol_id", "basis", "sector",
    "calc_mode", "spread", "bid", "ask", "trade_mode", "volume_min",
    "volume_step", "volume_max", "point", "spec_raw",
])
Tick = namedtuple("Tick", ["time", "bid", "ask", "last", "volume", "time_msc",
                           "flags"])
TradePosition = namedtuple("TradePosition", [
    "ticket", "order", "time", "time_update", "symbol", "type", "volume",
    "price_open", "price_current", "sl", "tp", "profit", "swap", "commission",
    "comment", "magic", "raw",
])
TradeOrder = namedtuple("TradeOrder", [
    "ticket", "symbol", "time_setup", "time_done", "type", "state", "volume_initial",
    "volume_current", "price_open", "price_current", "sl", "tp", "comment",
    "position_id", "magic", "raw",
])
TradeDeal = namedtuple("TradeDeal", [
    "ticket", "order", "time", "symbol", "type", "entry", "volume", "price",
    "profit", "commission", "swap", "fee", "position_id", "comment", "magic", "raw",
])
TradeResult = namedtuple("TradeResult", [
    "retcode", "retcode_name", "deal", "order", "volume", "price", "bid", "ask",
    "comment", "request", "raw",
])
Rate = namedtuple("Rate", ["time", "open", "high", "low", "close", "tick_volume",
                           "spread", "real_volume"])


class MT5LoginError(MT5Error):
    pass


class MT5TradeError(MT5Error):
    pass


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
class MT5Client:
    """一个 MT5 连接。"""

    def __init__(self, transport, *, debug: bool = False) -> None:
        self.t = transport
        self.debug = debug
        self.login_id: Optional[int] = None
        self.server: Optional[str] = None
        #: 本次连接的主机（登录报文里要填这个地址）
        self._host: Optional[str] = getattr(transport, "host", None)
        self._port: int = 443
        self.symbol_ids: dict[str, int] = {}
        self._symbols_meta: dict[str, dict] = {}
        self.specs: dict[str, dict] = {}
        self.ticks: dict[str, Tick] = {}
        #: 已经自动订阅过的品种（避免每次取报价都重发订阅）
        self._auto_subscribed: set[str] = set()
        self._positions: list[TradePosition] = []
        self._orders: list[TradeOrder] = []
        self._trade_events: list[Frame] = []
        self._lock = threading.Lock()
        self.notifications: list[int] = []
        self._tick_cb = None                          # 实时 tick 推送回调（服务端用它转发给前端 WS）
        # ---- 保活与重连 ----
        #: 心跳间隔（秒）。服务端会掐掉长时间静默的连接，实测空闲约 1~2 分钟即断。
        #: 0 表示关闭心跳。
        self.heartbeat_interval: float = 20.0
        #: 断线后是否自动重连（重连会重新登录并恢复订阅）
        self.auto_reconnect: bool = True
        #: 登录凭据**只留在内存里**（为了重连），绝不落盘
        self._login_args: Optional[dict] = None
        #: 订阅过的品种，重连后自动恢复
        self._subscribed: set[str] = set()
        self._hb_stop = threading.Event()
        self._hb_thread: Optional[threading.Thread] = None
        self._reconnect_count = 0
        self.t.add_push_handler(self._on_push)

    def set_tick_callback(self, cb) -> None:
        """注册实时报价推送回调：``cb(name, tick)``，在收到服务器 tick 推送时被调用。

        回调在传输层的接收线程里执行，务必保持轻量、非阻塞。
        """
        self._tick_cb = cb

    # ---------------- 建立连接 ----------------
    @classmethod
    def connect(cls, url: str, *, origin: Optional[str] = None,
                verify_tls: bool = False, debug: bool = False,
                timeout: float = 20.0) -> "MT5Client":
        tr = WebSocketTransport(url, origin=origin, verify_tls=verify_tls,
                                debug=debug)
        tr.connect(timeout=timeout)
        obj = cls(tr, debug=debug)
        #: 记住 host/port，自动重连时要用
        obj._host = getattr(tr, "host", None) or obj._host
        try:
            from urllib.parse import urlparse
            _u = urlparse(url)
            obj._port = _u.port or 443
        except Exception:                                 # noqa: BLE001
            obj._port = 443
        return obj

    @staticmethod
    def web_terminal_ok(host: str, port: int = 443, timeout: float = 6.0) -> bool:
        """探测某个接入点是否同时提供 Web 终端网关。

        MT5 同一个接入点既跑原生二进制协议，也跑 Web 终端（HTTP(S) 的
        `/terminal`）。本客户端走 Web 终端通道，所以要先筛掉没有网关的接入点
        ——判据就是 `GET /terminal` 返回 200。Exness-MT5Trial5 的 8 个接入点里
        有 6 个可用。
        """
        import ssl as _ssl
        try:
            ctx = _ssl._create_unverified_context()
            s = ctx.wrap_socket(socket.create_connection((host, port), timeout=timeout),
                                server_hostname=host)
            s.settimeout(timeout)
            s.sendall(f"GET /terminal HTTP/1.1\r\nHost: {host}\r\n"
                      "Connection: close\r\n\r\n".encode())
            head = s.recv(64)
            s.close()
            return b" 200" in head
        except Exception:  # noqa: BLE001
            return False

    @classmethod
    def connect_auto(cls, *, login: int, password: str, server: str,
                     servers_dat: Optional[str] = None, debug: bool = False,
                     endpoints: Optional[list[str]] = None,
                     probe_timeout: float = 6.0) -> "MT5Client":
        """全自动：servers.dat 找接入点 -> 挑支持 Web 终端的 -> 登录。

        服务器对频繁登录有限制（短时间反复登录会被掐线），所以这里把候选接入点
        依次试一遍，失败就换下一个。
        """

        # 接入点来源优先级：显式 endpoints > 内置表 > 本地 servers.dat
        if endpoints:
            cand = list(endpoints)
        else:
            from .endpoints import endpoints_for, last_working
            cand = endpoints_for(server)
            _last = last_working(server)          # 上次成功的优先试，省几秒探测
            if _last in cand:
                cand = [_last] + [e for e in cand if e != _last]
        if not cand:
            from . import servers as _servers
            path = servers_dat or _servers.default_servers_dat()
            if path:
                cand = [ep for ep in _servers.access_points(path, server)]
        if not cand:
            raise MT5Error("没有可用接入点：请传 endpoints=[\"host:port\"]，"
                           "或用 servers_dat= 指定本地 servers.dat")
        for ep in cand:
            errors: list[str] = []

            host, _, port_s = ep.partition(":")
            port = int(port_s or 443)
            if not cls.web_terminal_ok(host, port, timeout=probe_timeout):
                errors.append(f"{ep}: 无 Web 终端网关")
                continue
            client = None
            try:
                client = cls.connect(f"wss://{host}:{port}/terminal",
                                     origin=f"https://{host}:{port}", debug=debug)
                client.login(login, password, server=server, address=host)
                try:                     # 记住可用接入点，下次优先（仅加速，不含凭据）
                    from .endpoints import remember_working
                    remember_working(server, ep)
                except Exception:        # noqa: BLE001
                    pass
                return client
            except MT5Error as exc:
                errors.append(f"{ep}: {exc}")
                if client is not None:
                    client.shutdown()
        raise MT5Error("所有接入点都失败 / all access points failed: "
                       + " | ".join(errors))

    # ---------------- 推送处理 ----------------
    def _on_push(self, frame: Frame) -> None:
        if frame.cmd == protocol.CMD_QUOTES:
            self._handle_quotes(frame.body)
        elif frame.cmd == protocol.CMD_SYMBOL_SPEC:
            self._handle_spec(frame.body)
        elif frame.cmd in (protocol.CMD_TRADE_EVENT, protocol.CMD_PUSH):
            with self._lock:
                self._trade_events.append(frame)
        elif frame.cmd == protocol.CMD_NOTIFY:
            if len(frame.body) >= 4:
                self.notifications.append(struct.unpack_from("<I", frame.body, 0)[0])

    def _handle_quotes(self, body: bytes) -> None:
        n = len(body) // protocol.QUOTE_SIZE
        for i in range(n):
            off = i * protocol.QUOTE_SIZE
            vals, _ = serial.parse_fixed(body, protocol.QUOTE_SCHEMA, off)
            sid, t, flags, bid, ask, last, vol, ms, _f = vals
            name = self._name_of(sid)
            if name is None:
                continue
            digits = self._digits(name)
            scale = 10 ** digits
            tick = Tick(
                time=t, bid=bid / scale if bid else 0.0,
                ask=ask / scale if ask else 0.0,
                last=last / scale if last else 0.0,
                volume=vol, time_msc=t * 1000 + ms, flags=flags,
            )
            self.ticks[name] = tick
            if self._tick_cb is not None:
                try:
                    self._tick_cb(name, tick)
                except Exception:                        # noqa: BLE001 - 回调异常不影响收流
                    pass

    def _handle_spec(self, body: bytes) -> None:
        """品种规格推送（cmd_id=18）——字段随 build 有增删，这里按「已知前缀」
        解析并把完整原文留在 spec_raw 里，避免错位崩溃。"""
        try:
            raw = zlib.decompress(body) if body[:2] == b"\x78\x9c" else body
        except zlib.error:
            raw = body
        name = None
        try:
            if len(raw) >= 68:
                name = raw[0:64].decode("utf-16-le", "replace").rstrip("\x00")
        except Exception:  # noqa: BLE001
            name = None
        if not name:
            return
        spec = self.specs.get(name, {})
        spec["raw"] = raw
        # 常见布局：name(64) description(128) ... digits/spread 等在固定偏移上；
        # 这里只在首次出现时做保守解析，详细偏移见 日志/协议字段偏移实测.md
        if len(raw) >= 320:
            try:
                digits = struct.unpack_from("<I", raw, 192)[0]
                spread = struct.unpack_from("<I", raw, 196)[0]
                if 0 <= digits <= 12 and 0 <= spread < 10 ** 6:
                    spec["digits"] = digits
                    spec["spread"] = spread
            except struct.error:
                pass
        self.specs[name] = spec

    def _name_of(self, symbol_id: int) -> Optional[str]:
        for name, sid in self.symbol_ids.items():
            if sid == symbol_id:
                return name
        return None

    def _digits(self, symbol: str) -> int:
        spec = self.specs.get(symbol)
        if spec and "digits" in spec:
            return spec["digits"]
        info = self._symbols_meta.get(symbol)
        if info:
            return info.get("digits", 5)
        return 5

    # ---------------- 登录 ----------------
    def login(self, login: int, password: str, *, server: Optional[str] = None,
              address: Optional[str] = None, timeout: float = 25.0) -> AccountInfo:
        """登录交易账户。

        报文是 912 字节的定长结构，布局用浏览器里的真实 MT5 Web 终端采样对过
        （`日志/协议字段偏移实测.md`）：

        ============ ==================================================
        偏移          内容
        ============ ==================================================
        0..3         必须全 0（写别的值网关会直接掐线）
        4..67        密码，UTF-16LE，最长 32 字符
        196..211     **16 字节随机数**（缺了会被拒）
        476          ``address`` 的 UTF-16 **字符数**
        480..735     连接地址，UTF-16LE（浏览器填的是它连的那个 IP）
        736..743     账号 uint64
        ============ ==================================================

        ``address`` 默认取本次连接的 host——实测服务器要的就是「你连的那个地址」，
        不是服务器标签（``Exness-MT5Trial5`` 之类的标签在这里没用）。
        """
        if server:
            self.server = server
        hint = address or self._host or server or ""
        body = bytearray(912)
        pw = password.encode("utf-16-le")
        body[4:4 + min(len(pw), 64)] = pw[:64]
        body[196:212] = crypto.random_bytes(16)          # 会话随机数（实测必需）
        if hint:
            srv = hint.encode("utf-16-le")
            struct.pack_into("<I", body, 476, len(srv) // 2)   # 字符数，不是字节数
            body[480:480 + min(len(srv), 256)] = srv[:256]
        struct.pack_into("<Q", body, 736, login)
        frame = self._send(protocol.CMD_LOGIN, bytes(body), timeout=timeout)
        if frame.res_code != 0:
            raise MT5LoginError(
                f"登录被拒 / login refused: res_code={frame.res_code} "
                f"body={frame.body[:64].hex()}")
        self.login_id = login
        # 凭据**只放内存**，供断线自动重连使用；绝不写盘
        self._login_args = {"login": login, "password": password, "server": server}
        self.server = server or self.server
        if self.debug:
            print(f"[login] account_id={self.login_id} body={len(frame.body)}B")
        # 登录后把品种表拉下来，后面订阅/下单都要用
        self.load_symbols()
        # 起心跳线程：服务端会掐掉长时间静默的连接
        try:
            self.start_heartbeat()
        except Exception:                                 # noqa: BLE001
            pass
        return self.account_info()

    # ---------------- 账户 ----------------
    def account_info(self, timeout: float = 15.0, *,
                     with_positions: bool = True) -> AccountInfo:
        """账户信息。

        ⚠️ 本 build（6230）的账户帧**不下发** ``equity / margin / profit``
        （``equity_raw``/``margin`` 恒为 0，schema 里也压根没有 ``profit`` 字段），
        所以这三项要客户端自己算 —— 跟官方终端一样：

        * ``profit`` = 所有持仓的 ``profit + swap + commission`` 之和
        * ``equity`` = ``balance + credit + profit``
        * ``margin`` 需要品种的合约大小/保证金率，本库还没解析，服务端给 0 时保持 0

        ``with_positions=False`` 可跳过那次持仓请求（例如只关心余额时）。
        """
        frame = self._send(protocol.CMD_ACCOUNT, b"", timeout=timeout)
        if frame.res_code != 0:
            raise MT5Error(f"cmd 3 失败 / account_info failed: {frame.res_code}")
        vals, _ = serial.parse_series(frame.body, protocol.ACCOUNT_SCHEMA, 0)
        d = dict(zip(protocol.ACCOUNT_FIELDS, vals))
        # 字段映射按 MQL5 交叉验证修正：@9 = balance、@17 = credit。
        balance = d["balance"]
        credit = d["credit"]
        profit = d.get("profit", 0.0) or 0.0
        # 服务端不发 profit 时，用持仓自己算（官方终端也是这么做的）
        if not profit and with_positions:
            try:
                profit = sum((p.profit or 0.0) + (p.swap or 0.0) + (p.commission or 0.0)
                             for p in self.positions_get())
            except MT5Error:
                profit = 0.0
        margin = d.get("margin", 0.0) or 0.0
        equity = d.get("equity_raw", 0.0) or (balance + credit + profit)
        return AccountInfo(
            login=self.login_id or 0, trade_mode=d["trade_mode"],
            leverage=d["leverage"], balance=balance, equity=equity,
            profit=profit, margin=margin,
            margin_free=equity - margin, currency=d["currency"],
            server=d["server"], name=d["company"], group=d["group"],
            credit=credit, bonus=d.get("f748", 0), flags=d["flags"],
            rights=d["rights"], raw=d,
        )

    # ---------------- 品种 ----------------
    def load_symbols(self, timeout: float = 30.0) -> int:
        """拉取品种列表（cmd_id=34，响应是 gzip），返回品种数。"""
        frame = self._send(protocol.CMD_SYMBOLS, b"", timeout=timeout)
        if frame.res_code != 0:
            raise MT5Error(f"cmd 34 失败 / symbols failed: {frame.res_code}")
        payload = frame.body
        if len(payload) > 4:
            try:
                payload = zlib.decompress(payload[4:])
            except zlib.error:
                try:
                    payload = gzip.decompress(payload[4:])
                except OSError:
                    payload = payload[4:]
        count = struct.unpack_from("<I", payload, 0)[0]
        off = 4
        for _ in range(count):
            vals, off = serial.parse_series(payload, protocol.SYMBOL_SCHEMA, off)
            name, desc, digits, sid, path, calc, basis, sector = vals
            self.symbol_ids[name] = sid
            self._symbols_meta[name] = {
                "name": name, "description": desc, "path": path, "digits": digits,
                "symbol_id": sid, "calc_mode": calc, "basis": basis,
                "sector": sector,
            }
        if self.debug:
            print(f"[symbols] {count} 个品种")
        return count

    def symbols_get(self, pattern: Optional[str] = None) -> list[SymbolInfo]:
        out = []
        for name, m in sorted(self._symbols_meta.items()):
            if pattern and pattern.lower() not in name.lower():
                continue
            tick = self.ticks.get(name)
            out.append(SymbolInfo(
                name=name, description=m["description"], path=m["path"],
                digits=m["digits"], symbol_id=m["symbol_id"], basis=m["basis"],
                sector=m["sector"], calc_mode=m["calc_mode"],
                spread=self.specs.get(name, {}).get("spread", 0),
                bid=tick.bid if tick else 0.0, ask=tick.ask if tick else 0.0,
                trade_mode=0, volume_min=0.0, volume_step=0.0, volume_max=0.0,
                point=10 ** -m["digits"],
                spec_raw=self.specs.get(name, {}).get("raw", b""),
            ))
        return out

    def symbol_info(self, name: str) -> Optional[SymbolInfo]:
        got = self.symbols_get(name)
        for s in got:
            if s.name == name:
                return s
        return None

    def subscribe(self, symbols: Iterable[str] | str, timeout: float = 15.0) -> int:
        """订阅实时报价；服务器随后用 cmd_id=8 持续推送。

        ``symbols`` 可以是单个字符串，也可以是字符串序列 ——
        **注意**：直接把 ``str`` 丢进 ``for`` 会**按字符迭代**
        （曾因此报出 ``unknown symbol: B``，见 §11.208）。
        """
        if isinstance(symbols, str):
            symbols = [symbols]
        symbols = list(symbols)
        self._subscribed.update(symbols)      # 重连后自动恢复
        ids = []
        for s in symbols:
            sid = self.symbol_ids.get(s)
            if sid is None:
                raise MT5Error(f"未知品种 / unknown symbol: {s}")
            ids.append(sid)
        payload = struct.pack("<I", len(ids)) + b"".join(
            struct.pack("<I", i) for i in ids)
        frame = self._send(protocol.CMD_SUBSCRIBE, payload, timeout=timeout)
        if frame.res_code != 0:
            raise MT5Error(f"订阅失败 / subscribe failed: {frame.res_code}")
        return len(ids)


    def symbol_spec(self, name: str, *, timeout: float = 10.0) -> dict:
        """请求并解析**品种规格**（cmd 18）。

        返回 ``{name, digits, point, contract_size, volume_min, volume_max,
        volume_step, raw_len}``，字段偏移见 :mod:`mt5api.symbol_spec`。

        实测：请求载荷 ``[u32 count][u32 symbol_id]``，响应 body 3072 字节、
        ``res=0``；三个品种（XAU/EUR/BTC）交叉验证全部符合预期。
        """
        sid = self.symbol_ids.get(name)
        if sid is None:
            raise MT5Error(f"未知品种 / unknown symbol: {name}")
        from .symbol_spec import parse_spec

        payload = struct.pack("<II", 1, sid)
        frame = self._send(protocol.CMD_SYMBOL_SPEC, payload, timeout=timeout)
        if frame.res_code != 0:
            raise MT5Error(f"规格请求失败 / spec failed: {frame.res_code}")
        info = parse_spec(bytes(frame.body))
        info.setdefault("name", name)
        self.specs[name] = {**(self.specs.get(name) or {}), **info,
                            "raw": bytes(frame.body)}
        return info



    def margin_required(self, symbol: str, volume: float,
                        *, price: float | None = None) -> float:
        """估算某品种某手数所需的**保证金**（服务端不下发 margin，只能本地算）。

        ``margin = 手数 × 合约大小 × 价格 / 杠杆``；合约大小取自
        :meth:`symbol_spec`，价格缺省用最新 bid，杠杆取账户值。
        """
        from .symbol_spec import margin_from_spec

        spec = self.specs.get(symbol) or self.symbol_spec(symbol)
        if price is None:
            t = self.symbol_info_tick(symbol)
            price = (t.bid if t else 0.0)
        lev = self.account_info().leverage or 1
        return margin_from_spec(volume, spec, price, lev)

    def margin_summary(self) -> dict:
        """按品种汇总当前持仓的**估算保证金**（服务端 margin 恒为 0 时的替代）。"""
        from .symbol_spec import margin_from_spec

        lev = self.account_info().leverage or 1
        agg: dict[str, dict] = {}
        for p in self.positions_get():
            slot = agg.setdefault(p.symbol, {"volume": 0.0, "count": 0})
            slot["volume"] += p.volume
            slot["count"] += 1
        total = 0.0
        for sym, slot in agg.items():
            spec = self.specs.get(sym) or self.symbol_spec(sym)
            t = self.symbol_info_tick(sym)
            price = t.bid if t else 0.0
            m = margin_from_spec(slot["volume"], spec, price, lev)
            slot.update(price=price, margin=m,
                        contract_size=spec.get("contract_size"))
            total += m
        agg["__total__"] = {"margin": total, "leverage": lev}
        return agg


    def symbol_info_tick(self, name: str, *, wait: float = 5.0,
                         auto_subscribe: bool = True) -> Optional[Tick]:
        """取最新报价；若还没收到推送，最多等 ``wait`` 秒。

        ``auto_subscribe=True``（默认）时，如果该品种还没订阅过，会**自动订阅一次**
        ——MT5 的报价只来自订阅推送，不订阅永远是空（这是很容易踩的坑）。
        """
        if name in self.ticks:
            return self.ticks[name]

        if auto_subscribe and name not in self._auto_subscribed:
            self._auto_subscribed.add(name)
            try:
                self.subscribe(name)
            except Exception:                                    # noqa: BLE001
                pass

        end = time.time() + wait
        while time.time() < end:
            time.sleep(0.05)
            if name in self.ticks:
                return self.ticks[name]
        return self.ticks.get(name)


    # ---------------- K 线 ----------------
    @staticmethod
    def _tf_value(timeframe) -> int:
        if isinstance(timeframe, int):
            return timeframe
        try:
            return protocol.TIMEFRAMES[timeframe.upper()]
        except KeyError:
            raise ValueError(f"未知周期 / unknown timeframe: {timeframe}") from None

    def copy_rates_range(self, symbol: str, timeframe, date_from: int,
                         date_to: int, *, timeout: float = 30.0) -> list[Rate]:
        """按时间区间取 K 线（cmd_id=11）。时间用 unix 秒。"""
        tf = self._tf_value(timeframe)
        payload = bytearray(74)
        name = symbol.encode("utf-16-le")[:64]
        payload[0:len(name)] = name
        struct.pack_into("<H", payload, 64, tf)
        struct.pack_into("<i", payload, 66, int(date_from))
        struct.pack_into("<i", payload, 70, int(date_to))
        frame = self._send(protocol.CMD_CANDLES, bytes(payload), timeout=timeout)
        if frame.res_code != 0:
            raise MT5Error(f"取 K 线失败 / candles failed: {frame.res_code}")
        body = frame.body
        n = len(body) // protocol.CANDLE_SIZE
        out = []
        for i in range(n):
            off = i * protocol.CANDLE_SIZE
            ts, o, h, l, c, vol, spread = struct.unpack_from("<iddddqi", body, off)
            out.append(Rate(time=ts, open=o, high=h, low=l, close=c,
                            tick_volume=vol, spread=spread, real_volume=0))
        return out

    def copy_rates_from_pos(self, symbol: str, timeframe, start_pos: int,
                            count: int, *, timeout: float = 30.0) -> list[Rate]:
        """从最新一根往回取 ``count`` 根（官方包同名函数的语义）。"""
        tf = self._tf_value(timeframe)
        secs = protocol.TIMEFRAME_SECONDS.get(tf, 60)
        now = int(time.time())
        span = secs * (count + start_pos + 10)
        # 先按理论跨度取；若为空（周末/停盘/周期太细）就把窗口放大重试
        for factor in (1, 8, 64):
            rates = self.copy_rates_range(symbol, timeframe, now - span * factor,
                                          now, timeout=timeout)
            if rates:
                break
        if start_pos:
            rates = rates[: len(rates) - start_pos] if start_pos < len(rates) else []
        return rates[-count:] if count < len(rates) else rates

    # ---------------- 持仓 / 订单 / 历史 ----------------
    def _parse_positions(self, body: bytes):
        pos_count = struct.unpack_from("<I", body, 0)[0]
        off = 4
        positions = []
        for _ in range(pos_count):
            vals, off = serial.parse_series(body, protocol.POSITION_SCHEMA, off)
            positions.append(TradePosition(
                ticket=vals[0], order=vals[1], time=vals[2], time_update=vals[3],
                symbol=vals[4], type=vals[5], price_open=vals[6],
                price_current=vals[7], sl=vals[8], tp=vals[9],
                volume=vals[10] / 1e8, profit=vals[11], commission=vals[14],
                swap=vals[15], magic=vals[22], comment=vals[18], raw=vals,
            ))
        orders = []
        if off + 4 <= len(body):
            order_count = struct.unpack_from("<I", body, off)[0]
            off += 4
            for _ in range(order_count):
                vals, off = serial.parse_series(body, protocol.ORDER_SCHEMA, off)
                orders.append(TradeOrder(
                    ticket=vals[0], symbol=vals[2], time_setup=vals[3],
                    time_done=vals[5], type=vals[6], state=vals[17],
                    volume_initial=vals[15] / 1e8, volume_current=vals[16] / 1e8,
                    price_open=vals[10], price_current=vals[12], sl=vals[13],
                    tp=vals[14], comment=vals[20], position_id=vals[19],
                    magic=vals[18], raw=vals,
                ))
        return positions, orders

    def positions_get(self, symbol: Optional[str] = None, *,
                      min_interval: float = 0.5,
                      timeout: float = 15.0) -> list[TradePosition]:
        """查询持仓（cmd 4）。

        ⚠️ **已知问题（2026-10-22 真机实测）**：

        * 同一会话内**刚下过单**后立刻查询，服务器会返回**陈旧快照**
          （实测连续 10 秒都是旧数据）；**重新连接**后才看到新仓位。
          交易后要复核持仓，**请重连再查**。
        * **连续快速**查询（4~5 次）会导致连接被断开，故内置
          ``min_interval=0.5`` 秒节流；传 0 可关闭。
        """
        # 节流：实测连续快速查持仓会把连接查断（4~5 次即断）
        gap = time.time() - getattr(self, "_last_pos_query", 0.0)
        if min_interval > 0 and gap < min_interval:
            time.sleep(min_interval - gap)
        self._last_pos_query = time.time()
        frame = self._send(protocol.CMD_POSITIONS, b"", timeout=timeout)
        if frame.res_code != 0:
            raise MT5Error(f"cmd 4 失败 / positions failed: {frame.res_code}")
        positions, orders = self._parse_positions(frame.body)
        self._positions, self._orders = positions, orders
        if symbol:
            return [p for p in positions if p.symbol == symbol]
        return positions

    def orders_get(self, symbol: Optional[str] = None, *,
                   timeout: float = 15.0) -> list[TradeOrder]:
        self.positions_get(timeout=timeout)
        if symbol:
            return [o for o in self._orders if o.symbol == symbol]
        return list(self._orders)

    def history_deals_get(self, date_from: int = 0, date_to: int = 0, *,
                          timeout: float = 40.0) -> tuple[list[TradeDeal], list[TradeOrder]]:
        payload = struct.pack("<ii", int(date_from), int(date_to))
        frame = self._send(protocol.CMD_HISTORY, payload, timeout=timeout)
        if frame.res_code != 0:
            raise MT5Error(f"cmd 5 失败 / history failed: {frame.res_code}")
        body = frame.body
        deal_count = struct.unpack_from("<I", body, 0)[0]
        off = 4
        deals = []
        for _ in range(deal_count):
            vals, off = serial.parse_series(body, protocol.DEAL_SCHEMA, off)
            deals.append(TradeDeal(
                ticket=vals[0], order=vals[2], time=vals[3], symbol=vals[5],
                type=vals[6], entry=vals[7], price=vals[8], volume=vals[12] / 1e8,
                profit=vals[13], commission=vals[16], swap=vals[17], fee=vals[27],
                position_id=vals[19], comment=vals[20], magic=vals[18], raw=vals,
            ))
        orders = []
        if off + 4 <= len(body):
            order_count = struct.unpack_from("<I", body, off)[0]
            off += 4
            for _ in range(order_count):
                vals, off = serial.parse_series(body, protocol.ORDER_SCHEMA, off)
                orders.append(TradeOrder(
                    ticket=vals[0], symbol=vals[2], time_setup=vals[3],
                    time_done=vals[5], type=vals[6], state=vals[17],
                    volume_initial=vals[15] / 1e8, volume_current=vals[16] / 1e8,
                    price_open=vals[10], price_current=vals[12], sl=vals[13],
                    tp=vals[14], comment=vals[20], position_id=vals[19],
                    magic=vals[18], raw=vals,
                ))
        return deals, orders

    # ---------------- 下单 ----------------
    @staticmethod
    def build_trade_request(*, action: int, symbol: str, volume: float,
                            type_: int, price: float = 0.0, digits: int = 0,
                            sl: float = 0.0, tp: float = 0.0,
                            deviation: int = 0, comment: str = "",
                            order_ticket: int = 0, position_ticket: int = 0,
                            filling: int = protocol.ORDER_FILLING_FOK,
                            type_time: int = protocol.ORDER_TIME_GTC,
                            trigger: float = 0.0, expiration: int = 0,
                            action_id: int = 0) -> bytes:
        """构造 248 字节 MqlTradeRequest 等价体。"""
        vol_raw = int(round(volume * 1e8))
        buf = bytearray(protocol.TRADE_REQUEST_SIZE)
        struct.pack_into("<I", buf, 0, action_id)
        struct.pack_into("<I", buf, 4, action)
        name = symbol.encode("utf-16-le")[:64]
        buf[8:8 + len(name)] = name
        struct.pack_into("<Q", buf, 72, vol_raw)
        struct.pack_into("<I", buf, 80, digits)
        struct.pack_into("<Q", buf, 84, order_ticket)
        struct.pack_into("<I", buf, 92, type_)
        struct.pack_into("<I", buf, 96, filling)
        struct.pack_into("<I", buf, 100, type_time)
        struct.pack_into("<I", buf, 104, 2)     # type_flags = 2（新订单，实测）
        struct.pack_into("<I", buf, 108, 0)     # reason = client
        struct.pack_into("<d", buf, 112, price)
        struct.pack_into("<d", buf, 120, trigger)
        struct.pack_into("<d", buf, 128, sl)
        struct.pack_into("<d", buf, 136, tp)
        struct.pack_into("<I", buf, 144, deviation)
        struct.pack_into("<d", buf, 148, 0.0)
        struct.pack_into("<d", buf, 156, 0.0)
        cm = comment.encode("utf-16-le")[:64]
        buf[164:164 + len(cm)] = cm
        struct.pack_into("<Q", buf, 228, position_ticket)
        struct.pack_into("<Q", buf, 236, 0)
        struct.pack_into("<I", buf, 244, expiration)
        return bytes(buf)

    def order_send(self, request: dict, *, timeout: float = 30.0,
                   wait_event: bool = True) -> TradeResult:
        """发送交易请求；返回服务器回执。

        ``request`` 的键与官方包一致：action / symbol / volume / type / price /
        sl / tp / deviation / comment / position / order / type_filling ...
        """
        symbol = request.get("symbol", "")
        digits = request.get("digits") or self._digits(symbol)
        payload = self.build_trade_request(
            action=int(request["action"]),
            symbol=symbol,
            volume=float(request.get("volume", 0.0)),
            type_=int(request.get("type", 0)),
            price=float(request.get("price", 0.0)),
            digits=int(digits),
            sl=float(request.get("sl", 0.0) or 0.0),
            tp=float(request.get("tp", 0.0) or 0.0),
            deviation=int(request.get("deviation", 0) or 0),
            comment=str(request.get("comment", "") or ""),
            order_ticket=int(request.get("order", 0) or 0),
            position_ticket=int(request.get("position", 0) or 0),
            filling=int(request.get("type_filling", protocol.ORDER_FILLING_FOK)),
            type_time=int(request.get("type_time", protocol.ORDER_TIME_GTC)),
            trigger=float(request.get("stoplimit", 0.0) or 0.0),
            expiration=int(request.get("expiration", 0) or 0),
            action_id=int(request.get("action_id", 0) or 0),
        )
        with self._lock:
            self._trade_events.clear()
        frame = self._send(protocol.CMD_TRADE, payload, timeout=timeout)
        result_raw = frame.body
        if frame.res_code != 0 and len(frame.body) < 128:
            # 有些服务器把 4 字节 retcode 直接放在响应体里
            rc = struct.unpack_from("<I", frame.body, 0)[0] if len(frame.body) >= 4 else frame.res_code
            return TradeResult(retcode=rc,
                               retcode_name=protocol.RETCODE_NAMES.get(rc, "?"),
                               deal=0, order=0, volume=0, price=0.0, bid=0.0,
                               ask=0.0, comment="", request=request, raw=frame.body)
        event = None
        if wait_event:
            event = self._wait_trade_event(timeout=timeout)
        if event is not None:
            result_raw = event.body
        return self._parse_trade_result(result_raw, request)

    def _wait_trade_event(self, timeout: float = 20.0) -> Optional[Frame]:
        """等最终回执：服务器会先发 retcode=10002（ACK），再发 10009（成交）。"""
        end = time.time() + timeout
        best = None
        while time.time() < end:
            with self._lock:
                events = list(self._trade_events)
                self._trade_events.clear()
            for ev in events:
                rc = None
                if len(ev.body) >= protocol.TRADE_RESULT_OFFSET + 4:
                    rc = struct.unpack_from("<I", ev.body, protocol.TRADE_RESULT_OFFSET)[0]
                if rc == 10002:
                    best = best or ev
                    continue
                return ev
            time.sleep(0.05)
        return best

    def _parse_trade_result(self, body: bytes, request: dict) -> TradeResult:
        if len(body) >= protocol.TRADE_RESULT_OFFSET + 128:
            off = protocol.TRADE_RESULT_OFFSET
            retcode, deal, order, volume = struct.unpack_from("<Iqqq", body, off)
            price, bid, ask = struct.unpack_from("<ddd", body, off + 28)
            comment = body[off + 64:off + 128].decode("utf-16-le", "replace").rstrip("\x00")
        elif len(body) >= 4:
            retcode = struct.unpack_from("<I", body, 0)[0]
            deal = order = volume = 0
            price = bid = ask = 0.0
            comment = ""
        else:
            retcode, deal, order, volume = 0, 0, 0, 0
            price = bid = ask = 0.0
            comment = ""
        return TradeResult(
            retcode=retcode, retcode_name=protocol.RETCODE_NAMES.get(retcode, "?"),
            deal=deal, order=order, volume=volume / 1e8 if volume else 0.0,
            price=price, bid=bid, ask=ask, comment=comment, request=request,
            raw=body,
        )

    # ---------------- 便捷封装 ----------------
    def market_order(self, symbol: str, side: str, volume: float, *,
                     sl: float = 0.0, tp: float = 0.0, comment: str = "",
                     wait_tick: float = 5.0) -> TradeResult:
        """市价开仓。``side`` = "buy"/"sell"。"""
        tick = self.symbol_info_tick(symbol, wait=wait_tick)
        if tick is None:
            raise MT5Error(f"没有 {symbol} 报价 / no quote for {symbol}")
        side = side.lower()
        price = tick.ask if side == "buy" else tick.bid
        return self.order_send({
            "action": protocol.TRADE_ACTION_MARKET,
            "symbol": symbol, "volume": volume,
            "type": protocol.ORDER_TYPE_BUY if side == "buy" else protocol.ORDER_TYPE_SELL,
            "price": price, "sl": sl, "tp": tp, "comment": comment,
            "type_filling": protocol.ORDER_FILLING_FOK,
        })

    def close_position(self, position: TradePosition, *, comment: str = "",
                       wait_tick: float = 5.0) -> TradeResult:
        """按持仓平仓（反向市价 + position ticket）。"""
        tick = self.symbol_info_tick(position.symbol, wait=wait_tick)
        if tick is None:
            raise MT5Error(f"没有 {position.symbol} 报价 / no quote")
        is_buy = position.type == protocol.ORDER_TYPE_BUY
        return self.order_send({
            "action": protocol.TRADE_ACTION_MARKET,
            "symbol": position.symbol, "volume": position.volume,
            "type": protocol.ORDER_TYPE_SELL if is_buy else protocol.ORDER_TYPE_BUY,
            "price": tick.bid if is_buy else tick.ask,
            "position": position.order or position.ticket,
            "comment": comment,
            "type_filling": protocol.ORDER_FILLING_FOK,
        })

    def modify_position(self, position: TradePosition, *, sl: float = 0.0,
                        tp: float = 0.0) -> TradeResult:
        """改持仓止损止盈（trade_action=6，用 order ticket）。"""
        tick = self.symbol_info_tick(position.symbol, wait=3.0)
        price = tick.bid if tick else position.price_current
        return self.order_send({
            "action": protocol.TRADE_ACTION_MODIFY_POSITION,
            "symbol": position.symbol, "volume": position.volume,
            "type": position.type, "price": price, "sl": sl, "tp": tp,
            "position": position.order or position.ticket,
            "type_filling": protocol.ORDER_FILLING_RETURN,
        })

    # ---------------- 杂项 ----------------
    # ---------------- 保活 / 重连 ----------------
    def _send(self, cmd, payload: bytes = b"", timeout: float = 20.0):
        """内部收发：断线时自动重连一次再重试。"""
        try:
            return self.t.send(cmd, payload, timeout=timeout)
        except MT5Disconnected:
            if not self.auto_reconnect or not self._login_args:
                raise
            self._reconnect()
            return self._send(cmd, payload, timeout=timeout)

    def _reconnect(self) -> None:
        """重建连接、重新登录、恢复订阅。失败抛 MT5Error。"""
        args = dict(self._login_args or {})
        if not args:
            raise MT5Error("没有登录凭据，无法自动重连")
        self._reconnect_count += 1
        host = self._host
        try:
            self.t.close()
        except Exception:                                 # noqa: BLE001
            pass
        tr = WebSocketTransport(f"wss://{host}:{self._port}/terminal",
                                origin=f"https://{host}:{self._port}")
        tr.connect(timeout=20.0)
        self.t = tr
        self.login(args["login"], args["password"], server=args["server"], address=host)
        for sym in list(self._subscribed):
            try:
                self.subscribe([sym])
            except Exception:                             # noqa: BLE001
                pass

    def start_heartbeat(self) -> None:
        """启动后台心跳线程（幂等）。"""
        if self.heartbeat_interval <= 0 or self._hb_thread is not None:
            return
        self._hb_stop.clear()

        def _loop():
            while not self._hb_stop.wait(self.heartbeat_interval):
                try:
                    self.t.send(protocol.CMD_HEARTBEAT, b"", timeout=10.0)
                except Exception:                         # noqa: BLE001
                    if not self.auto_reconnect or not self._login_args:
                        return
                    try:
                        self._reconnect()
                    except Exception:                     # noqa: BLE001
                        pass

        t = threading.Thread(target=_loop, name="mt5-heartbeat", daemon=True)
        self._hb_thread = t
        t.start()

    def heartbeat(self, timeout: float = 10.0) -> bool:
        frame = self._send(protocol.CMD_HEARTBEAT, b"", timeout=timeout)
        return frame.res_code == 0

    def account_url_hint(self) -> Optional[str]:
        return self.server

    def shutdown(self) -> None:
        self.t.close()

    def __enter__(self) -> "MT5Client":
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()
