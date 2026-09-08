#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集模块：拉取群成员、加入群组、采集频道历史消息（文案池）。"""
import re
import random
import asyncio

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    InviteRequestSentError,
    UserAlreadyParticipantError,
)
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
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


async def collect_members(client, peer_arg, limit=5000, recent_only_days=0):
    """从群/频道拉取成员并加入名单。"""
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
    skipped = {"bot": 0, "deleted": 0, "no_username": 0, "self": 0, "inactive": 0}
    try:
        async for user in client.iter_participants(entity, limit=limit):
            # 排除：非用户对象 / 机器人 / 已注销(删除) / 无用户名 / 账号自己
            if not isinstance(user, User):
                continue
            if user.bot:
                skipped["bot"] += 1
                continue
            if user.deleted:
                skipped["deleted"] += 1
                continue
            if not user.username:
                skipped["no_username"] += 1
                continue
            if my_id is not None and user.id == my_id:
                skipped["self"] += 1
                continue
            # 开关：只保留近期活跃成员
            if recent_only_days and not _is_recently_active(user, recent_only_days):
                skipped["inactive"] += 1
                continue
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
        skip_msg = f"，已排除（{('、'.join(parts))}）"
    recent_note = f"（仅保留近{recent_only_days}天活跃）" if recent_only_days else ""
    return f"✅ 从「{name}」拉取完成：新增有效成员 {added} 人{skip_msg}{recent_note}，名单共 {total} 人"


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


async def _peek_invite_title(client, invite_hash):
    """只在需要群名时尝试预检查取标题；失败一律吞掉（预检查对新链接可能误报 expired）。"""
    try:
        check = await client(CheckChatInviteRequest(invite_hash))
    except Exception as e:
        log.info(f"[加群] 预检查取标题失败(忽略): {type(e).__name__}")
        return None
    chat = getattr(check, "chat", None)
    return getattr(chat, "title", None) or getattr(check, "title", None)


async def join_group_all_accounts(accounts, link):
    """逐账号尝试加群，谁加得上用谁；全失败时列出每个账号的错。
    用途：区分「链接/群侧失效（两账号同错）」还是「单账号被限制（错不同）。"""
    lines = []
    for idx, (acc_no, client, phone) in enumerate(accounts):
        tag = getattr(client, "phone", None) or phone or f"账号{acc_no}"
        try:
            txt, ent = await join_group_by_link(client, link)
        except Exception as e:
            log.warning(f"[加群] 账号{acc_no} 异常 {type(e).__name__}: {e}", exc_info=True)
            txt, ent = f"❌ {type(e).__name__}: {e}", None
        first = str(txt).splitlines()[0][:100]
        log.info(f"[加群] 账号{acc_no}({tag}) 结果: {first}")
        lines.append((tag, first, ent, client))
        if first.startswith("✅") or first.startswith("⏳"):
            return (f"{txt}\n📝 使用账号：{tag}", ent, client)
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


async def join_group_by_link(client, link):
    """让账号通过群链接加入群/频道（支持私密邀请链接与公开群，支持需批准入群）。
    加群成功后自动读取群信息并存入 groups_info 表。"""
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
            log.info(f"[加群] 邀请hash={invite_hash} {_decode_invite_hash(invite_hash)} "
                     f"→ 直接执行加入(不再预检查，预检查对新链接会误报 expired)")
            # 关键修正：旧顺序是先 CheckChatInvite 再 Import，而新版 t.me/+xxx 链接
            # 在 CheckChatInvite 阶段就被 Telegram 判 INVITE_HASH_EXPIRED，
            # 加入动作永远轮不到执行。正确顺序：直接 ImportChatInvite（它本身就返回群实体）。
            try:
                res = await client(ImportChatInviteRequest(invite_hash))
            except InviteRequestSentError:
                t = await _peek_invite_title(client, invite_hash)
                log.info(f"[加群] 「{t}」需群主批准，申请已发送")
                return (f"⏳ 已向「{t or '该群'}」发送入群申请，等待群主批准", None)
            except UserAlreadyParticipantError:
                t = await _peek_invite_title(client, invite_hash)
                ent = await _find_dialog_by_title(client, t)
                if ent:
                    await _save_group_info(client, ent)
                return (f"✅ 已在「{t or '该群'}」中", ent)
            except (InviteHashExpiredError, InviteHashInvalidError) as e:
                log.warning(f"[加群] ImportChatInvite 报 {type(e).__name__} "
                            f"hash={invite_hash} {_decode_invite_hash(invite_hash)}", exc_info=True)
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
            log.info(f"[加群] 解析到实体 id={getattr(entity, 'id', '?')} "
                     f"type={type(entity).__name__} title={getattr(entity, 'title', None)} "
                     f"megagroup={getattr(entity, 'megagroup', None)} broadcast={getattr(entity, 'broadcast', None)}")
            try:
                await client(JoinChannelRequest(entity))
                title, cnt = await _save_group_info(client, entity)
                log.info(f"[加群] ✅ 成功加入「{title}」成员{cnt}")
                return (f"✅ 已成功加入「{title}」，成员 {cnt} 人", entity)
            except UserAlreadyParticipantError:
                title, cnt = await _save_group_info(client, entity)
                return (f"✅ 已在「{title}」中，成员 {cnt} 人", entity)
            except InviteRequestSentError:
                log.info(f"[加群] 「{getattr(entity, 'title', token)}」需批准")
                return (f"⏳ 已向「{getattr(entity, 'title', token)}」发送入群申请，等待批准", None)
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
        log.warning(f"[加群] ❌ 异常 {type(e).__name__}: {e} (输入={raw!r})", exc_info=True)
        return (f"❌ 加入失败: {type(e).__name__}: {e}", None)