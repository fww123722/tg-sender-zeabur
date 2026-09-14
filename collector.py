#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集模块：拉取群成员、加入群组、采集频道历史消息（文案池）。"""
import re
import random
import time
import asyncio

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    InviteRequestSentError,
    UserAlreadyParticipantError,
)
from ops_state import get as ops_get, set as ops_set
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.functions.messages import (
    CheckChatInviteRequest, ImportChatInviteRequest, DeleteChatUserRequest,
)
from telethon.tl.types import (
    Channel, Chat, ChatInvite, ChatInviteAlready, User,
    PeerUser,
    UserStatusOnline, UserStatusOffline, UserStatusRecently,
    UserStatusLastWeek, UserStatusLastMonth, UserStatusEmpty,
)


def _is_recently_active(user, days):
    """判断用户是否在最近 days 天内上线过（近似）。
    隐私隐藏精确时间时，Recently/LastWeek 视为活跃；LongAgo/LastMonth/Empty 排除。"""
    from datetime import datetime, timezone
    st = user.status
    if st is None:
        return False
    if isinstance(st, (UserStatusOnline, UserStatusRecently, UserStatusLastWeek)):
        return True
    if isinstance(st, UserStatusOffline):
        wo = getattr(st, "was_online", None)
        if wo is None:
            return False
        try:
            now = datetime.now(timezone.utc)
            if wo.tzinfo is None:
                wo = wo.replace(tzinfo=timezone.utc)
            return (now - wo).total_seconds() <= days * 86400
        except Exception:
            return False
    return False

from config import log
from db import db_add_targets, db_count_targets, db_add_group, db_get_all_groups


def _norm_gid(did, entity=None):
    """对话 id 归一化为原始正数 id（与 groups_info 表存法一致）。
    basic群(Chat): id=-chat_id → chat_id；频道/超级群(Channel): id=-100channel_id → channel_id。"""
    from telethon.tl.types import Chat
    n = int(did)
    if entity is not None and isinstance(entity, Chat):
        return abs(n)
    if n < 0:
        s = str(n)
        if s.startswith("-100"):
            return int(s[4:])
        return -n
    return n


async def _resolve_entity(client, peer_arg):
    """解析群实体（失败抛异常）。纯数字 ID 在 session 无缓存时会查不到，先扫对话列表建立缓存再重试。"""
    try:
        return await client.get_entity(peer_arg)
    except Exception as first_err:
        # 仅对纯数字 ID/带-100前缀的情况做对话扫描回退
        s = str(peer_arg).strip()
        if not re.fullmatch(r"-?\d+", s):
            raise first_err
        try:
            target_id = int(s)
        except ValueError:
            raise first_err
        # 扫描对话列表：既能命中实体，也顺带把实体写入 session 缓存
        raw = int(s[4:]) if s.startswith("-100") else abs(target_id)
        # 先按 peer 类型直接试（session 有缓存时一步到位）
        from telethon.tl.types import PeerChannel, PeerChat
        for peer in (PeerChannel(raw), PeerChat(raw)):
            try:
                return await client.get_entity(peer)
            except Exception:
                pass
        # 兜底：扫对话按归一化 id 精确匹配（兼容 basic群/超级群/频道 三种形态）
        async for dialog in client.iter_dialogs(limit=200):
            if _norm_gid(dialog.id, dialog.entity) == raw:
                return dialog.entity
        raise first_err


async def _members_meta(client, entity):
    """查这个群「真实成员数」+「当前账号能不能看全名单」。

    返回 (total, can_view, hidden, admins)：拿不到就是全 0/None，绝不抛异常。
    total 来自 ChatFull.participants_count——这个数是服务端统计，不受
    「成员页只显示管理员」影响，所以能拿来判断我们是不是只读到了一角。
    """
    try:
        if isinstance(entity, Channel):
            full = (await client(GetFullChannelRequest(entity))).full_chat
        elif isinstance(entity, Chat):
            full = (await client(GetFullChatRequest(entity.id))).full_chat
        else:
            return 0, None, None, None
        total = int(getattr(full, "participants_count", 0) or 0)
        can_view = getattr(full, "can_view_participants", None)
        hidden = getattr(full, "participants_hidden", None)
        admins = int(getattr(full, "admins_count", 0) or 0)
        return total, can_view, hidden, admins
    except Exception as e:
        log.warning(f"[名单] 查成员总数失败(忽略): {type(e).__name__}: {e}")
        return 0, None, None, None


def _join_since(user):
    """从成员对象上拿「他什么时候进的这个群」。

    telethon 的 iter_participants 会把 participant 附到 user 上，
    ChannelParticipant.date 就是入群时间（拉到的顺序、筛 3 天新人全靠它）。
    采发言人那条路拿不到（消息里的 sender 不带 participant），只能退回发言时间。
    """
    p = getattr(user, "participant", None)
    d = getattr(p, "date", None) or getattr(p, "joined_date", None)
    if d is None:
        return None
    try:
        from datetime import timezone
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _joined_within(user, days):
    """入群时间在 days 天内（拿不到入群时间 -> 不算新人，保守排除）。"""
    d = _join_since(user)
    if d is None:
        return False
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    try:
        return (now - d).total_seconds() <= days * 86400
    except Exception:
        return False


def _target_from_user(user, my_id, recent_only_days, skipped, join_days=0):
    """按「能不能群发」的标准清洗一个成员/发言人。

    返回 (uid, username, access_hash, via, source_group, member_since) 或 None
    （None = 该过滤掉，已计入 skipped）。
    拉名单与采发言人共用这一套，避免两条路出来的名单干净程度不一致。

    join_days：只留最近 N 天进群的（老板：进群后只关心新面孔）。
    拿不到入群时间的（比如采发言人那条路）不当新人，走原有全量逻辑。
    """
    if user.bot:
        skipped["bot"] += 1
        return None
    if user.deleted:
        skipped["deleted"] += 1
        return None
    if not user.username:
        skipped["no_username"] += 1
        return None
    if my_id is not None and user.id == my_id:
        skipped["self"] += 1
        return None
    # 开关：只保留近期活跃成员（按上次上线时间）
    if recent_only_days and not _is_recently_active(user, recent_only_days):
        skipped["inactive"] += 1
        return None
    since = _join_since(user)
    if join_days and not _joined_within(user, join_days):
        skipped["old_member"] += 1
        return None
    return (str(user.id), user.username, getattr(user, "access_hash", 0) or 0,
            "", 0, since)


async def collect_members(client, peer_arg, limit=5000, recent_only_days=0,
                          join_days=0, via="members"):
    """从群/频道拉取成员并加入名单。

    join_days>0：只要最近 N 天进群的人（老板要的就是一进新群只吃新面孔）。
    """
    try:
        entity = await _resolve_entity(client, peer_arg)
    except Exception as e:
        estr = str(e)
        low = estr.lower()
        # 邀请链接失效/无效单独提示（这类不是实体缓存问题，重试无用）
        if "expired" in low or "not valid anymore" in low or "INVITE_HASH_EXPIRED" in estr:
            return ("❌ 邀请链接已过期失效（Telegram 报 INVITE_HASH_EXPIRED），这个链接用不了了。\n"
                    "💡 解释：群主重置过邀请链接 / 链接设了有效期或次数上限 / 群升级成超级群后旧 joinchat 链接全部作废。\n"
                    "✔ 正确做法：\n"
                    "  · 找群主重新要一条 t.me/+xxxx 邀请链接，用「加群」加入\n"
                    "  · 若账号本来已在群里：改用群名称里的数字 ID、或 t.me/群用户名，不要发邀请链接")
        if "USERNAME_NOT_OCCUPIED" in estr or "Could not find the input entity" in estr:
            return (f"❌ 找不到该群/频道: {estr}\n"
                    f"💡 提示：请确认链接形式正确（公开群用 t.me/用户名，纯数字 ID 仅适用于账号已在的群）。\n"
                    f"     若是刚进的群，先到「👥 账号管理 → 账号列表」同步一次对话后重试。")
        return f"❌ 找不到该群/频道: {estr}\n💡 提示：如果是刚进的群，先在账号管理里同步一次对话，或用群链接（@用户名 / t.me/xxx）重试。"
    # 取本账号 id，用于排除“账号自己”
    try:
        me = await client.get_me()
        my_id = me.id if me else None
    except Exception:
        my_id = None
    batch = []
    added = 0
    seen = 0          # Telegram 实际下发了多少条（含被排除的），用来识破「只给管理员」
    gid = int(getattr(entity, "id", 0) or 0)
    # 这个群到底多少人、本账号有没有权限看全（拿不到就是 0/None）
    real_total, can_view, hidden, admins_n = await _members_meta(client, entity)
    skipped = {"bot": 0, "deleted": 0, "no_username": 0, "self": 0, "inactive": 0,
               "old_member": 0}
    no_join_date = 0
    try:
        async for user in client.iter_participants(entity, limit=limit):
            # 排除：非用户对象 / 机器人 / 已注销(删除) / 无用户名 / 账号自己
            seen += 1
            if not isinstance(user, User):
                continue
            if join_days and _join_since(user) is None:
                no_join_date += 1
            item = _target_from_user(user, my_id, recent_only_days, skipped,
                                     join_days=join_days)
            if item is None:
                continue
            batch.append(item[:3] + (via or "members", gid, item[5]))
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
    # 只显示有效成员；被排除的各类单独说明，便于确认名单干净
    skip_msg = ""
    if any(skipped.values()):
        parts = []
        if skipped["bot"]:
            parts.append(f"机器人 {skipped['bot']}")
        if skipped["deleted"]:
            parts.append(f"已注销 {skipped['deleted']}")
        if skipped["no_username"]:
            parts.append(f"无用户名 {skipped['no_username']}")
        if skipped["self"]:
            parts.append(f"账号自己 {skipped['self']}")
        if skipped["inactive"]:
            parts.append(f"久未上线 {skipped['inactive']}")
        if skipped.get("old_member"):
            parts.append(f"老成员 {skipped['old_member']}")
        skip_msg = f"，已排除（{('、'.join(parts))}）"
    recent_note = f"（仅保留近{recent_only_days}天活跃）" if recent_only_days else ""
    join_note = (f"（只要近{join_days}天进群的新人）" if join_days else "")
    if join_days and no_join_date:
        join_note += f"，{no_join_date} 人拿不到入群时间没当新人算"
    # 读到的数远少于服务端统计的真实人数：肯定有东西把名单拦住了一半以上。
    # 拦住的原因有两种，文案分开说，别一律推给「只显示管理员」：
    #   A) 只读到个位数且不超管理员数 -> 群主把成员页设成只显示管理员（老板碰到的就是这个）
    #   B) 读到一大批但仍远少于一万-> 超大群本身列举不全部（Telegram 只给前面那部分）
    # seen 撞上 limit 是被自家上限截断（正常截短，不算被限制），所以要看 seen < limit。
    if real_total and seen < max(1, int(real_total * 0.9)) and seen < limit:
        acc = getattr(client, "phone", None) or "?"
        log.info(f"[名单] 「{name}」只读到 {seen}/{real_total}，名单拿不全"
                 f"(can_view={can_view} hidden={hidden} admins={admins_n}) acc={acc}")
        only_admins = bool(admins_n) and seen <= admins_n
        if only_admins:
            head = f"⚠️ 「{name}」把成员页设成了只显示管理员。"
            why = (f"   ❗ 这是 Telegram 服务端的限制，不是拉取失败、也不是链接过期，"
                   f"换个普通成员号重拉照样拿不到。\n"
                   f"   ✔ 要拿全名单只有两条路：该账号在这个群里是管理员；"
                   f"或群主关掉「显示成员」限制。\n"
                   f"   🗣 不当管理员也能补一波：下面会自动改从历史消息里"
                   f"把说过话的人采进名单。\n")
        else:
            head = f"⚠️ 「{name}」的名单拿不全（Telegram 只放行了前面一部分）。"
            why = (f"   ❗ 不是拉取失败：超大群 Telegram 本身就不让人一次列完成员，"
                   f"而普通成员也看不到完整名单。\n"
                   f"   ✔ 要更多人的话：用「按关键字搜成员」分批拉，或该账号当上管理员。\n")
        tip = []
        if admins_n:
            tip.append(f"其中管理员 {admins_n} 人")
        if can_view is False or hidden:
            tip.append("Telegram 已标记成员列表不可见")
        return (
            head + "\n"
            f"   群里真实 {real_total} 人，本次只读到 {seen} 人"
            + (f"（{'、'.join(tip)}）" if tip else "")
            + f"，已入表 {added} 人。\n"
            + why
            + f"   💡 名单共 {total} 人，可以先拿这部分跑群发。")
    cap_note = (
        f"（本群 {real_total} 人，受单次上限 {limit} 人限制）"
        if real_total and limit and real_total > limit else "")
    return (f"✅ 从「{name}」拉取完成：新增有效成员 {added} 人"
            f"{skip_msg}{join_note}{recent_note}{cap_note}，名单共 {total} 人")


async def collect_speakers(client, peer_arg, msg_limit=2000, recent_only_days=0,
                           via="speakers", min_date=None):
    """从群/频道「历史消息」里采发过言的人入名单（成员页被锁时的备用通道）。

    为什么走得通：成员名单是服务端就没往下发（换号也没用），但「这条消息是谁发的」
    本来就是公开的。采到的是「活跃发言人」，不是全量成员，且只能看到本账号可见的那段历史。
    只追加入名单，绝不清空（不拆别人正在跑的名单）。

    msg_limit：翻多少条历史。越大采越全，也越容易撞限流。
    min_date：只收这个时间之后的消息（「只读 3 天内」这条要求就落在这儿）。
    返回文案统一用 ✅/❌ 开头，与 collect_members 一致，调用方据此判断。
    """
    try:
        entity = await _resolve_entity(client, peer_arg)
    except Exception as e:
        return f"❌ 找不到该群/频道（采发言人前要先解析到群）: {str(e)[:120]}\n💡 用 t.me/用户名 或群 ID，且该账号需在群里。"

    try:
        me = await client.get_me()
        my_id = me.id if me else None
    except Exception:
        my_id = None

    name = getattr(entity, "title", str(peer_arg))
    gid = int(getattr(entity, "id", 0) or 0)
    counts = {}   # uid -> 发言条数（越活跃越该先进名单）
    objs = {}     # uid -> User 对象（可能是 min、缺 username）
    seen_time = {}  # uid -> 首次发言时间（当「他什么时候冒出来」的近似）
    msgs_read = 0
    too_old = 0     # 比 min_date 更早、已经不看的条数
    anon = 0      # 拿不到作者的条数（匿名发言/系统消息）
    flood = 0
    try:
        async for msg in client.iter_messages(entity, limit=msg_limit):
            if min_date is not None:
                md = getattr(msg, "date", None)
                if md is not None and md < min_date:
                    # 历史是从新往老翻的，一旦翻过窗口边界，后面只会更老 -> 直接收工
                    too_old += 1
                    break
            msgs_read += 1
            sid = getattr(msg, "sender_id", None)
            if not sid:
                anon += 1
                continue
            counts[sid] = counts.get(sid, 0) + 1
            if sid not in seen_time:
                seen_time[sid] = getattr(msg, "date", None)
            s = getattr(msg, "sender", None)
            if isinstance(s, User) and sid not in objs:
                objs[sid] = s
    except FloodWaitError as e:
        # 已经读到的那部分不丢：用本地已有的对象收尾，不再多发请求惹限流
        flood = getattr(e, "seconds", 0) or 0
    except Exception as e:
        return f"❌ 读取群历史失败: {type(e).__name__}: {str(e)[:140]}"

    # min 用户（只有昵称没 username）升级：挑发言最多的先试，单批 100、总量 300 锁顶，
    # 避免几百个号逐个查反而把自己坑进 FloodWait。
    UPGRADE_CAP = 300
    missing = [i for i in sorted(counts, key=lambda k: -counts[k])
               if not (isinstance(objs.get(i), User) and getattr(objs[i], "username", None))]
    tried_upgrade = 0
    if missing and not flood:
        for i in range(0, min(len(missing), UPGRADE_CAP), 100):
            chunk = missing[i:i + 100]
            tried_upgrade += len(chunk)
            try:
                got = await client.get_entity([PeerUser(int(x)) for x in chunk])
            except FloodWaitError as e:
                flood = getattr(e, "seconds", 0) or 0
                break
            except Exception as e:
                log.info(f"[发言人] 批量升级 min 用户失败(忽略): {type(e).__name__}: {e}")
                break
            for g in (got or []):
                if isinstance(g, User):
                    objs[g.id] = g

    real_total, _cv, _hd, _ad = await _members_meta(client, entity)
    batch = []
    added = 0
    skipped = {"bot": 0, "deleted": 0, "no_username": 0, "self": 0, "inactive": 0,
               "old_member": 0}
    for sid in sorted(counts, key=lambda k: -counts[k]):
        u = objs.get(sid)
        if not isinstance(u, User):
            # min 没升级成功：光有 id 发不了，归到 no_username 这类
            skipped["no_username"] += 1
            continue
        item = _target_from_user(u, my_id, recent_only_days, skipped)
        if item is None:
            continue
        # 发言人这条路拿不到 participant，入群时间用「第一次发言时间」近似
        batch.append((item[0], item[1], item[2], via or "speakers", gid,
                      item[5] or seen_time.get(sid)))
        added += 1
        if len(batch) >= 500:
            db_add_targets(batch)
            batch = []
    if batch:
        db_add_targets(batch)

    total = db_count_targets()
    parts = []
    if skipped["bot"]:
        parts.append(f"机器人 {skipped['bot']}")
    if skipped["deleted"]:
        parts.append(f"已注销 {skipped['deleted']}")
    if skipped["no_username"]:
        parts.append(f"拿不到可发对象 {skipped['no_username']}")
    if skipped["self"]:
        parts.append(f"账号自己 {skipped['self']}")
    if skipped["inactive"]:
        parts.append(f"久未上线 {skipped['inactive']}")
    skip_msg = f"，已排除（{'、'.join(parts)}）" if any(skipped.values()) else ""
    recent_note = f"（仅保留近{recent_only_days}天活跃）" if recent_only_days else ""
    cap_note = f"（本群真实 {real_total} 人）" if real_total else ""
    win_note = f"（只看 {min_date:%m-%d} 之后的消息）" if min_date else ""
    head = (f"✅ 从「{name}」历史采完：翻了 {msgs_read} 条消息，"
            f"{len(counts)} 个不同发言人，入名单 {added} 人{skip_msg}{recent_note}{win_note}"
            f"{cap_note}，名单共 {total} 人")
    tails = []
    if flood:
        tails.append(f"   ⏳ 中途撞到频率限制（需等 {flood}s），已把读到的部分入库，没浪费。")
    if too_old:
        tails.append(f"   ⏱ 翻到窗口边界就停了（更早的 {too_old} 条不再看）。")
    if anon:
        tails.append(f"   ℹ️ 有 {anon} 条拿不到作者（匿名发言/系统消息），已跳过。")
    if tried_upgrade:
        tails.append(f"   🔎 补全了 {tried_upgrade} 个只显示昵称的发言人，拿不到的那些发不了。")
    tails.append("   ❗ 这是「活跃发言人」不是全量成员：只说过话的人在里面，"
                 "也只能看到本账号可见的那段历史。")
    return head + "\n" + "\n".join(tails)


async def collect_members_or_speakers(client, peer_arg, limit=5000,
                                      recent_only_days=0, msg_limit=2000,
                                      join_days=0):
    """先按成员列表拉；被服务端挡住时**自动**接力采发言人，不用人再点一次。

    老板原话：「不用点击采发言人，就自动读取」。
    成员列表权限是服务端锁死的，换号也变不出来；但「谁发过言」是公开的，
    所以拿不全名单时直接转这条通道，而不是只弹一句提示让人自己琢磨。
    join_days>0：只要最近 N 天进群的人（新群只吃新面孔）。
    返回拼接后的文本，开头仍保留 ✅/⚠️/❌ 供调用方判断。
    """
    from datetime import datetime, timedelta, timezone
    r = await collect_members(client, peer_arg, limit=limit,
                              recent_only_days=recent_only_days,
                              join_days=join_days)
    if not r.startswith("⚠️"):
        return r
    before = db_count_targets()
    # 只要新人时，翻历史也别翻到窗口外去：省请求、也少惹限流
    min_date = (datetime.now(timezone.utc) - timedelta(days=join_days)
                if join_days else None)
    r2 = await collect_speakers(client, peer_arg, msg_limit=msg_limit,
                                recent_only_days=recent_only_days,
                                min_date=min_date)
    got = db_count_targets() - before
    return (r + "\n\n"
            + f"🗣 名单拿不全就不卡在这：已**自动**改从历史消息采发言人"
              + (f"（本轮又入 {got} 人）" if got > 0 else "")
              + "\n" + r2)


async def collect_channel_history(client, peer_arg, limit=50):
    """采集频道/群的历史消息，存入文案池（后续可群发）。"""
    try:
        entity = await client.get_entity(peer_arg)
    except Exception as e:
        return f"❌ 找不到该频道: {e}"
    title = getattr(entity, "title", str(peer_arg))
    msgs = []
    try:
        async for msg in client.iter_messages(entity, limit=limit):
            if msg.message or msg.media:
                msgs.append({
                    "id": msg.id,
                    "text": msg.message or "",
                    "has_media": bool(msg.media),
                    "date": str(msg.date),
                })
    except Exception as e:
        return f"❌ 读取历史消息失败: {e}"
    if not msgs:
        return f"⚠️ 从「{title}」未采集到任何消息"
    # 保存到文案池表（先建表）
    _save_to_pool(title, msgs)
    return f"✅ 从「{title}」采集完成：{len(msgs)} 条消息已入库"


def _save_to_pool(source_title, msgs):
    """把采集的消息写入文案池（messages_pool 表）。"""
    from db import DB
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            for m in msgs:
                cur.execute(
                    "INSERT INTO messages_pool (source, msg_id, text, has_media, msg_date) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (source, msg_id) DO NOTHING",
                    (source_title, m["id"], m["text"], m["has_media"], m["date"]),
                )
        conn.commit()
    finally:
        DB.putconn(conn)


def db_load_pool_messages(limit=100):
    """从文案池随机取 N 条消息用于群发。"""
    from db import DB
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT text, has_media FROM messages_pool ORDER BY random() LIMIT %s",
                (limit,),
            )
            return cur.fetchall()
    finally:
        DB.putconn(conn)


def db_count_pool():
    from db import DB
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM messages_pool")
            return cur.fetchone()[0]
    finally:
        DB.putconn(conn)


# ---- 文案池手动管理（旧版只能 /collect_history 采集，菜单里根本没入口）----
POOL_MANUAL = "manual"


def db_add_pool_text(text):
    """手动存一条文案，返回 id（毫秒时间戳，天然不撞且按时间有序）。"""
    import time as _t
    mid = int(_t.time() * 1000)
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO messages_pool (source, msg_id, text, has_media) "
                "VALUES (%s, %s, %s, FALSE)",
                (POOL_MANUAL, mid, text),
            )
        conn.commit()
        return mid
    finally:
        DB.putconn(conn)


def db_list_pool(limit=30):
    """列文案池（采集的+手动的）：返回 [(source, msg_id, text)]，按时间正序。"""
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source, msg_id, text FROM messages_pool "
                "ORDER BY msg_date NULLS LAST, source, msg_id LIMIT %s",
                (int(limit),),
            )
            return cur.fetchall()
    finally:
        DB.putconn(conn)


def db_del_pool(source, msg_id):
    """删单条，返回是否真删了。"""
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM messages_pool WHERE source = %s AND msg_id = %s",
                        (source, int(msg_id)))
            n = cur.rowcount
        conn.commit()
        return n > 0
    finally:
        DB.putconn(conn)


def db_clear_pool():
    """清空文案池，返回清除条数。"""
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM messages_pool")
            n = cur.fetchone()[0]
            cur.execute("DELETE FROM messages_pool")
        conn.commit()
        return n
    finally:
        DB.putconn(conn)


def db_pool_texts(limit=500):
    """取可用于群发的纯文本文案（排除带图/空的），带内容去重。"""
    conn = DB.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT text FROM messages_pool "
                "WHERE has_media IS NOT TRUE AND COALESCE(text, '') <> '' LIMIT %s",
                (int(limit),),
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        DB.putconn(conn)


async def list_my_groups(client=None):
    """列出通过本 Bot 加入/导入的群/频道（数据源：DB groups_info 表，
    不遍历 Telegram 全部会话，避免把账号原有的群/私聊也列出来）。"""
    rows = db_get_all_groups()
    if not rows:
        return "📁 还没有通过本 Bot 加入的群。\n点「加群」或「批量导入」加入后会自动记录在这里。"
    result = [f"📁 通过本 Bot 加入的群/频道（共 {len(rows)} 个）：\n"]
    for gid, title, username, member_count, _creator in rows:
        uname = f"  @{username}" if username else ""
        mc = f"  成员:{member_count}" if member_count else ""
        result.append(f"• {title or '(无标题)'}  (id={gid}){uname}{mc}")
    return "\n".join(result)


async def leave_group(accounts, gid):
    """让所有在该群里的账号退出该群（不碰数据库）。

    basic 群(Chat) 走 messages.deleteChatUser(把自己踢出)，频道/超级群(Channel) 走 channels.leaveChannel。
    accounts 为 (acc_no, client, phone) 列表。返回 (退出成功数, 明细文本列表)。
    """
    lines = []
    for acc_no, client, _ph in accounts:
        try:
            entity = await _resolve_entity(client, gid)
        except Exception:
            lines.append(f"  [账号{acc_no}] ⏭ 不在这个群里，跳过")
            continue
        try:
            if isinstance(entity, Channel):
                await client(LeaveChannelRequest(entity))
            elif isinstance(entity, Chat):
                me = await client.get_me()
                await client(DeleteChatUserRequest(abs(int(entity.id)), PeerUser(int(me.id))))
            else:
                lines.append(f"  [账号{acc_no}] ❌ 不是群/频道（{type(entity).__name__}），跳过")
                continue
            lines.append(f"  [账号{acc_no}] ✅ 已退出「{getattr(entity, 'title', gid)}」")
        except FloodWaitError as e:
            lines.append(f"  [账号{acc_no}] ⏳ 退群频率限制，需等 {e.seconds}s")
        except Exception as e:
            lines.append(f"  [账号{acc_no}] ❌ {str(e)[:60]}")
        await asyncio.sleep(1.5)
    ok = sum(1 for x in lines if "✅" in x)
    return ok, lines


async def diag_groups(accounts):
    """诊断：逐账号列出真实群/频道对话，并与 groups_info 表对账。
    accounts 为 (acc_no, client, phone) 列表。"""
    rows = db_get_all_groups()
    table_ids = {int(g[0]) for g in rows}
    table_titles = {int(g[0]): (g[1] or "(无标题)") for g in rows}
    out = [f"📋 groups_info 表共 {len(rows)} 个群："]
    for gid, title, username, mc, _c in rows:
        out.append(f"  · id={gid} 「{title or '(无标题)'}」@({username or '-'}) 成员:{mc or '?'}")
    found_ids = set()
    for acc_no, client, _ph in accounts:
        try:
            dialogs = await client.get_dialogs()
        except Exception as e:
            out.append(f"\n[账号{acc_no}] ❌ 拉对话失败: {str(e)[:60]}")
            continue
        gs = [d for d in dialogs if getattr(d.entity, "title", None) is not None]
        out.append(f"\n[账号{acc_no}] 真实群/频道 {len(gs)} 个：")
        for d in gs[:50]:
            nid = _norm_gid(d.id, d.entity)
            hit = nid in table_ids
            if hit:
                found_ids.add(nid)
            out.append(f"  · id={d.id} 「{d.title}」 {'✅在表' if hit else '❌不在表'}")
    missing = table_ids - found_ids
    if missing:
        out.append("\n⚠️ 表里有但没有任何账号在群里的僵尸记录：")
        for gid in missing:
            out.append(f"  · id={gid} 「{table_titles.get(gid, '?')}」 ← 需重新加群或删除")
    return "\n".join(out)


async def _save_group_info(client, entity):
    """保存群组信息到数据库，并返回 (title, member_count)。"""
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
    # 新群一入表就纳入自动补录（不等下一轮补扫）；失败不影响加群主流程
    try:
        from member_watch import warm_groups
        warm_groups()
    except Exception:
        pass
    return title, member_count


async def _find_dialog_by_title(client, title):
    """按标题在对话列表里找刚加入的群（邀请链接是一次性凭证，加入后不能再拿去解析）。"""
    if not title:
        return None
    t = str(title).strip().lower()
    try:
        async for d in client.iter_dialogs(limit=300):
            if (getattr(d.entity, "title", "") or "").strip().lower() == t:
                return d.entity
    except Exception as e:
        log.warning(f"[加群] 按标题找对话失败({title}): {e}")
    return None


def _entity_from_updates(res):
    """从 ImportChatInvite 等返回的 updates 里抠出群实体。
    注意：ChatInvite 类型本身不带 chat 字段（只有 ChatInviteAlready/Peek 才有），
    所以必须从 updates.chats 取，否则实体永远为 None。"""
    seen = 0
    while res is not None and seen < 5:
        seen += 1
        chats = getattr(res, "chats", None)
        if chats:
            for c in chats:
                if getattr(c, "title", None):
                    return c
        res = getattr(res, "updates", None)
    return None


def _decode_invite_hash(h):
    """尽力从邀请链接 hash 解出 DC 与群 id，仅用于诊断日志，失败返回 '?'。"""
    try:
        import base64
        pad = "=" * (-len(h) % 4)
        raw = base64.urlsafe_b64decode(h + pad)
        if len(raw) < 9:
            return f"hash长{len(h)}字/解出{len(raw)}字节 ⚠短于9字节非标准邀请hash"
        dc = raw[0]
        peer = int.from_bytes(raw[1:9], "little")
        warn = "" if 1 <= dc <= 5 else " ⚠新版随机hash,dc仅供参考"
        return f"dc={dc}{warn} peer_id={peer} 解出{len(raw)}字节 hex={raw.hex()}"
    except Exception as e:
        return f"?({type(e).__name__})"


async def _peek_invite(client, invite_hash):
    """预检查取邀请详情（拿群名 + 判断群侧开了哪种门槛）。
    失败一律吞掉返回 None：新版 t.me/+xxx 链接在 CheckChatInvite 阶段会被误报 expired，
    所以预检查只能在「加入动作已经失败/需补充信息」时用作参考，绝不能当成败依据。"""
    try:
        return await client(CheckChatInviteRequest(invite_hash))
    except Exception as e:
        log.info(f"[加群] 预检查失败(忽略): {type(e).__name__}")
        return None


async def _peek_invite_title(client, invite_hash):
    check = await _peek_invite(client, invite_hash)
    if check is None:
        return None
    chat = getattr(check, "chat", None)
    return getattr(chat, "title", None) or getattr(check, "title", None)


def _invite_gate(check) -> str:
    """从预检查结果判断群侧门槛：'verify'(需人工验证/答题) | 'request'(需批准) | ''(未知)。"""
    if check is None:
        return ""
    if getattr(check, "bot_verification", None):
        return "verify"
    if getattr(check, "request_needed", False):
        return "request"
    return ""


def _err_gate(err) -> str:
    """从异常类型判断是不是「要人工验证」这类（只识别，不尝试绕过）。
    注意只，得窄：子串写宽了会把 BotInvalidError / PremiumAccountRequired 之类误判成验证。"""
    name = type(err).__name__
    if any(k in name for k in ("Captcha", "Verif", "Screenshot")):
        return "verify"
    return ""


async def _probe_chat_state(client, title_hint=None, gid_hint=None):
    """查本账号在这个群里的状态（只读，不发消息）。
    用于定性 InviteHashExpired：被踢/被封的账号 Telegram 也会回'链接过期'。"""
    t = (title_hint or "").strip().lower()
    try:
        async for d in client.iter_dialogs(limit=400):
            ent = getattr(d, "entity", None)
            name = (getattr(ent, "title", "") or "").strip().lower()
            if not name or (t and name != t):
                continue
            flags = [f for f in ("kicked", "left", "creator", "megagroup", "broadcast",
                                 "unavailable", "gigagroup", "restricted")
                     if getattr(ent, f, False)]
            banned = getattr(ent, "banned_by", None) is not None
            call = getattr(d, "conversation", None)
            info = "kicked/left/flags: " + (",".join(flags) if flags else "无")
            if banned:
                info += " +被群封禁(banned_by)"
            return info
        return "对话列表里找不到该群（未加入、且无本地记录）"
    except Exception as e:
        return f"查询失败 {type(e).__name__}: {e}"


# ---------------- 待批准的入群申请（A：需群主批准 / C：需人工验证）----------------
# 为什么要记账：Telegram 的入群申请重复提交会给群主刷屏、也容易被判骚扰；
# 而且「批准通过后」再发同一链接，正确反应应该是认出已通过并开始拉人，
# 不是再发一次申请。所以把在途申请存下来（按 邀请hash/群token + 账号 记）。
K_PENDING_JOINS = "pending_joins"
PENDING_JOIN_TTL = 7 * 86400  # 7 天后不再当在途处理（申请早凉透了）


def _acc_tag(client, acc=None) -> str:
    """账号标识：调用方传的用者传的（phone/uid），否则 client.phone，再否则 '?'。
    绝不能抛异常；但也不能轻易返 '?'——多账号都撞在 '?' 上会让 A 号的申请挡住 B 号。"""
    if acc:
        return str(acc)
    try:
        p = getattr(client, "phone", None)
        if p:
            return str(p)
    except Exception:
        pass
    try:
        uid = getattr(getattr(client, "me", None), "id", None)
        if uid:
            return str(uid)
    except Exception:
        pass
    return "?"


def _pending_key(link, client, acc=None) -> str:
    """在途申请的键 = 链接指纹 + 账号，避免 A 账号的申请挡住 B 账号。"""
    m = re.search(r"t\.me/(?:joinchat/|\+)?([A-Za-z0-9_\-]+)", link or "")
    token = m.group(1) if m else (link or "").strip()
    return f"{token}@{_acc_tag(client, acc)}"


def _pending_load() -> dict:
    """读在途申请表；DB 抽风时当成空表，绝不让记账逻辑搞挂加群主流程。"""
    try:
        data = ops_get(K_PENDING_JOINS)
    except Exception as e:
        log.info(f"[加群] 读在途申请失败(忽略): {type(e).__name__}")
        return {}
    return data if isinstance(data, dict) else {}


def _pending_save(data: dict):
    try:
        ops_set(K_PENDING_JOINS, data)
    except Exception as e:
        log.info(f"[加群] 写在途申请失败(忽略): {type(e).__name__}")


def _pending_remember(link, client, title="", kind="request", acc=None):
    """kind: request=等群主批准 | verify=等人工验证（不自动过，只挂起）。"""
    data = _pending_load()
    now = int(time.time())
    data[_pending_key(link, client, acc)] = {
        "at": now,
        "kind": kind,
        "title": title or "",
        "link": link or "",
    }
    # 顺手清掉过期项，别让表无限膨胀
    data = {k: v for k, v in data.items()
            if isinstance(v, dict) and now - int(v.get("at") or 0) < PENDING_JOIN_TTL}
    _pending_save(data)


def _pending_get(link, client, acc=None):
    data = _pending_load()
    if not data:
        return None
    k = _pending_key(link, client, acc)
    v = data.get(k)
    if not isinstance(v, dict):
        return None
    try:
        age = int(time.time()) - int(v.get("at") or 0)
    except (TypeError, ValueError):
        age = PENDING_JOIN_TTL + 1
    if age >= PENDING_JOIN_TTL:
        data.pop(k, None)
        _pending_save(data)
        return None
    return v


def _pending_forget(link, client, acc=None):
    data = _pending_load()
    k = _pending_key(link, client, acc)
    if k in data:
        data.pop(k, None)
        _pending_save(data)
        return True
    return False


def pending_joins_list():
    """供内联键盘用的结构化在途申请清单（按时间升序）。"""
    data = _pending_load()
    now = int(time.time())
    rows = []
    for k, v in sorted(data.items(), key=lambda kv: int(kv[1].get("at") or 0)
                       if isinstance(kv[1], dict) else 0):
        if not isinstance(v, dict):
            continue
        try:
            age = now - int(v.get("at") or 0)
        except (TypeError, ValueError):
            age = PENDING_JOIN_TTL + 1
        if age >= PENDING_JOIN_TTL:
            continue
        token, _, acc = k.partition("@")
        rows.append({"key": k, "kind": v.get("kind") or "request",
                     "title": v.get("title") or "", "link": v.get("link") or "",
                     "token": token, "acc": acc, "age_min": max(0, age // 60)})
    return rows


def _find_account(tag):
    """按标识找回账号元组 (acc_no, client, phone)：支持 phone / uid / 序号。"""
    from config import ACTIVE_ACCOUNTS
    t = str(tag or "").strip()
    for acc_no, client, phone in list(ACTIVE_ACCOUNTS or []):
        cands = {str(acc_no), str(phone or ""),
                 str(getattr(client, "phone", "") or ""),
                 str(getattr(getattr(client, "me", None), "id", "") or "")}
        if t and t in cands:
            return (acc_no, client, phone)
    return None


def _verify_arm(client, link, title="", acc=None):
    """撞入群验证时开「人在环」中继窗口：把该号收到的验证题**原样**转给老板，
    等老板点/答。本函数不答题、不发消息，只是把耳朵暂借给中继模块。"""
    try:
        import verify_relay
        verify_relay.arm(client, _acc_tag(client, acc), link, title or "")
    except Exception as e:
        log.info(f"[加群] 开验证中继失败(忽略): {type(e).__name__}: {e}")


def verify_arm_by_tag(tag):
    """「继续盯验证消息」按钮用：按在途记账里的 link/title 重新开窗。"""
    rows = pending_joins_list()
    hit = next((r for r in rows if r.get("acc") == str(tag) and r.get("kind") == "verify"), None)
    acc_row = _find_account(tag)
    if acc_row is None:
        return f"❌ 账号 {tag} 当前不在线（未登录或已掉线），没法盯。"
    _acc_no, client, _phone = acc_row
    link = (hit or {}).get("link") or ""
    title = (hit or {}).get("title") or ""
    _verify_arm(client, link, title, acc=str(tag))
    return (f"📡 已开始盯账号 {tag} 的验证消息（盯 30 分钟）。\n"
            + (f"目标群：「{title}」" if title else "")
            + "\n题目一出现就转到这个会话，你点哪个我只提交哪个。")


async def join_group_one_account(link, acc_tag=None):
    """用**指定那一个**账号重跑一次加群（老板说「我验证过了」时用）。
    找不到该账号就退回全池逐号试。返回 (文本, 实体或None, 使用的账号标识)，
    与 join_group_all_accounts 同构，调用方可以共用后续入表/拉名单逻辑。"""
    from config import ACTIVE_ACCOUNTS
    accounts = list(ACTIVE_ACCOUNTS or [])
    if not accounts:
        return ("❌ 没有可用账号", None, None)
    row = _find_account(acc_tag)
    if row is None:
        log.info(f"[加群] 指定账号 {acc_tag!r} 不在线，退回全池重试")
        return await join_group_all_accounts(accounts, link)
    acc_no, client, phone = row
    tag = getattr(client, "phone", None) or phone or f"账号{acc_no}"
    try:
        txt, ent = await join_group_by_link(client, link, acc=tag)
    except Exception as e:
        log.warning(f"[加群] 单账号重试异常 {type(e).__name__}: {e}", exc_info=True)
        return (f"❌ 重试失败：{type(e).__name__}: {str(e)[:80]}", None, client)
    return (f"{txt}\n📝 使用账号：{tag}", ent, client)


def pending_joins_report() -> str:
    """列出所有在途申请（供「加群」复查用）。"""
    data = _pending_load()
    now = int(time.time())
    rows = [(k, v) for k, v in data.items() if isinstance(v, dict)
            and now - int(v.get("at") or 0) < PENDING_JOIN_TTL]
    if not rows:
        return "没有在途的入群申请。"
    rows.sort(key=lambda kv: int(kv[1].get("at") or 0))
    lines = [f"【在途入群申请 {len(rows)} 条】"]
    for k, v in rows:
        token, _, acc = k.partition("@")
        mins = max(0, (now - int(v.get("at") or 0)) // 60)
        if v.get("kind") == "verify":
            lines.append(f"  🔒 「{v.get('title') or '?'}」 账号{acc} 需人工验证，已挂起{mins}分钟")
        else:
            lines.append(f"  ⏳ 「{v.get('title') or '?'}」 账号{acc} 等批准{mins}分钟")
    lines.append("💡 批准后/验证后，再点一次「加群」发同一链接：通过就自动入表+拉名单。")
    return "\n".join(lines)


async def join_group_all_accounts(accounts, link):
    """逐账号尝试加群，谁加得上用谁；全失败时列出每个账号的错。
    用途：区分「链接/群侧失效（两账号同错）」还是「单账号被限制（错不同）。"""
    lines = []
    for idx, (acc_no, client, phone) in enumerate(accounts):
        tag = getattr(client, "phone", None) or phone or f"账号{acc_no}"
        try:
            txt, ent = await join_group_by_link(client, link, acc=tag)
        except Exception as e:
            log.warning(f"[加群] 账号{acc_no} 异常 {type(e).__name__}: {e}", exc_info=True)
            txt, ent = f"❌ {type(e).__name__}: {e}", None
        first = str(txt).splitlines()[0][:100]
        log.info(f"[加群] 账号{acc_no}({tag}) 结果: {first}")
        lines.append((tag, first, ent, client))
        if first.startswith("✅") or first.startswith("⏳") or first.startswith("🔒"):
            failed = [(t, m) for t, m, _, _ in lines[:-1]]
            note = ""
            if failed:
                seg = []
                for (t, m), (_tag, _c, _ph) in zip(failed, accounts):
                    st = await _probe_chat_state(_c, getattr(ent, "title", None))
                    seg.append(f"{t}（当前状态：{st}）")
                    break  # 只报第一个失败账号，避免刷屏
                note = (f"\n⚠️ 未能加入的账号：{'；'.join(seg)}\n"
                        f"   同一链接其他账号能加 → 不是链接问题，是该账号单独被拒：\n"
                        f"   · 状态含 kicked/left/被封禁 → 该号被这个群踢过或封了，"
                        f"请群主在「群设置 → 管理员 → 被封禁的成员」里解除后重发链接\n"
                        f"   · 状态正常却仍被拒 → 该号被 Telegram 限制加群（找 @SpamBot 查）\n")
            return (f"{txt}\n📝 使用账号：{tag}{note}", ent, client)
        if idx < len(accounts) - 1:
            await asyncio.sleep(1.2)
    if not lines:
        return ("❌ 无可用账号", None, None)
    if len(lines) > 1:
        detail = "\n".join(f"  [{t}] {m}" for t, m, _, _ in lines)
        same = len({m for _, m, _, _ in lines}) == 1
        hint = ("🔍 两个账号报同一个错 → 问题在链接/群侧（链接已失效或已被用过），与账号无关"
                if same else
                "🔍 两个账号错不同 → 可能是单账号风控，可让群主直接「添加成员」拉人")
        body = f"{lines[0][1]}\n—— {len(lines)} 个账号均未成功 ——\n{detail}\n{hint}"
    else:
        body = (f"{lines[0][1]}\n💡 仅 1 个账号可用，无法交叉验证。可多绑一个账号重试，"
                f"或让群主直接「添加成员」把账号拉进群。")
    return (body, lines[0][2], lines[0][3])


async def join_group_by_link(client, link, acc=None):
    """让账号通过群链接加入群/频道（支持私密邀请链接与公开群，支持需批准入群）。
    加群成功后自动读取群信息并存入 groups_info 表。
    acc: 调用方传入的账号标识（phone/uid），用于在途申请记账区分账号；不传则从 client 推。"""
    raw = link
    link = (link or "").strip().strip("<>").strip()
    priv_m = re.search(r"t\.me/(?:joinchat/|\+)([A-Za-z0-9_\-]+)", link)
    pub_m = re.search(r"t\.me/([A-Za-z0-9_][A-Za-z0-9_\-]{3,})", link)
    me = None
    try:
        me = await client.get_me()
    except Exception:
        pass
    log.info(f"[加群] 发起: 输入={raw!r} 清洗后={link!r} 私密={bool(priv_m)} 公开={bool(pub_m)} "
             f"账号=@{(getattr(me, 'username', '') or getattr(me, 'phone', '') or '?')}")
    # 邀请链接是一次性凭证：加群成功后绝不能拿旧链接再去解析(会报 expired)，
    # 统一返回 (结果文本, 群实体或None)。
    try:
        if priv_m:
            invite_hash = priv_m.group(1)
            pend = _pending_get(link, client, acc)
            if pend:
                # 之前已经发过申请/被验证拦过：先查进没进来，绝不默默重发（重发=给群主刷屏+被判骚扰）
                check = await _peek_invite(client, invite_hash)
                already = getattr(check, "chat", None) if isinstance(check, ChatInviteAlready) else None
                if already is not None:
                    _pending_forget(link, client, acc)
                    await _save_group_info(client, already)
                    log.info(f"[加群] ✅ 批准已通过 title={getattr(already, 'title', None)}")
                    return (f"✅ 「{getattr(already, 'title', None) or pend.get('title') or '群'}」已批准入群，可以拉名单了", already)
                gate = _invite_gate(check)
                t = pend.get("title") or getattr(check, "title", None)
                mins = ""
                try:
                    mins = f"（已等 {max(0, int(time.time()) - int(pend.get('at') or 0)) // 60} 分钟）"
                except (TypeError, ValueError):
                    pass
                if pend.get("kind") == "verify" or gate == "verify":
                    _verify_arm(client, link, t, acc=acc)
                    return (f"🔒 「{t or '该群'}」开了入群验证，需要你本人过一道（抢题/按钮）。\n"
                            f"   我不会代你抢——这是 Telegram 专门拦自动加群的门槛。\n"
                            f"   📡 已改为「你在 Bot 里选、系统只提交」：验证机器人的原话和按钮\n"
                            f"      会自动转到本会话，你点哪个我只提交哪个；或去官方客户端自己点。\n"
                            f"   验证过了再发一次同一链接，就会自动入表+拉名单。", None)
                return (f"⏳ 「{t or '该群'}」的入群申请已在途{mins}，未重复提交。\n"
                        f"   等群主/管理员在「群设置 → 管理员 → 入群申请」里批准后，再发一次同一链接即可入表+拉名单。", None)
            log.info(f"[加群] 邀请hash={invite_hash} {_decode_invite_hash(invite_hash)} "
                     f"→ 直接执行加入(不再预检查，预检查对新链接会误报 expired)")
            # 关键修正：旧顺序是先 CheckChatInvite 再 Import，而新版 t.me/+xxx 链接
            # 在 CheckChatInvite 阶段就被 Telegram 判 INVITE_HASH_EXPIRED，
            # 加入动作永远轮不到执行。正确顺序：直接 ImportChatInvite（它本身就返回群实体）。
            try:
                res = await client(ImportChatInviteRequest(invite_hash))
            except InviteRequestSentError:
                t = await _peek_invite_title(client, invite_hash)
                _pending_remember(link, client, t, kind="request", acc=acc)
                log.info(f"[加群] 「{t}」需群主批准，申请已发送")
                return (f"⏳ 已向「{t or '该群'}」发送入群申请，等待群主批准。\n"
                        f"   批准后请再发一次同一链接，会自动入表+拉名单（不会重复提交申请）。", None)
            except UserAlreadyParticipantError:
                _pending_forget(link, client, acc)
                t = await _peek_invite_title(client, invite_hash)
                ent = await _find_dialog_by_title(client, t)
                if ent:
                    await _save_group_info(client, ent)
                return (f"✅ 已在「{t or '该群'}」中", ent)
            except (InviteHashExpiredError, InviteHashInvalidError) as e:
                log.warning(f"[加群] ImportChatInvite 报 {type(e).__name__} "
                            f"hash={invite_hash} {_decode_invite_hash(invite_hash)}", exc_info=True)
                # 坑：开了入群验证的私密群，ImportChatInvite 也经常回
                # INVITE_HASH_EXPIRED，直接报「链接过期」是误判（老板看到的
                # 「要验证就加不进去」就是这条）。先只读预检查一次，把真门槛验出来。
                chk = await _peek_invite(client, invite_hash)
                if _invite_gate(chk) == "verify":
                    t = getattr(chk, "title", None) or getattr(
                        getattr(chk, "chat", None), "title", None) or "该群"
                    _pending_remember(link, client, t, kind="verify", acc=acc)
                    _verify_arm(client, link, t, acc=acc)
                    log.info(f"[加群] 「{t}」Import 报 {type(e).__name__}，"
                             f"但预检查显示需人工验证，改判 🔒（不再误报链接过期）")
                    return (f"🔒 「{t}」开了入群验证，需要你本人过一道（抢题/按钮）。\n"
                            f"   我不会代你抢——这是 Telegram 专门拦自动加群的门槛。\n"
                            f"   刚才报的「链接过期」是假报错：验证群就是会这么回。\n"
                            f"   📡 已开「盯验证消息」：验证机器人的原话和按钮会自动转到本会话，\n"
                            f"      你点哪个我只提交哪个（一次只提交一次，不重复、不自作主张）。\n"
                            f"   验证过了再发一次同一链接，就会自动入表+拉名单。", None)
                kind = "已过期" if isinstance(e, InviteHashExpiredError) else "无效"
                return (f"❌ 加入失败：Telegram 判定该邀请链接{kind}（{type(e).__name__}）\n"
                        f"💡 如果是刚生成的链接还报这个，通常是：链接设了有效期/次数已用完，\n"
                        f"   或该账号被 Telegram 限制加入新群（新号/风控）。\n"
                        f"✔ 可换另一个账号重试，或让群主直接「添加成员」把账号拉进群。", None)
            ent = _entity_from_updates(res)
            t = getattr(ent, "title", None)
            if ent is None:
                t = await _peek_invite_title(client, invite_hash)
                ent = await _find_dialog_by_title(client, t)
            if ent:
                _pending_forget(link, client, acc)
                await _save_group_info(client, ent)
            log.info(f"[加群] ✅ ImportChatInvite 成功 title={t} entity={'有' if ent else '无'}")
            return (f"✅ 已成功加入「{t or '群'}」", ent)
        elif pub_m:
            token = pub_m.group(1)
            try:
                entity = await client.get_entity(token)
            except Exception as e:
                log.warning(f"[加群] get_entity({token!r}) 失败: {type(e).__name__}: {e}")
                return (f"❌ 找不到该群/频道（可能已被封禁、用户名错误、或是需邀请的群）: {token}\n"
                        f"错误: {type(e).__name__}\n"
                        f"提示：私密群请用 t.me/xxxx 完整邀请链接（带 + 号或 joinchat/）；"
                        f"若账号不在该群，无法用纯数字 ID 解析。", None)
            title0 = getattr(entity, "title", None) or token
            log.info(f"[加群] 解析到实体 id={getattr(entity, 'id', '?')} "
                     f"type={type(entity).__name__} title={getattr(entity, 'title', None)} "
                     f"megagroup={getattr(entity, 'megagroup', None)} broadcast={getattr(entity, 'broadcast', None)} "
                     f"join_request={getattr(entity, 'join_request', None)} "
                     f"bot_verify={bool(getattr(entity, 'bot_verification_icon', None))}")
            # C：开了入群验证（机器人/答题）——只识别+挂起，不尝试代过
            if getattr(entity, "bot_verification_icon", None):
                _pending_remember(link, client, title0, kind="verify", acc=acc)
                _verify_arm(client, link, title0, acc=acc)
                log.info(f"[加群] 「{title0}」需人工验证，已挂起+开中继盯题")
                return (f"🔒 「{title0}」开了入群验证，需要你本人过一道（抢题/按钮）。\n"
                        f"   我不会代你抢——这是 Telegram 专门拦自动加群的门槛。\n"
                        f"   📡 已开「盯验证消息」：验证机器人发的原话和按钮会自动转到本会话，\n"
                        f"      你点哪个我只提交哪个（一次只提交一次，不重复、不自作主张）。\n"
                        f"   也可以在官方客户端自己点，过了再发同一链接就会自动入表+拉名单。", None)
            pend = _pending_get(link, client, acc)
            if pend:
                # 已发过申请：不重复提交（重发=给群主刷屏+被判骚扰）
                if pend.get("kind") == "verify":
                    _verify_arm(client, link, title0, acc=acc)
                    return (f"🔒 「{title0}」需人工验证，已继续盯着该号的验证消息。\n"
                            f"   题目一到就转过来，你选完提交；过了之后发同一链接即自动入表+拉名单。", None)
                try:
                    mins = f"（已等 {max(0, int(time.time()) - int(pend.get('at') or 0)) // 60} 分钟）"
                except (TypeError, ValueError):
                    mins = ""
                return (f"⏳ 「{title0}」的入群申请已在途{mins}，未重复提交。\n"
                        f"   群主批准后，再发一次同一链接即可入表+拉名单。", None)
            try:
                await client(JoinChannelRequest(entity))
                title, cnt = await _save_group_info(client, entity)
                _pending_forget(link, client, acc)
                log.info(f"[加群] ✅ 成功加入「{title}」成员{cnt}")
                return (f"✅ 已成功加入「{title}」，成员 {cnt} 人", entity)
            except UserAlreadyParticipantError:
                title, cnt = await _save_group_info(client, entity)
                _pending_forget(link, client, acc)
                return (f"✅ 已在「{title}」中，成员 {cnt} 人", entity)
            except InviteRequestSentError:
                _pending_remember(link, client, title0, kind="request", acc=acc)
                log.info(f"[加群] 「{title0}」需批准，申请已发送")
                return (f"⏳ 已向「{title0}」发送入群申请，等待群主批准。\n"
                        f"   批准后请再发一次同一链接，会自动入表+拉名单（不会重复提交申请）。", None)
        else:
            log.warning(f"[加群] 无法识别链接格式: {raw!r}")
            return (f"❌ 无法识别的群链接: {link}\n"
                    f"支持的形式：\n"
                    f"  · 公开群 https://t.me/username\n"
                    f"  · 私密群 https://t.me/+xxxx 或 t.me/joinchat/xxxx\n"
                    f"（纯数字群 ID 不能用于加群，只能用于拉已在群的成员）", None)
    except FloodWaitError as e:
        log.warning(f"[加群] FloodWait {e.seconds}s (request={getattr(e, 'request', '?')})")
        return (f"⏳ 操作过于频繁，请 {e.seconds} 秒后再试", None)
    except Exception as e:
        # C：Telethon 把验证码/机器人验证类错误直接抛出来时，也只识别+挂起，不代过
        if _err_gate(e) == "verify":
            try:
                _pending_remember(link, client, "", kind="verify", acc=acc)
                _verify_arm(client, link, "", acc=acc)
            except Exception:
                pass
            log.info(f"[加群] 碰人工验证类错误 {type(e).__name__}，已挂起+开中继盯题")
            return (f"🔒 这个群要人工验证（{type(e).__name__}），需要你本人过一道。\n"
                    f"   我不会代你抢——这是 Telegram 专门拦自动加群的门槛。\n"
                    f"   📡 已开「盯验证消息」：题目一到就转到本会话，你点哪个我只提交哪个。\n"
                    f"   过了再发一次同一链接，就会自动入表+拉名单。", None)
        log.warning(f"[加群] ❌ 异常 {type(e).__name__}: {e} (输入={raw!r})", exc_info=True)
        return (f"❌ 加入失败: {type(e).__name__}: {e}", None)
