"""MT5 传输层 / MT5 transports.

MT5 的「报文」在两种通道上是同一套内容：

* **Web 终端通道** —— `wss://<broker>:443/terminal`（本文件已实现，用于打通全流程）
* **原生 TCP 通道** —— 安卓/桌面终端直连 broker 的 access server（见
  `tcp_transport.py`，从 `libcommon.so` 分析）

两者的差别只在「握手怎么换会话密钥」和「怎么切包」，之后都是
`[16 字节小块] + 命令号 + 报文体`，用同一把会话密钥做 AES-256-CBC。
"""

from __future__ import annotations

import queue
import ssl
import struct
import threading
import time
from typing import Callable, Optional

from . import crypto, protocol

FRAME_PREFIX = 8  # payload_length(4) + version(4)


class MT5Error(RuntimeError):
    pass


class MT5Disconnected(MT5Error):
    pass


class Frame:
    """一帧解密后的内容。"""

    __slots__ = ("tag", "cmd", "res_code", "body", "raw", "at")

    def __init__(self, tag: int, cmd: int, res_code: int, body: bytes, raw: bytes):
        self.tag = tag
        self.cmd = cmd
        self.res_code = res_code
        self.body = body
        self.raw = raw
        self.at = time.time()

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (f"<Frame tag={self.tag} cmd={self.cmd} res={self.res_code} "
                f"body={len(self.body)}B>")


class BaseTransport:
    """请求/响应 + 服务器推送 的通用逻辑。"""

    def __init__(self, *, debug: bool = False) -> None:
        self.session_key: Optional[bytes] = None
        self.debug = debug
        self._pending: dict[int, queue.Queue] = {}
        self._lock = threading.Lock()
        self._pushes: queue.Queue = queue.Queue()
        self._push_handlers: list[Callable[[Frame], None]] = []
        self._closed = False
        self._reader: Optional[threading.Thread] = None
        self.frames_seen: list[Frame] = []

    # ---------------- 子类要实现的部分 ----------------
    def _send_frame(self, data: bytes) -> None:
        raise NotImplementedError

    def _recv_frame_bytes(self, timeout: float) -> Optional[bytes]:
        raise NotImplementedError

    def close(self) -> None:
        self._closed = True

    # ---------------- 握手 ----------------
    def handshake(self) -> bytes:
        """发 64 字节握手包，取回会话密钥。"""
        payload = crypto.random_bytes(64)
        self._send_frame(self.encode(protocol.CMD_AUTH, payload, key=crypto.AUTH_KEY))
        raw = self._recv_frame_bytes(15.0)
        if raw is None:
            raise MT5Error("握手失败：服务器未响应 / handshake: no reply")
        plain = self.decode_plain(raw, key=crypto.AUTH_KEY)
        if self.debug:
            print(f"[handshake] cipher={len(raw)}B plain={len(plain)}B")
            print(f"[handshake] plain hex: {plain.hex()}")
        if len(plain) < 5:
            raise MT5Error(f"握手响应过短 / short handshake: {plain.hex()}")
        tag, cmd, res_code = struct.unpack_from("<HHB", plain, 0)
        if self.debug:
            print(f"[handshake] tag={tag} cmd={cmd} res={res_code}")
        if res_code != 0:
            raise MT5Error(f"握手被拒 / handshake refused: res_code={res_code}")
        if len(plain) < 5 + 32:
            raise MT5Error(f"握手响应过短 / short handshake body: {plain.hex()}")
        # 会话密钥 = 响应明文最后 32 字节（实测 Exness 网关 build 6230）
        self.session_key = plain[-32:]
        self.handshake_plain = plain
        self._start_reader()
        return self.session_key

    # ---------------- 组帧 / 解帧 ----------------
    def encode(self, cmd: int, payload: bytes = b"", *, key: Optional[bytes] = None,
               header: str = "short") -> bytes:
        """组帧。

        ``header="short"``（默认，实测 MT5 请求用的就是 4 字节头）::

            [rnd:1][rnd:1][cmd:uint16 LE][payload]

        ``header="tagged"``（5 字节头，带 tag，备用）::

            [tag:uint16][cmd:uint16][payload]
        """
        key = key or self.session_key
        if key is None:
            raise MT5Error("尚未握手，没有会话密钥 / no session key yet")
        rnd = crypto.random_bytes(2)
        if header == "short":
            inner = rnd + struct.pack("<H", cmd & 0xFFFF) + payload
        elif header == "tagged":
            inner = struct.pack("<HH", rnd[0] | (rnd[1] << 8), cmd & 0xFFFF) + payload
        else:
            raise ValueError(header)
        ct = crypto.encrypt(key, inner)
        return struct.pack("<II", len(ct), 1) + ct

    def strip_prefix(self, raw: bytes) -> bytes:
        """去掉 8 字节帧头。

        实测：客户端发出的帧版本号写 1，而**服务器回的帧版本号是 0**
        （Exness 网关 build 6230），所以这里不能死认 version==1，
        只校验「长度字段自洽 + 密文按 16 字节对齐」。
        """
        if len(raw) >= FRAME_PREFIX:
            length, _version = struct.unpack_from("<II", raw, 0)
            if length == len(raw) - FRAME_PREFIX and length % 16 == 0:
                return raw[FRAME_PREFIX:]
        if len(raw) % 16 == 0:
            return raw
        # 兜底：按 8 字节头切开，让 AES 层报出更有意义的错误
        return raw[FRAME_PREFIX:] if len(raw) > FRAME_PREFIX else raw

    def decode_plain(self, raw: bytes, *, key: Optional[bytes] = None) -> bytes:
        """去掉帧头并解密，返回明文的原始字节（不解释头部）。"""
        key = key or self.session_key
        if key is None:
            raise MT5Error("尚未握手，没有会话密钥 / no session key yet")
        return crypto.decrypt(key, self.strip_prefix(raw))

    def decode(self, raw: bytes, *, key: Optional[bytes] = None):
        """解出一帧：返回 ``(tag, cmd, res_code, body)``。

        响应头 5 字节（实测）::

            [tag:uint16][cmd:uint16][res_code:uint8][body]
        """
        plain = self.decode_plain(raw, key=key)
        if len(plain) < 4:
            raise MT5Error(f"报文过短 / frame too short: {plain.hex()}")
        tag, cmd, res_code = struct.unpack_from("<HHB", plain, 0)
        body = plain[5:]
        return tag, cmd, res_code, body

    # ---------------- 收发 ----------------
    def send(self, cmd: int, payload: bytes = b"", *, wait: bool = True,
             timeout: float = 20.0, header: str = "short") -> Frame:
        if self._closed:
            raise MT5Disconnected("连接已关闭 / transport closed")
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._pending[cmd] = q
        try:
            self._send_frame(self.encode(cmd, payload, header=header))
            if not wait:
                return Frame(tag, cmd, 0, b"", b"")
            try:
                frame = q.get(timeout=timeout)
            except queue.Empty:
                raise MT5Error(f"命令 {cmd} 等待响应超时 / timeout waiting for cmd {cmd}")
            if isinstance(frame, Exception):
                raise frame
            return frame
        finally:
            with self._lock:
                self._pending.pop(cmd, None)

    def _start_reader(self) -> None:
        self._reader = threading.Thread(target=self._read_loop, name="mt5-reader",
                                        daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        while not self._closed:
            try:
                raw = self._recv_frame_bytes(1.0)
            except MT5Disconnected:
                self._dispatch_error(MT5Disconnected("连接断开 / disconnected"))
                return
            except Exception as exc:  # noqa: BLE001 - 网络层任何异常都当断开
                if self._closed:
                    return
                self._dispatch_error(exc)
                return
            if raw is None:
                continue
            try:
                tag, cmd, res_code, body = self.decode(raw)
            except Exception:  # noqa: BLE001
                if self.debug:
                    print(f"[reader] 无法解帧 / undecodable frame {len(raw)}B")
                continue
            frame = Frame(tag, cmd, res_code, body, raw)
            self.frames_seen.append(frame)
            if self.debug:
                print(f"[frame] {frame}")
            with self._lock:
                q = self._pending.get(cmd)
            if q is not None:
                q.put(frame)
            else:
                self._pushes.put(frame)
                for handler in list(self._push_handlers):
                    try:
                        handler(frame)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[push handler] {exc!r}")

    def _dispatch_error(self, exc: Exception) -> None:
        with self._lock:
            pend = list(self._pending.values())
        for q in pend:
            q.put(exc)

    # ---------------- 推送 ----------------
    def add_push_handler(self, fn: Callable[[Frame], None]) -> None:
        self._push_handlers.append(fn)

    def poll_push(self, timeout: float = 0.0) -> Optional[Frame]:
        try:
            return self._pushes.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain_pushes(self, seconds: float = 1.0) -> list[Frame]:
        out: list[Frame] = []
        end = time.time() + seconds
        while time.time() < end:
            f = self.poll_push(timeout=max(0.01, end - time.time()))
            if f is None:
                break
            out.append(f)
        return out


class WebSocketTransport(BaseTransport):
    """MT5 Web 终端通道 `wss://<host>:443/terminal`。

    MT5 的 Web 终端是官方终端引擎的 Web 版，连的就是 broker 的「web terminal
    gateway」，报文体与原生终端一致（命令号、序列化方式都相同），只是外面套了
    WebSocket + TLS，并且握手用固定密钥而不是 RSA。
    """

    def __init__(self, url: str, *, origin: Optional[str] = None,
                 verify_tls: bool = False, debug: bool = False,
                 recv_timeout: float = 1.0) -> None:
        super().__init__(debug=debug)
        self.url = url
        self.origin = origin
        self.verify_tls = verify_tls
        self.recv_timeout = recv_timeout
        self.ws = None
        self.host = self._parse_host(url)

    @staticmethod
    def _parse_host(url: str) -> Optional[str]:
        from urllib.parse import urlparse
        try:
            return urlparse(url).hostname
        except Exception:  # noqa: BLE001
            return None

    def connect(self, timeout: float = 15.0) -> "WebSocketTransport":
        # 两种后端：优先 websocket-client，缺失则用纯标准库实现（mt5api._ws_pure）
        self.ws = self._open_websocket(timeout)
        self.ws.settimeout(self.recv_timeout)
        self.handshake()
        return self

    def _open_websocket(self, timeout: float):
        try:
            import websocket  # websocket-client
        except ImportError:
            from . import _ws_pure
            return _ws_pure.create_connection(
                self.url, origin=self.origin or None, timeout=timeout)

        sslopt = {"cert_reqs": ssl.CERT_REQUIRED if self.verify_tls else ssl.CERT_NONE,
                  "check_hostname": self.verify_tls}
        header = []
        if self.origin:
            header.append(f"Origin: {self.origin}")
        return websocket.create_connection(
            self.url, timeout=timeout, sslopt=sslopt,
            header=header or None,
            enable_multithread=True,
        )

    def _send_frame(self, data: bytes) -> None:
        if self.ws is None:
            raise MT5Disconnected("尚未连接 / not connected")
        self.ws.send_binary(data)

    def _recv_frame_bytes(self, timeout: float) -> Optional[bytes]:
        if self.ws is None:
            raise MT5Disconnected("尚未连接 / not connected")
        self.ws.settimeout(timeout)
        try:
            data = self.ws.recv()
        except Exception as exc:
            msg = str(exc)
            if "timed out" in msg or "timeout" in msg.lower():
                return None
            raise MT5Disconnected(msg) from exc
        if isinstance(data, str):
            return data.encode("latin-1")
        return data

    def close(self) -> None:
        self._closed = True
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:  # noqa: BLE001
            pass
        self.ws = None
