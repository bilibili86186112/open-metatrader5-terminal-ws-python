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

### 1.0.0 附加：Docker 部署

* `Dockerfile`（python:3.12-alpine，非 root，入口 `mt5cli`，镜像约 76 MB）
* `docker-compose.yml`：`mt5` 一次性/交互服务 + `watch` 常驻看板（每 60 秒打印账户与持仓）
* `.dockerignore` / `.env.example`
* 修了两处：`pyproject.toml` 里 `[project.urls]` 重复导致 `pip install` 失败（构建时才发现）；
  `quote`/`kline` 未先 `symbol_select` 导致"无报价"；并为稀疏报价加了 **K 线兜底**

### 1.0.0 附加：全屏终端界面 `mt5tui`

* 纯 `curses`（标准库，零依赖）：标题栏 + 账户行 + 自选/持仓双面板 + 日志 + 命令行
* 按键：`q` 退出、`r` 刷新、`Tab` 切换、`j/k` 移动、`b/s` 买卖、`c` 平仓、`:` 命令
* 买卖与平仓**都要二次确认**；成交后自动重连刷新
* `--render` 可无 TTY 渲染一帧，便于测试
* 注册为 `mt5tui` 控制台脚本

**顺带修的性能问题**：`symbol_info_tick` 原来每个品种阻塞 8 秒等推送，
3 个品种白等 **24.6 秒**；实测 `tick x3 = 24.6s` 是启动耗时的大头。
现改为**默认不阻塞**（`wait=0`），需要等待时显式传 `wait=`；
TUI/CLI 用「最后一根 M1 收盘价」兜底。冷启动 **36s → 10s**。

### 1.0.0 附加：接入点记忆

`mt5ws/endpoints.py` 增加 `last_working()` / `remember_working()`：
把上次成功的接入点记到 `~/.cache/mt5ws/last_endpoint.json`（**只存 host:port，不含凭据**），
下次优先试它。`connect_auto` 已接入。

### 1.0.0 附加：Docker 镜像内置 TUI

* `Dockerfile`：`COPY` 补上 `mt5tui.py`（之前漏了，镜像里根本没有这个命令）；
  新增 `apk add ncurses-terminfo-base` 与 `ENV TERM=xterm-256color`
  —— Alpine 默认不带 terminfo，缺了 curses 全屏画不出来
* `docker-compose.yml`：新增 `tui` 服务（`entrypoint: mt5tui` + `stdin_open`/`tty: true`），
  与 `mt5`（命令行）、`watch`（常驻看板）并存
* 镜像 **76.2 MB**；容器内实测 `--render` 与真 TTY 全屏均正常，`q` 退出码 0

### 1.0.0 修复：WebSocket 连接不能持续

用户反馈"连接每多久就断了"。修复内容：

* **自动心跳**：`MT5Client` 登录后启动后台守护线程，每 **20 秒**发 `CMD_HEARTBEAT`
  （之前有个 `heartbeat()` 方法但**没有任何地方调用它**）
* **自动重连**：新增 `_send()` 包装，任何收发遇到 `MT5Disconnected` 就
  **重建连接 → 重新登录 → 恢复订阅 → 重试原请求**；统计在 `_reconnect_count`
* 凭据为自动重连只存在内存（`_login_args`），**不落盘**
* 心跳间隔 `heartbeat_interval`（默认 20s，0 关闭）、自动重连 `auto_reconnect`（默认开）可调

实测：带心跳 6 分钟 0 掉线；**强制掐断连接后下一次查询自动恢复**（重连计数 0→1）。
空转 5 分钟未断、高频查询 40 次未失败 —— 说明单纯空闲/高频都不是主因，
因此把"断了能自愈"作为主要防线。
