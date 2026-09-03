#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置 / 全局状态 / 日志 / 常量。其余模块都依赖本模块。"""
import asyncio
import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler

# =====================================================================
#  配置：优先环境变量（Zeabur）
# =====================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


API_ID = _env_int("API_ID", 0)
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = _env_int("OWNER_ID", 0)
DATABASE_URL = os.environ.get("DATABASE_URL", "")

MIN_DELAY = _env_int("MIN_DELAY", 20)
MAX_DELAY = _env_int("MAX_DELAY", 60)
DAILY_LIMIT = _env_int("DAILY_LIMIT", 100)   # 每个账号每日上限
BATCH_SLEEP = _env_int("BATCH_SLEEP", 300)
BATCH_SIZE = _env_int("BATCH_SIZE", 30)
MAX_FLOOD_WAIT = _env_int("MAX_FLOOD_WAIT", 3600)
PORT = _env_int("PORT", 8080)

# =====================================================================
#  自动发现账号已废弃：所有账号通过 Bot 对话交互登录，
#  不依赖环境变量预设手机号。ACCS 运行时动态记录 (acc_no -> phone)，
#  第一个登录的账号自动成为主账号（序号 1），负责加群等主账号操作。
# =====================================================================
ACCS = {}  # {acc_no: phone}，运行时动态填充
N_ACCOUNTS = 0

if not (API_ID and API_HASH and BOT_TOKEN and OWNER_ID and DATABASE_URL):
    missing = []
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not OWNER_ID:
        missing.append("OWNER_ID")
    if not DATABASE_URL:
        missing.append("DATABASE_URL")
    logging.getLogger("tg_sender").error(
        f"❌ 缺少必填环境变量: {', '.join(missing)}。"
        "请在 Zeabur 控制台 -> 本服务 -> Variables 中添加后重新部署。"
    )
    sys.exit(1)

# 账号信息：不再强制要求环境变量预设。所有账号通过 Bot 对话登录。


# =====================================================================
#  数据目录（仅存 session；业务数据全在 PostgreSQL）
# =====================================================================
DATA_DIR = os.environ.get("DATA_DIR", "/data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    test_file = os.path.join(DATA_DIR, ".write_test")
    with open(test_file, "w") as f:
        f.write("ok")
    os.remove(test_file)
except Exception:
    DATA_DIR = BASE_DIR
    os.makedirs(DATA_DIR, exist_ok=True)

# =====================================================================
#  日志
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            os.path.join(DATA_DIR, "tg_sender.log"),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("tg_sender")

# =====================================================================
#  全局状态
# =====================================================================
state = {
    "busy": False,
    "paused": False,
    "stop": False,
    "min_delay": MIN_DELAY,
    "max_delay": MAX_DELAY,
    "daily_limit": DAILY_LIMIT,
}

# 活跃账号列表（运行时动态添加/热替换）元素: (acc_no, client, phone)
ACTIVE_ACCOUNTS = []

# 收到 session 压缩包后置位（启动时缺 session 会等待此事件）
ZIP_RECEIVED = asyncio.Event()

# ---- 服务器端 Bot 交互登录：全局状态机 ----
# LOGIN_STATE = None 或 {
#   "stage": "code" | "password",
#   "acc_no": int, "phone": str, "client": TelegramClient,
#   "owner_entity": chat_id, "queue": asyncio.Queue,
# }
LOGIN_STATE = None
