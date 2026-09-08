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


def _env_ids(name):
    """解析逗号/空格/分号分隔的 Telegram user_id 列表。"""
    raw = os.environ.get(name, "") or ""
    out = []
    for part in re.split(r"[,;\s]+", raw):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


# 多人使用：操作员白名单。
# 运行时以 PostgreSQL operators 表为准（/addop /dropop 即刻生效），
# 环境变量 OPERATOR_IDS 仅作首次启动引导 + DB 暂不可用时的兜底。
OPERATOR_IDS_ENV = set(_env_ids("OPERATOR_IDS"))

MIN_DELAY = _env_int("MIN_DELAY", 20)
MAX_DELAY = _env_int("MAX_DELAY", 60)
DAILY_LIMIT = _env_int("DAILY_LIMIT", 100)   # 每个账号每日上限
BATCH_SLEEP = _env_int("BATCH_SLEEP", 300)
BATCH_SIZE = _env_int("BATCH_SIZE", 30)
MAX_FLOOD_WAIT = _env_int("MAX_FLOOD_WAIT", 3600)
# 撞 430 后该账号冷却时长（秒），冷却期内不再派活；默认 15 分钟，可在 Zeabur 用 COOLDOWN_SEC 覆盖
COOLDOWN_SEC = _env_int("COOLDOWN_SEC", 900)
PORT = _env_int("PORT", 8080)

# ============================================================
#  自动发现账号已废弃：所有账号通过 Bot 对话交互登录，
#  不依赖环境变量预设手机号。ACCS 运行时动态记录 (acc_no -> phone)，
#  第一个登录的账号自动成为主账号（序号 1），负责加群等主账号操作。
# ============================================================
ACCS = {}  # {acc_no: phone}，运行时动态填充

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
#  多人使用：角色判定（owner = 主人，operator = 被授权的操作员）
#  白名单存在 PostgreSQL operators 表，OPERATORS 是它的内存缓存，
#  启动时由 main.py 载入，/addop /dropop 实时同步。
# =====================================================================
OPERATORS = {}  # {uid: display_name}


def refresh_operators(mapping):
    """用 DB 返回的 {uid: name} 整体替换缓存，并合入 env 兜底 ID。"""
    new = {}
    for uid, name in (mapping or {}).items():
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            continue
        new[uid] = (str(name) if name else "").strip() or f"user{uid}"
    for uid in OPERATOR_IDS_ENV:
        new.setdefault(uid, f"user{uid}")
    OPERATORS.clear()
    OPERATORS.update(new)
    log.info(f"👥 操作员白名单已刷新：共 {len(OPERATORS)} 人")


def add_operator(uid, name=""):
    uid = int(uid)
    OPERATORS[uid] = (str(name) if name else "").strip() or OPERATORS.get(uid) or f"user{uid}"


def drop_operator(uid):
    OPERATORS.pop(int(uid), None)


def is_owner(uid) -> bool:
    return bool(uid) and uid == OWNER_ID


def is_operator(uid) -> bool:
    return bool(uid) and uid != OWNER_ID and uid in OPERATORS


def is_authorized(uid) -> bool:
    return is_owner(uid) or is_operator(uid)


# =====================================================================
#  进行中操作的汇报去向
#  默认发主人；谁发起了 session 热替换/批量加载，
#  就临时改发给谁，避免操作者的进度消息全灌进主人聊天。
# =====================================================================
NOTIFY_UID = OWNER_ID


def set_notify(uid):
    """把汇报临时指向发起人；传 None/0 回落到主人。"""
    global NOTIFY_UID
    try:
        NOTIFY_UID = int(uid) if uid else OWNER_ID
    except (TypeError, ValueError):
        NOTIFY_UID = OWNER_ID
    return NOTIFY_UID


def get_notify():
    return NOTIFY_UID or OWNER_ID


def actor_name(uid) -> str:
    if is_owner(uid):
        return "主人"
    return OPERATORS.get(uid) or f"user{uid}"

# =====================================================================
#  全局状态
# =====================================================================
state = {
    "busy": False,
    "busy_by": None,   # 当前占用号池的人 {uid, name, at}
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
