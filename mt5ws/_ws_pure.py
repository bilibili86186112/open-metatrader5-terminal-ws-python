"""纯标准库 WebSocket 客户端（RFC 6455），作为 ``websocket-client`` 的替代。

只实现本项目用得到的部分：

* 客户端握手（HTTP/1.1 Upgrade，``Sec-WebSocket-Key`` / ``Sec-WebSocket-Version: 13``）
* 二进制/文本收发（客户端帧**必须掩码**，服务端帧不掩码）
* 分片重组（FIN 位）
* ping → 自动回 pong；close → 回 close 并抛异常
* TLS（``wss://``）走标准库 :mod:`ssl`

对外接口刻意与 ``websocket-client`` 的几个常用方法同名
（``send_binary`` / ``recv`` / ``settimeout`` / ``close``），
这样 :mod:`mt5api.transport` 可以直接二选一替换。
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
from urllib.parse import urlsplit

__all__ = ["create_connection", "WebSocketError", "LocalProtocolError"]

_OP_CONT = 0x0
_OP_TEXT = 0x1
_OP_BIN = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketError(Exception):
    """连接/协议错误。"""


class LocalProtocolError(WebSocketError):
    """本地调用不符合协议。"""


class WebSocket:
    """极简 WebSocket 客户端。"""

    def __init__(self, sock: socket.socket, recv_timeout: float = 10.0) -> None:
        self._sock = sock
        self.recv_timeout = recv_timeout
        self._buf = bytearray()

    # ---------------------------------------------------------- 发送
    def send_binary(self, data: bytes) -> None:
        self._send_frame(_OP_BIN, data)

    def send(self, data: bytes | str) -> None:
        if isinstance(data, str):
            self._send_frame(_OP_TEXT, data.encode("utf-8"))
        else:
            self._send_frame(_OP_BIN, data)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if len(payload) > 0xFFFFFFFF:
            raise LocalProtocolError("payload 过大")
        header = bytearray()
        header.append(0x80 | opcode)                 # FIN + opcode
        mask_bit = 0x80                              # 客户端必须掩码
        n = len(payload)
        if n < 126:
            header.append(mask_bit | n)
        elif n < 65536:
            header.append(mask_bit | 126)
            header += struct.pack(">H", n)
        else:
            header.append(mask_bit | 127)
            header += struct.pack(">Q", n)
        key = os.urandom(4)
        header += key
        masked = bytes(b ^ key[i & 3] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    # ---------------------------------------------------------- 接收
    def _fill(self, n: int) -> None:
        while len(self._buf) < n:
            chunk = self._sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise WebSocketError("连接已关闭")
            self._buf += chunk

    def _read_frame(self) -> tuple[bool, int, bytes]:
        """读一帧，返回 ``(fin, opcode, payload)``。"""
        self._fill(2)
        b0, b1 = self._buf[0], self._buf[1]
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        off = 2
        if length == 126:
            self._fill(off + 2)
            length = struct.unpack_from(">H", self._buf, off)[0]
            off += 2
        elif length == 127:
            self._fill(off + 8)
            length = struct.unpack_from(">Q", self._buf, off)[0]
            off += 8
        key = b""
        if masked:
            self._fill(off + 4)
            key = bytes(self._buf[off:off + 4])
            off += 4
        self._fill(off + length)
        payload = bytes(self._buf[off:off + length])
        del self._buf[:off + length]
        if masked:
            payload = bytes(b ^ key[i & 3] for i, b in enumerate(payload))
        return fin, opcode, payload

    def recv(self) -> bytes:
        """收一条**完整**消息（自动重组分片、自动回 pong）。"""
        parts: list[bytes] = []
        first_op: int | None = None
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == _OP_PING:
                self._send_frame(_OP_PONG, payload)
                continue
            if opcode == _OP_PONG:
                continue
            if opcode == _OP_CLOSE:
                try:
                    self._send_frame(_OP_CLOSE, payload[:2])
                except Exception:                    # noqa: BLE001
                    pass
                raise WebSocketError("服务端关闭连接")
            if opcode in (_OP_TEXT, _OP_BIN):
                if first_op is None:
                    first_op = opcode
                parts.append(payload)
            elif opcode == _OP_CONT:
                if first_op is None:
                    raise WebSocketError("收到孤立的分片")
                parts.append(payload)
            else:
                raise WebSocketError(f"未知 opcode {opcode}")
            if fin and first_op is not None:
                return b"".join(parts)

    def recv_text(self) -> str:
        return self.recv().decode("utf-8", "replace")

    # ---------------------------------------------------------- 杂项
    def settimeout(self, timeout: float | None) -> None:
        self.recv_timeout = timeout if timeout is not None else 10.0
        self._sock.settimeout(timeout)

    def ping(self, data: bytes = b"") -> None:
        self._send_frame(_OP_PING, data)

    def close(self) -> None:
        try:
            self._send_frame(_OP_CLOSE, b"\x03\xe8")  # 1000 normal
        except Exception:                            # noqa: BLE001
            pass
        try:
            self._sock.close()
        except Exception:                            # noqa: BLE001
            pass


def create_connection(url: str, *, origin: str | None = None,
                      timeout: float = 10.0,
                      header: dict[str, str] | None = None) -> WebSocket:
    """建立 WebSocket 连接（``ws://`` / ``wss://``）。"""
    parts = urlsplit(url)
    if parts.scheme not in ("ws", "wss"):
        raise LocalProtocolError(f"不支持的 scheme: {parts.scheme}")
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "wss" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query

    sock = socket.create_connection((host, port), timeout=timeout)
    if parts.scheme == "wss":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(sock, server_hostname=host)

    key = base64.b64encode(os.urandom(16)).decode()
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
        "User-Agent: mt5api/1.0 (python-stdlib)",
    ]
    if origin:
        lines.append(f"Origin: {origin}")
    for k, v in (header or {}).items():
        lines.append(f"{k}: {v}")
    req = ("\r\n".join(lines) + "\r\n\r\n").encode()
    sock.sendall(req)

    # 读响应头
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise WebSocketError("握手时连接被关闭")
        buf += chunk
        if len(buf) > 65536:
            raise WebSocketError("握手响应过大")
    head, _, rest = bytes(buf).partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("latin-1")
    if "101" not in status_line:
        raise WebSocketError(f"握手失败：{status_line}")
    accept_expect = base64.b64encode(
        hashlib.sha1((key + _GUID).encode()).digest()).decode()
    headers = {}
    for line in head.split(b"\r\n")[1:]:
        if b":" in line:
            k, v = line.split(b":", 1)
            headers[k.strip().lower().decode()] = v.strip().decode()
    if headers.get("sec-websocket-accept") != accept_expect:
        raise WebSocketError("Sec-WebSocket-Accept 校验失败")

    ws = WebSocket(sock, recv_timeout=timeout)
    if rest:                       # 握手响应后面可能已经带了数据帧
        ws._buf += rest
    return ws
