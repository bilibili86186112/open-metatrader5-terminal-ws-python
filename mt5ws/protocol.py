"""MT5 协议常量与记录结构 / MT5 protocol constants and record layouts.

所有 schema 都写成 `{propType, propLength?}` 列表，交给 `serial.parse_series`。
字段名来自实机采样比对 + 官方 MQL5 术语（MqlTradeRequest / MqlTradeResult /
MqlRates / MqlAccountInfo / MqlTick），便于和官方 `MetaTrader5` Python 包对照。

字段偏移在 `日志/协议字段偏移实测.md` 里逐条记录（含与第三方文档不一致时
以本机实测为准的说明）。
"""

from __future__ import annotations

from . import serial

# --------------------------------------------------------------------------- #
# 命令号 / command ids (inside the encrypted frame)
# --------------------------------------------------------------------------- #
CMD_AUTH = 0          # 握手：客户端发 64 字节，服务器回会话密钥
CMD_ACCOUNT = 3       # 账户信息
CMD_POSITIONS = 4     # 持仓 + 挂单
CMD_HISTORY = 5       # 历史成交(deals) + 历史订单(orders)
CMD_SUBSCRIBE = 7     # 订阅报价
CMD_QUOTES = 8        # 服务器推送：报价
CMD_PUSH = 10         # 服务器推送：交易事件
CMD_CANDLES = 11      # K 线
CMD_TRADE = 12        # 下单/改单/平仓
CMD_NOTIFY = 15       # 登录后的系统通知
CMD_SYMBOL_SPEC = 18  # 品种规格（订阅后自动推送）
CMD_TRADE_EVENT = 19  # 下单结果
CMD_LOGIN = 28        # 登录
CMD_SYMBOLS = 34      # 品种列表（gzip 压缩）
CMD_HEARTBEAT = 51    # 心跳

# --------------------------------------------------------------------------- #
# 时间周期 / timeframes（MT5 的 ENUM_TIMEFRAMES 数值：分线直接用分钟数，
# 小时/日线以上带 0x4000 高位标志）
# --------------------------------------------------------------------------- #
TIMEFRAMES: dict[str, int] = {
    "M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5, "M6": 6,
    "M10": 10, "M12": 12, "M15": 15, "M20": 20, "M30": 30,
    "H1": 16385, "H2": 16386, "H3": 16387, "H4": 16388,
    "H6": 16390, "H8": 16392, "H12": 16396,
    "D1": 16408, "W1": 32769, "MN1": 49153,
}
TIMEFRAME_SECONDS: dict[int, int] = {
    1: 60, 2: 120, 3: 180, 4: 240, 5: 300, 6: 360, 10: 600, 12: 720,
    15: 900, 20: 1200, 30: 1800,
    16385: 3600, 16386: 7200, 16387: 10800, 16388: 14400,
    16390: 21600, 16392: 28800, 16396: 43200,
    16408: 86400, 32769: 604800, 49153: 2592000,
}
TIMEFRAME_NAMES: dict[int, str] = {v: k for k, v in TIMEFRAMES.items()}

# --------------------------------------------------------------------------- #
# 交易枚举 / trade enums
# --------------------------------------------------------------------------- #
#: trade_action —— 请求类型
TRADE_ACTION_DEAL = 0
TRADE_ACTION_PENDING = 5
TRADE_ACTION_MARKET = 3
TRADE_ACTION_MODIFY_POSITION = 6
TRADE_ACTION_MODIFY_ORDER = 7
TRADE_ACTION_CANCEL = 8

#: trade_type —— 方向/挂单类型
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TYPE_BUY_STOP = 4
ORDER_TYPE_SELL_STOP = 5
ORDER_TYPE_BUY_STOP_LIMIT = 6
ORDER_TYPE_SELL_STOP_LIMIT = 7

ORDER_TYPE_NAMES = {
    0: "buy", 1: "sell", 2: "buy_limit", 3: "sell_limit",
    4: "buy_stop", 5: "sell_stop", 6: "buy_stop_limit", 7: "sell_stop_limit",
}

#: 成交模式
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2

#: 订单有效期
ORDER_TIME_GTC = 0
ORDER_TIME_DAY = 1
ORDER_TIME_SPECIFIED = 2

#: 订单状态
ORDER_STATE_STARTED = 0
ORDER_STATE_PLACED = 1
ORDER_STATE_CANCELED = 2
ORDER_STATE_PARTIAL = 3
ORDER_STATE_FILLED = 4
ORDER_STATE_REJECTED = 5

ORDER_STATE_NAMES = {
    0: "started", 1: "placed", 2: "canceled", 3: "partial",
    4: "filled", 5: "rejected",
}

#: 服务器返回码（MQL5 的 TRADE_RETCODE_*）
RETCODE_NAMES = {
    0: "OK/PLACED",
    10001: "TRADE_RETCODE_INVALID", 10002: "TRADE_RETCODE_NOT_ACCEPTED",
    10003: "TRADE_RETCODE_SERVER_DISABLES_AT",
    10004: "TRADE_RETCODE_CLIENT_DISABLES_AT",
    10005: "TRADE_RETCODE_LOCKED",
    10006: "TRADE_RETCODE_REJECT",
    10007: "TRADE_RETCODE_CANCEL",
    10008: "TRADE_RETCODE_PLACED",
    10009: "TRADE_RETCODE_DONE",
    10010: "TRADE_RETCODE_DONE_PARTIAL",
    10011: "TRADE_RETCODE_ERROR",
    10012: "TRADE_RETCODE_TIMEOUT",
    10013: "TRADE_RETCODE_INVALID_REQUEST",
    10014: "TRADE_RETCODE_INVALID_VOLUME",
    10015: "TRADE_RETCODE_INVALID_PRICE",
    10016: "TRADE_RETCODE_INVALID_STOPS",
    10017: "TRADE_RETCODE_TRADE_DISABLED",
    10018: "TRADE_RETCODE_MARKET_CLOSED",
    10019: "TRADE_RETCODE_NO_MONEY",
    10020: "TRADE_RETCODE_PRICE_CHANGED",
    10021: "TRADE_RETCODE_PRICE_OFF",
    10022: "TRADE_RETCODE_INVALID_EXPIRATION",
    10023: "TRADE_RETCODE_ORDER_CHANGED",
    10024: "TRADE_RETCODE_TOO_MANY_REQUESTS",
    10025: "TRADE_RETCODE_NO_CHANGES",
    10026: "TRADE_RETCODE_SERVER_DISABLES_AT",
    10027: "TRADE_RETCODE_CLIENT_DISABLES_AT",
    10028: "TRADE_RETCODE_LOCKED",
    10029: "TRADE_RETCODE_FROZEN",
    10030: "TRADE_RETCODE_INVALID_FILL",
    10031: "TRADE_RETCODE_CONNECTION",
    10032: "TRADE_RETCODE_ONLY_REAL",
    10033: "TRADE_RETCODE_LIMIT_ORDERS",
    10034: "TRADE_RETCODE_LIMIT_VOLUME",
    10035: "TRADE_RETCODE_INVALID_ORDER",
    10036: "TRADE_RETCODE_POSITION_CLOSED",
}

# --------------------------------------------------------------------------- #
# 记录结构 / record layouts
# --------------------------------------------------------------------------- #

#: 账户信息（cmd_id=3）。实测 Exness-MT5Trial5 / build 6230 响应 6496 字节，
#: 头部 816 字节是下面这组字段（偏移已用「已知值定位法」逐字段核对：
#: 'USD'@25、'Standardwt1'@97、6230@353、'Exness-MT5Trial5'@355、
#: 'Exness Technologies Ltd'@483、余额 60.0@752、杠杆 2000@93）。
ACCOUNT_SCHEMA = [
    {"propType": 4},                       # 0    flags
    {"propType": 3},                       # 1    trade rights
    {"propType": 3},                       # 5    trade flags
    {"propType": 8},                       # 9    balance 余额（MQL5 交叉验证）
    {"propType": 8},                       # 17   credit 信用
    {"propType": 11, "propLength": 64},    # 25   currency
    {"propType": 6},                       # 89   （保留）
    {"propType": 6},                       # 93   leverage 杠杆（1:2000 -> 2000）
    {"propType": 11, "propLength": 256},   # 97   group
    {"propType": 5},                       # 353  build 服务器版本（6230）
    {"propType": 11, "propLength": 128},   # 355  server
    {"propType": 11, "propLength": 256},   # 483  company 公司名
    {"propType": 3},                       # 739  trade_mode
    {"propType": 1},                       # 743
    {"propType": 6},                       # 744  credit
    {"propType": 6},                       # 748  bonus
    {"propType": 8},                       # 752  （实测恒为 60，疑为强平水平；不是余额）
    {"propType": 8},                       # 760  equity
    {"propType": 6},                       # 768
    {"propType": 8},                       # 772  margin
    {"propType": 6},                       # 780  stop out
    {"propType": 8},                       # 784  margin_free
    {"propType": 8},                       # 792  margin_level
    {"propType": 8},                       # 800
    {"propType": 6},                       # 808  password_min
    {"propType": 6},                       # 812  password_flags
]
ACCOUNT_HEADER_SIZE = 816

#: 账户字段的 Python 名字（按上面 schema 顺序）
ACCOUNT_FIELDS = [
    "flags",
    "rights",
    "trade_flags",
    "balance",
    "credit",
    "currency",
    "f89",
    "leverage",
    "group",
    "build",
    "server",
    "company",
    "trade_mode",
    "f743",
    "f744",
    "f748",
    "f752",
    "equity_raw",
    "f768",
    "margin",
    "stop_out",
    "margin_free",
    "margin_level",
    "f800",
    "password_min",
    "password_flags",
]

#: 行情报价（服务器推送 cmd_id=8，每条 50 字节；价格是「原始值」，需要 /10^digits）
QUOTE_SCHEMA = [
    {"propType": 6},   # 0 symbol_id
    {"propType": 3},   # 1 time (unix seconds)
    {"propType": 6},   # 2 flags / 已提供字段的位掩码
    {"propType": 8},   # 3 bid (raw)
    {"propType": 8},   # 4 ask (raw)
    {"propType": 8},   # 5 last (raw)
    {"propType": 17},  # 6 tick volume
    {"propType": 6},   # 7 ms 小数部分
    {"propType": 5},   # 8 flags2
]
QUOTE_SIZE = 50

#: 持仓（cmd_id=4 的第一段，每条 344 字节）
POSITION_SCHEMA = [
    {"propType": 17},                      # 0  position id
    {"propType": 17},                      # 1  order ticket
    {"propType": 6},                       # 2  time create
    {"propType": 6},                       # 3  time update
    {"propType": 11, "propLength": 64},    # 4  symbol
    {"propType": 6},                       # 5  action (0 buy / 1 sell)
    {"propType": 8},                       # 6  price open
    {"propType": 8},                       # 7  price close/current
    {"propType": 8},                       # 8  sl
    {"propType": 8},                       # 9  tp
    {"propType": 18},                      # 10 volume (raw)
    {"propType": 8},                       # 11 profit
    {"propType": 8},                       # 12 rate profit
    {"propType": 8},                       # 13 rate margin
    {"propType": 8},                       # 14 commission
    {"propType": 8},                       # 15 swap
    {"propType": 17},                      # 16 expert / magic
    {"propType": 17},                      # 17 position by
    {"propType": 11, "propLength": 64},    # 18 comment
    {"propType": 8},                       # 19 contract size
    {"propType": 6},                       # 20 digits
    {"propType": 6},                       # 21 digits currency
    {"propType": 6},                       # 22 magic
    {"propType": 11, "propLength": 64},    # 23 reason
    {"propType": 3},                       # 24 ms
    {"propType": 3},                       # 25 ms
]
POSITION_SIZE = 344

#: 订单（挂单 / 历史订单，每条 356 字节）
ORDER_SCHEMA = [
    {"propType": 17},                      # 0  ticket
    {"propType": 11, "propLength": 64},    # 1  order id
    {"propType": 11, "propLength": 64},    # 2  symbol
    {"propType": 6},                       # 3  time setup
    {"propType": 6},                       # 4  time expiration
    {"propType": 6},                       # 5  time done
    {"propType": 6},                       # 6  type
    {"propType": 6},                       # 7  filling
    {"propType": 6},                       # 8  time type
    {"propType": 6},                       # 9  reason
    {"propType": 8},                       # 10 price order
    {"propType": 8},                       # 11 price trigger
    {"propType": 8},                       # 12 price current
    {"propType": 8},                       # 13 sl
    {"propType": 8},                       # 14 tp
    {"propType": 17},                      # 15 volume initial
    {"propType": 17},                      # 16 volume current
    {"propType": 6},                       # 17 state
    {"propType": 17},                      # 18 expert / magic
    {"propType": 17},                      # 19 position id
    {"propType": 11, "propLength": 64},    # 20 comment
    {"propType": 8},                       # 21 contract size
    {"propType": 6},                       # 22 digits
    {"propType": 6},                       # 23 digits currency
    {"propType": 8},                       # 24 commission daily
    {"propType": 8},                       # 25 commission monthly
    {"propType": 8},                       # 26 margin rate
    {"propType": 6},                       # 27 activation mode
    {"propType": 3},                       # 28 ms
    {"propType": 3},                       # 29 ms
]
ORDER_SIZE = 356

#: 成交（历史 cmd_id=5 的第一段，每条 356 字节）
DEAL_SCHEMA = [
    {"propType": 17},                      # 0  deal ticket
    {"propType": 11, "propLength": 64},    # 1  deal id
    {"propType": 17},                      # 2  order ticket
    {"propType": 6},                       # 3  time create
    {"propType": 6},                       # 4  time update
    {"propType": 11, "propLength": 64},    # 5  symbol
    {"propType": 6},                       # 6  action (0 buy / 1 sell)
    {"propType": 6},                       # 7  entry (0 in / 1 out)
    {"propType": 8},                       # 8  price open
    {"propType": 8},                       # 9  price close
    {"propType": 8},                       # 10 sl
    {"propType": 8},                       # 11 tp
    {"propType": 18},                      # 12 volume
    {"propType": 8},                       # 13 profit
    {"propType": 8},                       # 14 rate profit
    {"propType": 8},                       # 15 rate margin
    {"propType": 8},                       # 16 commission
    {"propType": 8},                       # 17 swap
    {"propType": 17},                      # 18 expert / magic
    {"propType": 17},                      # 19 position id
    {"propType": 11, "propLength": 64},    # 20 comment
    {"propType": 8},                       # 21 contract size
    {"propType": 6},                       # 22 digits
    {"propType": 6},                       # 23 digits currency
    {"propType": 6},                       # 24 reason
    {"propType": 3},                       # 25 ms
    {"propType": 3},                       # 26 ms
    {"propType": 8},                       # 27 fee
]
DEAL_SIZE = 356

#: 品种列表（cmd_id=34，gzip 解压后按此结构连续排列）
SYMBOL_SCHEMA = [
    {"propType": 11, "propLength": 64},    # 0 name
    {"propType": 11, "propLength": 128},   # 1 description
    {"propType": 6},                       # 2 digits
    {"propType": 6},                       # 3 symbol id（订阅报价用）
    {"propType": 11, "propLength": 256},   # 4 path
    {"propType": 6},                       # 5 calc mode
    {"propType": 11, "propLength": 64},    # 6 basis
    {"propType": 5},                       # 7 sector
]
SYMBOL_SIZE = 526

#: K 线记录（cmd_id=11 响应，纯 48 字节定长流）
CANDLE_SIZE = 48

#: 下单请求体（cmd_id=12，248 字节定长）
TRADE_REQUEST_SIZE = 248
TRADE_REQUEST_FIELDS = [
    ("action_id", "u32", 0),
    ("trade_action", "u32", 4),
    ("symbol", "str64", 8),
    ("volume", "u64", 72),
    ("digits", "u32", 80),
    ("trade_order", "u64", 84),
    ("trade_type", "u32", 92),
    ("type_filling", "u32", 96),
    ("type_time", "u32", 100),
    ("type_flags", "u32", 104),
    ("type_reason", "u32", 108),
    ("price_order", "f64", 112),
    ("price_trigger", "f64", 120),
    ("price_sl", "f64", 128),
    ("price_tp", "f64", 136),
    ("price_deviation", "u32", 144),
    ("price_top", "f64", 148),
    ("price_bottom", "f64", 156),
    ("comment", "str64", 164),
    ("trade_position", "u64", 228),
    ("position_by", "u64", 236),
    ("time_expiration", "u32", 244),
]

#: 下单结果（cmd_id=19，380 字节 = 4 前缀 + 248 回显 + 128 结果）
TRADE_EVENT_SIZE = 380
TRADE_RESULT_OFFSET = 252
TRADE_RESULT_FIELDS = [
    ("retcode", "u32", 0),
    ("deal", "i64", 4),
    ("order", "i64", 12),
    ("volume", "i64", 20),
    ("price", "f64", 28),
    ("bid", "f64", 36),
    ("ask", "f64", 44),
    ("comment", "str64", 64),
]


def symbol_id_map(payload: bytes) -> dict[str, int]:
    """把 cmd_id=34 的响应解成 {symbol: symbol_id}。"""
    import struct
    import zlib

    if len(payload) > 4:
        try:
            payload = zlib.decompress(payload[4:])
        except zlib.error:
            payload = payload[4:]
    count = struct.unpack_from("<I", payload, 0)[0]
    off = 4
    out: dict[str, int] = {}
    for _ in range(count):
        vals, off = serial.parse_series(payload, SYMBOL_SCHEMA, off)
        out[vals[0]] = vals[3]
    return out
