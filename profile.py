#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量修改账号资料：名字/姓氏/用户名/简介/头像。

两种用法：
1) Bot 交互（推荐）：直接传 name / bio / username_mode，不依赖任何文件。
   username_mode: "skip" 不改 | "random" 每账号生成随机可用用户名 | 其他字符串=统一用户名
2) 文件模式（兼容 qfbot Win 端「配置」目录）：名字.txt / 姓氏.txt / 用户名.txt / 简介.txt / 头像*.jpg

Telegram 用户名规则：5-32 位，字母/数字/下划线，必须字母开头，不能下划线结尾，全局唯一。
"""
import asyncio
import glob
import os
import random
import re
import string

from telethon.errors import (
    FloodWaitError,
    UsernameInvalidError,
    UsernameNotModifiedError,
    UsernameOccupiedError,
)
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest

from config import ACTIVE_ACCOUNTS, DATA_DIR, log

# 随机用户名生成字符集（首字母必须是字母，末位不能是下划线，这里干脆不用下划线）
_U_FIRST = string.ascii_lowercase
_U_REST = string.ascii_lowercase + string.digits


def gen_username(length=None, taken=None):
    """生成一个随机 Telegram 用户名（小写字母开头 + 字母数字）。
    taken: 已占用/已用过的集合，避免本批次重复。"""
    taken = taken or set()
    for _ in range(200):
        n = length or random.randint(8, 11)
        n = max(5, min(32, n))
        name = random.choice(_U_FIRST) + "".join(random.choice(_U_REST) for _ in range(n - 1))
        if name not in taken:
            return name
    # 兜底：加时间戳后缀保证唯一
    return random.choice(_U_FIRST) + "".join(random.choice(_U_REST) for _ in range(7)) + str(random.randint(100000, 999999))


def _txt_value(path):
    """读取配置 txt：返回内容或 None（不存在/空文件）"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            val = f.read().strip()
        return val or None
    except Exception:
        return None


def _load_profile_from_dir(profiles_dir):
    """从 qfbot 同构目录读取资料配置（兼容旧行为）。"""
    profile = {}
    first = _txt_value(os.path.join(profiles_dir, "名字.txt"))
    if first:
        profile["first_name"] = first
    for key, fname in (("last_name", "姓氏.txt"), ("username", "用户名.txt"), ("about", "简介.txt")):
        val = _txt_value(os.path.join(profiles_dir, fname))
        if val is None:
            continue
        profile[key] = None if val == "删除" else val
    return profile


async def _set_username(client, acc_no, new_user, taken):
    """设置用户名，被占用则自动重试随机生成。返回结果描述或 None。"""
    for attempt in range(8):
        try:
            await client(UpdateProfileRequest(username=new_user))
            taken.add(new_user)
            return f"用户名→@{new_user}"
        except UsernameOccupiedError:
            log.info(f"[账号{acc_no}] 用户名 @{new_user} 已被占用，重新生成…")
            new_user = gen_username(taken=taken)
        except UsernameInvalidError:
            log.info(f"[账号{acc_no}] 用户名 @{new_user} 非法，重新生成…")
            new_user = gen_username(taken=taken)
        except UsernameNotModifiedError:
            return None
        except FloodWaitError as e:
            return f"⏳ 用户名频率限制，需等待 {e.seconds}s"
        except Exception as e:
            estr = str(e)
            if "USERNAME_OCCUPIED" in estr or "taken" in estr.lower():
                new_user = gen_username(taken=taken)
                continue
            if "USERNAME_NOT_MODIFIED" in estr:
                return None
            log.warning(f"[账号{acc_no}] 用户名修改失败: {e}")
            return f"用户名失败:{estr[:40]}"
    return "用户名:多次尝试仍被占用，已跳过"


async def _apply_profile(client, acc_no, profile, taken_usernames):
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
        if new_bio is not None and new_bio != (me.bio or ""):
            try:
                await client(UpdateProfileRequest(about=new_bio))
                changed.append(f"简介→{new_bio[:20] or '(已删除)'}")
            except Exception as e:
                log.warning(f"[账号{acc_no}] 简介修改失败: {e}")

        # ---- 用户名 ----
        mode = profile.get("_username_mode", "skip")
        if mode == "random":
            if cur_user:
                changed.append(f"用户名已有@{cur_user}(跳过)")
                taken_usernames.add(cur_user)
            else:
                cand = gen_username(taken=taken_usernames)
                r = await _set_username(client, acc_no, cand, taken_usernames)
                if r:
                    changed.append(r)
                await asyncio.sleep(1.5)
        elif mode and mode != "skip":
            if mode == cur_user:
                pass
            else:
                r = await _set_username(client, acc_no, mode, taken_usernames)
                if r:
                    changed.append(r)
                await asyncio.sleep(1.5)

        if changed:
            log.info(f"✅ [账号{acc_no}] {me.first_name}: " + ", ".join(changed))
            return f"✅ [账号{acc_no}] " + ", ".join(changed)
        return f"⏭ [账号{acc_no}] 无需修改（资料已一致）"
    except FloodWaitError as e:
        return f"⏳ [账号{acc_no}] 频率限制，需等待 {e.seconds}s"
    except Exception as e:
        log.warning(f"[账号{acc_no}] 修改资料失败: {e}")
        return f"❌ [账号{acc_no}] {e}"


async def _apply_avatar(client, acc_no, avatar_path):
    """对单个账号设置头像，返回状态文本"""
    if not avatar_path or not os.path.isfile(avatar_path):
        return f"[账号{acc_no}] 无头像文件"
    try:
        me = await client.get_me()
        await client(UploadProfilePhotoRequest(file=await client.upload_file(avatar_path)))
        log.info(f"✅ [账号{acc_no}] {me.first_name} 头像已更换")
        return f"✅ [账号{acc_no}] 头像已更换"
    except FloodWaitError as e:
        return f"⏳ [账号{acc_no}] 频率限制，需等待 {e.seconds}s"
    except Exception as e:
        log.warning(f"[账号{acc_no}] 头像修改失败: {e}")
        return f"❌ [账号{acc_no}] 头像: {e}"


async def edit_all_profiles(owner_entity, name=None, bio=None, last_name=None,
                            username_mode="skip", avatar=None, profiles_dir=None):
    """遍历全部活跃账号，批量修改资料与头像。

    参数（Bot 交互模式直接传，不传则回退读配置目录）：
      name          统一名字（None=不改）
      last_name     统一姓氏（None=不改，""或"删除"=清空）
      bio           统一简介（None=不改，""或"删除"=清空）
      username_mode "skip"不改 / "random"每账号随机生成 / 其他字符串=统一设置
      avatar        头像文件路径（None=不改）
    """
    if not ACTIVE_ACCOUNTS:
        return "❌ 当前没有可用账号"

    profiles_dir = profiles_dir or DATA_DIR

    # ---- 组装 profile：优先用显式参数，其次回退配置文件 ----
    profile = {}
    if name:
        profile["first_name"] = name
    if last_name is not None:
        profile["last_name"] = None if last_name in ("", "删除") else last_name
    if bio is not None:
        profile["about"] = None if bio in ("", "删除") else bio

    if not any(k in profile for k in ("first_name", "last_name", "about")):
        file_profile = _load_profile_from_dir(profiles_dir)
        profile.update({k: v for k, v in file_profile.items() if k not in profile})

    profile["_username_mode"] = username_mode or "skip"

    # 头像：显式参数优先，否则目录里 头像*.jpg 随机
    if avatar is None:
        candidates = sorted(glob.glob(os.path.join(profiles_dir, "头像*.jpg")))
        avatar = random.choice(candidates) if candidates else None

    has_edit = bool(profile.get("first_name") or profile.get("last_name") is not None
                    or profile.get("about") is not None
                    or profile["_username_mode"] not in ("skip", None, ""))

    if not has_edit and not avatar:
        return ("❌ 没有要修改的内容。\n"
                "请点「批量改资料」后按提示输入统一名字，"
                "或在配置目录放 名字.txt / 简介.txt / 头像*.jpg。")

    # 预收集已有用户名，避免随机生成撞车
    taken = set()
    for _no, _c, _p in list(ACTIVE_ACCOUNTS):
        try:
            m = await _c.get_me()
            if m.username:
                taken.add(m.username.lower())
        except Exception:
            pass

    results = []
    if has_edit:
        results.append(f"📝 待改账号 {len(ACTIVE_ACCOUNTS)} 个 | "
                       f"名字={profile.get('first_name') or '(不改)'} | "
                       f"用户名={'随机生成' if profile['_username_mode'] == 'random' else (profile['_username_mode'] or '不改')}")
        for acc_no, client, _ph in list(ACTIVE_ACCOUNTS):
            results.append(await _apply_profile(client, acc_no, profile, taken))
            await asyncio.sleep(2)  # 账号之间间隔，避免风控
    if avatar:
        for acc_no, client, _ph in list(ACTIVE_ACCOUNTS):
            results.append(await _apply_avatar(client, acc_no, avatar))
            await asyncio.sleep(2)

    return "📝 批量修改结果：\n" + "\n".join(results)
