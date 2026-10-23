"""mt5api —— 纯 Python 的 MetaTrader 5 客户端。

入口 / entry points::

    from mt5api import MT5Client
    with MT5Client.connect("wss://<broker>:443/terminal", origin="https://<broker>:443") as c:
        c.login(login=12345678, password="...", server="Exness-MT5Trial17")
        print(c.account_info())
        print(c.copy_rates("XAUUSDm", "M5", count=10))

协议来源与通道说明
------------------

MetaTrader 5 的同一个接入点（如 `15.206.31.153:443`）**同时**提供两套服务：

============ ============================================ ==================
通道          传输                                          谁在用
============ ============================================ ==================
Web 终端网关  TLS + WebSocket `wss://<ip>:443/terminal`     **浏览器**的 Web 终端
原生二进制     裸 TCP `:443`（应用层自己加密）                **桌面终端 / 安卓 App**
============ ============================================ ==================

**本库走 Web 终端网关（wss）** —— 它的协议是用 CDP 抓**真实浏览器 Web 终端**的
报文分析出来的（原始采样见 `../../日志/参考/`）。

两条通道的**报文层是共通的**（命令号、序列化格式、记录结构都一样），
差别只在「怎么换会话密钥」和「怎么切帧」，所以本库对三类客户端在语义上等价。

安卓 App 的 `libcommon.so` 里 **没有任何 WebSocket 代码**（`wss://` / `WebSocket` /
`HTTP/1.1` 出现次数均为 0），可见手机端走的是原生 TCP —— 那条通道的分析见
`native_client.py` 与 `../../产出/原生通道协议规格.md`。

细节见 `../../日志/`。
"""

from .client import MT5Client, MT5LoginError, MT5TradeError  # noqa: F401
from .transport import MT5Disconnected, MT5Error  # noqa: F401

__all__ = ["MT5Client", "MT5Error", "MT5LoginError", "MT5TradeError",
           "MT5Disconnected"]
__version__ = "0.1.0"
