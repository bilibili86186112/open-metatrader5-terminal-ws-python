# open-metatrader5-terminal-ws-python

**用券商网关的 WebSocket 通道，提供与官方 `MetaTrader5` 包兼容的 Python API。**

* **不需要**安装 MT5 终端，**不需要** Wine，**不需要**任何 DLL
* 运行时**零第三方依赖**（AES 与 WebSocket 都有纯标准库实现）
* 直接替换官方包：把 `import MetaTrader5 as mt5` 指到本项目即可

```python
import MetaTrader5 as mt5

if not mt5.initialize(login=123456, password="...", server="Exness-MT5Trial5"):
    print("连接失败:", mt5.last_error()); raise SystemExit(1)

print(mt5.account_info().balance)                      # 账户
mt5.symbol_select("XAUUSDm", True)                     # 官方惯例：先选品种
t = mt5.symbol_info_tick("XAUUSDm")                    # 报价（订阅后读本地缓存，0ms）
rates = mt5.copy_rates_from_pos("XAUUSDm", mt5.TIMEFRAME_M1, 0, 10)

r = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL,   # 下单
                    "symbol": "XAUUSDm", "volume": 0.01,
                    "type": mt5.ORDER_TYPE_BUY, "price": t.ask,
                    "deviation": 20, "type_filling": mt5.ORDER_FILLING_IOC})
print(r.retcode, r.comment)                            # 成功: 0 或 10009

mt5.shutdown()
```

---

## 安装

```bash
pip install .                 # 零依赖
pip install ".[fast]"         # 可选：AES/WebSocket 加速件
pip install ".[numpy]"        # 可选：copy_rates_* 返回 numpy 结构化数组
```

或者不安装，把仓库根目录加进 `sys.path` 直接用（`import MetaTrader5`）。

---

## 已实现的接口

| 生命周期 | 账户/品种 | 行情 | 交易 | 历史 |
|---|---|---|---|---|
| `initialize()` | `account_info()` | `copy_rates_from_pos()` | `order_send()` | `history_deals_get()` |
| `login()` | `symbols_get()` | `copy_rates_from()` | `order_check()` | `history_orders_get()`¹ |
| `shutdown()` | `symbol_info()` | `copy_rates_range()` | | |
| `last_error()` | `symbol_info_tick()` | | | |
| `version()` | `symbol_select()` | | | |
| `terminal_info()`² | `positions_get()` / `orders_get()` | | | |

常量全部按**官方数值**：`TIMEFRAME_M1=1` / `TIMEFRAME_H1=16385` / `TIMEFRAME_D1=16408`、
`ORDER_TYPE_BUY=0` / `ORDER_FILLING_IOC=1` / `TRADE_ACTION_DEAL=1` / `TRADE_RETCODE_DONE=10009` …

¹ 暂返回空元组　² 无本地终端，返回 `None`

---

## 与官方包的区别（**请务必读**）

| 项 | 官方包 | 本项目 |
|---|---|---|
| 传输 | 本地终端 IPC | **WebSocket 直连券商网关** |
| 是否需要终端 | 必须 | **不需要** |
| `initialize(path)` | 终端路径 | **忽略 `path`**，改用 `login`/`password`/`server` |
| 接入点发现 | 终端负责 | 内置表 + 可传 `endpoints=[...]` + 可选 `servers.dat` |
| `copy_rates_*` | numpy 结构化数组 | **dict 列表**（`r["close"]` 可用；装 `[numpy]` 后返回数组）|
| 交易后持仓 | 立即更新 | ⚠️ **同会话可能返回旧快照**，核实请重连 |
| 成交号 | 响应里有 | ⚠️ 响应里 `deal=0`/`order=0`，需 `history_deals_get()` 反查 |
| 事件回调 | 内建 | 额外提供 `set_tick_callback()` |

### 已知实测数据（2026-10，Exness demo）

* **开/平仓往返 ≈ 280 ms**（p50；232~321 ms）
* 服务器记录的成交时间与本地发起**同秒**（< 1 s；本机比服务器快约 0.7 s）
* `symbol_info_tick`（已订阅）≈ **0 ms**（本地缓存）
* `copy_rates_from_pos` ≈ 237 ms / `positions_get` ≈ 205 ms / `account_info` ≈ 411 ms
* **成功有两种 `retcode`：`0` 和 `10009`** —— 判断成功别只认一个

---

## 目录

```
MetaTrader5.py          # ← 兼容层（import MetaTrader5 as mt5）
mt5ws/                  # 底层实现（也可直接用）
  client.py             #   MT5Client：账户/行情/交易/历史
  transport.py          #   WebSocket 传输（帧编解码 + 推送分发）
  crypto.py / _aes_pure.py   # AES（优先第三方，缺失则纯标准库）
  _ws_pure.py           #   纯标准库 WebSocket（RFC 6455）
  protocol.py / serial.py    # 协议常量与 MT5 序列化
  symbol_spec.py        #   品种规格解析（手数档位/合约/精度）
  endpoints.py          #   内置接入点表
  servers.py            #   servers.dat 解析（可选）
examples/               # 可运行示例
tests/                  # 单元测试（标准库 unittest）
```

---

## 测试

```bash
python3 -m unittest discover -s tests -v
```

覆盖：常量数值、接口齐全性、namedtuple 字段名、未初始化行为、AES 向量、
WebSocket 帧编解码、品种规格解析、序列化往返。

---

## 免责声明

仅用于**你自己的、有授权的**账户（如经纪商提供的 demo 账户）。
交易有风险，本项目不保证任何交易结果；实盘使用前请自行充分测试。
