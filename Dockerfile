# open-metatrader5-terminal-ws-python —— 交易 CLI + TUI 容器
#
# 构建：
#   docker build -t mt5ws:1.0.0 .
#
# 全屏 TUI（需要 TTY）：
#   docker run --rm -it -e MT5_LOGIN=... -e MT5_PASSWORD=... \
#              --entrypoint mt5tui mt5ws:1.0.0
#
# 命令行（一次性 / 交互 REPL）：
#   docker run --rm -e MT5_LOGIN=... -e MT5_PASSWORD=... mt5ws:1.0.0 account
#   docker run --rm -it -e MT5_LOGIN=... -e MT5_PASSWORD=... mt5ws:1.0.0
#
# 说明：运行时**零第三方依赖**（AES / WebSocket 均为纯标准库实现），
# 所以镜像里不需要编译工具链。ncurses-terminfo-base 是为了 curses 全屏界面。

FROM python:3.12-alpine

LABEL org.opencontainers.image.title="open-metatrader5-terminal-ws-python" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.description="MetaTrader 5 client over the broker WebSocket gateway (MetaTrader5-compatible API + trading CLI + full-screen TUI)" \
      org.opencontainers.image.licenses="Proprietary"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    TERM=xterm-256color

# curses 全屏界面需要 terminfo 数据库（Alpine 默认不带）
RUN apk add --no-cache ncurses-terminfo-base

WORKDIR /app

COPY pyproject.toml README.md ./
COPY mt5ws ./mt5ws
COPY MetaTrader5.py mt5cli.py mt5tui.py ./

RUN pip install --no-cache-dir . && \
    adduser -D -H -u 10001 mt5 && \
    rm -rf /root/.cache

USER 10001

# 默认入口是命令行；要全屏 TUI 用 --entrypoint mt5tui（或 compose 的 tui 服务）
ENTRYPOINT ["mt5cli"]
CMD ["--help"]
