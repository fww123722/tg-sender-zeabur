#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账号过滤分类：遍历名单，检测每个账号状态 → 冻结 / 禁言 / error / ok。

对应 qfbot(Win端) 的「过滤账号」目录：ok / error / 冻结账号 / 禁言账号。
结果按状态分类统计，可导出到文件供后续处理。
"""
import asyncio
import os

from telethon.errors import FloodWaitError

from config import ACTIVE_ACCOUNTS, DATA_DIR, log


def _status_key(estate: str) -> str:
    """把 Telegram 异常信息归类为: ok / frozen / muted / error"""
    estate = (estate or "").lower()
    if "flood" in estate or "wait" in estate or "rate" in estate:
        return "frozen"          # 限流/冻结
    if "spam" in estate or "banned" in estate or "deactivated" in estate:
        return "frozen"          # 封禁/停用 → 冻结
    if "mute" in estate or "privacy" in estate:
        return "muted"           # 禁言/隐私限制
    return "error"


async def check_one_user(client, uid, ah=0):
    """检测单个用户状态，返回 (ok, 状态key, 详情)。
    ok=True 表示可正常私信；否则返回状态分类。"""
    from telethon.tl.types import InputPeerUser
    try:
        if ah:
            entity = InputPeerUser(int(uid), int(ah))
        else:
            entity = await client.get_entity(int(uid))
        # 尝试发一个空测试消息不可行（会真的发出），这里改用解析实体判定可达性
        # 可达 = 能解析实体，说明账号存在且未被隐私完全屏蔽
        await client.get_entity(entity)
        return True, "ok", "可达"
    except FloodWaitError as e:
        return False, "frozen", f"flood_wait {e.seconds}s"
    except Exception as e:
        key = _status_key(str(e))
        return False, key, str(e)[:80]


async def filter_accounts(owner_entity, out_dir=None, limit=5000, per_user_delay=1.0):
    """遍历名单，检测全部账号状态并分类统计。
    结果写回分类目录（ok.txt / error.txt / 冻结账号.txt / 禁言账号.txt）。
    返回汇总文本。"""
    from db import db_load_targets

    if not ACTIVE_ACCOUNTS:
        return "❌ 当前没有可用账号"
    targets = db_load_targets()
    if not targets:
        return "❌ 名单为空，请先用 /collect 收集"

    out_dir = out_dir or os.path.join(DATA_DIR, "filter")
    os.makedirs(out_dir, exist_ok=True)

    acc_no, client, _ph = ACTIVE_ACCOUNTS[0]  # 用主账号检测
    counts = {"ok": 0, "frozen": 0, "muted": 0, "error": 0}
    buckets = {"ok": [], "frozen": [], "muted": [], "error": []}

    checked = 0
    for uid, info in list(targets.items())[:limit]:
        ok, key, detail = await check_one_user(client, uid, info.get("access_hash", 0))
        buckets[key].append((uid, detail))
        counts[key] += 1
        checked += 1
        if checked % 50 == 0:
            log.info(f"🔍 过滤进度: {checked}/{min(limit, len(targets))}")
        await asyncio.sleep(per_user_delay)

    # 写分类文件
    label_map = {"ok": "ok.txt", "frozen": "冻结账号.txt", "muted": "禁言账号.txt", "error": "error.txt"}
    for key, fname in label_map.items():
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            for uid, detail in buckets[key]:
                f.write(f"{uid}\t{detail}\n")

    summary = (
        f"🔍 账号过滤完成（检查 {checked} 个）：\n"
        f"• 可用(ok): {counts['ok']}\n"
        f"• 冻结: {counts['frozen']}\n"
        f"• 禁言: {counts['muted']}\n"
        f"• error: {counts['error']}\n"
        f"结果已保存到 {out_dir}"
    )
    return summary
