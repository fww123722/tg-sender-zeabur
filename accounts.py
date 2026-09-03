#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账号管理：Bot 交互登录、session 导入导出、热替换、文件查找。"""
import asyncio
import os
import re
import zipfile

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

from config import ACCS, ACTIVE_ACCOUNTS, DATA_DIR, LOGIN_STATE, ZIP_RECEIVED, API_ID, API_HASH, OWNER_ID, log


async def login_send(bot, text):
    """登录流程向 owner 发消息（容错）"""
    try:
        await bot.send_message(OWNER_ID, text)
    except Exception:
        pass


async def login_wait_reply(timeout=300):
    """等待 owner 的下一条回复（从队列取）。超时返回 None。"""
    try:
        return await asyncio.wait_for(LOGIN_STATE["queue"].get(), timeout=timeout)
    except asyncio.TimeoutError:
        return None


async def login_flow(bot, client, phone, acc_no, owner_entity):
    """完整的交互式登录状态机。
    前置条件：LOGIN_STATE 已设置、client 已 connect 且未授权。
    成功返回 True。"""
    from telethon.errors import (
        PhoneCodeExpiredError,
        PhoneCodeInvalidError,
        PhoneNumberInvalidError,
        SessionPasswordNeededError,
    )

    global LOGIN_STATE
    try:
        for attempt in (1, 2, 3):
            await login_send(bot, f"📱 [账号{acc_no}] 正在向 {phone} 发送验证码…（第 {attempt}/3 次）")
            try:
                await client.send_code_request(phone)
            except PhoneNumberInvalidError:
                await login_send(bot, "❌ 手机号无效（Telegram 不认可），请检查国家码后重新添加")
                return False
            except FloodWaitError as e:
                await login_send(bot, f"⏳ 发送过于频繁，需等待 {e.seconds} 秒。请稍后再试")
                return False

            await login_send(bot, "🔐 请回复收到的验证码数字（5 分钟内有效）：")
            code = await login_wait_reply(300)
            if code is None:
                await login_send(bot, "❌ 验证码输入超时，登录中止。请重新发起登录")
                return False
            code = code.strip()

            try:
                await client.sign_in(phone, code)
                break
            except PhoneCodeExpiredError:
                await login_send(bot, "⌛ 验证码已过期，自动重发新验证码…")
                continue
            except PhoneCodeInvalidError:
                await login_send(bot, "❌ 验证码不正确，自动重发新验证码…请回复最新一条的数字")
                continue
            except SessionPasswordNeededError:
                await login_send(bot, "🔑 该账号开启了二步验证，请回复密码：")
                pwd = await login_wait_reply(300)
                if pwd is None:
                    await login_send(bot, "❌ 密码输入超时，登录中止")
                    return False
                try:
                    await client.sign_in(password=pwd.strip())
                    break
                except Exception as e:
                    await login_send(bot, f"❌ 密码错误或登录失败: {e}\n请重新发起登录")
                    return False
            except Exception as e:
                estr = str(e)
                if "PHONE_CODE_INVALID" in estr or "previously shared" in estr:
                    await login_send(
                        bot,
                        "🚫 Telegram 风控拦截：该验证码被判定为「已共享/泄露」。\n"
                        "自动重发新验证码…请只在本对话回复新验证码，不要在其他任何地方输入。",
                    )
                    continue
                await login_send(bot, f"❌ 登录失败: {e}")
                return False
        else:
            await login_send(bot, "❌ 验证码连续 3 次失败，登录中止。请稍后重试")
            return False

        if not await client.is_user_authorized():
            await login_send(bot, "❌ 登录未完成（Telegram 可能拦截了本次登录，请查看该账号的官方通知后重试）")
            return False

        me = await client.get_me()
        log.info(f"✅ [账号{acc_no}] 登录成功: {me.first_name} (@{me.username})")
        await login_send(bot, f"✅ [账号{acc_no}] 登录成功: {me.first_name} (@{me.username})")
        return True
    finally:
        LOGIN_STATE = None


async def _login_accounts(bot, accounts, targets, owner_entity):
    """通过 Bot 交互式登录指定账号（验证码/2FA 密码通过 Bot 问答）。
    登录成功后自动加入 ACTIVE_ACCOUNTS 并启动客户端。"""
    global LOGIN_STATE
    for acc_no, client, phone in accounts:
        if acc_no not in targets:
            continue
        if LOGIN_STATE is not None:
            await login_send(bot, "⏳ 已有登录流程进行中，请先完成或等待超时")
            return
        try:
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                await login_send(bot, f"⏭️ [账号{acc_no}] 已登录: {me.first_name} (@{me.username})，无需重复登录")
                continue

            LOGIN_STATE = {
                "stage": "code",
                "acc_no": acc_no,
                "phone": phone,
                "client": client,
                "owner_entity": owner_entity,
                "queue": asyncio.Queue(),
            }
            ok = await login_flow(bot, client, phone, acc_no, owner_entity)
            if ok:
                if not any(a[0] == acc_no for a in ACTIVE_ACCOUNTS):
                    ACTIVE_ACCOUNTS.append((acc_no, client, phone))
                    asyncio.create_task(client.run_until_disconnected())
                from db import DB
                try:
                    DB.save_session("tg_session_%d" % acc_no, client.session.save())
                except Exception as exc:
                    log.warning("? 保存 session 到 PostgreSQL 失败: %s", exc)
                await login_send(bot, f"🟢 [账号{acc_no}] 已上线，当前共 {len(ACTIVE_ACCOUNTS)} 个账号在线")
            else:
                try:
                    await client.disconnect()
                except Exception:
                    pass
        except Exception as e:
            log.error(f"❌ [账号{acc_no}] 登录异常: {e}")
            LOGIN_STATE = None
            try:
                await bot.send_message(owner_entity, f"❌ [账号{acc_no}] 登录异常: {e}")
                await client.disconnect()
            except Exception:
                pass


async def _add_account_interactive(bot, phone, owner_entity):
    """通过 Bot 交互式添加任意账号：输入手机号 → 验证码 → （2FA密码）→ 上线。
    不依赖环境变量，成功后自动分配下一个可用序号。"""
    from db import DB
    global LOGIN_STATE
    phone = (phone or "").strip()
    if not re.match(r"^\+?\d{6,15}$", phone):
        await bot.send_message(owner_entity, "❌ 手机号格式无效（应含国家码，如 +8613800138000）")
        return
    if LOGIN_STATE is not None:
        await bot.send_message(owner_entity, "⏳ 已有登录流程进行中，请先完成或等待超时")
        return

    acc_no = max(list(ACCS.keys()) + [a[0] for a in ACTIVE_ACCOUNTS] + [0]) + 1
    sess = os.path.join(DATA_DIR, f"tg_session_{acc_no}.session")

    session_str = DB.load_session("tg_session_%d" % acc_no)
    if session_str:
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    else:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
    try:
        await client.connect()
        if await client.is_user_authorized():
            me = await client.get_me()
            await bot.send_message(owner_entity, f"✅ 该手机号已有有效 session: {me.first_name} (@{me.username})")
            return

        LOGIN_STATE = {
            "stage": "code",
            "acc_no": acc_no,
            "phone": phone,
            "client": client,
            "owner_entity": owner_entity,
            "queue": asyncio.Queue(),
        }
        ok = await login_flow(bot, client, phone, acc_no, owner_entity)
        if ok:
            ACCS[acc_no] = phone
            ACTIVE_ACCOUNTS.append((acc_no, client, phone))
            asyncio.create_task(client.run_until_disconnected())
            try:
                DB.save_session("tg_session_%d" % acc_no, client.session.save())
            except Exception as exc:
                log.warning("? 保存 session 到 PostgreSQL 失败: %s", exc)
            await bot.send_message(
                owner_entity,
                f"🟢 [账号{acc_no}] 已上线，可直接使用群发功能。当前共 {len(ACTIVE_ACCOUNTS)} 个账号在线。",
            )
        else:
            try:
                await client.disconnect()
            except Exception:
                pass
    except Exception as e:
        log.error(f"❌ [新账号] 添加异常: {e}")
        LOGIN_STATE = None
        try:
            await bot.send_message(owner_entity, f"❌ 添加异常: {e}")
            await client.disconnect()
        except Exception:
            pass


# =====================================================================
#  session 压缩包导入 / 热替换
# =====================================================================
def _extract_session_zip(download_path):
    """把 zip 里的 session 文件解压到 DATA_DIR，返回 (成功数, 失败原因)。"""
    from db import DB
    try:
        with zipfile.ZipFile(download_path, "r") as zf:
            targets = [n for n in zf.namelist() if n.endswith(".session") or n.endswith(".session-journal")]
            if not targets:
                return 0, "压缩包里没有找到 .session 文件"
            os.makedirs(DATA_DIR, exist_ok=True)
            for old in os.listdir(DATA_DIR):
                if old.startswith("tg_session_") and (old.endswith(".session") or old.endswith(".session-journal")):
                    try:
                        os.remove(os.path.join(DATA_DIR, old))
                    except Exception:
                        pass
            for n in targets:
                base = os.path.basename(n)
                if not base or not re.match(r"^tg_session_\d+\.session(-journal)?$", base):
                    continue
                with zf.open(n) as src, open(os.path.join(DATA_DIR, base), "wb") as dst:
                    dst.write(src.read())
                    m = re.match(r"tg_session_(\d+)\.session", base)
                    if m:
                        try:
                            _import_session_file_to_pg(int(m.group(1)), os.path.join(DATA_DIR, base))
                        except Exception as exc:
                            log.warning("⚠️ 导入 session 到 PostgreSQL 失败: %s", exc)
        try:
            os.remove(download_path)
        except Exception:
            pass
        return len(targets), None
    except Exception as e:
        return 0, str(e)


async def _register_zip_receiver(bot):
    """在账号就绪前先注册 zip 接收 handler，让 owner 能直接发 session 压缩包"""
    from telethon import events

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
        await event.client.send_message(event.chat_id, "📦 收到 session 压缩包，正在解压…")
        try:
            dl = await event.download_media(file=os.path.join(DATA_DIR, "_incoming.zip"))
            if not dl:
                await event.client.send_message(event.chat_id, "❌ 下载失败")
                return
            cnt, err = _extract_session_zip(dl)
            if err:
                await event.client.send_message(event.chat_id, f"❌ 解压失败: {err}")
                return
            ZIP_RECEIVED.set()
            await event.client.send_message(event.chat_id, f"✅ 已解压 {cnt} 个 session 文件。正在重新加载…")
        except Exception as e:
            await event.client.send_message(event.chat_id, f"❌ 处理压缩包失败: {e}")


def _import_session_file_to_pg(acc_no: int, filepath: str):
    """读取本地 .session 文件（SQLite），转换成 StringSession 字符串，写入 PostgreSQL。"""
    from db import DB
    if not os.path.isfile(filepath):
        return
    try:
        import telethon.sessions as tsess
        sql_sess = tsess.SQLiteSession(filepath)
        string_sess = tsess.StringSession()
        string_sess._dc_id = sql_sess.dc_id
        string_sess._server_address = sql_sess.server_address
        string_sess._port = sql_sess.port
        if sql_sess.auth_key:
            string_sess._auth_key = sql_sess.auth_key
        data = string_sess.save()
        if data:
            DB.save_session("tg_session_%d" % acc_no, data)
    except Exception as e:
        log.warning("⚠️ 导入 session[%s] 到 PostgreSQL 失败: %s", acc_no, e)


def _find_session(acc_no: int) -> str:
    """依次在 DATA_DIR 和 BASE_DIR 下查找 session 文件，返回第一个存在的路径，或默认 DATA_DIR 路径"""
    from config import BASE_DIR
    candidates = [
        os.path.join(DATA_DIR, f"tg_session_{acc_no}.session"),
        os.path.join(BASE_DIR, f"tg_session_{acc_no}.session"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return candidates[0]
