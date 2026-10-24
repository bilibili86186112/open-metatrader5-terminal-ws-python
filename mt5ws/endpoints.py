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

__all__ = ["KNOWN_ENDPOINTS", "endpoints_for", "DEFAULT_SERVER",
           "last_working", "remember_working"]

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


# ---------------------------------------------------------------- 记住可用接入点
#
# 探测每个接入点要几秒，6 个候选最多能拖到 30 秒。这里把**上次成功**的那个记下来，
# 下次优先试它 —— 冷启动 ~30s → 热启动 ~3s。只存 host:port，**不存任何凭据**。
import json as _json
import os as _os


def _cache_path() -> str:
    d = _os.environ.get("MT5WS_CACHE_DIR") or _os.path.join(
        _os.path.expanduser("~"), ".cache", "mt5ws")
    return _os.path.join(d, "last_endpoint.json")


def last_working(server: str):
    """读"上次成功"的接入点；没有/读不出返回 None。"""
    try:
        with open(_cache_path(), "r", encoding="utf-8") as fh:
            data = _json.load(fh)
        ep = data.get(server)
        return ep if isinstance(ep, str) and ":" in ep else None
    except Exception:                                     # noqa: BLE001
        return None


def remember_working(server: str, endpoint: str) -> None:
    """记下成功的接入点（失败静默 —— 这只是加速，不该影响主流程）。"""
    try:
        path = _cache_path()
        _os.makedirs(_os.path.dirname(path), exist_ok=True)
        data = {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = _json.load(fh)
        except Exception:                                 # noqa: BLE001
            data = {}
        data[server] = endpoint
        with open(path, "w", encoding="utf-8") as fh:
            _json.dump(data, fh)
    except Exception:                                     # noqa: BLE001
        pass
