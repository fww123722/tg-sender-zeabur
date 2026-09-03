#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量修改账号资料：名字/姓氏/用户名/简介/头像。

对应 qfbot(Win端) 的「配置」目录：名字.txt / 姓氏.txt / 用户名.txt / 简介.txt / 头像*.jpg。
规则（Telegram 限制）：
  - 名字必填，不能删除为空
  - 姓氏/用户名/简介 可填「删除」二字来清空
"""
import asyncio
import os
import re

from telethon.errors import FloodWaitError
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.photos import DeletePhotosRequest, UploadProfilePhotoRequest
from telethon.tl.types import InputPhoto

from config import ACTIVE_ACCOUNTS, DATA_DIR, log


def _txt_value(path):
    """读取配置 txt：返回内容或 None（不存在/空文件）"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            val = f.read().strip()
        return val or None
    except Exception:
        return None


async def _apply_profile(client, acc_no, owner_entity, profile):
    """对单个账号应用资料修改，返回状态文本"""
    changed = []
    try:
        me = await client.get_me()
        cur_name = (me.first_name or "").strip()
        cur_last = (me.last_name or "").strip() if me.last_name else ""
        cur_user = (me.username or "").strip() if me.username else ""

        # ---- 名字 ----
        new_name = profile.get("first_name")
        if new_name and new_name != cur_name:
            await client(UpdateProfileRequest(first_name=new_name))
            changed.append(f"名字→{new_name}")

        # ---- 姓氏 ----
        new_last = profile.get("last_name")
        if new_last is not None and new_last != cur_last:
            await client(UpdateProfileRequest(last_name=new_last))
            changed.append(f"姓氏→{new_last or '(已删除)'}")

        # ---- 简介 ----
        new_bio = profile.get("about")
        if new_bio is not None:
            try:
                await client(UpdateProfileRequest(about=new_bio))
                changed.append(f"简介→{new_bio[:20] or '(已删除)'}")
            except Exception as e:
                log.warning(f"[账号{acc_no}] 简介修改失败: {e}")

        # ---- 用户名 ----
        new_user = profile.get("username")
        if new_user is not None:
            try:
                await client(UpdateProfileRequest(username=new_user or None))
                changed.append(f"用户名→@{new_user or '(已删除)'}")
            except Exception as e:
                log.warning(f"[账号{acc_no}] 用户名修改失败: {e}")

        if changed:
            log.info(f"✅ [账号{acc_no}] {me.first_name}: " + ", ".join(changed))
            return f"[账号{acc_no}] " + ", ".join(changed)
        return f"[账号{acc_no}] 无需修改（资料已一致）"
    except FloodWaitError as e:
        return f"[账号{acc_no}] ⏳ 频率限制，需等待 {e.seconds}s"
    except Exception as e:
        log.warning(f"[账号{acc_no}] 修改资料失败: {e}")
        return f"[账号{acc_no}] ❌ {e}"


async def _apply_avatar(client, acc_no, avatar_path):
    """对单个账号设置头像，返回状态文本"""
    if not avatar_path or not os.path.isfile(avatar_path):
        return f"[账号{acc_no}] 无头像文件"
    try:
        me = await client.get_me()
        # 先删旧头像（可选保留；这里直接上传覆盖）
        await client(UploadProfilePhotoRequest(file=await client.upload_file(avatar_path)))
        log.info(f"✅ [账号{acc_no}] {me.first_name} 头像已更换")
        return f"[账号{acc_no}] 头像已更换"
    except FloodWaitError as e:
        return f"[账号{acc_no}] ⏳ 频率限制，需等待 {e.seconds}s"
    except Exception as e:
        log.warning(f"[账号{acc_no}] 头像修改失败: {e}")
        return f"[账号{acc_no}] ❌ 头像: {e}"


async def edit_all_profiles(owner_entity, profiles_dir=None):
    """遍历全部活跃账号，批量修改资料与头像。
    profiles_dir 指向 Win 端「配置」同构目录：
      名字.txt / 姓氏.txt / 用户名.txt / 简介.txt / 头像1.jpg / 头像2.jpg(随机)
    返回汇总文本。"""
    if not ACTIVE_ACCOUNTS:
        return "❌ 当前没有可用账号"
    profiles_dir = profiles_dir or DATA_DIR

    profile = {}
    # 名字必填（没有则跳过整次修改）
    first = _txt_value(os.path.join(profiles_dir, "名字.txt"))
    if first:
        profile["first_name"] = first
    # 姓氏/用户名/简介：内容为「删除」→ 清空；有内容 → 设置；文件不存在 → 不处理
    for key, fname in (("last_name", "姓氏.txt"), ("username", "用户名.txt"), ("about", "简介.txt")):
        val = _txt_value(os.path.join(profiles_dir, fname))
        if val is None:
            continue  # 文件不存在：不处理该字段
        profile[key] = None if val == "删除" else val

    # 头像：支持 头像1.jpg/头像2.jpg 随机
    avatar = None
    import glob
    candidates = sorted(glob.glob(os.path.join(profiles_dir, "头像*.jpg")))
    if candidates:
        avatar = random.choice(candidates)

    if not profile and not avatar:
        return "❌ 配置目录里没有可用的修改内容（至少需要 名字.txt）"

    results = []
    # 资料修改与头像分开做，避免一个失败全挂
    if profile:
        for acc_no, client, _ph in list(ACTIVE_ACCOUNTS):
            results.append(await _apply_profile(client, acc_no, owner_entity, profile))
            await asyncio.sleep(2)  # 账号之间间隔，避免风控
    if avatar:
        for acc_no, client, _ph in list(ACTIVE_ACCOUNTS):
            results.append(await _apply_avatar(client, acc_no, avatar))
            await asyncio.sleep(2)

    return "📝 批量修改结果：\n" + "\n".join(results)


import random  # noqa: E402  放末尾避免顶部 import 顺序混乱
