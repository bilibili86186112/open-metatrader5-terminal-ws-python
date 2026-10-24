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

---

## 交易命令行 `mt5cli`

装好后直接 `mt5cli`；或在本仓库根目录 `python3 mt5cli.py`。

```bash
export MT5_LOGIN=123456 MT5_PASSWORD='...' MT5_SERVER=Exness-MT5Trial5

# 只读
mt5cli account                 # 账户：余额/净值/保证金/杠杆
mt5cli symbols GOLD            # 品种搜索
mt5cli quote XAUUSDm           # 报价
mt5cli kline XAUUSDm --tf M5 --count 20
mt5cli positions [SYMBOL]      # 持仓（表格 + 浮动盈亏合计）
mt5cli orders                  # 挂单
mt5cli history --days 7 --symbol GBPUSDm

# 交易（**必须** `-y` 或用交互模式按 y 确认）
mt5cli buy  XAUUSDm 0.01 --sl 3950 --tp 4100 -y
mt5cli sell EURUSDm 0.01 -y
mt5cli close GBPUSDm -y         # 平某品种全部
mt5cli close --all -y           # 平所有（危险）
mt5cli modify 2740800934 GBPUSDm --sl 1.3300 --tp 1.3500 -y

# 交互模式
mt5cli                          # 进入 mt5> REPL，输入 help 看命令
```

### 安全设计

* **默认不下单** —— 交易类命令不加 `-y`、且在非交互环境（stdin 为空）时会**自动取消**
* 下单前先跑 `order_check`（可交易性 / 手数规整 / 保证金 / SL-TP 方向），
  不通过直接拒绝并说明原因
* 下单成功后**自动重连复核**并在持仓里确认（因为同会话快照会滞后）
* 成功判定用 `retcode ∈ {0, 10009}`；其余码翻译成中文说明

实测输出示例（真实账户）：

```
mt5> buy XAUUSDm 0.01 --sl 3950
即将 BUY  XAUUSDm  0.01 手 @ 3997.15  (sl=3950.0 tp=- dev=20 点)
校验通过：预估保证金 2.07，可用 12427.35
确认执行？[y/N] y
结果：retcode=0 (成功)  284 ms
已确认为持仓：#2740800934 XAUUSDm 0.01 手 @ 3997.15
```

---

## Docker 部署

镜像 **75.9 MB**（`python:3.12-alpine` + 纯标准库，无需编译工具链）。

```bash
# 1) 构建
docker build -t mt5ws:1.0.0 .

# 2) 一次性执行
docker run --rm -e MT5_LOGIN=123456 -e MT5_PASSWORD='...' \
              -e MT5_SERVER=Exness-MT5Trial5 mt5ws:1.0.0 account
docker run --rm -e MT5_LOGIN=... -e MT5_PASSWORD=... mt5ws:1.0.0 quote XAUUSDm
docker run --rm -e MT5_LOGIN=... -e MT5_PASSWORD=... mt5ws:1.0.0 positions

# 3) 交互 REPL
docker run --rm -it -e MT5_LOGIN=... -e MT5_PASSWORD=... mt5ws:1.0.0

# 4) 真下单（注意 -y）
docker run --rm -e MT5_LOGIN=... -e MT5_PASSWORD=... mt5ws:1.0.0 \
           buy XAUUSDm 0.01 --sl 3950 -y
```

### docker compose

```bash
cp .env.example .env        # 填入账号密码（.env 已在 .gitignore 里）
docker compose run --rm mt5 account            # 一次性
docker compose run --rm -it mt5                # 交互
docker compose up watch                        # 常驻看板：每 60 秒打印账户+持仓
```

### 容器设计要点

| 项 | 做法 |
|---|---|
| 基础镜像 | `python:3.12-alpine`（约 76 MB，无编译工具链）|
| 依赖 | **零第三方依赖**，`pip install .` 即可 |
| 用户 | **非 root**（uid 10001）|
| 入口 | `ENTRYPOINT ["mt5cli"]`，容器名后直接跟子命令 |
| 凭据 | 只用环境变量，**不写进镜像**（`.env` 已 gitignore）|
| 时区/编码 | `LANG=C.UTF-8`、`PYTHONUNBUFFERED=1` |

### 实测（容器内，真实账户）

```
$ docker run --rm -e MT5_LOGIN=... -e MT5_PASSWORD=... mt5ws:1.0.0 account
连接 277985376 @ Exness-MT5Trial5 …
已连接：277985376 | 余额 10,018.63 USD | 杠杆 1:2000
余额    : 10,018.63
净值    : 12,441.54

$ docker run --rm ... mt5ws:1.0.0 quote GBPUSDm
GBPUSDm      bid=1.3372  ask=1.3373  点差=0.00010  digits=5
```

> 注：`quote` 若在冷启动时等不到 tick 推送（有些品种报价稀疏），会**自动退回最后一根 M1 收盘价**并在输出里注明。

---

## 全屏终端界面 `mt5tui`

纯 `curses`（标准库），零依赖。装好后 `mt5tui`，或在本目录 `python3 mt5tui.py`。

```bash
export MT5_LOGIN=... MT5_PASSWORD=... MT5_SERVER=Exness-MT5Trial5

mt5tui                                  # 全屏界面
mt5tui --watch XAUUSDm,GBPUSDm --interval 2 --volume 0.02
mt5tui --render                         # 只渲染一帧到 stdout（无 TTY 也能看布局/做测试）
```

界面：

```
 MT5 TUI │ 2026-10-24 04:20:05 UTC
账户 277985376  Exness-MT5Trial5   余额 10,018.63 USD   净值 12,431.28   可用 12,431.28   杠杆 1:2000
┌ 自选 (3) ─────────────────────┐┌ 持仓 (73) ────────────────────────────────┐
│>XAUUSDm      3996.057  3996.057 ││>#2689674685 BTCUSDm BUY  0.01 80745.98 +0.29 │
│ EURUSDm         1.12023   1.12023││ #2689997127 EURUSDm BUY  0.01  1.12102 -0.79 │
│ GBPUSDm         1.33740   1.33740││ #2673683035 XAUUSDm BUY  0.01 4191.989 -195.93│
└──────────────────────────────┘└──────────────────────────────────────────┘
┌ 日志 ────────────────────────────────────────────────────────────────────┐
│ 04:20:05 已连接 277985376 @ Exness-MT5Trial5                              │
└──────────────────────────────────────────────────────────────────────────┘
 q退出  r刷新  Tab切换  j/k移动  b买 s卖 c平仓  :命令
```

### 按键

| 键 | 作用 |
|---|---|
| `q` | 退出 |
| `r` | 立即刷新（含账户与持仓）|
| `Tab` / `h` / `l` | 左右面板切换 |
| `j` / `k` / `↑` / `↓` | 上下移动选中项 |
| `b` / `s` | 买入 / 卖出选中品种（**底部 y/N 二次确认**）|
| `c` | 平掉选中持仓（**二次确认**）|
| `:` 或 `/` | 命令行：`buy SYM [VOL]`、`sell SYM [VOL]`、`close TICKET`、`add SYM`、`del SYM`、`refresh` |

### 设计要点

* **每次下单都要二次确认**，误触不会成交
* 交易成功后**自动重连刷新**（同会话持仓快照会滞后）
* 报价**等不到推送就用最后一根 M1 收盘价兜底**（很多品种推送很稀疏）
* `--render` 模式可无 TTY 渲染一帧，便于测试与截图

### 实测

| 项 | 结果 |
|---|---|
| curses 全屏（pty 驱动）| 标题/账户/双面板/日志均正常渲染，`q` 退出码 0 |
| `--render` 一帧 | 账户 + 3 品种报价 + 73 笔持仓（含浮动盈亏）|
| 启动耗时 | **约 10 秒**（连接 4.2s + 订阅 1.2s + 其余查询）|

> 启动变慢的原因曾定位到 `symbol_info_tick` 每个品种阻塞 8 秒等推送（3 个品种白等 **24.6 秒**）。
> 现已改为**默认不阻塞**，需要等待时显式传 `wait=`；TUI/CLI 用 K 线兜底。
> 冷启动 36s → 10s（约 3.3×）。

### Docker 里跑 TUI

镜像里**已经包含 `mt5tui`**（`Dockerfile` 里 `COPY ... mt5tui.py`），
并且装了 `ncurses-terminfo-base`（Alpine 默认没有 terminfo，curses 全屏会画不出来）。

```bash
cp .env.example .env        # 填账号密码
docker compose run --rm tui                                  # 全屏 TUI（推荐）
docker compose run --rm tui --watch XAUUSDm,GBPUSDm --interval 2

# 不用 compose：
docker run --rm -it --env-file .env --entrypoint mt5tui mt5ws:1.0.0
docker run --rm --env-file .env --entrypoint mt5tui mt5ws:1.0.0 --render   # 无 TTY 看一帧
```

`docker-compose.yml` 现在有三个服务：

| 服务 | 用途 | 要 TTY |
|---|---|---|
| `tui` | 全屏终端界面 | **是**（`stdin_open` + `tty`）|
| `mt5` | 命令行（一次性 / 交互 REPL）| 交互时需要 |
| `watch` | 常驻看板（每 60 秒打印账户+持仓）| 否 |

#### 容器内实测（2026-10-24）

| 项 | 结果 |
|---|---|
| 镜像大小 | **76.2 MB** |
| `--render` 一帧 | 账户 + 3 品种报价 + 73 笔持仓（含浮动盈亏）✅ |
| 真 TTY（`script` 分配 pty）| 标题/账户/自选/持仓/按键提示**全部渲染**，`q` 退出码 **0** ✅ |
| `docker compose run --rm tui` | 同样正常，退出码 **0**，无残留容器 ✅ |
