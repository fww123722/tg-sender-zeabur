#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram 群发推广系统 — Zeabur + PostgreSQL + 多账号版
======================================================
- 配置从环境变量读取（Zeabur 控制台 Variables 填写）
- 数据存 PostgreSQL（名单/发送记录/统计，重启零丢失）
- session 在本地用 make_session.py 生成，服务器只读取（不碰验证码）
- 支持多个账号群发（多个账号摊开频率，降低封号风险）
- 内置健康检查端口

环境变量:
  API_ID           必填  my.telegram.org 获取（所有账号共用一套）
  API_HASH         必填  my.telegram.org 获取（所有账号共用一套）
  BOT_TOKEN        必填  控制 Bot 的 token（只需一个）
  OWNER_ID         必填  你的 Telegram user_id（白名单）
  DATABASE_URL     必填  PostgreSQL 连接串
  ACCOUNT_1_PHONE  必填  账号1 手机号（含国家码，用于匹配本地 session）
  ACCOUNT_2_PHONE  可选  账号2 手机号（要几个号就配几组）
  ACCOUNT_3_PHONE  可选  账号3 ...
  ACCOUNT_N_PHONE  可选  ...（自动发现，无需改代码）
  可选: MIN_DELAY / MAX_DELAY / DAILY_LIMIT / BATCH_SIZE / BATCH_SLEEP / PORT
"""

import asyncio
import logging
import os
import random
import re
import sys
import threading
import time
import zipfile
from datetime import date
from logging.handlers import RotatingFileHandler
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg2
import psycopg2.pool
from telethon import TelegramClient, events
from telethon.errors import (
    FloodWaitError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    InviteRequestSentError,
    UserAlreadyParticipantError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import (
    Channel,
    Chat,
    ChatInvite,
    ChatInviteAlready,
    InputPeerUser,
    KeyboardButton,
    KeyboardButtonRow,
    ReplyKeyboardMarkup,
    User,
)

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

# 自动发现账号：ACCOUNT_1_PHONE, ACCOUNT_2_PHONE, ...
ACCS = {}
for env_key, env_val in sorted(os.environ.items()):
    m = re.match(r"^ACCOUNT_(\d+)_PHONE$", env_key)
    if m and env_val:
        n = int(m.group(1))
        ACCS[n] = env_val
N_ACCOUNTS = len(ACCS)

if not (API_ID and API_HASH and BOT_TOKEN and OWNER_ID and DATABASE_URL):
    # 逐项列出缺失的配置，方便在 Zeabur 日志里一眼定位
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
if N_ACCOUNTS == 0:
    logging.getLogger("tg_sender").error(
        "❌ 未配置任何账号：请设置至少一个 ACCOUNT_1_PHONE，多个可加 ACCOUNT_2_PHONE 等"
    )
    sys.exit(1)

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

# 收到 session 压缩包后置位（启动时缺 session 会等待此事件）
ZIP_RECEIVED = asyncio.Event()

# ---- 服务器端 Bot 交互登录辅助 ----
# 结构: {(account_no, kind): {"event": asyncio.Event, "value": str|None}}
# kind: phone / code / password
AUTH_PENDING = {}


def _auth_key(acc_no, kind):
    return (acc_no, kind)


async def ask_owner(bot, prompt, acc_no, kind, timeout=300):
    """向 owner 提问并等待回复（验证码/密码/手机号）。超时返回 None。"""
    key = _auth_key(acc_no, kind)
    ev = asyncio.Event()
    AUTH_PENDING[key] = {"event": ev, "value": None}
    try:
        await bot.send_message(OWNER_ID, prompt)
        await asyncio.wait_for(ev.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        AUTH_PENDING.pop(key, None)
    return AUTH_PENDING.get(key, {}).get("value")

# 当前在线账号容器（(account_no, client, phone)）。
# 热替换时原地 clear+extend，register_handlers 的闭包引用同一对象，自动感知变化。
ACTIVE_ACCOUNTS = []


# =====================================================================
#  PostgreSQL 数据层
# =====================================================================
class DB:
    _pool = None

    @classmethod
    def init(cls, max_retries: int = 30, retry_delay: int = 5):
        """初始化连接池并建表。PostgreSQL 可能比本服务晚就绪，带重试。"""
        dsn = DATABASE_URL
        if "keepalives" not in dsn:
            sep = "&" if "?" in dsn else "?"
            dsn = f"{dsn}{sep}keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=3"
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                if cls._pool is not None:
                    try:
                        cls._pool.closeall()
                    except Exception:
                        pass
                cls._pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn=dsn)
                with cls._pool.getconn() as conn:
                    with conn.cursor() as cur:
                        cls._create_tables(cur)
                    conn.commit()
                log.info("🗄️ PostgreSQL 已连接")
                return
            except Exception as e:
                last_err = e
                log.warning(f"⏳ 数据库连接失败（第 {attempt}/{max_retries} 次）: {e}")
                time.sleep(retry_delay)
        log.error(f"❌ 数据库连接重试耗尽: {last_err}")
        raise last_err

    @classmethod
    def _create_tables(cls, cur):
        cur.execute("""
            CREATE TABLE IF NOT EXISTS targets (
                uid TEXT PRIMARY KEY,
                username TEXT DEFAULT '',
                access_hash BIGINT DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        # 兼容已存在的旧表：补上 access_hash 列
        cur.execute("""
            ALTER TABLE targets ADD COLUMN IF NOT EXISTS access_hash BIGINT DEFAULT 0
        """)
        # 每个账号的发送记录：account_no + uid 组合唯一
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sent_log (
                account_no INT NOT NULL,
                uid TEXT NOT NULL,
                sent_at TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (account_no, uid)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                stat_date DATE NOT NULL,
                account_no INT NOT NULL,
                sent_today INT DEFAULT 0,
                total_sent BIGINT DEFAULT 0,
                PRIMARY KEY (stat_date, account_no)
            )
        """)
        # 群组信息表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS groups_info (
                group_id BIGINT PRIMARY KEY,
                title TEXT DEFAULT '',
                username TEXT DEFAULT '',
                member_count INT DEFAULT 0,
                creator_uid TEXT DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)

    @classmethod
    def getconn(cls):
        try:
            return cls._pool.getconn()
        except psycopg2.pool.PoolError:
            # 连接池被关闭或耗尽时重建
            cls.init()
            return cls._pool.getconn()

    @classmethod
    def putconn(cls, conn):
        cls._pool.putconn(conn)

    @classmethod
    def reset(cls):
        try:
            cls._pool.closeall()
        except Exception:
            pass
        cls.init()


# ---- targets ----
def db_load_targets():
    """返回 {uid: {"username": ..., "access_hash": ...}}"""
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT uid, username, access_hash FROM targets")
            return {row[0]: {"username": row[1], "access_hash": row[2] or 0} for row in cur.fetchall()}
    finally:
        DB.putconn(conn)


def db_add_targets(items):
    """items: list of (uid, username, access_hash)"""
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO targets (uid, username, access_hash) VALUES (%s, %s, %s) "
                "ON CONFLICT (uid) DO UPDATE SET "
                "username = EXCLUDED.username, access_hash = EXCLUDED.access_hash",
                items,
            )
        conn.commit()
    finally:
        DB.putconn(conn)


def db_count_targets():
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM targets")
            return cur.fetchone()[0]
    finally:
        DB.putconn(conn)


# ---- sent_log（按账号）----
def db_load_sent(account_no):
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT uid FROM sent_log WHERE account_no = %s", (account_no,))
            return {row[0] for row in cur.fetchall()}
    finally:
        DB.putconn(conn)


def db_add_sent(account_no, uid):
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sent_log (account_no, uid) VALUES (%s, %s) "
                "ON CONFLICT (account_no, uid) DO NOTHING",
                (account_no, uid),
            )
        conn.commit()
    finally:
        DB.putconn(conn)


def db_sent_global():
    """统计已发过(去重 uid)的总人数"""
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT uid) FROM sent_log")
            return cur.fetchone()[0]
    finally:
        DB.putconn(conn)


# ---- stats（按账号）----
def db_load_stats(account_no):
    today = date.today()
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sent_today, total_sent FROM stats WHERE stat_date = %s AND account_no = %s",
                (today, account_no),
            )
            row = cur.fetchone()
            if row:
                return {"sent_today": row[0], "total_sent": row[1]}
            cur.execute(
                "SELECT COALESCE(SUM(sent_today),0), COALESCE(SUM(total_sent),0) FROM stats WHERE account_no = %s",
                (account_no,),
            )
            r = cur.fetchone()
            return {"sent_today": 0, "total_sent": r[1] or 0}
    finally:
        DB.putconn(conn)


def db_bump_sent(account_no):
    today = date.today()
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO stats (stat_date, account_no, sent_today, total_sent)
                VALUES (%s, %s, 1, 1)
                ON CONFLICT (stat_date, account_no)
                DO UPDATE SET sent_today = stats.sent_today + 1, total_sent = stats.total_sent + 1
            """, (today, account_no))
        conn.commit()
    finally:
        DB.putconn(conn)


# ---- groups ----
def db_add_group(gid, title, username, member_count, creator_uid):
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO groups_info (group_id, title, username, member_count, creator_uid) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (group_id) DO UPDATE SET "
                "title=EXCLUDED.title, username=EXCLUDED.username, "
                "member_count=EXCLUDED.member_count, creator_uid=EXCLUDED.creator_uid",
                (gid, title, username, member_count, creator_uid),
            )
        conn.commit()
    finally:
        DB.putconn(conn)


def db_get_all_groups():
    """返回所有群组信息列表"""
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT group_id, title, username, member_count, creator_uid FROM groups_info ORDER BY created_at DESC")
            return cur.fetchall()
    finally:
        DB.putconn(conn)


def db_group_count():
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM groups_info")
            return cur.fetchone()[0]
    finally:
        DB.putconn(conn)


# =====================================================================
#  健康检查端口
# =====================================================================
def start_health_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    try:
        srv = HTTPServer(("0.0.0.0", PORT), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log.info(f"🩺 健康检查端口已启动: {PORT}")
    except Exception as e:
        log.warning(f"健康检查端口启动失败(不影响运行): {e}")


# =====================================================================
#  发送工具
# =====================================================================
async def safe_send(client, entity, text):
    try:
        await client.send_message(entity, text)
        return True, None
    except FloodWaitError as e:
        wait = e.seconds
        log.warning(f"⏳ flood wait {wait}s")
        if wait <= MAX_FLOOD_WAIT:
            await asyncio.sleep(wait)
            try:
                await client.send_message(entity, text)
                return True, None
            except Exception as e2:
                return False, str(e2)
        return False, f"flood_wait_too_long:{wait}s"
    except Exception as e:
        return False, str(e)


# =====================================================================
#  目标名单收集
# =====================================================================
async def collect_members(client, peer_arg, limit=5000):
    try:
        entity = await client.get_entity(peer_arg)
    except Exception as e:
        return f"❌ 找不到该群/频道: {e}"
    batch = []
    added = 0
    try:
        async for user in client.iter_participants(entity, limit=limit):
            if isinstance(user, User) and not user.bot and not user.deleted:
                uid = str(user.id)
                uname = user.username or ""
                ah = getattr(user, "access_hash", 0) or 0
                batch.append((uid, uname, ah))
                added += 1
                if len(batch) >= 500:
                    db_add_targets(batch)
                    batch = []
    except Exception as e:
        return f"❌ 拉取成员失败: {e}"
    if batch:
        db_add_targets(batch)
    name = getattr(entity, "title", peer_arg)
    total = db_count_targets()
    return f"✅ 从「{name}」拉取完成：新增 {added} 人，名单共 {total} 人"


async def list_my_groups(client):
    result = ["📁 你加入的群/频道：\n"]
    try:
        dialogs = await client.get_dialogs()
        for d in dialogs:
            e = d.entity
            if isinstance(e, (Channel, Chat)):
                uname = getattr(e, "username", "") or ""
                result.append(f"• {getattr(e, 'title', '?')}  (id={e.id})  @{uname}")
    except Exception as e:
        return f"❌ {e}"
    return "\n".join(result)


async def _save_group_info(client, entity):
    """保存群组信息到数据库，并返回 (title, member_count)"""
    title = getattr(entity, "title", "") or ""
    username = getattr(entity, "username", "") or ""
    gid = int(entity.id)
    creator_uid = ""
    member_count = 0
    try:
        full = await client.get_entity(entity)
        member_count = getattr(full, "participants_count", 0) or 0
    except Exception:
        pass
    db_add_group(gid, title, username, member_count, creator_uid)
    return title, member_count


async def join_group_by_link(client, link):
    """让账号通过群链接加入群/频道（支持私密邀请链接与公开群，支持需批准入群）。
    加群成功后自动读取群信息并存入 groups_info 表。"""
    link = (link or "").strip().strip("<>").strip()
    # 私密邀请: t.me/+hash 或 t.me/joinchat/hash
    priv_m = re.search(r"t\.me/(?:joinchat/|\+)([A-Za-z0-9_\-]+)", link)
    # 公开群/频道: t.me/username
    pub_m = re.search(r"t\.me/([A-Za-z0-9_][A-Za-z0-9_\-]{3,})", link)

    try:
        if priv_m:
            invite_hash = priv_m.group(1)
            try:
                check = await client(CheckChatInviteRequest(invite_hash))
            except (InviteHashExpiredError, InviteHashInvalidError):
                return "❌ 邀请链接已失效或无效"
            if isinstance(check, ChatInviteAlready):
                title = getattr(check.chat, "title", "?")
                await _save_group_info(client, check.chat)
                return f"✅ 已在群「{title}」中，已更新群信息"
            title = getattr(check, "title", "群")
            if isinstance(check, ChatInvite) and getattr(check, "request_needed", False):
                try:
                    await client(ImportChatInviteRequest(invite_hash))
                    entity = await client.get_entity(title) if title else None
                    await _save_group_info(client, entity)
                    return f"✅ 已加入「{title}」"
                except InviteRequestSentError:
                    return f"⏳ 已向「{title}」发送入群申请，等待群主批准"
            try:
                await client(ImportChatInviteRequest(invite_hash))
                # 通过 invite 里的 chat 信息保存
                chat = getattr(check, "chat", None)
                if chat:
                    await _save_group_info(client, chat)
                return f"✅ 已成功加入「{title}」"
            except UserAlreadyParticipantError:
                return f"✅ 已在「{title}」中"
        elif pub_m:
            token = pub_m.group(1)
            try:
                entity = await client.get_entity(token)
            except Exception:
                return f"❌ 找不到该群/频道（可能已被封禁或需私密邀请）: {token}"
            try:
                await client(JoinChannelRequest(entity))
                title, cnt = await _save_group_info(client, entity)
                return f"✅ 已成功加入「{title}」，成员 {cnt} 人"
            except UserAlreadyParticipantError:
                title, cnt = await _save_group_info(client, entity)
                return f"✅ 已在「{title}」中，成员 {cnt} 人"
            except InviteRequestSentError:
                return f"⏳ 已向「{getattr(entity, 'title', token)}」发送入群申请，等待批准"
        else:
            return f"❌ 无法识别的群链接: {link}"
    except FloodWaitError as e:
        return f"⏳ 操作过于频繁，请 {e.seconds} 秒后再试"
    except Exception as e:
        return f"❌ 加入失败: {e}"


# =====================================================================
#  多账号并发发送
# =====================================================================
async def send_to_list_multi(accounts, targets, text, owner_entity):
    """多个账号轮流派发目标，各自控制频率，并发执行。
    群发过程中定期向 owner 汇总推送进度（百分比 + 各账号明细）。"""
    # targets: {uid: {"username": ..., "access_hash": ...}}
    uid_list = list(targets.keys())

    # 每个账号分配到的子集：轮流均匀分配
    per_account = {acc_no: [] for acc_no, _ in accounts}
    for i, uid in enumerate(uid_list):
        acc_no = accounts[i % len(accounts)][0]
        per_account[acc_no].append(uid)

    # 共享进度（asyncio 单线程，普通 dict 安全）
    progress = {
        "total": len(uid_list),
        "done": 0,          # 已处理（含跳过/失败）
        "sent": 0,          # 成功发送
        "fail": 0,          # 发送失败
        "skipped": 0,       # 跳过（已发过/解析失败）
        "per_acc": {acc_no: {"sent": 0, "fail": 0} for acc_no, _ in accounts},
    }

    async def progress_reporter():
        """后台协程：每 15 秒向 owner 汇总推送一次进度"""
        while True:
            await asyncio.sleep(15)
            if progress["done"] >= progress["total"] or state["stop"]:
                return
            await _report_progress(owner_entity, accounts, progress)

    async def _report_progress(owner_entity, accounts, progress):
        pct = (progress["done"] / progress["total"] * 100) if progress["total"] else 100.0
        lines = [
            f"📊 群发进度：{progress['done']}/{progress['total']}（{pct:.0f}%）",
            f"   成功 {progress['sent']} | 失败 {progress['fail']} | 跳过 {progress['skipped']}",
        ]
        for acc_no, _client, _ph in accounts:
            pa = progress["per_acc"].get(acc_no, {"sent": 0, "fail": 0})
            lines.append(f"   [账号{acc_no}] 成功 {pa['sent']} | 失败 {pa['fail']}")
        try:
            await accounts[0][1].send_message(owner_entity, "\n".join(lines))
        except Exception:
            log.warning("进度消息发送失败")

    async def worker(client, acc_no, my_uids):
        """单个账号的处理循环"""
        sent_set = db_load_sent(acc_no)
        stats = db_load_stats(acc_no)
        for uid in my_uids:
            if state["stop"]:
                return
            if state["paused"]:
                try:
                    await client.send_message(owner_entity, f"⏸ 账号{acc_no} 已暂停")
                except Exception:
                    pass
                # 暂停时循环等待，不退出
                while state["paused"] and not state["stop"]:
                    await asyncio.sleep(5)
                if state["stop"]:
                    return
            if stats["sent_today"] >= state["daily_limit"]:
                try:
                    await client.send_message(
                        owner_entity,
                        f"🚫 账号{acc_no} 今日已达上限 {state['daily_limit']} 条，该账号停止",
                    )
                except Exception:
                    pass
                return
            if uid in sent_set:
                progress["skipped"] += 1
                progress["done"] += 1
                continue
            info = targets.get(uid, {})
            ah = info.get("access_hash", 0) or 0
            if ah:
                try:
                    entity = InputPeerUser(int(uid), int(ah))
                except Exception as e:
                    log.warning(f"[账号{acc_no}] 构造 InputPeerUser 失败 {uid}: {e}")
                    progress["skipped"] += 1
                    progress["done"] += 1
                    continue
            else:
                try:
                    entity = await client.get_entity(int(uid))
                except Exception as e:
                    log.warning(f"[账号{acc_no}] 跳过 {uid}: {e}")
                    progress["skipped"] += 1
                    progress["done"] += 1
                    continue
            ok, err = await safe_send(client, entity, text)
            progress["done"] += 1
            if ok:
                progress["sent"] += 1
                progress["per_acc"][acc_no]["sent"] += 1
                db_add_sent(acc_no, uid)
                db_bump_sent(acc_no)
                stats["sent_today"] += 1
                stats["total_sent"] += 1
            else:
                progress["fail"] += 1
                progress["per_acc"][acc_no]["fail"] += 1
                log.warning(f"[账号{acc_no}] 发送失败 {uid}: {err}")
            delay = random.uniform(state["min_delay"], state["max_delay"])
            await asyncio.sleep(delay)
            if (progress["done"] % BATCH_SIZE) == 0:
                await asyncio.sleep(BATCH_SLEEP)

    # 启动进度汇报协程
    reporter = asyncio.create_task(progress_reporter())

    tasks = [
        asyncio.create_task(worker(client, acc_no, per_account[acc_no]))
        for acc_no, client, _ph in accounts
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    # 收尾：停掉汇报协程，发最终汇总
    reporter.cancel()
    try:
        await reporter
    except asyncio.CancelledError:
        pass

    pct = 100.0 if not progress["total"] else (progress["done"] / progress["total"] * 100)
    parts = []
    total_s = total_f = 0
    for acc_no, _client, _ph in accounts:
        pa = progress["per_acc"].get(acc_no, {"sent": 0, "fail": 0})
        s, f = pa["sent"], pa["fail"]
        total_s += s
        total_f += f
        parts.append(f"账号{acc_no}:成功{s}失败{f}")
    try:
        await accounts[0][1].send_message(
            owner_entity,
            f"✅ 多账号群发完成（{len(accounts)}个账号，{pct:.0f}%）\n"
            + "\n".join(parts)
            + f"\n合计：成功 {total_s}，失败 {total_f}，跳过 {progress['skipped']}",
        )
    except Exception:
        pass
    return f"✅ 多账号群发完成（{len(accounts)}个账号）\n" + "\n".join(parts) + f"\n合计：成功 {total_s}，失败 {total_f}，跳过 {progress['skipped']}"


async def broadcast_to_groups(client, group_args, text, owner_entity):
    groups = [g.strip() for g in group_args.split(",") if g.strip()]
    ok_cnt = 0
    fail_cnt = 0
    for g in groups:
        try:
            entity = await client.get_entity(g)
            ok, err = await safe_send(client, entity, text)
            if ok:
                ok_cnt += 1
            else:
                fail_cnt += 1
                log.warning(f"广播失败 {g}: {err}")
        except Exception as e:
            fail_cnt += 1
            log.warning(f"找不到群 {g}: {e}")
        await asyncio.sleep(random.uniform(state["min_delay"], state["max_delay"]))
    return f"✅ 广播完成：成功 {ok_cnt} 个群，失败 {fail_cnt} 个"


async def forward_from_channel(client, src_arg, dst_arg, owner_entity, count=5):
    try:
        src = await client.get_entity(src_arg)
        dst = await client.get_entity(dst_arg)
    except Exception as e:
        return f"❌ 无法解析源/目标: {e}"
    ok_cnt = 0
    try:
        async for msg in client.iter_messages(src, limit=count):
            if msg.media or msg.message or msg.entities:
                try:
                    await client.send_message(dst, msg.text, file=msg.media)
                    ok_cnt += 1
                except Exception as e:
                    log.warning(f"转发失败: {e}")
                await asyncio.sleep(random.uniform(state["min_delay"], state["max_delay"]))
    except Exception as e:
        return f"❌ 读取频道失败: {e}"
    return f"✅ 转发完成：成功 {ok_cnt} 条到目标"


# =====================================================================
#  Bot 指令处理
# =====================================================================
async def _stats_text(accounts) -> str:
    """各账号发送统计（/stats 命令用）"""
    total = db_count_targets()
    sent = db_sent_global()
    lines = [
        f"📊 统计：",
        f"• 名单: {total} | 已发(去重): {sent}",
        f"• 今日上限/账号: {state['daily_limit']}",
    ]
    for acc_no, client, _ph in accounts:
        try:
            me = await client.get_me()
            name = me.first_name or f"账号{acc_no}"
        except Exception:
            name = f"账号{acc_no}"
        s = db_load_stats(acc_no)
        lines.append(f"• [{acc_no}] {name}: 今日{s['sent_today']} 累计{s['total_sent']}")
    return "\n".join(lines)


async def _acc_status_text(accounts) -> str:
    """账号实时状态：可用/冻结/双向 检测"""
    lines = ["账号实时状态："]
    for acc_no, client, _ph in accounts:
        try:
            await client.connect()
            me = await client.get_me()
            name = me.first_name or f"账号{acc_no}"
            # 尝试取自己的 dialogs 判断账号是否被限制
            try:
                await client.get_dialogs(limit=1)
                status = "可用"
            except Exception as e:
                estr = str(e)
                if "A wait" in estr or "flood" in estr.lower():
                    status = "双向（限流中）"
                else:
                    status = "冻结"
            lines.append(f"• [{acc_no}] {name}: {status}")
        except Exception as e:
            estr = str(e)
            if "A wait" in estr or "flood" in estr.lower():
                lines.append(f"• [账号{acc_no}]: 双向（限流中）")
            else:
                lines.append(f"• [账号{acc_no}]: 冻结（{estr[:50]}）")
    return "\n".join(lines)


def _group_status_text() -> str:
    """群组状态：展示所有已加入的群组及已读取的成员数"""
    rows = db_get_all_groups()
    if not rows:
        return "尚未添加任何群组。点击「添加群组」发送群链接/ID 即可。"
    lines = [f"已加入的群组（{len(rows)} 个）："]
    for gid, title, uname, mcount, _creator in rows:
        un = f" @{uname}" if uname else ""
        lines.append(f"• {title}{un} (ID:{gid}) · 成员 {mcount}")
    return "\n".join(lines)


def _list_text() -> str:
    """生成名单统计文本（/list 与按钮共用）"""
    total = db_count_targets()
    sent = db_sent_global()
    return (
        f"📋 名单统计：\n"
        f"• 总目标: {total} 人\n"
        f"• 已发过(去重): {sent} 人\n"
        f"• 待发送: {max(0, total - sent)} 人"
    )


MENU_TEXT = (
    "控制面板\n\n"
    "点击下方主按钮进入功能：\n"
    "· 群发功能 — 私聊群发 / 群组广播\n"
    "· 添加群组 — 加群并读取群信息与成员\n"
    "· 账号状态 — 添加账号 / 实时状态\n"
    "· 群组状态 — 已加入群组与成员数\n\n"
    "登录账号：/login（全部）或 /login 2（指定序号）"
)

# 按钮点击后等待 owner 输入的会话状态: {chat_id: {"action": str, "hint": str}}
pending_input = {}

# ---- 二级菜单键盘 ----
# 主菜单：4 个主按钮
BTN_MAIN = ("群发功能", "添加群组", "账号状态", "群组状态")
# 子菜单按钮
BTN_SUB = {
    "群发功能": ("私聊群发", "群组广播"),
    "账号状态": ("添加账号", "实时状态"),
}
# 所有按钮 → 动作
KBD_ACTIONS = {
    "私聊群发": "sendto",
    "群组广播": "broadcast",
    "添加群组": "addgroup",
    "添加账号": "addaccount",
    "实时状态": "accstatus",
    "群组状态": "groupstatus",
    "暂停": "pause",
    "继续": "resume",
    "停止任务": "stop",
}


def _kb(rows):
    """由按钮文字构造回复键盘（Telethon 1.44 需显式 KeyboardButtonRow）"""
    return ReplyKeyboardMarkup(
        [KeyboardButtonRow([KeyboardButton(t) for t in row]) for row in rows],
        resize=True,
    )


def _menu_buttons():
    """主菜单：4 个主按钮，2 个一行"""
    return _kb([(BTN_MAIN[0], BTN_MAIN[1]), (BTN_MAIN[2], BTN_MAIN[3])])


def _submenu_buttons(main_btn):
    """子菜单键盘：子按钮 + 主菜单（2 个一行，始终保留）。
    暂停/继续/停止任务仅属于群发功能（私聊群发/群组广播运行时控制）。"""
    if main_btn == "群发功能":
        rows = [BTN_SUB["群发功能"], ("暂停", "继续", "停止任务")]
    elif main_btn == "添加群组":
        rows = [("添加群组",)]
    elif main_btn == "账号状态":
        rows = [BTN_SUB["账号状态"]]
    elif main_btn == "群组状态":
        rows = [("群组状态",)]
    else:
        rows = [(BTN_MAIN[0], BTN_MAIN[1]), (BTN_MAIN[2], BTN_MAIN[3])]
    return _kb(rows + [(BTN_MAIN[0], BTN_MAIN[1]), (BTN_MAIN[2], BTN_MAIN[3])])


async def _reply(event, *args, **kwargs):
    """Telethon 1.44 移除了 Event.reply，统一用 client.send_message 实现"""
    return await event.client.send_message(event.chat_id, *args, **kwargs)


async def _login_accounts(bot, accounts, targets, owner_entity):
    """通过 Bot 交互式登录指定账号（验证码/2FA 密码通过 Bot 问答）。
    登录成功后自动加入 ACTIVE_ACCOUNTS 并启动客户端。"""
    from telethon.errors import SessionPasswordNeededError

    for acc_no, client, phone in accounts:
        if acc_no not in targets:
            continue
        try:
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                await bot.send_message(owner_entity, f"⏭️ [账号{acc_no}] 已登录: {me.first_name} (@{me.username})，无需重复登录")
                continue

            # 1) 发送验证码
            await bot.send_message(owner_entity, f"📱 [账号{acc_no}] 正在向 {phone} 发送验证码…")
            await client.send_code_request(phone)

            # 2) 等 owner 回复验证码
            code = await ask_owner(bot, f"🔐 [账号{acc_no}] 请输入 {phone} 收到的验证码（直接回复数字）：", acc_no, "code")
            if not code:
                await bot.send_message(owner_entity, f"❌ [账号{acc_no}] 验证码输入超时，登录中止。可重新发 /login {acc_no}")
                await client.disconnect()
                continue

            # 3) 登录（处理 2FA）
            try:
                await client.sign_in(phone, code.strip())
            except SessionPasswordNeededError:
                pwd = await ask_owner(bot, f"🔑 [账号{acc_no}] 该账号开启了二步验证，请回复密码：", acc_no, "password")
                if not pwd:
                    await bot.send_message(owner_entity, f"❌ [账号{acc_no}] 密码输入超时，登录中止。可重新发 /login {acc_no}")
                    await client.disconnect()
                    continue
                await client.sign_in(password=pwd.strip())

            if not await client.is_user_authorized():
                await bot.send_message(owner_entity, f"❌ [账号{acc_no}] 登录失败（验证码/密码错误）。可重新发 /login {acc_no}")
                await client.disconnect()
                continue

            me = await client.get_me()
            log.info(f"✅ [账号{acc_no}] 登录成功: {me.first_name} (@{me.username})")
            await bot.send_message(owner_entity, f"✅ [账号{acc_no}] 登录成功: {me.first_name} (@{me.username})")

            # 4) 加入在线列表并启动（若尚未在列）
            exists = any(a[0] == acc_no for a in ACTIVE_ACCOUNTS)
            if not exists:
                ACTIVE_ACCOUNTS.append((acc_no, client, phone))
                asyncio.create_task(client.run_until_disconnected())
        except Exception as e:
            log.error(f"❌ [账号{acc_no}] 登录异常: {e}")
            try:
                await bot.send_message(owner_entity, f"❌ [账号{acc_no}] 登录异常: {e}")
                await client.disconnect()
            except Exception:
                pass


async def _add_account_interactive(bot, phone, owner_entity):
    """通过 Bot 交互式添加任意账号：输入手机号 → 验证码 → （2FA密码）→ 上线。
    不依赖环境变量，成功后自动分配下一个可用序号。"""
    from telethon.errors import SessionPasswordNeededError, PhoneNumberInvalidError

    phone = (phone or "").strip()
    if not re.match(r"^\+?\d{6,15}$", phone):
        await bot.send_message(owner_entity, "❌ 手机号格式无效（应含国家码，如 +8613800138000）")
        return

    # 分配序号：现有最大序号 +1
    acc_no = max(list(ACCS.keys()) + [a[0] for a in ACTIVE_ACCOUNTS] + [0]) + 1
    sess = os.path.join(DATA_DIR, f"tg_session_{acc_no}.session")

    client = TelegramClient(sess, API_ID, API_HASH)
    try:
        await client.connect()
        if await client.is_user_authorized():
            me = await client.get_me()
            await bot.send_message(owner_entity, f"⏭️ 该手机号已有有效 session: {me.first_name} (@{me.username})")
            return

        await bot.send_message(owner_entity, f"📱 [新账号{acc_no}] 正在向 {phone} 发送验证码…")
        await client.send_code_request(phone)

        code = await ask_owner(bot, f"🔐 [新账号{acc_no}] 请输入 {phone} 收到的验证码（直接回复数字）：", acc_no, "code")
        if not code:
            await bot.send_message(owner_entity, f"❌ 验证码输入超时，添加中止。可重新点「添加账号」")
            await client.disconnect()
            return

        try:
            await client.sign_in(phone, code.strip())
        except SessionPasswordNeededError:
            pwd = await ask_owner(bot, f"🔑 [新账号{acc_no}] 该账号开启了二步验证，请回复密码：", acc_no, "password")
            if not pwd:
                await bot.send_message(owner_entity, "❌ 密码输入超时，添加中止")
                await client.disconnect()
                return
            await client.sign_in(password=pwd.strip())

        if not await client.is_user_authorized():
            await bot.send_message(owner_entity, "❌ 登录失败（验证码/密码错误），可重新点「添加账号」")
            await client.disconnect()
            return

        me = await client.get_me()
        # 登记到 ACCS，让 /login、热替换等流程也能感知
        ACCS[acc_no] = phone
        ACTIVE_ACCOUNTS.append((acc_no, client, phone))
        asyncio.create_task(client.run_until_disconnected())
        log.info(f"✅ [新账号{acc_no}] 添加成功: {me.first_name} (@{me.username})")
        await bot.send_message(
            owner_entity,
            f"✅ [账号{acc_no}] 添加成功: {me.first_name} (@{me.username})\n"
            f"已上线，可直接使用群发功能。当前共 {len(ACTIVE_ACCOUNTS)} 个账号在线。",
        )
    except PhoneNumberInvalidError:
        await bot.send_message(owner_entity, "❌ 手机号无效（Telegram 不认可），请检查国家码")
        try:
            await client.disconnect()
        except Exception:
            pass
    except Exception as e:
        log.error(f"❌ [新账号] 添加异常: {e}")
        try:
            await bot.send_message(owner_entity, f"❌ 添加异常: {e}")
            await client.disconnect()
        except Exception:
            pass


def register_handlers(bot, accounts):
    # accounts: list of (account_no, client, phone)

    def _no_accounts(event) -> bool:
        """账号未就绪时统一提示，返回 True 表示应终止处理"""
        if not accounts:
            asyncio.ensure_future(_reply(event,
                "⚠️ 当前没有可用账号。\n"
                "请点「账号状态」→「添加账号」，输入手机号即可登录。"
            ))
            return True
        return False

    @bot.on(events.NewMessage(pattern="^/start$"))
    async def on_start(event):
        if event.sender_id != OWNER_ID:
            await _reply(event, "⛔ 无权限")
            return
        await _reply(event,
            "👋 群发系统已启动\n\n"
            f"📡 当前账号数: {len(accounts)}\n\n"
            "请使用下方按钮操作：\n"
            "· 群发功能：私聊群发 / 群组广播\n"
            "· 添加群组：发送群链接或ID，自动加群并读取信息\n"
            "· 账号状态：添加账号 / 实时状态（可用/冻结/双向）\n"
            "· 群组状态：查看所有已加入的群组与成员数\n"
            "· 暂停 / 继续 / 停止任务\n\n"
            "登录账号：/login（全部）或 /login 2（指定序号）",
            buttons=_menu_buttons(),
        )

    @bot.on(events.NewMessage(pattern="^/mygroups$"))
    async def on_mygroups(event):
        if event.sender_id != OWNER_ID:
            return
        if _no_accounts(event):
            return
        # 用第一个账号列出
        await _reply(event, await list_my_groups(accounts[0][1]))

    @bot.on(events.NewMessage(pattern=r"^/collect ([\s\S]+)$"))
    async def on_collect(event):
        if event.sender_id != OWNER_ID:
            return
        if _no_accounts(event):
            return
        arg = event.pattern_match.group(1).strip()
        if state["busy"]:
            await _reply(event, "⏳ 正在执行其他任务")
            return
        state["busy"] = True
        await _reply(event, "🔄 正在拉取成员，请稍候…")
        try:
            result = await collect_members(accounts[0][1], arg)
            await _reply(event, result)
        finally:
            state["busy"] = False

    @bot.on(events.NewMessage(pattern="^/list$"))
    async def on_list(event):
        if event.sender_id != OWNER_ID:
            return
        await _reply(event, _list_text())

    @bot.on(events.NewMessage(pattern=r"^/sendto ([\s\S]+)$"))
    async def on_sendto(event):
        if event.sender_id != OWNER_ID:
            return
        if _no_accounts(event):
            return
        text = event.pattern_match.group(1).strip()
        if not text:
            await _reply(event, "❌ 内容为空")
            return
        if state["busy"]:
            await _reply(event, "⏳ 正在执行其他任务")
            return
        targets = db_load_targets()
        if not targets:
            await _reply(event, "❌ 名单为空，先用 /collect 收集")
            return
        state["busy"] = True
        state["paused"] = False
        state["stop"] = False
        await _reply(event, 
            f"🚀 开始多账号群发：{len(accounts)}个账号 | 目标 {len(targets)} 人 | "
            f"间隔 {state['min_delay']}-{state['max_delay']}s\n"
            "目标将轮流分配给各账号，账号越多越快。"
        )
        try:
            result = await send_to_list_multi(accounts, targets, text, event.chat_id)
            await _reply(event, result)
        finally:
            state["busy"] = False

    @bot.on(events.NewMessage(pattern=r"^/broadcast ([\s\S]+)$"))
    async def on_broadcast(event):
        if event.sender_id != OWNER_ID:
            return
        if _no_accounts(event):
            return
        raw = event.pattern_match.group(1).strip()
        parts = raw.split(" ", 1)
        if len(parts) < 2:
            await _reply(event, "❌ 用法: /broadcast <群1,群2> <内容>")
            return
        groups, text = parts[0], parts[1]
        if state["busy"]:
            await _reply(event, "⏳ 正在执行其他任务")
            return
        state["busy"] = True
        await _reply(event, f"🚀 用账号1 广播到: {groups}")
        try:
            result = await broadcast_to_groups(accounts[0][1], groups, text, event.chat_id)
            await _reply(event, result)
        finally:
            state["busy"] = False

    @bot.on(events.NewMessage(pattern=r"^/forward ([\s\S]+)$"))
    async def on_forward(event):
        if event.sender_id != OWNER_ID:
            return
        if _no_accounts(event):
            return
        parts = event.pattern_match.group(1).split()
        if len(parts) < 2:
            await _reply(event, "❌ 用法: /forward <源频道> <目标群>")
            return
        src, dst = parts[0], parts[1]
        if state["busy"]:
            await _reply(event, "⏳ 正在执行其他任务")
            return
        state["busy"] = True
        await _reply(event, "🔄 正在转发…")
        try:
            result = await forward_from_channel(accounts[0][1], src, dst, event.chat_id)
            await _reply(event, result)
        finally:
            state["busy"] = False

    @bot.on(events.NewMessage(pattern="^/stats$"))
    async def on_stats(event):
        if event.sender_id != OWNER_ID:
            return
        await _reply(event, await _stats_text(accounts))

    @bot.on(events.NewMessage(pattern="^/pause$"))
    async def on_pause(event):
        if event.sender_id != OWNER_ID:
            return
        state["paused"] = True
        await _reply(event, "⏸ 已暂停")

    @bot.on(events.NewMessage(pattern="^/resume$"))
    async def on_resume(event):
        if event.sender_id != OWNER_ID:
            return
        state["paused"] = False
        await _reply(event, "▶️ 已继续")

    @bot.on(events.NewMessage(pattern=r"^/speed (\d+)$"))
    async def on_speed(event):
        if event.sender_id != OWNER_ID:
            return
        sec = int(event.pattern_match.group(1))
        state["min_delay"] = max(1, sec)
        state["max_delay"] = max(1, sec + 10)
        await _reply(event, f"⚡ 间隔已设为 {state['min_delay']}-{state['max_delay']}s")

    @bot.on(events.NewMessage(pattern=r"^/quota (\d+)$"))
    async def on_quota(event):
        if event.sender_id != OWNER_ID:
            return
        state["daily_limit"] = int(event.pattern_match.group(1))
        await _reply(event, f"🎯 每账号今日上限已设为 {state['daily_limit']} 条")

    @bot.on(events.NewMessage(pattern="^/stop$"))
    async def on_stop(event):
        if event.sender_id != OWNER_ID:
            return
        state["stop"] = True
        state["paused"] = True
        state["busy"] = False
        await _reply(event, "🛑 已停止当前任务（任务循环将在下次检测到停止标志时退出）")

    # ---- 控制面板 /menu - 发送底部键盘并显示说明 ----
    @bot.on(events.NewMessage(pattern="^/menu$"))
    async def on_menu(event):
        if event.sender_id != OWNER_ID:
            return
        await _reply(event, MENU_TEXT, buttons=_menu_buttons())

    # ---- 登录账号：/login [序号]（不带序号则登录所有未授权账号） ----
    @bot.on(events.NewMessage(pattern=r"^/login(?:\s+(\d+))?$"))
    async def on_login(event):
        if event.sender_id != OWNER_ID:
            return
        if _no_accounts(event):
            return
        target = event.pattern_match.group(1)
        targets = [int(target)] if target else [acc_no for acc_no, _c, _p in accounts]
        await _reply(event, f"🔄 开始登录账号 {targets}，验证码将发到这里，直接回复数字即可…")
        asyncio.ensure_future(_login_accounts(bot, accounts, targets, event.chat_id))

    # ---- owner 回复分发：优先喂给等待中的登录提问（验证码/密码） ----
    @bot.on(events.NewMessage())
    async def on_auth_reply(event):
        if event.sender_id != OWNER_ID:
            return
        text = (event.text or "").strip()
        if not text or not AUTH_PENDING:
            return
        # 只把回复喂给最早发起的提问（同一时刻一般只有一个登录流程）
        for key, info in sorted(AUTH_PENDING.items()):
            if info["value"] is None:
                info["value"] = text
                info["event"].set()
                return

    # ---- 底部按钮处理（二级菜单：主按钮切键盘，子按钮执行动作） ----
    @bot.on(events.NewMessage())
    async def on_kbd(event):
        if event.sender_id != OWNER_ID:
            return
        text = (event.text or "").strip()
        if not text:
            return

        # 1) 主按钮 → 切换到对应子菜单键盘
        if text in BTN_MAIN:
            if event.sender_id in pending_input:
                del pending_input[event.sender_id]
            if text == "添加群组":
                # 添加群组：切键盘 + 直接引导输入
                if _no_accounts(event):
                    return
                pending_input[event.sender_id] = {
                    "action": "addgroup",
                    "hint": "请发送群链接或群ID，主账号将尝试加群并读取群信息与成员：",
                }
                await _reply(event,
                    "添加群组\n\n"
                    "支持：公开群 t.me/xxx · 私密邀请 t.me/+xxx · 群ID\n"
                    "加群成功后自动读取群信息并拉取成员到名单。\n\n"
                    "请发送群链接或ID：",
                    buttons=_submenu_buttons(text),
                )
            else:
                await _reply(event, f"{text}\n\n请选择具体操作：", buttons=_submenu_buttons(text))
            return

        # 2) 子按钮 → 执行动作
        if text not in KBD_ACTIONS:
            return
        if event.sender_id in pending_input:
            del pending_input[event.sender_id]
        action = KBD_ACTIONS[text]

        # 需要等待输入的操作
        INPUT_ACTIONS = {"sendto", "broadcast", "addaccount"}
        INPUT_HINTS = {
            "sendto": "请输入要群发的消息内容（可多行）：",
            "broadcast": "请输入 <群1,群2> <内容> （群名用逗号分隔）：",
            "addaccount": "请输入要添加的账号手机号（含国家码，如 +8613800138000）：",
        }

        if action in INPUT_ACTIONS:
            if action != "addaccount" and _no_accounts(event):
                return
            pending_input[event.sender_id] = {"action": action, "hint": INPUT_HINTS[action]}
            await _reply(event, INPUT_HINTS[action])
            return

        # 即时执行的操作
        if action == "accstatus":
            if _no_accounts(event):
                return
            await _reply(event, await _acc_status_text(accounts))
        elif action == "groupstatus":
            await _reply(event, _group_status_text())
        elif action == "pause":
            state["paused"] = True
            await _reply(event, "已暂停（点「继续」恢复）")
        elif action == "resume":
            state["paused"] = False
            await _reply(event, "已继续")
        elif action == "stop":
            state["stop"] = True
            state["paused"] = True
            state["busy"] = False
            await _reply(event, "已停止当前任务")
        else:
            return

    # ---- 处理 pending_input（按钮点击后的回复） ----
    @bot.on(events.NewMessage())
    async def on_pending_input(event):
        if event.sender_id != OWNER_ID:
            return
        text = (event.text or "").strip()
        if not text or text.startswith("/"):
            return
        # 底部按钮文字由 on_kbd 处理，不作为输入内容（避免刚点按钮就被消费）
        if text in KBD_ACTIONS or text in BTN_MAIN:
            return
        # 检查是否有 pending_input
        inp = pending_input.get(event.sender_id)
        if not inp:
            return
        action = inp["action"]
        del pending_input[event.sender_id]

        # 添加账号不需要已有账号，也不受 busy 限制（首次添加时必然无账号）
        if action == "addaccount":
            await _reply(event, f"🔄 开始添加账号 {text} …")
            await _add_account_interactive(bot, text, event.chat_id)
            return

        if _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, "当前正在执行其他任务，请稍后再试")
            return

        if action == "sendto":
            targets = db_load_targets()
            if not targets:
                await _reply(event, "名单为空。请先点「添加群组」加入群并拉取成员，再回来群发。")
                return
            state["busy"] = True
            state["paused"] = False
            state["stop"] = False
            await _reply(event, 
                f"开始多账号群发：{len(accounts)}个账号 | 目标 {len(targets)} 人 | "
                f"间隔 {state['min_delay']}-{state['max_delay']}s"
            )
            try:
                result = await send_to_list_multi(accounts, targets, text, event.chat_id)
                await _reply(event, result)
            finally:
                state["busy"] = False

        elif action == "broadcast":
            parts = text.split(" ", 1)
            if len(parts) < 2:
                await _reply(event, "格式错误，请用「群1,群2 内容」的格式发送")
                return
            groups, msg = parts[0], parts[1]
            state["busy"] = True
            await _reply(event, f"正在广播到: {groups}")
            try:
                result = await broadcast_to_groups(accounts[0][1], groups, msg, event.chat_id)
                await _reply(event, result)
            finally:
                state["busy"] = False

        elif action == "addgroup":
            if state["busy"]:
                await _reply(event, "当前正在执行其他任务，稍后再试")
                return
            await _reply(event, "正在用主账号加入群组并读取信息，请稍候…")
            # 先加群
            join_result = await join_group_by_link(accounts[0][1], text)
            await _reply(event, join_result)
            # 再尝试拉取群成员到名单
            await _reply(event, "正在读取群成员到名单…")
            collect_result = await collect_members(accounts[0][1], text)
            await _reply(event, collect_result)

    # ---- 自动识别群链接（兼容直接发链接，也走 addgroup 流程） ----
    @bot.on(events.NewMessage())
    async def on_auto_join_link(event):
        if event.sender_id != OWNER_ID:
            return
        text = (event.text or "").strip()
        if not text or text.startswith("/"):
            return
        # 如果已有 pending_input，不在此处理（on_pending_input 会处理）
        if event.sender_id in pending_input:
            return
        m = re.search(r"(?:https?://)?t\.me/(?:joinchat/|\+)?[A-Za-z0-9_\-]+", text)
        if not m:
            return
        link = m.group(0)
        if _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, "当前正在执行其他任务，请稍后再试")
            return
        await _reply(event, "检测到群链接，正在用主账号加入并读取成员…")
        join_result = await join_group_by_link(accounts[0][1], link)
        await _reply(event, join_result)
        collect_result = await collect_members(accounts[0][1], link)
        await _reply(event, collect_result)


def _extract_session_zip(download_path):
    """把 zip 里的 session 文件解压到 DATA_DIR，返回 (成功数, 失败原因)。
    新 zip 视为账号全集：解压前先清理旧的 tg_session_*（死号自然移除）。"""
    try:
        with zipfile.ZipFile(download_path, "r") as zf:
            targets = [n for n in zf.namelist() if n.endswith(".session") or n.endswith(".session-journal")]
            if not targets:
                return 0, "压缩包里没有找到 .session 文件"
            os.makedirs(DATA_DIR, exist_ok=True)
            # 清理旧账号 session（保留 bot_session 和控制文件）
            for old in os.listdir(DATA_DIR):
                if old.startswith("tg_session_") and (old.endswith(".session") or old.endswith(".session-journal")):
                    try:
                        os.remove(os.path.join(DATA_DIR, old))
                    except Exception:
                        pass
            for n in targets:
                base = os.path.basename(n)
                if not base:
                    continue
                if not re.match(r"^tg_session_\d+\.session(-journal)?$", base):
                    continue
                with zf.open(n) as src, open(os.path.join(DATA_DIR, base), "wb") as dst:
                    dst.write(src.read())
        try:
            os.remove(download_path)
        except Exception:
            pass
        return len(targets), None
    except Exception as e:
        return 0, str(e)


async def _register_zip_receiver(bot):
    """在账号就绪前先注册 zip 接收 handler，让 owner 能直接发 session 压缩包"""

    @bot.on(events.NewMessage())
    async def on_early_zip(event):
        if event.sender_id != OWNER_ID:
            return
        if not event.document and not event.file:
            return
        mime_type = ""
        fname = ""
        try:
            mime_type = event.document.mime_type or ""
        except Exception:
            pass
        try:
            fname = event.file.name or ""
        except Exception:
            pass
        if not (mime_type.endswith("zip") or fname.endswith(".zip")):
            return
        await _reply(event, "📦 收到 session 压缩包，正在解压…")
        try:
            dl = await event.download_media(file=os.path.join(DATA_DIR, "_incoming.zip"))
            if not dl:
                await _reply(event, "❌ 下载失败")
                return
            cnt, err = _extract_session_zip(dl)
            if err:
                await _reply(event, f"❌ 解压失败: {err}")
                return
            ZIP_RECEIVED.set()
            await _reply(event, f"✅ 已解压 {cnt} 个 session 文件。正在重新加载…")
        except Exception as e:
            await _reply(event, f"❌ 处理压缩包失败: {e}")


def _find_session(acc_no: int) -> str:
    """依次在 DATA_DIR 和 BASE_DIR 下查找 session 文件，返回第一个存在的路径，或默认 DATA_DIR 路径"""
    candidates = [
        os.path.join(DATA_DIR, f"tg_session_{acc_no}.session"),
        os.path.join(BASE_DIR, f"tg_session_{acc_no}.session"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return candidates[0]  # 都不存在时返回 DATA_DIR 下的路径，让 Telethon 自行处理


# =====================================================================
#  主入口
# =====================================================================
async def main():
    start_health_server()
    DB.init()

    bot_session_path = os.path.join(DATA_DIR, "bot_session.session")
    bot = TelegramClient(bot_session_path, API_ID, API_HASH)
    for attempt in (1, 2):
        try:
            await bot.start(bot_token=BOT_TOKEN)
            me_bot = await bot.get_me()
            log.info(f"🤖 控制 Bot 已连接: @{me_bot.username}")
            break
        except Exception as e:
            estr = str(e)
            if "AUTH_KEY_UNREGISTERED" in estr and attempt == 1:
                # 服务器上的 bot_session 已失效（被注销或残留旧 session）：
                # 删除后重试一次，Bot 会用 BOT_TOKEN 重新登录，无需人工干预
                log.warning("⚠️ bot_session 已失效（AUTH_KEY_UNREGISTERED），删除后重新登录…")
                try:
                    await bot.disconnect()
                except Exception:
                    pass
                for suffix in ("", "-journal"):
                    p = bot_session_path + suffix
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                    except Exception:
                        pass
                bot = TelegramClient(bot_session_path, API_ID, API_HASH)
                continue
            if "API_ID_INVALID" in estr or "api_id" in estr.lower():
                log.error("❌ Bot 连接失败: API_ID/API_HASH 无效。请核对 my.telegram.org 的值是否正确。")
            elif "TOKEN_INVALID" in estr or "token" in estr.lower():
                log.error("❌ Bot 连接失败: BOT_TOKEN 无效。请用 @BotFather 重新获取 token。")
            else:
                log.error(f"❌ Bot 连接失败: {e}")
            # 非零退出码，让 Zeabur 自动重启重试（可能是暂时性网络问题）
            sys.exit(1)

    # 先注册 zip 接收 handler：owner 可在任意时刻把本地打包的 tg_sessions.zip 发给 Bot
    await _register_zip_receiver(bot)

    # 为每个账号准备 TelegramClient（同一套 API_ID/API_HASH）。
    # session 由本地 make_session.py 生成并打包，通过 Bot 发来；服务器只「加载 + 校验」，
    # 绝不触发验证码登录（服务器无法交互）。
    accounts = []  # (account_no, client, phone)
    for acc_no in sorted(ACCS.keys()):
        phone = ACCS[acc_no]
        sess = _find_session(acc_no)
        client = TelegramClient(sess, API_ID, API_HASH)
        accounts.append((acc_no, client, phone))
    N = len(accounts)
    log.info(f"📡 检测到 {N} 个账号")

    async def load_accounts():
        """尝试加载所有账号的 session，返回 (ready, failed)"""
        ready = []
        failed = []
        for acc_no, client, phone in accounts:
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    failed.append(acc_no)
                    await client.disconnect()
                    continue
                me = await client.get_me()
                ready.append((acc_no, client, phone))
                log.info(f"✅ [账号{acc_no}] 已加载 session: {me.first_name} (@{me.username})")
                try:
                    await bot.send_message(OWNER_ID, f"✅ [账号{acc_no}] 已加载 session: {me.first_name} (@{me.username})")
                except Exception:
                    pass
            except Exception as e:
                failed.append(acc_no)
                log.error(f"❌ [账号{acc_no}] 加载 session 失败: {e}")
                try:
                    await client.disconnect()
                except Exception:
                    pass
                try:
                    await bot.send_message(OWNER_ID, f"❌ [账号{acc_no}] 加载 session 失败: {e}")
                except Exception:
                    pass
        return ready, failed

    ready, failed = await load_accounts()

    # 先注册指令 handler（含 /start /menu /login 等），让 owner 随时可操作 Bot。
    # handler 引用 ACTIVE_ACCOUNTS 模块级容器，账号加载/登录后自动感知。
    register_handlers(bot, ACTIVE_ACCOUNTS)
    ACTIVE_ACCOUNTS.extend(ready)

    if not ready:
        try:
            await bot.send_message(
                OWNER_ID,
                "⚠️ 服务器上还没有已登录的账号。\n\n"
                f"两种方式登录：\n"
                f"1️⃣ 直接发 /login —— 我会向 ACCOUNT_1_PHONE 发送验证码，你回复数字即可\n"
                f"2️⃣ 发送 tg_sessions.zip（本地生成）—— 我会自动解压加载\n\n"
                f"多账号用 /login 2、/login 3 … 指定序号。",
                buttons=_menu_buttons(),
            )
        except Exception:
            pass
        log.info("⏳ 等待账号登录（/login 或 session zip）…")
        while not ready:
            try:
                await asyncio.wait_for(ZIP_RECEIVED.wait(), timeout=900)
            except asyncio.TimeoutError:
                log.info("⏳ 仍在等待账号登录…")
                continue
            ZIP_RECEIVED.clear()
            log.info("📦 已收到 session 压缩包，尝试重新加载…")
            ready, failed = await load_accounts()
            ACTIVE_ACCOUNTS.clear()
            ACTIVE_ACCOUNTS.extend(ready)

    await bot.send_message(
        OWNER_ID,
        f"🟢 群发系统已上线，{len(ready)}/{N} 个账号可用。\n"
        "下方按钮可直接操作；需要输入的功能，点按钮后按提示回复内容。",
        buttons=_menu_buttons(),
    )

    # ---- 运行时热替换：监听新 zip 到达，断开旧客户端、重新加载 ----
    async def hot_reload_watcher():
        while True:
            try:
                await asyncio.wait_for(ZIP_RECEIVED.wait(), timeout=300)
            except asyncio.TimeoutError:
                continue
            ZIP_RECEIVED.clear()
            log.info("♻️ 收到新 session 压缩包，开始热替换账号…")
            try:
                await bot.send_message(OWNER_ID, "♻️ 收到新 session 压缩包，正在热替换账号…")
            except Exception:
                pass
            # 断开所有旧客户端
            old_clients = [(acc_no, client) for acc_no, client, _ph in ACTIVE_ACCOUNTS]
            ACTIVE_ACCOUNTS.clear()
            for acc_no, client in old_clients:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            # 重新创建 TelegramClient（因为 session 文件可能已更新）
            new_accounts = []
            for acc_no in sorted(ACCS.keys()):
                phone = ACCS[acc_no]
                sess = _find_session(acc_no)
                client = TelegramClient(sess, API_ID, API_HASH)
                new_accounts.append((acc_no, client, phone))
            # 重新加载
            new_ready = []
            for acc_no, client, phone in new_accounts:
                try:
                    await client.connect()
                    if not await client.is_user_authorized():
                        await client.disconnect()
                        continue
                    me = await client.get_me()
                    new_ready.append((acc_no, client, phone))
                    log.info(f"✅ [账号{acc_no}] 热替换成功: {me.first_name} (@{me.username})")
                except Exception as e:
                    log.error(f"❌ [账号{acc_no}] 热替换失败: {e}")
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
            if new_ready:
                ACTIVE_ACCOUNTS.extend(new_ready)
                msg = f"♻️ 热替换完成，当前 {len(new_ready)} 个账号在线"
                # 新账号也跑起来（与旧账号共享同一个 bot）
                for acc_no, client, phone in new_ready:
                    asyncio.create_task(client.run_until_disconnected())
                log.info(msg)
                try:
                    await bot.send_message(OWNER_ID, msg)
                except Exception:
                    pass
            else:
                # 回退：恢复旧客户端（session 没变，直接重连）
                msg = "❌ 热替换失败：新 zip 中没有可用账号，已回退到原账号"
                log.error(msg)
                for acc_no, client in old_clients:
                    try:
                        await client.connect()
                        ACTIVE_ACCOUNTS.append((acc_no, client, ACCS.get(acc_no, "")))
                    except Exception:
                        pass
                try:
                    await bot.send_message(OWNER_ID, msg)
                except Exception:
                    pass

    # 运行所有组件
    tasks = [bot.run_until_disconnected(), hot_reload_watcher()]
    for acc_no, client, phone in ready:
        tasks.append(client.run_until_disconnected())
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 已退出")
