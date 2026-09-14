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
        # 名单来源追溯（老板要「进群后一直读取」）：每个人是从哪个群、经哪条通道
        # 进来的、他什么时候进的那个群——不记下来就没法验证 3 天窗口有没有生效。
        # 同时 created_at 就是老板要的「录入时间」：群发默认按它从新到旧排队。
        cur.execute("""
            ALTER TABLE targets ADD COLUMN IF NOT EXISTS via TEXT DEFAULT ''
        """)
        cur.execute("""
            ALTER TABLE targets ADD COLUMN IF NOT EXISTS source_group BIGINT DEFAULT 0
        """)
        cur.execute("""
            ALTER TABLE targets ADD COLUMN IF NOT EXISTS member_since TIMESTAMPTZ
        """)
        # 「未发送」看板要按来源群分组统计，没索引的话每次都得全表扫 sent_log
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_targets_source_group ON targets (source_group)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_targets_created_at ON targets (created_at DESC)
        """)
        # 每个群的监听进度：读到哪条消息了。容器重启后从这儿接着读，不从头再翻一遍。
        cur.execute("""
            CREATE TABLE IF NOT EXISTS watch_cursor (
                group_id BIGINT PRIMARY KEY,
                title TEXT DEFAULT '',
                last_msg_id BIGINT DEFAULT 0,
                last_scan_at TIMESTAMPTZ,
                new_since_last INT DEFAULT 0,
                updated_at TIMESTAMPTZ DEFAULT now()
            )
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
        # 账号冷却表：撞 430/限流后按账号记账，跨批次、跨重启生效
        cur.execute("""
            CREATE TABLE IF NOT EXISTS account_cooldown (
                account_no INT PRIMARY KEY,
                until_at TIMESTAMPTZ NOT NULL,
                reason TEXT DEFAULT '',
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        # 操作员白名单表（多人共管同一套）：owner 在 Bot 里 /addop 即可增删
        cur.execute("""
            CREATE TABLE IF NOT EXISTS operators (
                uid BIGINT PRIMARY KEY,
                name TEXT DEFAULT '',
                enabled BOOLEAN DEFAULT TRUE,
                added_by BIGINT DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        # 操作审计表：谁在什么时候干了什么，全部留痕
        cur.execute("""
            CREATE TABLE IF NOT EXISTS op_log (
                id BIGSERIAL PRIMARY KEY,
                actor_uid BIGINT NOT NULL,
                actor_name TEXT DEFAULT '',
                action TEXT NOT NULL,
                detail TEXT DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS op_log_actor_time_idx
                ON op_log (actor_uid, created_at DESC)
        """)
        # 群发任务归属：campaign 记录是谁发起的，看板按人统计
        cur.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id BIGSERIAL PRIMARY KEY,
                actor_uid BIGINT NOT NULL,
                actor_name TEXT DEFAULT '',
                group_key TEXT DEFAULT '',
                group_title TEXT DEFAULT '',
                target_count INT DEFAULT 0,
                sent_count INT DEFAULT 0,
                status TEXT DEFAULT 'running',
                text_preview TEXT DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT now(),
                finished_at TIMESTAMPTZ
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
    def list_sessions(cls, prefix: str = "") -> list:
        """返回所有 session 名称中带指定前缀的账号序号列表（升序）。
        例如 prefix='tg_session_' 返回 [1,2,3]，用于重启后自动恢复账号。"""
        try:
            conn = cls.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT name FROM tg_sessions WHERE name LIKE %s",
                        (prefix + '%',),
                    )
                    rows = cur.fetchall()
            finally:
                cls.putconn(conn)
        except Exception as e:
            log.warning(f"⚠️ list_sessions[{prefix}] 失败: {e}")
            return []
        nums = []
        for (name,) in rows:
            tail = name[len(prefix):]
            try:
                nums.append(int(tail))
            except ValueError:
                continue
        return sorted(nums)

    @classmethod
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
    """返回 {uid: {"username": ..., "access_hash": ...}}。

    排序固定按录入时间从新到旧（created_at DESC）：老板「默认从所有群聊以从新
    到旧录入的人开始群发」。sender 那边是 list(targets.keys()) 入队，字典顺序
    就是发货顺序，所以这里排好就行，不须动发送逻辑。
    同一个人重复录入（被多个群各自补录一次）只会刷新来源，created_at 保持第一次
    入表的时间，不会被「挤」到新那一端。
    """
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT uid, username, access_hash FROM targets "
                "ORDER BY created_at DESC, uid")
            return {row[0]: {"username": row[1], "access_hash": row[2] or 0} for row in cur.fetchall()}
    finally:
        DB.putconn(conn)


def db_add_targets(items):
    """items: list of (uid, username, access_hash) 或
    (uid, username, access_hash, via, source_group, member_since)。

    老调用方传 3 元组照样能用；同一 uid 再来一次只更新来源信息，不产生重复。
    """
    rows = []
    for it in items:
        if len(it) >= 6:
            uid, username, access_hash, via, sg, ms = it[0], it[1], it[2], it[3], it[4], it[5]
        else:
            uid, username, access_hash = it[0], it[1], it[2]
            via, sg, ms = "", 0, None
        rows.append((str(uid), username or "", access_hash or 0,
                     via or "", int(sg or 0), ms))
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO targets (uid, username, access_hash, via, source_group, member_since) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (uid) DO UPDATE SET "
                "username = EXCLUDED.username, access_hash = EXCLUDED.access_hash, "
                "via = CASE WHEN EXCLUDED.via <> '' THEN EXCLUDED.via ELSE targets.via END, "
                "source_group = CASE WHEN EXCLUDED.source_group <> 0 "
                "                   THEN EXCLUDED.source_group ELSE targets.source_group END, "
                "member_since = COALESCE(EXCLUDED.member_since, targets.member_since)",
                rows,
            )
        conn.commit()
    finally:
        DB.putconn(conn)


def db_watch_cursor(gid):
    """取某个群读到哪条消息了；没有记录返回 0（第一次从头翻）。"""
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT last_msg_id FROM watch_cursor WHERE group_id = %s", (int(gid),))
            row = cur.fetchone()
            return int(row[0] or 0) if row else 0
    finally:
        DB.putconn(conn)


def db_watch_set(gid, title, last_msg_id, new_since_last=None):
    """推进监听游标。只在往前走了的时候才改，避免并发把水位线倒退。"""
    try:
        gid, last_msg_id = int(gid), int(last_msg_id or 0)
    except (TypeError, ValueError):
        return
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO watch_cursor (group_id, title, last_msg_id, last_scan_at, updated_at)
                VALUES (%s, %s, %s, now(), now())
                ON CONFLICT (group_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    last_msg_id = GREATEST(watch_cursor.last_msg_id, EXCLUDED.last_msg_id),
                    last_scan_at = now(),
                    updated_at = now()
            """, (gid, (title or "")[:60], last_msg_id))
            if new_since_last is not None:
                cur.execute("UPDATE watch_cursor SET new_since_last = %s WHERE group_id = %s",
                            (int(new_since_last), gid))
        conn.commit()
    finally:
        DB.putconn(conn)


def db_watch_list():
    """所有群的监听进度（按最后扫描时间倒序）。"""
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT group_id, title, last_msg_id, last_scan_at, new_since_last
                FROM watch_cursor ORDER BY updated_at DESC
            """)
            return cur.fetchall()
    except Exception as e:
        log.warning(f"读监听进度失败: {e}")
        return []
    finally:
        DB.putconn(conn)


def db_targets_by_via():
    """名单构成：各来源通道各多少人（members/speakers/watch/未知）。"""
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(NULLIF(via, ''), '未知'), COUNT(*) FROM targets GROUP BY 1
            """)
            return dict(cur.fetchall())
    except Exception as e:
        log.warning(f"名单构成统计失败: {e}")
        return {}
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


def db_unsent_stats():
    """名单里还没发过的人：返回 (未发人数, 名单总数)。

    「发过」按 sent_log 去重算（任何一个号发过就算），跟看板上那个「已发(去重)」同一口径；
    开了「重复推广」也不影响这里的定义——名单里只要没进过 sent_log，就是没发过。
    """
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM targets t "
                "WHERE NOT EXISTS (SELECT 1 FROM sent_log s WHERE s.uid = t.uid)")
            unsent = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM targets")
            total = cur.fetchone()[0]
            return unsent, total
    finally:
        DB.putconn(conn)


def db_unsent_by_group(limit=10):
    """未发送按来源群分组（从新到旧）：[(群名, 未发人数, 名单总数), ...]。

    没来源群的（早期旧数据/手动导入）归到「未记录来源」，不丢人。
    """
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(NULLIF(t.source_group, 0), 0) AS sg,
                       COUNT(*) FILTER (WHERE NOT EXISTS
                           (SELECT 1 FROM sent_log s WHERE s.uid = t.uid)) AS unsent,
                       COUNT(*) AS total,
                       MAX(t.created_at) AS newest
                FROM targets t
                GROUP BY 1
                ORDER BY newest DESC
                LIMIT %s
            """, (int(limit),))
            rows = cur.fetchall()
            cur.execute("SELECT group_id, title, username FROM groups_info")
            names = {int(r[0]): (r[1] or r[2] or str(r[0])) for r in cur.fetchall()}
        out = []
        for sg, unsent, total, _newest in rows:
            label = names.get(int(sg)) if sg else None
            out.append((label or ("未记录来源" if not sg else f"群 {sg}"),
                        int(unsent), int(total)))
        return out
    finally:
        DB.putconn(conn)


def db_clear_targets():
    """清空名单（重新拉取前调用，避免旧名单混入）。返回清除条数。"""
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM targets")
            n = cur.fetchone()[0]
            cur.execute("DELETE FROM targets")
        conn.commit()
        return n
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


def db_delete_group(gid):
    """从 groups_info 表删除指定群记录，返回是否删到。"""
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM groups_info WHERE group_id = %s", (int(gid),))
            conn.commit()
            return cur.rowcount > 0
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


# ---- 账号冷却记账（撞 430 后护号：到点前不参与群发） ----
def db_mark_cooldown(account_no, seconds, reason=""):
    """标记账号冷却 seconds 秒。已存在则只延长不缩短（避免刚撞完又被放出来硬撞）。"""
    try:
        seconds = int(seconds or 0)
    except Exception:
        seconds = 0
    if seconds <= 0:
        return
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM account_cooldown WHERE until_at <= now()")
            cur.execute("""
                INSERT INTO account_cooldown (account_no, until_at, reason)
                VALUES (%s, now() + (%s * interval '1 second'), %s)
                ON CONFLICT (account_no) DO UPDATE SET
                    until_at = GREATEST(account_cooldown.until_at,
                                        now() + (%s * interval '1 second')),
                    reason = EXCLUDED.reason,
                    updated_at = now()
            """, (account_no, seconds, (reason or "")[:200], seconds))
        conn.commit()
    except Exception as e:
        log.warning(f"冷却记账写入失败(不影响群发): {e}")
    finally:
        DB.putconn(conn)


def db_cooldowns():
    """返回仍在冷却中的账号 {account_no: {"seconds": 剩余秒, "reason": 原因}}。"""
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT account_no,
                       GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (until_at - now())))::int),
                       reason
                FROM account_cooldown
            """)
            rows = cur.fetchall()
    finally:
        DB.putconn(conn)
    return {int(r[0]): {"seconds": int(r[1]), "reason": r[2] or ""}
            for r in rows if int(r[1]) > 0}


def db_clear_cooldown(account_no=None):
    """清除冷却记账：传 account_no 清单个（发送成功即解除），不传清全部。"""
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            if account_no is None:
                cur.execute("DELETE FROM account_cooldown")
            else:
                cur.execute("DELETE FROM account_cooldown WHERE account_no = %s",
                            (account_no,))
        conn.commit()
    except Exception as e:
        log.warning(f"冷却记账清除失败: {e}")
    finally:
        DB.putconn(conn)


# =====================================================================
#  多人共管：operators / op_log / campaigns
# =====================================================================
def db_load_operators():
    """返回 {uid: name}（仅 enabled）。失败返回 None，调用方保留旧缓存。"""
    try:
        conn = DB.getconn()
    except Exception as e:
        log.warning(f"读取操作员列表失败（连接）: {e}")
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT uid, name FROM operators WHERE enabled = TRUE")
            return {int(r[0]): (r[1] or "") for r in cur.fetchall()}
    except Exception as e:
        log.warning(f"读取操作员列表失败: {e}")
        return None
    finally:
        DB.putconn(conn)


def db_add_operator(uid, name="", added_by=0) -> bool:
    try:
        conn = DB.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO operators (uid, name, enabled, added_by, created_at)
                    VALUES (%s, %s, TRUE, %s, now())
                    ON CONFLICT (uid) DO UPDATE
                    SET name = EXCLUDED.name, enabled = TRUE, added_by = EXCLUDED.added_by
                """, (int(uid), (name or "")[:64], int(added_by or 0)))
            conn.commit()
            return True
        finally:
            DB.putconn(conn)
    except Exception as e:
        log.warning(f"添加操作员失败: {e}")
        return False


def db_drop_operator(uid) -> bool:
    """停用操作员（软删除，保留审计关联）。"""
    try:
        conn = DB.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE operators SET enabled = FALSE WHERE uid = %s", (int(uid),))
                n = cur.rowcount
            conn.commit()
            return n > 0
        finally:
            DB.putconn(conn)
    except Exception as e:
        log.warning(f"停用操作员失败: {e}")
        return False


def db_log_op(actor_uid, actor, action, detail=""):
    """写审计日志（失败不影响主流程）。"""
    try:
        conn = DB.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO op_log (actor_uid, actor_name, action, detail) VALUES (%s, %s, %s, %s)",
                    (int(actor_uid or 0), (actor or "")[:64], (action or "")[:64],
                     (detail or "")[:500]),
                )
            conn.commit()
        finally:
            DB.putconn(conn)
    except Exception as e:
        log.warning(f"审计日志写入失败: {e}")


def db_op_log_since(uid, since_epoch, actions=None):
    """查某人在 since_epoch 之后的操作记录（用于确认是不是自己重复点了）。"""
    try:
        conn = DB.getconn()
        try:
            with conn.cursor() as cur:
                sql = ("SELECT action, detail, created_at FROM op_log "
                       "WHERE actor_uid = %s AND created_at > to_timestamp(%s)")
                args = [int(uid), int(since_epoch)]
                if actions:
                    sql += " AND action = ANY(%s)"
                    args.append(list(actions))
                cur.execute(sql + " ORDER BY created_at DESC LIMIT 5", args)
                return cur.fetchall()
        finally:
            DB.putconn(conn)
    except Exception as e:
        log.warning(f"审计查询失败: {e}")
        return []


def db_op_log_recent(limit=20):
    try:
        conn = DB.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT actor_name, actor_uid, action, detail, created_at
                    FROM op_log ORDER BY id DESC LIMIT %s
                """, (int(limit),))
                return cur.fetchall()
        finally:
            DB.putconn(conn)
    except Exception as e:
        log.warning(f"审计读取失败: {e}")
        return []


def db_campaign_start(uid, actor, group_key, group_title, target_count, text_preview=""):
    """登记一次群发任务归属，返回 campaign id（失败返回 None）。"""
    try:
        conn = DB.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO campaigns
                        (actor_uid, actor_name, group_key, group_title,
                         target_count, status, text_preview)
                    VALUES (%s, %s, %s, %s, %s, 'running', %s)
                    RETURNING id
                """, (int(uid or 0), (actor or "")[:64], str(group_key or "")[:64],
                      (group_title or "")[:120], int(target_count or 0),
                      (text_preview or "")[:200]))
                cid = cur.fetchone()[0]
            conn.commit()
            return int(cid)
        finally:
            DB.putconn(conn)
    except Exception as e:
        log.warning(f"群发任务登记失败: {e}")
        return None


def db_campaign_finish(campaign_id, sent_count, status="done"):
    if not campaign_id:
        return
    try:
        conn = DB.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE campaigns
                    SET sent_count = %s, status = %s, finished_at = now()
                    WHERE id = %s
                """, (int(sent_count or 0), (status or "done")[:24], int(campaign_id)))
            conn.commit()
        finally:
            DB.putconn(conn)
    except Exception as e:
        log.warning(f"群发任务收尾失败: {e}")


def db_campaign_leaderboard(days=7):
    """按人统计近 N 天的群发任务与发出量。"""
    try:
        conn = DB.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT actor_uid,
                           MAX(actor_name),
                           COUNT(*),
                           COALESCE(SUM(sent_count), 0),
                           COALESCE(SUM(target_count), 0)
                    FROM campaigns
                    WHERE created_at > now() - (%s || ' days')::interval
                    GROUP BY actor_uid
                    ORDER BY COALESCE(SUM(sent_count), 0) DESC
                """, (str(int(days)),))
                return cur.fetchall()
        finally:
            DB.putconn(conn)
    except Exception as e:
        log.warning(f"群发统计失败: {e}")
        return []
