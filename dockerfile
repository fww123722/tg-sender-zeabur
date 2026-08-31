FROM python:3.11-slim

WORKDIR /app

# 复制依赖并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY . .

# 数据目录（配合 Zeabur Volume 挂载 /data）
RUN mkdir -p /data

# 健康检查端口（Zeabur 需要端口探测）
ENV PORT=8080

# 长驻进程启动
CMD ["python", "tg_sender.py"]
