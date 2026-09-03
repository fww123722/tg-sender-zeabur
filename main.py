#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""程序入口：启动健康检查 → 连接控制Bot → 加载账号 → 注册面板 → 热替换监听。"""
import asyncio
import os
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import ACCS, API_ID, API_HASH, BOT_TOKEN, OWNER_ID, N_ACCOUNTS, log
from config import ACTIVE_ACCOUNTS, ZIP_RECEIVED
from db import DB
from health import start_health_server
from accounts import _find_session, _register_zip_receiver
from bot import register_handlers


async def load_accounts(bot):
    """从 PostgreSQL 扫描所有已保存的 tg_session_* 加载账号，返回 (ready, failed)。
    账号全部通过 Bot 对话登录并持久化到 DB；重启后自动恢复，无需环境变量预设手机号。"""
    ready = []
    failed = []
    acc_nos = DB.list_sessions("tg_session_")
    if not acc_nos:
        return ready, failed
    for acc_no in acc_nos:
        phone = ACCS.get(acc_no, "")
        session_str = DB.load_session("tg_session_%d" % acc_no)
        if session_str:
            client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        else:
            sess = _find_session(acc_no)
            client = TelegramClient(sess, API_ID, API_HASH)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                failed.append(acc_no)
                await client.disconnect()
                continue
            me = await client.get_me()
            if not ACCS.get(acc_no):
                ACCS[acc_no] = phone or (me.phone or "")
            ready.append((acc_no, client, phone or ACCS[acc_no]))
            log.info(f"✅ [账号{acc_no}] 已加载 session: {me.first_name} (@{me.username})")
            try:
                DB.save_session("tg_session_%d" % acc_no, client.session.save())
            except Exception as exc:
                log.warning("⚠️ 保存 session 到 PostgreSQL 失败: %s", exc)
            try:
                await bot.send_message(
                    OWNER_ID, f"✅ [账号{acc_no}] 已加载 session: {me.first_name} (@{me.username})"
                )
            except Exception:
                pass
        except Exception as e:
            failed.append(acc_no)
            log.error(f"❌ [账号{acc_no}] 加载 session 失败: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
            try:
                await bot.send_message(OWNER_ID, f"❌ [账号{acc_no}] 加载 session 失败: {e}")
            except Exception:
                pass
    return ready, failed


async def main():
    start_health_server()
    DB.init()

    # bot_session：优先从 PostgreSQL 加载 StringSession，容器重启不丢失
    bot_session_str = DB.load_session("bot_session")
    bot = TelegramClient(
        StringSession(bot_session_str) if bot_session_str else StringSession(), API_ID, API_HASH
    )
    for attempt in (1, 2):
        try:
            await bot.start(bot_token=BOT_TOKEN)
            me_bot = await bot.get_me()
            log.info(f"🤖 控制 Bot 已连接: @{me_bot.username}")
            try:
                DB.save_session("bot_session", bot.session.save())
            except Exception as exc:
                log.warning("⚠️ 保存 bot_session 到 PostgreSQL 失败: %s", exc)
            break
        except Exception as e:
            estr = str(e)
            if "AUTH_KEY_UNREGISTERED" in estr and attempt == 1:
                log.warning("⚠️ bot_session 已失效（AUTH_KEY_UNREGISTERED），删除后重新登录…")
                try:
                    await bot.disconnect()
                except Exception:
                    pass
                DB.save_session("bot_session", "")  # 清空无效 session
                bot = TelegramClient(StringSession(), API_ID, API_HASH)
                continue
            if "API_ID_INVALID" in estr or "api_id" in estr.lower():
                log.error("❌ Bot 连接失败: API_ID/API_HASH 无效。请核对 my.telegram.org 的值是否正确。")
            elif "TOKEN_INVALID" in estr or "token" in estr.lower():
                log.error("❌ Bot 连接失败: BOT_TOKEN 无效。请用 @BotFather 重新获取 token。")
            else:
                log.error(f"❌ Bot 连接失败: {e}")
            sys.exit(1)

    # 注册 zip 接收 handler：owner 可随时发 tg_sessions.zip
    await _register_zip_receiver(bot)

    # 先注册指令 handler（含 /start /menu /login 等），让 owner 随时可操作 Bot
    register_handlers(bot, ACTIVE_ACCOUNTS)

    ready, failed = await load_accounts(bot)
    ACTIVE_ACCOUNTS.extend(ready)

    if not ready:
        try:
            await bot.send_message(
                OWNER_ID,
                "⚠️ 服务器上还没有已登录的账号。\n\n"
                "两种方式添加账号：\n"
                "1️⃣ 点「账号状态」→「添加账号」，直接发送手机号，验证码发到这里回复数字即可（推荐）\n"
                "2️⃣ 发送 /login —— 向 ACCOUNT_1_PHONE（环境变量）发送验证码\n\n"
                "登录成功后自动上线，无需重启。",
            )
        except Exception:
            pass
        log.info("⏳ 等待账号登录（添加账号按钮 / /login / session zip）…")
        import time
        remind_at = time.time() + 1800  # 每 30 分钟提醒一次
        while not ready:
            try:
                await asyncio.wait_for(ZIP_RECEIVED.wait(), timeout=60)
            except asyncio.TimeoutError:
                if time.time() >= remind_at:
                    try:
                        await bot.send_message(
                            OWNER_ID,
                            "⏳ 仍未添加账号。点「账号状态」→「添加账号」发送手机号，或发 /login。",
                        )
                    except Exception:
                        pass
                    remind_at = time.time() + 1800
                continue
            ZIP_RECEIVED.clear()
            log.info("📦 已收到 session 压缩包，尝试重新加载…")
            ready, failed = await load_accounts(bot)
            ACTIVE_ACCOUNTS.clear()
            ACTIVE_ACCOUNTS.extend(ready)

    await bot.send_message(
        OWNER_ID,
        f"🟢 群发系统已上线，{len(ready)}/{N_ACCOUNTS} 个账号可用。\n"
        "点 /menu 打开控制面板。",
    )

    # ---- 运行时热替换：监听新 zip 到达，断开旧客户端、重新加载 ----
    async def hot_reload_watcher():
        while True:
            try:
                await asyncio.wait_for(ZIP_RECEIVED.wait(), timeout=300)
            except asyncio.TimeoutError:
                continue
            ZIP_RECEIVED.clear()
            log.info("♻️ 收到新 session 压缩包，开始热替换账号…")
            try:
                await bot.send_message(OWNER_ID, "♻️ 收到新 session 压缩包，正在热替换账号…")
            except Exception:
                pass
            old_clients = [(acc_no, client) for acc_no, client, _ph in ACTIVE_ACCOUNTS]
            ACTIVE_ACCOUNTS.clear()
            for acc_no, client in old_clients:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            # 重新创建 TelegramClient
            new_accounts = []
            for acc_no in DB.list_sessions("tg_session_"):
                phone = ACCS.get(acc_no, "")
                session_str = DB.load_session("tg_session_%d" % acc_no)
                if session_str:
                    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
                else:
                    sess = _find_session(acc_no)
                    client = TelegramClient(sess, API_ID, API_HASH)
                try:
                    await client.connect()
                    if not await client.is_user_authorized():
                        await client.disconnect()
                        continue
                    me = await client.get_me()
                    new_accounts.append((acc_no, client, phone))
                    log.info(f"✅ [账号{acc_no}] 热替换成功: {me.first_name} (@{me.username})")
                except Exception as e:
                    log.error(f"❌ [账号{acc_no}] 热替换失败: {e}")
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
            if new_accounts:
                ACTIVE_ACCOUNTS.extend(new_accounts)
                msg = f"♻️ 热替换完成，当前 {len(new_accounts)} 个账号在线"
                for acc_no, client, phone in new_accounts:
                    asyncio.create_task(client.run_until_disconnected())
                log.info(msg)
                try:
                    await bot.send_message(OWNER_ID, msg)
                except Exception:
                    pass
            else:
                msg = "❌ 热替换失败：新 zip 中没有可用账号，已回退到原账号"
                log.error(msg)
                for acc_no, client in old_clients:
                    try:
                        await client.connect()
                        ACTIVE_ACCOUNTS.append((acc_no, client, ACCS.get(acc_no, "")))
                    except Exception:
                        pass
                try:
                    await bot.send_message(OWNER_ID, msg)
                except Exception:
                    pass

    # 运行所有组件
    tasks = [bot.run_until_disconnected(), hot_reload_watcher()]
    for acc_no, client, phone in ready:
        tasks.append(client.run_until_disconnected())
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 已退出")
