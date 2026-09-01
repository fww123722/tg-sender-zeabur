FROM python:3.11-slim

# 时区：让"每日上限"按北京时间重置（默认 UTC 会早上 8 点才翻篇）
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 安装系统依赖（psycopg2-binary 需要 libpq，slim 镜像缺）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用 Docker 层缓存：代码改动不触发重装）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 只复制运行所需的源码（.dockerignore 已排除 session/密钥/venv 等）
COPY tg_sender.py .

# 数据目录（Zeabur Volume 挂载点：session 文件与日志）
RUN mkdir -p /data \
    && useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app /data

# 非 root 运行（容器安全最佳实践）
USER appuser

# 健康检查端口（Zeabur 端口探测）
ENV PORT=8080
EXPOSE 8080

# 长驻进程
CMD ["python", "tg_sender.py"]
