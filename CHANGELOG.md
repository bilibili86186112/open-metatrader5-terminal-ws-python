# 变更记录

## 1.0.0 — 2026-10-24

首个独立版本。从逆向项目里把 **WebSocket 通道**的 API 单独抽出来，去掉全部原生 TCP /
逆向分析相关代码，只保留"连券商网关、下单、取行情"这条完整可用的链路。

### 提供的接口（与官方 `MetaTrader5` 包兼容）

`initialize` / `login` / `shutdown` / `last_error` / `version` / `terminal_info`、
`account_info`、`symbols_get` / `symbol_info` / `symbol_info_tick` / `symbol_select`、
`copy_rates_from_pos` / `copy_rates_from` / `copy_rates_range`、
`positions_get` / `orders_get` / `history_deals_get` / `history_orders_get`、
`order_send` / `order_check`。
常量全部按官方数值（`TIMEFRAME_*` / `ORDER_TYPE_*` / `ORDER_FILLING_*` / `TRADE_ACTION_*` / `TRADE_RETCODE_*`）。

### 已实测确认（Exness demo，2026-10-24）

* 开/平仓往返 **≈280 ms**（232~321 ms）
* 服务器记录成交时间与本地发起**同秒**
* `symbol_info_tick`（已订阅）**≈0 ms**（本地缓存）
* **成功有两种 `retcode`：`0` 和 `10009`**
* 同会话 `positions_get()` 可能返回**旧快照**（交易后核实需重连）
* 响应**不回传** `deal`/`order` 号，需 `history_deals_get()` 反查

### 内置接入点

`mt5ws/endpoints.py` 带一份实测可用的 **Web 终端网关**表（Exness）。
⚠️ 注意与**原生 TCP 网关**（`203.29.60.x`）区分 —— 两者不通用。

### 1.0.0 附加：交易命令行 `mt5cli`

* 只读：`account` / `symbols` / `quote` / `kline` / `positions` / `orders` / `history`
* 交易：`buy` / `sell` / `close` / `modify` —— **必须 `-y` 或用交互确认**，否则只做 dry-run
* 交互 REPL（不传子命令时）
* 下单前自动跑 `order_check`；下单后**自动重连复核**持仓
* 安装后可直接 `mt5cli`；`[project.scripts]` 已注册

实测（Exness demo）：`buy XAUUSDm 0.01` → `retcode=0` 294ms → 自动确认为持仓 →
`close` → `retcode=0` → 持仓归零。
