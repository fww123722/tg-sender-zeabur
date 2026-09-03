#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bot 控制面板：多级菜单 + 群发运营向导 + 状态记忆 + 实时进度控制。

成体系交互：
主菜单 → 群发运营 / 群管理 / 账号管理 / 数据看板 / 系统设置
群发运营走 5 步向导（选群→拉名单→写文案→账号准备→开跑），每步影响推进顺序。
进度、选中的群、文案等持久化到 DB（ops_state），下次回来接着继续。
"""
import asyncio
import re

from telethon import events

from config import OWNER_ID, ACTIVE_ACCOUNTS, LOGIN_STATE, state, log
from db import (
    db_count_targets, db_load_targets, db_sent_global, db_get_all_groups,
    db_group_count, db_load_stats,
)
from collector import (
    db_count_pool, collect_members, list_my_groups, join_group_by_link,
    collect_channel_history,
)
from sender import send_to_list_multi, broadcast_to_groups, forward_from_channel
from profile import edit_all_profiles
from filter import filter_accounts
from accounts import _login_accounts, _add_account_interactive
from ops_state import (
    get_campaign, set_campaign, clear_campaign, campaign_text,
    get, set as ops_set,
)
from bot_menu import (
    BTN, BTN_ACTION, INPUT_ACTIONS, INPUT_HINTS,
    main_menu_kb, campaign_menu_kb, groups_menu_kb, accounts_menu_kb,
    settings_menu_kb, dashboard_menu_kb,
    main_menu_text, campaign_menu_text, groups_menu_text,
    accounts_menu_text, settings_menu_text,
)

# 按钮输入等待状态: {chat_id: 当前响应的动作标识}
pending_action = {}


async def _reply(event, text, buttons=None):
    try:
        return await event.client.send_message(event.chat_id, text, buttons=buttons)
    except Exception:
        return None


def _nice_name(acc_no, client):
    try:
        import asyncio as _a
        me_fut = asyncio.ensure_future(client.get_me())
        _a.get_event_loop()
    except Exception:
        pass
    return f"账号{acc_no}"


# ---- 设置持久化读写（每账号状态存内存，设置落 DB） ----
def _load_settings():
    s = get("settings") or {}
    s.setdefault("min_delay", state["min_delay"])
    s.setdefault("max_delay", state["max_delay"])
    s.setdefault("daily_limit", state["daily_limit"])
    return s


def _apply_settings_to_state():
    s = _load_settings()
    state["min_delay"] = s["min_delay"]
    state["max_delay"] = s["max_delay"]
    state["daily_limit"] = s["daily_limit"]


async def _push_main_menu(event):
    """发主菜单文本（含数据摘要），回到主键盘。"""
    _apply_settings_to_state()
    sent = db_sent_global()
    text = main_menu_text(
        ACTIVE_ACCOUNTS, db_group_count(), db_count_targets(),
        sent, db_count_pool(), state["busy"],
    )
    await _reply(event, text, buttons=main_menu_kb())


# =====================================================================
#  register_handlers
# =====================================================================
def register_handlers(bot, accounts):
    # accounts 引用 ACTIVE_ACCOUNTS 模块级容器

    def _no_accounts(event) -> bool:
        if not accounts:
            asyncio.ensure_future(_reply(event, "⚠️ 当前没有可用账号。\n请先点「账号管理」→「添加账号」。"))
            return True
        return False

    # ---------- /start 与 /menu：显示主面板 ----------
    @bot.on(events.NewMessage(pattern="^/start$"))
    async def on_start(event):
        if event.sender_id != OWNER_ID:
            await _reply(event, "⛔ 无权限")
            return
        await _push_main_menu(event)

    @bot.on(events.NewMessage(pattern="^/menu$"))
    async def on_menu(event):
        if event.sender_id != OWNER_ID:
            return
        await _push_main_menu(event)

    # ---------- 兼容旧命令 ----------
    @bot.on(events.NewMessage(pattern="^/stats$"))
    async def on_stats(event):
        if event.sender_id != OWNER_ID:
            return
        lines = [f"📊 名单: {db_count_targets()} | 已发(去重): {db_sent_global()} | 文案池: {db_count_pool()}"]
        for acc_no, client, _ph in accounts:
            s = db_load_stats(acc_no)
            lines.append(f"• [{acc_no}] 今日{s['sent_today']} 累计{s['total_sent']}")
        await _reply(event, "\n".join(lines))

    @bot.on(events.NewMessage(pattern=r"^/login(?:\s+(\d+))?$"))
    async def on_login(event):
        if event.sender_id != OWNER_ID:
            return
        if _no_accounts(event):
            return
        target = event.pattern_match.group(1)
        targets = [int(target)] if target else [a for a, _c, _p in accounts]
        await _reply(event, f"🔄 开始登录账号 {targets}，验证码发到这里，直接回复数字…",
                     buttons=main_menu_kb())
        asyncio.ensure_future(_login_accounts(bot, accounts, targets, event.chat_id))

    @bot.on(events.NewMessage(pattern="^/mygroups$"))
    async def on_mygroups(event):
        if event.sender_id != OWNER_ID or _no_accounts(event):
            return
        await _reply(event, await list_my_groups(accounts[0][1]), buttons=groups_menu_kb())

    @bot.on(events.NewMessage(pattern=r"^/collect ([\s\S]+)$"))
    async def on_collect(event):
        if event.sender_id != OWNER_ID or _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, "⏳ 正在执行其他任务")
            return
        state["busy"] = True
        try:
            await _reply(event, "🔄 正在拉取成员…")
            r = await collect_members(accounts[0][1], event.pattern_match.group(1).strip())
            await _reply(event, r)
        finally:
            state["busy"] = False

    @bot.on(events.NewMessage(pattern=r"^/collect_history([\s\S]*)$"))
    async def on_collect_history(event):
        if event.sender_id != OWNER_ID or _no_accounts(event):
            return
        arg = (event.pattern_match.group(1) or "").strip()
        if not arg:
            await _reply(event, "❌ 用法: /collect_history <频道> [数量]")
            return
        parts = arg.split()
        peer, limit = parts[0], int(parts[1]) if len(parts) > 1 else 50
        if state["busy"]:
            await _reply(event, "⏳ 正在执行其他任务")
            return
        state["busy"] = True
        try:
            await _reply(event, f"🔄 正在采集「{peer}」最近 {limit} 条…")
            r = await collect_channel_history(accounts[0][1], peer, limit)
            await _reply(event, r)
        finally:
            state["busy"] = False

    @bot.on(events.NewMessage(pattern=r"^/sendto ([\s\S]+)$"))
    async def on_sendto(event):
        if event.sender_id != OWNER_ID or _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, "⏳ 正在执行其他任务")
            return
        text = event.pattern_match.group(1).strip()
        _start_send_campaign(event, accounts, text)

    @bot.on(events.NewMessage(pattern=r"^/broadcast ([\s\S]+)$"))
    async def on_broadcast(event):
        if event.sender_id != OWNER_ID or _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, "⏳ 正在执行其他任务")
            return
        raw = event.pattern_match.group(1).strip().split(" ", 1)
        if len(raw) < 2:
            await _reply(event, "❌ 用法: /broadcast <群1,群2> <内容>")
            return
        state["busy"] = True
        try:
            await _reply(event, f"🚀 正在广播到: {raw[0]}")
            r = await broadcast_to_groups(accounts[0][1], raw[0], raw[1], event.chat_id)
            await _reply(event, r, buttons=main_menu_kb())
        finally:
            state["busy"] = False

    @bot.on(events.NewMessage(pattern=r"^/forward ([\s\S]+)$"))
    async def on_forward(event):
        if event.sender_id != OWNER_ID or _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, "⏳ 正在执行其他任务")
            return
        parts = event.pattern_match.group(1).split()
        if len(parts) < 2:
            await _reply(event, "❌ 用法: /forward <源频道> <目标群>")
            return
        state["busy"] = True
        try:
            await _reply(event, "🔄 正在转发…")
            r = await forward_from_channel(accounts[0][1], parts[0], parts[1], event.chat_id)
            await _reply(event, r)
        finally:
            state["busy"] = False

    # ---- 登录中：所有文本回复进登录队列 ----
    @bot.on(events.NewMessage())
    async def on_auth_reply(event):
        if event.sender_id != OWNER_ID:
            return
        if LOGIN_STATE is None:
            return
        text = (event.text or "").strip()
        if not text:
            return
        try:
            LOGIN_STATE["queue"].put_nowait(text)
        except Exception:
            pass

    def _show_menu_handler(name):
        """返回一个展示子菜单的 handler builder"""
        async def show(event, text, kb):
            pending_action.pop(event.sender_id, None)
            await _reply(event, text, buttons=kb())
        if name == "campaign":
            return lambda e: show(e, campaign_menu_text() + "\n\n" + campaign_text(), campaign_menu_kb)
        if name == "groups":
            return lambda e: show(e, groups_menu_text(), groups_menu_kb)
        if name == "accounts":
            return lambda e: show(e, accounts_menu_text(), accounts_menu_kb)
        if name == "settings":
            return lambda e: show(e, settings_menu_text(), settings_menu_kb)
        return lambda e: _push_main_menu(e)

    MENU_SHOW = {
        "menu_campaign": campaign_menu_text,
        "menu_groups": groups_menu_text,
        "menu_accounts": accounts_menu_text,
        "menu_settings": settings_menu_text,
    }
    MENU_KB = {
        "menu_campaign": campaign_menu_kb,
        "menu_groups": groups_menu_kb,
        "menu_accounts": accounts_menu_kb,
        "menu_settings": settings_menu_kb,
    }

    def _menu_text(action):
        base = MENU_SHOW[action]()
        if action == "menu_campaign":
            base += "\n\n" + campaign_text()
        return base

    async def _handle_menu_action(event, action):
        """执行一个菜单动作（不含需要输入的 INPUT_ACTIONS）。"""
        cid = event.sender_id
        special = {
            "acc_add_prompt": None, "add_group_prompt": None,
            "batch_import_prompt": None, "set_speed_prompt": None,
            "set_quota_prompt": None, "set_parallel_prompt": None,
            "camp_step3": None, "camp_step1": None,
        }

        # --- 输入类：先设 pending 并给提示 ---
        if action in INPUT_ACTIONS:
            pending_action[cid] = action
            key = action if action in INPUT_HINTS else None
            hint = INPUT_HINTS.get(action, "请输入：")
            kb = _menu_kb_for_action(action)
            await _reply(event, hint, buttons=kb() if kb else None)
            return

        # --- 主菜单切换 ---
        if action in MENU_SHOW:
            await _reply(event, _menu_text(action), buttons=MENU_KB[action]())
            return

        # --- 各功能即时动作 ---
        if action == "menu_dashboard":
            await _dashboard(event)
        elif action == "dashboard_view":
            await _dashboard(event)
        elif action == "refresh":
            await _push_main_menu(event)
        elif action == "my_groups":
            if _no_accounts(event):
                return
            await _reply(event, await list_my_groups(accounts[0][1]), buttons=groups_menu_kb())
        elif action == "acc_list":
            await _acc_list(event, accounts)
        elif action == "acc_filter":
            await _run_filter(event, accounts)
        elif action == "acc_edit_profile":
            await _run_editprofile(event, accounts)
        elif action == "camp_status":
            await _reply(event, campaign_menu_text() + "\n\n" + campaign_text(), buttons=campaign_menu_kb())
        elif action == "camp_step2":
            await _do_step2(event, accounts)
        elif action == "camp_step4":
            await _do_step4(event, accounts)
        elif action == "camp_start":
            await _do_start(event, accounts)
        elif action == "pause":
            state["paused"] = True
            await _reply(event, "⏸ 已暂停（点「继续」恢复）", buttons=campaign_menu_kb())
        elif action == "resume":
            state["paused"] = False
            await _reply(event, "▶️ 已继续", buttons=campaign_menu_kb())
        elif action == "stop":
            state["stop"] = True
            state["paused"] = True
            state["busy"] = False
            await _reply(event, "🛑 已停止当前任务", buttons=campaign_menu_kb())
        elif action == "back_home":
            await _push_main_menu(event)

    # ---------- 菜单动作统一分发（含 pending_input 处理） ----------
    @bot.on(events.NewMessage())
    async def on_any_text(event):
        if event.sender_id != OWNER_ID:
            return
        text = (event.text or "").strip()
        if not text:
            return

        # 1. 底部按钮 → 执行动作
        if text in BTN_ACTION:
            action = BTN_ACTION[text]
            await _handle_menu_action(event, action)
            return

        # 2. 数字快捷选群（从「我的群」返回的群序号，预留）
        # 3. 有 pending 输入 → 消费
        action = pending_action.get(event.sender_id)
        if action:
            del pending_action[event.sender_id]
            await _consume_input(event, action, text)
            return

        # 4. 未登录状态下多余输入提示
        if not accounts and not LOGIN_STATE:
            await _reply(event, "尚未登录账号，请点「账号管理」→「添加账号」。", buttons=main_menu_kb())
            return

        # 5. 自动识别群链接 → 加入并拉人
        if text.startswith("/"):
            return
        m = re.search(r"(?:https?://)?t\.me/(?:joinchat/|\+)?[A-Za-z0-9_\-]+", text)
        if m and not state["busy"] and accounts:
            await _auto_addgroup(event, accounts, m.group(0))

    def _menu_kb_for_action(action):
        if action in ("camp_step3",):  # 文案输入时保留群发菜单
            return campaign_menu_kb
        if action == "camp_step1":      # 选群输入保留群发菜单
            return campaign_menu_kb
        if action == "add_group_prompt" or action == "batch_import_prompt":
            return groups_menu_kb
        if action == "acc_add_prompt":
            return accounts_menu_kb
        return None

    # ---------- 消费一个 pending 输入 ----------
    async def _consume_input(event, action, text):
        if action == "camp_step1":
            await _do_step1(event, accounts, text)
        elif action == "camp_step3":
            set_campaign(text=text)
            body = campaign_menu_text() + "\n\n✅ 文案已保存：\n" + text[:100] + ("…" if len(text) > 100 else "")
            body += "\n\n" + campaign_text()
            await _reply(event, body, buttons=campaign_menu_kb())
        elif action == "add_group_prompt":
            await _finish_addgroup(event, accounts, text)
        elif action == "batch_import_prompt":
            await _finish_batchimport(event, accounts, text)
        elif action == "acc_add_prompt":
            await _reply(event, f"🔄 开始添加账号 {text} …", buttons=accounts_menu_kb())
            await _add_account_interactive(bot, text, event.chat_id)
        elif action == "set_speed_prompt":
            try:
                sec = max(1, int(text))
                s = _load_settings()
                s["min_delay"], s["max_delay"] = sec, sec + 10
                ops_set("settings", s)
                state["min_delay"], state["max_delay"] = sec, sec + 10
                await _reply(event, f"⚡ 间隔已设为 {sec}-{sec+10}s", buttons=settings_menu_kb())
            except ValueError:
                await _reply(event, "❌ 请输入数字秒数", buttons=settings_menu_kb())
        elif action == "set_quota_prompt":
            try:
                q = max(1, int(text))
                s = _load_settings()
                s["daily_limit"] = q
                ops_set("settings", s)
                state["daily_limit"] = q
                await _reply(event, f"🎯 每账号每日上限已设为 {q} 条", buttons=settings_menu_kb())
            except ValueError:
                await _reply(event, "❌ 请输入数字条数", buttons=settings_menu_kb())
        elif action == "set_parallel_prompt":
            await _reply(event, "⏩ 并行账号数由系统按可用账号自动分配，无需手动设置。\n"
                                "当前可用账号越多，自动分配越快。", buttons=settings_menu_kb())

    # ---------- 数据看板 ----------
    async def _dashboard(event):
        sent = db_sent_global()
        lines = [
            "📊 数据看板",
            f"• 名单: {db_count_targets()} 人",
            f"• 已发(去重): {sent} 人",
            f"• 待发送: {max(0, db_count_targets() - sent)} 人",
            f"• 文案池: {db_count_pool()} 条",
            f"• 已加群: {db_group_count()} 个",
            f"• 在线账号: {len(accounts)} 个",
            "—",
            "各账号今日/累计：",
        ]
        for acc_no, client, _ph in accounts:
            s = db_load_stats(acc_no)
            lines.append(f"  [{acc_no}] 今日 {s['sent_today']} | 累计 {s['total_sent']}")
        await _reply(event, "\n".join(lines), buttons=dashboard_menu_kb())

    async def _acc_list(event, accounts):
        if not accounts:
            await _reply(event, "⚠️ 没有在线账号。请点「添加账号」。", buttons=accounts_menu_kb())
            return
        lines = [f"👥 在线账号 {len(accounts)} 个："]
        for acc_no, client, phone in accounts:
            try:
                await client.connect()
                me = await client.get_me()
                name = me.first_name or f"账号{acc_no}"
                try:
                    await client.get_dialogs(limit=1)
                    st = "✅ 可用"
                except Exception:
                    st = "⏳ 受限"
                ph = phone or (me.phone or "")
                lines.append(f"• [{acc_no}] {name} ({ph}) {st}")
            except Exception as e:
                lines.append(f"• [账号{acc_no}] 冻结（{str(e)[:40]}）")
        lines.append("—")
        lines.append("如需改昵称/头像/简介，点「批量改资料」养号。")
        await _reply(event, "\n".join(lines), buttons=accounts_menu_kb())

    # ---------- 群发运营 5 步 ----------
    async def _do_step1(event, accounts, text):
        """① 选择群 → 加群并记录为当前运营群（不立刻拉人）"""
        if _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, "⏳ 正在执行加群…")
            return
        state["busy"] = True
        try:
            await _reply(event, f"🔄 正在加入群组 {text} …")
            r = await join_group_by_link(accounts[0][1], text)
            await _reply(event, r)
            set_campaign(group=text)
            # 尝试从返回文本里抠标题（简化：直接存原始输入，标题后续用 group id 回填）
            # 尝试解析群实体存 title / id
            try:
                ent = await accounts[0][1].get_entity(text)
                set_campaign(group_title=getattr(ent, "title", text))
            except Exception:
                pass
            body = campaign_menu_text() + "\n\n" + campaign_text()
            await _reply(event, body, buttons=campaign_menu_kb())
        finally:
            state["busy"] = False

    async def _do_step2(event, accounts):
        """② 拉名单：从未知目标群拉成员。若已设置运营群且名单为空则拉运营群。"""
        if _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, "⏳ 正在执行其他任务")
            return
        camp = get_campaign()
        target = camp.get("group")
        if not target:
            await _reply(event, "请先完成「① 选择群」。", buttons=campaign_menu_kb())
            return
        state["busy"] = True
        try:
            await _reply(event, f"🔄 正在从「{camp.get('group_title') or target}」拉取成员到名单…")
            r = await collect_members(accounts[0][1], target)
            await _reply(event, r)
            set_campaign(target_count=db_count_targets())
            body = campaign_menu_text() + "\n\n" + campaign_text()
            await _reply(event, body, buttons=campaign_menu_kb())
        finally:
            state["busy"] = False

    async def _do_step4(event, accounts):
        """④ 账号准备：显示账号在线数 & 名单下发前是否满足基本条件。"""
        if not accounts:
            await _reply(event, "⚠️ 没有在线账号，请先「添加账号」。", buttons=campaign_menu_kb())
            return
        set_campaign(accounts_ready=True)
        body = (
            f"✅ 账号就绪：{len(accounts)} 个在线可用。\n\n"
            "开始群发时会自动轮换分配目标。\n\n"
            + campaign_text()
        )
        await _reply(event, body, buttons=campaign_menu_kb())

    async def _do_start(event, accounts):
        """⑤ 开始群发：必须群+名单+文案齐全才放行。"""
        if _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, "⏳ 正在执行其他任务（请等待完成）")
            return
        camp = get_campaign()
        if not camp.get("group"):
            await _reply(event, "❌ 还没选群。请先「① 选择群」。", buttons=campaign_menu_kb())
            return
        text = camp.get("text")
        if not text:
            await _reply(event, "❌ 还没写文案。请先「③ 写文案」。", buttons=campaign_menu_kb())
            return
        targets = db_load_targets()
        if not targets:
            await _reply(event, "❌ 名单为空。请先「② 拉取名单」。", buttons=campaign_menu_kb())
            return
        _start_send_campaign(event, accounts, text)

    def _start_send_campaign(event, accounts, text):
        """真正启动多账号群发。"""
        targets = db_load_targets()
        if not targets:
            asyncio.ensure_future(_reply(event, "❌ 名单为空，请先拉取名单。"))
            return
        state["busy"] = True
        state["paused"] = False
        state["stop"] = False
        set_campaign(started=int(__import__("time").time()))
        asyncio.ensure_future(_run_and_finish(event, accounts, text, targets))

    async def _run_and_finish(event, accounts, text, targets):
        try:
            await _reply(event,
                f"🚀 开始群发：{len(accounts)}个账号 | 目标 {len(targets)} 人 | "
                f"间隔 {state['min_delay']}-{state['max_delay']}s\n"
                "每 15 秒自动汇报进度。可用「暂停/继续/停止」控制。")
            result = await send_to_list_multi(accounts, targets, text, event.chat_id)
            await _reply(event, result, buttons=main_menu_kb())
            # 发完清除一次性运营状态
            clear_campaign()
        finally:
            state["busy"] = False
            state["paused"] = False

    # ---------- 过滤 & 改资料 ----------
    async def _run_filter(event, accounts):
        if _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, "⏳ 正在执行其他任务")
            return
        state["busy"] = True
        try:
            await _reply(event, "🔄 正在过滤账号状态（逐个检测，可能较慢）…")
            r = await filter_accounts(event.chat_id, limit=5000, per_user_delay=1.0)
            await _reply(event, r, buttons=accounts_menu_kb())
        finally:
            state["busy"] = False

    async def _run_editprofile(event, accounts):
        if _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, "⏳ 正在执行其他任务")
            return
        state["busy"] = True
        try:
            await _reply(event, "🔄 正在批量修改账号资料（名字/姓氏/用户名/简介/头像）…")
            r = await edit_all_profiles(event.chat_id)
            await _reply(event, r, buttons=accounts_menu_kb())
        finally:
            state["busy"] = False

    # ---------- 加群 & 批量导入 & 自动识别 ----------
    async def _finish_addgroup(event, accounts, text):
        if _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, "⏳ 正在执行其他任务")
            return
        await _reply(event, f"🔄 正在加入 {text} …")
        state["busy"] = True
        try:
            r = await join_group_by_link(accounts[0][1], text)
            await _reply(event, r)
            await _reply(event, "正在读取群成员到名单…")
            r2 = await collect_members(accounts[0][1], text)
            await _reply(event, r2, buttons=groups_menu_kb())
        finally:
            state["busy"] = False

    async def _finish_batchimport(event, accounts, text):
        links = [ln for ln in text.splitlines() if ln.strip()]
        if not links:
            await _reply(event, "没有识别到链接。", buttons=groups_menu_kb())
            return
        if _no_accounts(event):
            return
        state["busy"] = True
        try:
            for i, ln in enumerate(links, 1):
                m = re.search(r"(?:https?://)?t\.me/(?:joinchat/|\+)?[A-Za-z0-9_\-]+", ln.strip())
                if not m:
                    await _reply(event, f"[{i}] ⚠️ 跳过（非链接）: {ln.strip()[:40]}")
                    continue
                link = m.group(0)
                try:
                    r1 = await join_group_by_link(accounts[0][1], link)
                except Exception:
                    r1 = "加群失败"
                try:
                    r2 = await collect_members(accounts[0][1], link)
                except Exception:
                    r2 = "拉人失败"
                await _reply(event, f"[{i}/{len(links)}] {link}\n{r1}\n{r2}")
            await _reply(event, f"✅ 批量导入完成，共处理 {len(links)} 个链接。", buttons=groups_menu_kb())
        finally:
            state["busy"] = False

    async def _auto_addgroup(event, accounts, link):
        if state["busy"]:
            return
        await _reply(event, "检测到群链接，正在加入并读取成员…")
        state["busy"] = True
        try:
            r = await join_group_by_link(accounts[0][1], link)
            await _reply(event, r)
            r2 = await collect_members(accounts[0][1], link)
            await _reply(event, r2, buttons=main_menu_kb())
        finally:
            state["busy"] = False

    # 初始化：把持久化设置应用到 state
    _apply_settings_to_state()
