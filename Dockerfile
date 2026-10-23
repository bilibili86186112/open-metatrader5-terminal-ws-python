# open-metatrader5-terminal-ws-python —— 交易 CLI 容器
#
# 构建：
#   docker build -t mt5ws:1.0.0 .
# 运行（一次性命令）：
#   docker run --rm -e MT5_LOGIN=... -e MT5_PASSWORD=... mt5ws:1.0.0 account
# 交互：
#   docker run --rm -it -e MT5_LOGIN=... -e MT5_PASSWORD=... mt5ws:1.0.0
#
# 说明：运行时**零第三方依赖**（AES / WebSocket 均为纯标准库实现），
# 所以镜像里不需要编译工具链，也不需要装 pycryptodome 之类。

FROM python:3.12-alpine

LABEL org.opencontainers.image.title="open-metatrader5-terminal-ws-python" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.description="MetaTrader 5 client over the broker WebSocket gateway (MetaTrader5-compatible API + trading CLI)" \
      org.opencontainers.image.licenses="Proprietary"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8

WORKDIR /app

# 先只拷打包所需的元数据，利用层缓存
COPY pyproject.toml README.md ./
COPY mt5ws ./mt5ws
COPY MetaTrader5.py mt5cli.py ./

# 安装到系统环境（不需要额外依赖）
RUN pip install --no-cache-dir . && \
    adduser -D -H -u 10001 mt5 && \
    rm -rf /root/.cache

USER 10001

# 默认入口就是交易 CLI；容器名可用任意子命令覆盖
ENTRYPOINT ["mt5cli"]
CMD ["--help"]
