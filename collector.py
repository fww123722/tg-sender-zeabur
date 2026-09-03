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
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import (
    Channel, Chat, ChatInvite, ChatInviteAlready, User,
)

from config import log
from db import db_add_targets, db_count_targets, db_add_group, db_get_all_groups


async def collect_members(client, peer_arg, limit=5000):
    """从群/频道拉取成员并加入名单。"""
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


async def join_group_by_link(client, link):
    """让账号通过群链接加入群/频道（支持私密邀请链接与公开群，支持需批准入群）。
    加群成功后自动读取群信息并存入 groups_info 表。"""
    link = (link or "").strip().strip("<>").strip()
    priv_m = re.search(r"t\.me/(?:joinchat/|\+)([A-Za-z0-9_\-]+)", link)
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