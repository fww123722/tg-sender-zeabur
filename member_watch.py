#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""进群之后一直盯着：全部群每轮都「拉成员 + 读近 3 天消息」，不用人再去点。

老板原话（21:45）：「自动补录改为全部群读取3天内的消息，拉取成员，删掉重拉成员逻辑。」
所以每轮补扫对每个群做两件事：
  1) 拉一次群成员：只收近 N 天（默认 3 天）进群的（入群时间来自 participant.date）；
  2) 读近 N 天的消息：发言人也收进名单 —— 成员页被锁、拿不到名单的群就靠这条。
另外还挂着实时监听：谁发了言 / 谁进了群，当场入名单，不等下一轮。

为什么用「发消息的人」当信号：成员页被群主设成「只显示管理员」时，名单在服务端就拿不到，
但「这条消息谁发的」是公开的 —— 这是不当管理员也能持续拿到新人的唯一通路。

所有写入都是「追加/合并」，绝不清空名单（不清空是老板定死的规矩）。
"""
import asyncio
import os
import time
from datetime import datetime, timedelta, timezone

from telethon import events
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    MessageActionChatAddUser, MessageActionChatJoinedByLink,
    MessageActionChatJoinedByRequest, User,
)
try:  # 部分 telethon 版本没有这个类型，拿到就用，拿不到不影响主流程
    from telethon.tl.types import MessageActionUserJoined
except ImportError:  # pragma: no cover
    MessageActionUserJoined = None

from config import log
from db import (
    db_add_targets, db_watch_cursor, db_watch_set, db_watch_list,
    db_targets_by_via, db_count_targets, db_get_all_groups,
)

# 新人窗口（天）：只收这段时间内进群/第一次冒出来的人（老板点名的 3 天）
# 写死在此，不给开关也不给设置项：要改就改这一行（或环境变量）重部。
JOIN_WINDOW_DAYS = int(os.environ.get("JOIN_WINDOW_DAYS", "3"))
# 兜底补扫间隔（分钟）
SWEEP_INTERVAL_MIN = int(os.environ.get("MEMBER_SWEEP_MIN", "20"))
# 单次补扫最多翻多少条（防大群把请求打爆）
SWEEP_MSG_LIMIT = int(os.environ.get("MEMBER_SWEEP_LIMIT", "300"))
# 每轮拉成员的人数上限（同 collector.collect_members 默认量级，只取近 3 天进群的）
MEMBER_PULL_LIMIT = int(os.environ.get("MEMBER_PULL_LIMIT", "5000"))

_claims = {}          # {gid: title} 要盯的群（来自 groups_info）
_clients = []         # [(acc_no, client)]
_seen = set()         # 已处理过的 uid，避免每条消息都写库
_seeing = set()       # 正在处理中的 uid（并发去重）
_stats = {"realtime": 0, "sweep": 0, "members": 0, "msgs": 0, "groups": 0,
          "last_sweep": 0, "last_err": "", "started_at": 0}
_task = None
_HANDLED_TYPES = tuple(t for t in (MessageActionChatAddUser,
                                   MessageActionChatJoinedByLink,
                                   MessageActionChatJoinedByRequest,
                                   MessageActionUserJoined) if t)


def window_days() -> int:
    """新人窗口天数（固定值，不设可改项）。"""
    return JOIN_WINDOW_DAYS


def _cutoff():
    return datetime.now(timezone.utc) - timedelta(days=JOIN_WINDOW_DAYS)


def _ok_user(u, my_id=None):
    """能不能当群发对象用：要有 username、不是 bot/deleted/自己。"""
    if not isinstance(u, User):
        return False
    if u.bot or u.deleted or not u.username:
        return False
    if my_id is not None and u.id == my_id:
        return False
    return True


def _add(u, gid, via, when=None):
    """一个人进名单：只追加/合并，绝不清空（不清空是老板定死的规矩）。

    内存里记一份已处理 uid，避免每条群消息都回写一次库（大群一小时就能写几千次）。
    via 用 realtime（当场看到）/ sweep（补扫翻历史）区分，统计时能看出谁在干活。
    """
    try:
        uid = int(u.id)
    except (TypeError, ValueError, AttributeError):
        return False
    if uid in _seen or uid in _seeing:
        return False
    _seeing.add(uid)
    try:
        db_add_targets([(str(uid), getattr(u, "username", "") or "",
                         getattr(u, "access_hash", 0) or 0, via, int(gid or 0),
                         when or datetime.now(timezone.utc))])
        _seen.add(uid)
        _stats[via] = _stats.get(via, 0) + 1
        return True
    except Exception as e:
        log.info(f"[补录] 入库失败 uid={uid}: {type(e).__name__}: {str(e)[:80]}")
        return False
    finally:
        _seeing.discard(uid)


def _gid_of(event=None, chat_id=None):
    """群 id 归一化：Telethon 的 chat_id 对频道/超级群是原始正数，
    basic 群也可能是正数或负的 -chat_id，一律取绝对值对齐 groups_info。"""
    raw = chat_id
    if raw is None and event is not None:
        raw = getattr(event, "chat_id", None)
    try:
        return abs(int(raw))
    except (TypeError, ValueError):
        return 0


def refresh_groups(rows=None):
    """把要盯的群换成 groups_info 里的最新集合（新加的群自动纳入，删掉的自动移出）。"""
    try:
        rows = rows if rows is not None else db_get_all_groups()
        new = {int(g[0]): (g[1] or "") for g in (rows or [])}
    except Exception as e:
        log.info(f"[补录] 读群列表失败(忽略本轮): {e}")
        return _stats["groups"]
    _claims.clear()
    _claims.update(new)
    _stats["groups"] = len(_claims)
    return len(_claims)


# =====================================================================
#  实时监听：挂在每个已登录账号上
# =====================================================================
async def _on_message(event):
    """群里冒出一条消息：能识别出发言人/进群的人，就直接记进名单。

    常驻自动跑，没有开关（老板：「自动的不需要开关」）。
    """
    gid = _gid_of(event)
    if not gid or gid not in _claims:
        return
    _stats["msgs"] += 1
    when = getattr(event, "date", None)
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    try:
        my_id = None
        client = event.client
        try:
            me = await client.get_me()
            my_id = me.id if me else None
        except Exception:
            pass
        hits = []
        act = getattr(event, "action", None)
        if isinstance(act, MessageActionChatAddUser):
            # 「管理员把 X 拉进群」：users 是 id 列表，要查一次才拿到 username
            for uid in (getattr(act, "users", None) or []):
                hits.append(await _resolve(client, uid))
        elif isinstance(act, (MessageActionChatJoinedByLink,
                              MessageActionChatJoinedByRequest,
                              MessageActionUserJoined)):
            uid = getattr(act, "user_id", None) or getattr(getattr(act, "peer", None),
                                                            "user_id", None)
            hits.append(await _resolve(client, uid) if uid else getattr(event, "sender", None))
        else:
            # 普通发言：sender 就地可用（消息更新里自带实体），不用再发请求
            s = None
            try:
                s = await event.get_sender()
            except Exception:
                s = getattr(event, "sender", None)
            hits.append(s)
        new = 0
        for u in hits:
            if u is None:
                continue
            if not _ok_user(u, my_id):
                continue
            if _add(u, gid, "realtime", when):
                new += 1
        if new:
            log.info(f"[补录] 「{_claims.get(gid) or gid}」实时 +{new} 人"
                     f"（累计 {db_count_targets()}）")
    except Exception as e:
        # 监听里绝不抛：一条消息解析失败不该把整个 client 的事件循环带崩
        log.info(f"[补录] 实时处理出错(忽略): {type(e).__name__}: {str(e)[:100]}")


async def _resolve(client, uid):
    """把 id 换成带 username 的 User（新进群的人常常只有 id）。拿不到就 None。"""
    try:
        ent = await client.get_entity(int(uid))
        return ent if isinstance(ent, User) else None
    except Exception:
        return None


def attach(client):
    """给一个账号挂上监听（幂等：同一个 client 只挂一次）。"""
    try:
        if getattr(client, "_mw_attached", False):
            return True
        client.on(events.NewMessage(incoming=True))(_on_message)
        client._mw_attached = True
        return True
    except Exception as e:
        log.info(f"[补录] 挂监听失败: {type(e).__name__}: {str(e)[:80]}")
        return False


def track_client(client, acc_no=None):
    """把账号记进补录的可用名单（新登录/热替换都要调，否则补扫轮不到它）。"""
    try:
        exists = any(c is client for _n, c in _clients)
    except Exception:
        exists = False
    if not exists:
        _clients.append((int(acc_no or 0), client))
    return len(_clients)


# =====================================================================
#  兜底补扫：重启/掉线之后把漏掉的那段历史补上
# =====================================================================
async def _pull_members(client, gid, title):
    """一个群拉一次成员，只收近窗口天内进群的（只追加/合并，绝不清空）。

    拿不到成员列表（非管理员/服务端锁）就返回 -1呌调用方知道只能靠消息通道。
    这里故意在函数内 import collector：collector 反过来会 import 本模块的 warm_groups，
    放顶部会变成循环 import。
    """
    try:
        from collector import collect_members
        res = await collect_members(client, _peer(gid), limit=MEMBER_PULL_LIMIT,
                                   recent_only_days=0, join_days=JOIN_WINDOW_DAYS,
                                   via="member_sweep")
        if not isinstance(res, str) or not res.startswith("✅"):
            return -1
        # "✅ 从「xxx」拉取完成：新增有效成员 N 人" 里拿 N，拿不到也不影响主流程
        import re as _re
        m = _re.search(r"新增[^\d]{0,8}(\d+)", res) or _re.search(r"入[^\d]{0,6}(\d+)", res)
        n = int(m.group(1)) if m else 0
        _stats["members"] = _stats.get("members", 0) + n
        return n
    except Exception as e:
        log.info(f"[补录] 拉成员「{title or gid}」出错(忽略): {type(e).__name__}: {str(e)[:90]}")
        return -1


async def _sweep_one(client, gid, title):
    """一个群补扫一轮：先拉成员（近3天进群的），再读近 3 天消息收发言人。

    老板 21:45 的口径：不再只从水位线往后读，而是**每轮都读窗口内的全部消息**，
    因为重启/掉线之后光标可能比 3 天更新，只从光标往后读会漏人。
    返回新入人数（拉成员 + 读消息）；负数 = 这个群这个账号读不动。
    """
    added = 0
    top = db_watch_cursor(gid)
    cutoff = _cutoff()
    my_id = None
    try:
        me = await client.get_me()
        my_id = me.id if me else None
    except Exception:
        pass
    try:
        entity = await client.get_entity(_peer(gid))
    except Exception:
        return -1        # 这个账号不在这个群：换下一个，别记失败
    # 1) 先拉一次成员（近 3 天进群的）
    got_m = await _pull_members(client, gid, title)
    if got_m > 0:
        added += got_m
    # 2) 再读近 3 天的消息，发言人入名单
    try:
        async for msg in client.iter_messages(entity, limit=SWEEP_MSG_LIMIT):
            d = getattr(msg, "date", None)
            if d is not None and d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            if d is not None:
                top = max(top, int(getattr(msg, "id", 0) or 0))
                if d < cutoff:
                    break   # 从新往老翻，翻过 3 天边界后面只会更老
            sid = getattr(msg, "sender_id", None)
            if not sid or sid in _seen or sid in _seeing:
                continue
            u = getattr(msg, "sender", None)
            if not isinstance(u, User):
                u = await _resolve(client, sid)
            if u is None or not _ok_user(u, my_id):
                continue
            if _add(u, gid, "sweep", d):
                added += 1
    except FloodWaitError as e:
        secs = getattr(e, "seconds", 0) or 0
        _stats["last_err"] = f"补扫撞限流 {secs}s（群「{title or gid}」）"
        log.info(f"[补录] {secs} 秒限流，跳过「{title}」本轮")
        return -1
    except Exception as e:
        _stats["last_err"] = f"{type(e).__name__}: {str(e)[:80]}"
        log.info(f"[补录] 补扫「{title or gid}」出错: {type(e).__name__}: {str(e)[:100]}")
        return -1
    if top > db_watch_cursor(gid):
        try:
            db_watch_set(gid, title, top, new_since_last=added)
        except Exception as e:
            log.info(f"[补录] 游标推进失败(忽略): {e}")
    return added


def _peer(gid):
    """groups_info 存的是原始正数 id；默认按频道/超级群解析，失败再按 basic 群试。"""
    from telethon.tl.types import PeerChannel, PeerChat
    return PeerChannel(int(gid))


async def sweep_once(reason="手动"):
    """全部群各扫一轮（拉成员 + 读近3天消息；谁在群里就用谁，一个号读不动就换一个）。"""
    refresh_groups()
    if not _claims:
        return "表里还没有群记录，先去「加群」或「批量导入」。"
    total_new = 0
    lines = []
    for gid, title in list(_claims.items()):
        got = -1
        for acc_no, client in _clients:
            try:
                if not await client.is_user_authorized():
                    continue
            except Exception:
                continue
            got = await _sweep_one(client, gid, title)
            if got >= 0:
                break
            await asyncio.sleep(0.3)
        if got > 0:
            total_new += got
            lines.append(f"  · 「{(title or str(gid))[:18]}」+{got} 人")
        elif got < 0:
            lines.append(f"  · 「{(title or str(gid))[:18]}」没读到（不在群/受限）")
        await asyncio.sleep(0.8)     # 别把请求连成一片，护号
    _stats["last_sweep"] = int(time.time())
    await _notify(f"📡 补录{reason}完成：{len(_claims)} 个群，新入 {total_new} 人"
                  "（已拉成员 + 已读近 3 天消息）"
                  + (("\n" + "\n".join(lines[:12])) if lines else "\n（没有新人）"))
    return f"补扫完成：新增 {total_new} 人"


async def _notify(text):
    """给 admin 报一句（不硬依赖：没 bot / 没网的时候悄悄跳过）。"""
    bot = _stats.get("bot")
    to = _stats.get("owner")
    if not bot or not to:
        log.info(text)
        return
    try:
        await bot.send_message(to, text)
    except Exception as e:
        log.info(f"{text}（通知发不出去: {type(e).__name__}）")


async def _loop():
    """常驻循环：每隔 SWEEP_INTERVAL_MIN 分钟补扫一轮。"""
    while True:
        try:
            await asyncio.sleep(SWEEP_INTERVAL_MIN * 60)
            await sweep_once(reason="定时")
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.info(f"[补录] 循环出错(继续跑): {type(e).__name__}: {str(e)[:120]}")


def status_text():
    """给 Bot 看的状态摘要（开了没、盯几个群、各通道各多少人）。"""
    by = {}
    try:
        by = db_targets_by_via() or {}
    except Exception:
        pass
    rows = []
    try:
        rows = db_watch_list()[:8]
    except Exception:
        pass
    out = ["📡 进群后自动补录：全部群常驻自动，无需设置",
           f"固定近 {JOIN_WINDOW_DAYS} 天：每轮拉成员（只收这段日期内进群的）"
           f"+ 读这段日期内的消息（发言人也收）",
           f"盯的群：{len(_claims)} 个 | 在线账号：{len(_clients)} 个",
           f"本轮统计：实时收 {_stats['realtime']} 人 / 补扫收 {_stats['sweep']} 人"
           f" / 拉成员收 {_stats.get('members', 0)} 人"
           f"（看到 {int(_stats['msgs'])} 条群消息）"]
    lst = time.strftime("%m-%d %H:%M", time.localtime(_stats["last_sweep"])) \
        if _stats["last_sweep"] else "还没跑过"
    out.append(f"上次补扫：{lst}（每 {SWEEP_INTERVAL_MIN} 分钟一轮）")
    if by:
        out.append("名单构成：" + "、".join(f"{k} {v} 人" for k, v in by.items()))
    if rows:
        out.append("各群进度：")
        for gid, title, last_id, at, newn in rows:
            out.append(f"  · {(title or str(gid))[:16]} 读到 msg={last_id}"
                       + (f" 上轮+{newn}" if newn else ""))
    if _stats["last_err"]:
        out.append(f"最后一次报错：{_stats['last_err']}")
    return "\n".join(out)


def start(accounts=None, bot=None, owner=None):
    """上线：挂监听 + 起补扫循环。accounts 是 [(acc_no, client, phone)]。"""
    global _task
    _stats["started_at"] = int(time.time())
    _stats["bot"] = bot
    _stats["owner"] = owner
    n = refresh_groups()
    attached = 0
    for acc_no, client, _ph in (accounts or []):
        _clients.append((acc_no, client))
        if attach(client):
            attached += 1
    # 已处理过的 uid 用当前名单打底，避免第一遍把老名单重复写一遍
    try:
        from db import db_load_targets
        _seen.update(int(k) for k in (db_load_targets() or {}).keys())
    except Exception as e:
        log.info(f"[补录] 预载名单失败(忽略): {e}")
    if _task is None:
        _task = asyncio.ensure_future(_loop())
    log.info(f"[补录] 已启动：{attached} 个账号挂监听，{n} 个群在盯，"
             f"窗口 {JOIN_WINDOW_DAYS} 天，补扫每 {SWEEP_INTERVAL_MIN} 分钟")
    return attached, n


def stop():
    """停掉常驻循环并清空已挂监听的账号（热替换时用）。"""
    global _task
    if _task is not None:
        try:
            _task.cancel()
        except Exception:
            pass
        _task = None
    _clients.clear()


def restart(accounts=None, bot=None, owner=None):
    """账号热替换后重新挂一轮（旧 client 已断开，监听不重挂会默默停摆）。"""
    stop()
    return start(accounts, bot=bot, owner=owner)


def warm_groups(rows=None):
    """加群成功后立刻把新群纳入监听（不用等下一轮补扫）。"""
    before = set(_claims)
    refresh_groups(rows)
    return sorted(set(_claims) - before)



