"""内置的接入点表 —— 让本包**不依赖本地 MT5 安装**也能连上。

官方 ``MetaTrader5`` 包靠本机终端工作；本包直接连**券商的 Web 终端网关**，
所以需要知道「服务器名 → 接入点(host:port)」。来源优先级：

1. 调用方显式传入 ``endpoints=["host:port", ...]``
2. 本文件的内置表 ``KNOWN_ENDPOINTS``
3. 本机若有 MT5 的 ``servers.dat``，从里面解析（``servers_dat=`` 指定路径）

⚠️ **注意区分两类接入点**（实测）：
* **Web 终端网关**（本包用）—— 形如 ``18.163.85.196:443``、``54.251.125.75:443``，
  握手后走 ``wss://host:443/terminal``
* **原生 TCP 网关**（MT5 桌面终端用）—— 形如 ``203.29.60.248:443``，
  是 TLS + 自有二进制协议，**本包不用**

两者不能混用。若内置表失效，请用 ``endpoints=`` 显式指定，或提供 ``servers.dat``。
"""

from __future__ import annotations

__all__ = ["KNOWN_ENDPOINTS", "endpoints_for", "DEFAULT_SERVER"]

#: 默认服务器名
DEFAULT_SERVER = "Exness-MT5Trial5"

#: 实测可用的 **Web 终端网关**（2026-10 在本机验证，探测通过）
KNOWN_ENDPOINTS: dict[str, list[str]] = {
    "Exness-MT5Trial5": [
        "18.163.85.196:443",
        "54.251.125.75:443",
        "35.79.6.111:443",
        "43.200.19.88:443",
        "3.34.111.183:443",
        "8.219.152.73:443",
    ],
    # 同券商的其它服务器共用同一批网关，按需补充
    "Exness-MT5Real8": [
        "18.163.85.196:443",
        "54.251.125.75:443",
        "35.79.6.111:443",
    ],
}


def endpoints_for(server: str) -> list[str]:
    """按服务器名取接入点；找不到返回空列表（调用方再走 servers.dat 兜底）。"""
    return list(KNOWN_ENDPOINTS.get(server, []))
