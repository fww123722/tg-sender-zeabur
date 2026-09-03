#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PostgreSQL 数据层：DB 连接池 + 建表 + 全部 CRUD 函数。"""
import time
from datetime import date

import psycopg2
import psycopg2.pool

from config import DATABASE_URL, log

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
        # session 持久化表（存 StringSession 字符串，容器重启不丢失）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tg_sessions (
                name TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        # 文案池表：采集频道历史消息（qfbot「采集」功能）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages_pool (
                source TEXT NOT NULL,
                msg_id BIGINT NOT NULL,
                text TEXT DEFAULT '',
                has_media BOOLEAN DEFAULT FALSE,
                msg_date TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (source, msg_id)
            )
        """)

    @classmethod
    def getconn(cls):
        try:
            return cls._pool.getconn()
        except psycopg2.pool.PoolError:
            cls.init()
            return cls._pool.getconn()

    @classmethod
    def putconn(cls, conn):
        cls._pool.putconn(conn)

    # ---- session 持久化（PostgreSQL） ----
    @classmethod

    @classmethod
    def list_sessions(cls, prefix: str = "") -> list:
        """返回所有 session 名称中带指定前缀的账号序号列表（升序）。
        例如 prefix='tg_session_' 返回 [1,2,3]，用于重启后自动恢复账号。"""
        with cls.cursor() as cur:
            cur.execute("SELECT name FROM tg_sessions WHERE name LIKE %s", (prefix + '%',))
            rows = cur.fetchall()
        nums = []
        for (name,) in rows:
            tail = name[len(prefix):]
            try:
                nums.append(int(tail))
            except ValueError:
                continue
        return sorted(nums)

    def load_session(cls, name: str):
        try:
            conn = cls.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT data FROM tg_sessions WHERE name = %s", (name,))
                    row = cur.fetchone()
                return row[0] if row else None
            finally:
                cls.putconn(conn)
        except Exception as e:
            log.warning(f"? 读取 session[{name}] 失败: {e}")
            return None

    @classmethod
    def save_session(cls, name: str, data: str):
        if not data:
            return
        try:
            conn = cls.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO tg_sessions (name, data, updated_at)
                        VALUES (%s, %s, now())
                        ON CONFLICT (name) DO UPDATE
                        SET data = EXCLUDED.data, updated_at = now()
                    """, (name, data))
                conn.commit()
            finally:
                cls.putconn(conn)
        except Exception as e:
            log.warning(f"? 保存 session[{name}] 失败: {e}")

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
            cur.execute("""
                INSERT INTO groups_info (group_id, title, username, member_count, creator_uid)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (group_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    username = EXCLUDED.username,
                    member_count = EXCLUDED.member_count,
                    creator_uid = EXCLUDED.creator_uid
            """, (gid, title, username, member_count, creator_uid))
        conn.commit()
    finally:
        DB.putconn(conn)


def db_get_all_groups():
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT group_id, title, username, member_count, creator_uid FROM groups_info ORDER BY created_at"
            )
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
