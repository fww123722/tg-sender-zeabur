#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bot 控制面板：多级菜单 + 群发运营向导 + 状态记忆 + 实时进度控制。

成体系交互：
主菜单 → 群发运营 / 群管理 / 账号管理 / 数据看板 / 系统设置
群发运营走 3 步（①选群自动拉成员 → ②写文案 → ③确认开跑）。
进度、选中的群、文案等持久化到 DB（ops_state），下次回来接着继续。
"""
import asyncio
import re
import time

from telethon import events

import config
from config import (
    OWNER_ID, ACTIVE_ACCOUNTS, state, log,
    is_owner, is_operator, is_authorized, actor_name, OPERATORS,
    add_operator, drop_operator, refresh_operators,
)
from db import (
    db_count_targets, db_load_targets, db_sent_global, db_get_all_groups,
    db_group_count, db_load_stats, db_clear_targets, db_delete_group,
    db_clear_cooldown, db_cooldowns,
    db_load_operators, db_add_operator, db_drop_operator,
    db_log_op, db_op_log_recent, db_op_log_since,
    db_campaign_start, db_campaign_finish, db_campaign_leaderboard,
)
from collector import (
    db_count_pool, collect_members, list_my_groups, join_group_by_link,
    join_group_all_accounts, collect_channel_history, diag_groups, leave_group,
)
from sender import send_to_list_multi, broadcast_to_groups, forward_from_channel
from profile import edit_all_profiles
from filter import check_login_accounts
from accounts import _login_accounts, _add_account_interactive
from ops_state import (
    get_campaign, set_campaign, clear_campaign, campaign_text,
    claim_list, release_list, get_list_claim, touch_list,
    get, set as ops_set,
)
from bot_menu import (
    BTN, BTN_ACTION, INPUT_ACTIONS, INPUT_HINTS,
    main_menu_kb, campaign_menu_kb, groups_menu_kb, accounts_menu_kb,
    settings_inline_kb, dashboard_menu_kb, dashboard_inline_kb, report_menu_kb, reason_menu_kb,
    group_pick_inline_kb, group_del_kb, profile_menu_kb, group_del_confirm_kb,
    main_menu_text, campaign_menu_text, groups_menu_text,
    accounts_menu_text, settings_menu_text, report_menu_text, profile_menu_text,
)
from reasons import REPORT_REASONS, REASON_CN
from reporter import (
    report_user, report_custom, report_super, cooldown_summary,
)
from ai import load_ai_config

# 举报理由按钮文字 -> 理由 key 的反查表
REASON_TEXT_TO_KEY = {v[0]: k for k, v in REPORT_REASONS.items()}

# 按钮输入等待状态: {chat_id: 当前响应的动作标识}
pending_action = {}

# ---- 多人共管权限（老板要求：操作员全部开放） ----
# 主人与操作员同权。唯一留给主人的是「增删操作员」，
# 否则操作员能自拉人扩权、甚至把主人踢出去。
OWNER_EXCLUSIVE = {
    "menu_opadmin", "op_list", "op_log",
}


def can_do(cid, action) -> bool:
    """主人与操作员同权；不在白名单的人永远 False。"""
    if is_owner(cid):
        return True
    if not is_authorized(cid):
        return False
    return action not in OWNER_EXCLUSIVE


def _owner_only_hint(event) -> str:
    return ("⛔ 只有主人能增删操作员（白名单由主人独占，防止自行扩权）。"
            "\n其他功能对你已全部开放。")


async def _reply(event, text, buttons=None):
    try:
        return await event.client.send_message(event.chat_id, text, buttons=buttons)
    except Exception:
        return None


# ---- 设置持久化读写（每账号状态存内存，设置落 DB） ----
def _load_settings():
    s = get("settings") or {}
    s.setdefault("min_delay", state["min_delay"])
    s.setdefault("max_delay", state["max_delay"])
    s.setdefault("daily_limit", state["daily_limit"])
    s.setdefault("parse_mode", None)
    s.setdefault("recent_only_days", 0)
    s.setdefault("allow_repeat", False)
    return s


def _apply_settings_to_state():
    s = _load_settings()
    state["min_delay"] = s["min_delay"]
    state["max_delay"] = s["max_delay"]
    state["daily_limit"] = s["daily_limit"]
    state["parse_mode"] = s.get("parse_mode")
    state["recent_only_days"] = s.get("recent_only_days", 0)
    state["allow_repeat"] = s.get("allow_repeat", False)


PARSE_LABEL = {None: "纯文本", "html": "HTML", "md": "Markdown"}
# =====================================================================
#  多人共管：菜单按角色适配 + 审计
# =====================================================================
def _is_op(event) -> bool:
    return False  # 全开：操作员与主人同一套键盘


def _main_kb(event):
    return main_menu_kb(_is_op(event))


def _groups_kb(event):
    return groups_menu_kb(_is_op(event))


def _groups_text(event):
    return groups_menu_text(_is_op(event))


def _audit(event, action, detail=""):
    """操作留痕：谁在什么时候干了什么。"""
    uid = getattr(event, "sender_id", None)
    db_log_op(uid, actor_name(uid) if uid else "", action, detail)





# =====================================================================
#  任务锁（号池全局共用，同一时刻只能跑一个重任务）
#  只记录「谁在占用」，让被挡住的人知道找谁，不靠它做权限。
# =====================================================================
def _set_busy(uid):
    state["busy"] = True
    state["busy_by"] = {"uid": int(uid or 0), "name": actor_name(uid), "at": int(time.time())}


def _clear_busy():
    state["busy"] = False
    state["busy_by"] = None


def _busy_tip(event) -> str:
    """生成「正忙」提示；别人占着时点名，自己占着时说清楚。"""
    b = state.get("busy_by") or {}
    name = b.get("name")
    if not name:
        return "⏳ 正在执行其他任务"
    if b.get("uid") == event.sender_id:
        return "⏳ 你自己刚发的任务还在跑，等它结束或先点「🛑 停止任务」。"
    mins = max(0, int((time.time() - (b.get("at") or time.time())) / 60))
    return (f"⏳ 正在执行任务，占用者：{name}（已 {mins} 分钟）。\n"
            "号池是共用的，得等它跑完；紧急情况可让主人点「🛑 停止任务」。")


def _settings_kb():
    """系统设置内联键盘（附在消息上，按钮显示当前值）。"""
    return settings_inline_kb(
        recent_on=bool(state.get("recent_only_days")),
        repeat_on=bool(state.get("allow_repeat")),
        parse_label=PARSE_LABEL.get(state.get("parse_mode"), "纯文本"),
        speed=state.get("min_delay"),
        quota=state.get("daily_limit"),
    )


async def _push_main_menu(event):
    """发主菜单文本（含数据摘要），回到主键盘。"""
    _apply_settings_to_state()
    sent = db_sent_global()
    b = state.get("busy_by") or {}
    text = main_menu_text(
        ACTIVE_ACCOUNTS, db_group_count(), db_count_targets(),
        sent, db_count_pool(), state["busy"],
        role="owner" if is_owner(event.sender_id) else "operator",
        name=actor_name(event.sender_id),
        busy_tip=(actor_name(b.get("uid")) if state["busy"] and b else ""),
    )
    await _reply(event, text, buttons=_main_kb(event))


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

    async def _deny(event):
        await _reply(event, _DENY_MSG)

    def _actor(event):
        uid = event.sender_id
        return uid, actor_name(uid)

    def _guard_owner(event):
        """owner-only 入口守卫：非主人回提示。"""
        if is_owner(event.sender_id):
            return True
        asyncio.ensure_future(_reply(event, _owner_only_hint(event)))
        return False

    # ---------- /start 与 /menu：显示主面板 ----------
    @bot.on(events.NewMessage(pattern="^/start$"))
    async def on_start(event):
        if not is_authorized(event.sender_id):
            await _reply(event, "⛔ 无权限\n\n这套系统仅限授权人员使用，请联系管理员开通。")
            return
        await _push_main_menu(event)

    @bot.on(events.NewMessage(pattern="^/menu$"))
    async def on_menu(event):
        if not is_authorized(event.sender_id):
            return
        await _push_main_menu(event)

    # ---------- 管理员：操作员白名单（仅主人） ----------
    @bot.on(events.NewMessage(pattern=r"^/addop\s+(\d+)(?:\s+([\s\S]+))?$"))
    async def on_addop(event):
        if not is_owner(event.sender_id):
            await _reply(event, "⛔ 只有主人能增删操作员。")
            return
        uid = int(event.pattern_match.group(1))
        name = (event.pattern_match.group(2) or "").strip()
        persisted = db_add_operator(uid, name, added_by=OWNER_ID)
        refreshed = db_load_operators()
        if refreshed is not None:
            refresh_operators(refreshed)
        add_operator(uid, name)
        shown = OPERATORS.get(uid, f"user{uid}")
        db_log_op(event.sender_id, actor_name(event.sender_id), "addop", f"+{uid} {shown}")

        async def _greet(new_uid):
            try:
                await bot.send_message(
                    new_uid,
                    "\U0001f389 \u4e3b\u4eba\u5df2\u7ed9\u4f60\u5f00\u901a\u4f7f\u7528\u6743\u9650\u3002\n"
                    "\u53d1 /start \u6253\u5f00\u63a7\u5236\u9762\u677f\uff1a\u7fa4\u53d1\u8fd0\u8425\u3001\u7fa4\u7ba1\u7406\u3001\u6570\u636e\u770b\u677f\u90fd\u80fd\u7528\u3002\n"
                    "\u8d26\u53f7\u6c60\u548c\u540d\u5355\u662f\u516c\u7528\u7684\uff0c\u540c\u4e00\u65f6\u523b\u53ea\u80fd\u8dd1\u4e00\u4e2a\u4efb\u52a1\uff0c\u6392\u5230\u961f\u8bf7\u7b49\u5f85\u3002")
            except Exception as e:
                log.info(f"\u63d0\u524d\u901a\u77e5\u65b0\u64cd\u4f5c\u5458\u5931\u8d25\uff08\u6b63\u5e38\uff0c\u4ed6\u81ea\u5df1\u53d1 /start \u5373\u53ef\uff09: {e}")

        asyncio.ensure_future(_greet(uid))
        await _reply(event,
            f"✅ 已授权：{shown}（id={uid}）"
            + ("" if persisted else "\n⚠️ 写数据库失败，本次授权只在内存生效，重启后会丢，请稍后重试。") + "\n\n"
            f"让他直接私聊本 Bot 发 /start 即可使用。\n"
            f"他能做：几乎全部——群发、群管理、账号登录/新增、设置、举报、删群。\n"
            f"他唯一不能做：增删操作员（/addop /dropop /oplist 你独占）。\n"
            f"当前操作员共 {len(OPERATORS)} 人，查看 /oplist")

    @bot.on(events.NewMessage(pattern=r"^/dropop\s+(\d+)$"))
    async def on_dropop(event):
        if not is_owner(event.sender_id):
            await _reply(event, "⛔ 只有主人能增删操作员。")
            return
        uid = int(event.pattern_match.group(1))
        db_drop_operator(uid)
        drop_operator(uid)
        refresh_operators(db_load_operators() or {})
        db_log_op(event.sender_id, actor_name(event.sender_id), "dropop", f"-{uid}")
        await _reply(event, f"✅ 已停用 id={uid} 的操作员权限。当前 {len(OPERATORS)} 人。")

    @bot.on(events.NewMessage(pattern="^/oplist$"))
    async def on_oplist(event):
        if not is_owner(event.sender_id):
            await _reply(event, "⛔ 只有主人能看操作员名单。")
            return
        lines = [f"👥 操作员白名单（{len(OPERATORS)} 人）"]
        for uid, name in sorted(OPERATORS.items(), key=lambda kv: kv[1]):
            lines.append(f"  • {name} — {uid}")
        lines.append("—")
        lines.append("/addop <user_id> [备注名]  新增")
        lines.append("/dropop <user_id>          停用")
        lines.append("（user_id 可让对方发 /whoami 查看）")
        lines.append("（停用后其历史审计记录保留，不会删）")
        await _reply(event, "\n".join(lines))

    @bot.on(events.NewMessage(pattern="^/whoami$"))
    async def on_whoami(event):
        uid = event.sender_id
        role = "主人" if is_owner(uid) else ("操作员" if is_authorized(uid) else "未授权")
        await _reply(event, f"你的 user_id: {uid}\n状态: {role}")

    @bot.on(events.NewMessage(pattern=r"^/oplog(?:\s+(\d+))?$"))
    async def on_oplog(event):
        """最近操作审计（主人看全量，操作员只看自己）。"""
        if not is_authorized(event.sender_id):
            return
        try:
            n = int(event.pattern_match.group(1) or 15)
        except (TypeError, ValueError):
            n = 15
        n = max(1, min(n, 50))
        only_self = False  # \u5168\u5f00\uff1a\u64cd\u4f5c\u5458\u4e5f\u53ef\u770b\u5168\u91cf
        uid = event.sender_id
        rows = []
        if only_self:
            import time as _t
            rows = [(OPERATORS.get(uid, "我"), uid, a, d, t) for a, d, t in
                    db_op_log_since(uid, _t.time() - 7 * 86400)][:n]
        else:
            rows = db_op_log_recent(n)
        if not rows:
            await _reply(event, "暂无操作记录。")
            return
        lines = ["📜 操作审计（近 %d 条）" % n if not only_self else "📜 我的操作记录（近 7 天）"]
        for r in rows:
            name, r_uid, action, detail, ts = r
            t = ts.strftime("%m-%d %H:%M") if hasattr(ts, "strftime") else ""
            lines.append(f"  {t} {name}({r_uid}) {action} {(detail or '')[:60]}".rstrip())
        await _reply(event, "\n".join(lines))

    # ---------- 兼容旧命令 ----------
    @bot.on(events.NewMessage(pattern="^/stats$"))
    async def on_stats(event):
        if not is_authorized(event.sender_id):
            return
        lines = [f"📊 名单: {db_count_targets()} | 已发(去重): {db_sent_global()} | 文案池: {db_count_pool()}"]
        for acc_no, client, _ph in accounts:
            s = db_load_stats(acc_no)
            lines.append(f"• [{acc_no}] 今日{s['sent_today']} 累计{s['total_sent']}")
        await _reply(event, "\n".join(lines))

    @bot.on(events.NewMessage(pattern=r"^/login(?:\s+(\d+))?$"))
    async def on_login(event):
        if not is_authorized(event.sender_id):
            return
        if _no_accounts(event):
            return
        target = event.pattern_match.group(1)
        targets = [int(target)] if target else [a for a, _c, _p in accounts]
        await _reply(event, f"🔄 开始登录账号 {targets}，验证码发到这里，直接回复数字…",
                     buttons=_main_kb(event))
        asyncio.ensure_future(_login_accounts(bot, accounts, targets, event.chat_id, event.sender_id))

    @bot.on(events.NewMessage(pattern="^/mygroups$"))
    async def on_mygroups(event):
        if not is_authorized(event.sender_id) or _no_accounts(event):
            return
        await _reply(event, await list_my_groups(accounts[0][1]), buttons=_groups_kb(event))

    @bot.on(events.NewMessage(pattern="^/diag$"))
    async def on_diag(event):
        """诊断：对账 groups_info 表 与 各账号真实群列表，找出僵尸群记录。"""
        if not is_authorized(event.sender_id) or _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, _busy_tip(event))
            return
        _set_busy(event.sender_id)
        try:
            await _reply(event, "🔬 正在诊断：拉取各账号真实群列表并与表对账…")
            r = await diag_groups(accounts)
            # 过长时分条发送
            for i in range(0, len(r), 3500):
                await _reply(event, r[i:i + 3500])
        except Exception as e:
            await _reply(event, f"❌ 诊断失败: {e}")
        finally:
            _clear_busy()

    @bot.on(events.NewMessage(pattern=r"^/collect ([\s\S]+)$"))
    async def on_collect(event):
        if not is_authorized(event.sender_id) or _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, _busy_tip(event))
            return
        _audit(event, "collect", (event.pattern_match.group(1) or "").strip()[:60])
        _set_busy(event.sender_id)
        try:
            await _reply(event, "🔄 正在拉取成员…")
            r = await collect_members(accounts[0][1], event.pattern_match.group(1).strip())
            await _reply(event, r)
        finally:
            _clear_busy()

    @bot.on(events.NewMessage(pattern=r"^/collect_history([\s\S]*)$"))
    async def on_collect_history(event):
        if not is_authorized(event.sender_id) or _no_accounts(event):
            return
        arg = (event.pattern_match.group(1) or "").strip()
        if not arg:
            await _reply(event, "❌ 用法: /collect_history <频道> [数量]")
            return
        parts = arg.split()
        peer, limit = parts[0], int(parts[1]) if len(parts) > 1 else 50
        if state["busy"]:
            await _reply(event, _busy_tip(event))
            return
        _set_busy(event.sender_id)
        try:
            await _reply(event, f"🔄 正在采集「{peer}」最近 {limit} 条…")
            r = await collect_channel_history(accounts[0][1], peer, limit)
            await _reply(event, r)
        finally:
            _clear_busy()

    @bot.on(events.NewMessage(pattern=r"^/sendto ([\s\S]+)$"))
    async def on_sendto(event):
        if not is_authorized(event.sender_id) or _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, _busy_tip(event))
            return
        text = event.pattern_match.group(1).strip()
        _start_send_campaign(event, accounts, text)

    @bot.on(events.NewMessage(pattern=r"^/broadcast ([\s\S]+)$"))
    async def on_broadcast(event):
        if not is_authorized(event.sender_id) or _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, _busy_tip(event))
            return
        raw = event.pattern_match.group(1).strip().split(" ", 1)
        if len(raw) < 2:
            await _reply(event, "❌ 用法: /broadcast <群1,群2> <内容>")
            return
        _set_busy(event.sender_id)
        try:
            await _reply(event, f"🚀 正在广播到: {raw[0]}")
            r = await broadcast_to_groups(accounts[0][1], raw[0], raw[1], event.chat_id)
            await _reply(event, r, buttons=_main_kb(event))
        finally:
            _clear_busy()

    @bot.on(events.NewMessage(pattern=r"^/forward ([\s\S]+)$"))
    async def on_forward(event):
        if not is_authorized(event.sender_id) or _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, _busy_tip(event))
            return
        parts = event.pattern_match.group(1).split()
        if len(parts) < 2:
            await _reply(event, "❌ 用法: /forward <源频道> <目标群>")
            return
        _set_busy(event.sender_id)
        try:
            await _reply(event, "🔄 正在转发…")
            r = await forward_from_channel(accounts[0][1], parts[0], parts[1], event.chat_id)
            await _reply(event, r)
        finally:
            _clear_busy()

    # ---- 登录中：所有文本回复进登录队列 ----
    @bot.on(events.NewMessage())
    async def on_auth_reply(event):
        ls = config.LOGIN_STATE
        if not ls or not is_authorized(event.sender_id):
            return
        req = ls.get("requester")
        if req and event.sender_id != req:
            return
        text = (event.text or "").strip()
        if not text:
            return
        try:
            config.LOGIN_STATE["queue"].put_nowait(text)
        except Exception:
            pass

    def _show_menu_handler(name):
        """返回一个展示子菜单的 handler builder"""
        async def show(event, text, kb):
            pending_action.pop(event.sender_id, None)
            await _reply(event, text, buttons=kb())
        if name == "campaign":
            return lambda e: show(e, campaign_menu_text() + "\n\n" + campaign_text(e.sender_id), campaign_menu_kb)
        if name == "groups":
            return lambda e: show(e, _groups_text(event), groups_menu_kb)
        if name == "accounts":
            return lambda e: show(e, accounts_menu_text(), accounts_menu_kb)
        if name == "settings":
            return lambda e: show(e, settings_menu_text(), _settings_kb)
        return lambda e: _push_main_menu(e)

    MENU_SHOW = {
        "menu_campaign": campaign_menu_text,
        "menu_groups": groups_menu_text,
        "menu_accounts": accounts_menu_text,
        "menu_settings": settings_menu_text,
        "menu_report": report_menu_text,
    }
    MENU_KB = {
        "menu_campaign": campaign_menu_kb,
        "menu_groups": groups_menu_kb,
        "menu_accounts": accounts_menu_kb,
        "menu_settings": _settings_kb,
        "menu_report": report_menu_kb,
    }

    def _menu_text(event, action):
        if action == "menu_settings":
            _apply_settings_to_state()
            return settings_menu_text(state.get("recent_only_days"), state.get("allow_repeat"))
        base = MENU_SHOW[action]()
        if action == "menu_groups":
            base = groups_menu_text(_is_op(event))
        if action == "menu_campaign":
            base += "\n\n" + campaign_text(event.sender_id)
        return base

    def _menu_kb(event, action):
        """菜单键盘按角色适配（操作员看到的是精简版）。"""
        if action == "menu_groups":
            return groups_menu_kb(_is_op(event))
        if action == "menu_dashboard":
            return dashboard_menu_kb()
        return MENU_KB[action]()

    async def _handle_menu_action(event, action):
        """执行一个菜单动作（不含需要输入的 INPUT_ACTIONS）。"""
        cid = event.sender_id
        if not can_do(cid, action):
            await _reply(event, _owner_only_hint(event))
            return

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
            await _reply(event, _menu_text(event, action), buttons=_menu_kb(event, action))
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
            await _reply(event, await list_my_groups(accounts[0][1]), buttons=_groups_kb(event))
        elif action == "del_group_menu":
            groups = db_get_all_groups()
            if not groups:
                await _reply(event, "ℹ️ 表里没有任何群记录。", buttons=_groups_kb(event))
                return
            state.setdefault("del_group_map_by", {})[str(cid)] = {
                str(i): g for i, g in enumerate(groups, 1)}
            await _reply(event,
                "🗑 点选要删除的群（会先让在群里的账号退出该群，再删 Bot 记录）：",
                buttons=group_del_kb(groups))
        elif action == "del_confirm":
            pend = state.get("pending_del_group")
            if not pend:
                await _reply(event, "⚠️ 没有待确认的删除，请重新点「🗑 删除群」。", buttons=_groups_kb(event))
                return
            gid, title = pend
            state["pending_del_group"] = None
            if _no_accounts(event):
                return
            if state["busy"]:
                await _reply(event, _busy_tip(event), buttons=_groups_kb(event))
                return
            _set_busy(event.sender_id)
            try:
                await _reply(event, f"🚪 正在让账号退出「{title}」…")
                _ok_n, lines = await leave_group(accounts, gid)
                deleted = db_delete_group(gid)
            finally:
                _clear_busy()
            remain = db_get_all_groups()
            state.setdefault("del_group_map_by", {})[str(cid)] = {
                str(i): gg for i, gg in enumerate(remain, 1)}
            msg = (f"🗑 删除「{title}」(id={gid}) 完成：\n"
                   + "\n".join(lines)
                   + f"\n{'✅ Bot 记录已删除' if deleted else 'ℹ️ Bot 记录本就不存在'}"
                   + f"\n剩余 {len(remain)} 个群。")
            if not remain:
                msg += "\n表已清空，返回群管理。"
                await _reply(event, msg, buttons=_groups_kb(event))
            else:
                await _reply(event, msg, buttons=group_del_kb(remain))
        elif action == "del_cancel":
            state["pending_del_group"] = None
            groups = db_get_all_groups()
            state.setdefault("del_group_map_by", {})[str(cid)] = {
                str(i): g for i, g in enumerate(groups, 1)}
            await _reply(event, "↩️ 已取消删除。",
                         buttons=group_del_kb(groups) if groups else _groups_kb(event))
        elif action == "acc_list":
            await _acc_list(event, accounts)
        elif action == "acc_filter":
            await _run_filter(event, accounts)
        elif action == "acc_cd_reset":
            try:
                n = len(db_cooldowns())
                db_clear_cooldown()
                await _reply(event, f"🧊 已重置冷却记账：{n} 个冷却中的账号全部立即解锁。",
                             buttons=accounts_menu_kb())
            except Exception as e:
                log.warning(f"重置冷却失败: {e}")
                await _reply(event, f"❌ 重置失败：{str(e)[:80]}", buttons=accounts_menu_kb())
        elif action == "profile_menu":
            await _reply(event, profile_menu_text(), buttons=profile_menu_kb())
        elif action == "profile_random":
            await _run_editprofile(event, accounts, random_mode=True)
        elif action == "back_accounts":
            await _reply(event, accounts_menu_text(), buttons=accounts_menu_kb())
        elif action == "back_groups":
            await _reply(event, _groups_text(event), buttons=_groups_kb(event))
        elif action == "camp_status":
            await _reply(event, campaign_menu_text() + "\n\n" + campaign_text(event.sender_id), buttons=campaign_menu_kb())
        elif action == "camp_step1":
            await _campaign_pick_group(event, accounts)
        elif action == "camp_start":
            await _do_start(event, accounts)
        elif action == "pause" or action == "resume":
            b = state.get("busy_by") or {}
            if not state["busy"]:
                await _reply(event, "\u2139\ufe0f \u73b0\u5728\u6ca1\u6709\u5728\u8dd1\u7684\u4efb\u52a1\u3002")
                return
            who = b.get("name") or "\u672a\u77e5"
            if action == "pause":
                state["paused"] = True
                _audit(event, "pause", "\u6682\u505c\u4e86 " + who + " \u7684\u4efb\u52a1")
                await _reply(event, "\u23f8 \u5df2\u6682\u505c\uff08\u70b9\u300c\u7ee7\u7eed\u300d\u6062\u590d\uff09",
                             buttons=campaign_menu_kb())
            else:
                state["paused"] = False
                _audit(event, "resume", "\u7ee7\u7eed\u4e86 " + who + " \u7684\u4efb\u52a1")
                await _reply(event, "\u25b6\ufe0f \u5df2\u7ee7\u7eed", buttons=campaign_menu_kb())
        elif action == "stop":
            b = state.get("busy_by") or {}
            who = b.get("name") or "\u5f53\u524d\u4efb\u52a1"
            state["stop"] = True
            state["paused"] = True
            _clear_busy()
            release_list(b.get("uid"))
            _audit(event, "stop", "\u505c\u6389\u4e86 " + who + " \u7684\u4efb\u52a1")
            await _reply(event, "🛑 已停止当前任务", buttons=campaign_menu_kb())
        elif action == "set_recent_filter":
            _apply_settings_to_state()
            s = _load_settings()
            new = 0 if s.get("recent_only_days") else 7
            s["recent_only_days"] = new
            ops_set("settings", s)
            state["recent_only_days"] = new
            await _reply(event,
                ("✅ 已开启「近7天活跃」：拉取成员时只保留近 7 天内上线过的用户。"
                 if new else "❌ 已关闭「近7天活跃」：拉取全部有效成员（不看上线时间）。")
                + f"\n\n{settings_menu_text(new, s.get('allow_repeat'))}",
                buttons=_settings_kb())
        elif action == "set_repeat":
            _apply_settings_to_state()
            s = _load_settings()
            new = not s.get("allow_repeat", False)
            s["allow_repeat"] = new
            ops_set("settings", s)
            state["allow_repeat"] = new
            await _reply(event,
                ("✅ 已开启「重复推广」：同一账号可以再次推给已发过的用户（适合换文案重推）。"
                 if new else
                 "❌ 已关闭「重复推广」：每人只推一次，已发过的自动跳过。")
                + f"\n\n{settings_menu_text(s.get('recent_only_days'), new)}",
                buttons=_settings_kb())
        elif action == "back_home":
            await _push_main_menu(event)
        # ---- 举报中心 ----
        elif action == "rep_reason_menu":
            await _reply(event, "📋 请选择举报理由：", buttons=reason_menu_kb())
        elif action == "rep_status":
            await _reply(event, f"⏳ 账号冷却状态\n\n{cooldown_summary()}\n\n"
                                "（举报后账号进入30分钟冷却，防止触发风控）",
                                buttons=report_menu_kb())
        elif action == "back_report":
            await _reply(event, report_menu_text(), buttons=report_menu_kb())

    # ---------- 菜单动作统一分发（含 pending_input 处理） ----------
    @bot.on(events.NewMessage())
    async def on_any_text(event):
        if not is_authorized(event.sender_id):
            return
        text = (event.text or "").strip()
        if not text:
            return

        # 1. 底部按钮 → 执行动作
        if text in BTN_ACTION:
            action = BTN_ACTION[text]
            if action in OWNER_EXCLUSIVE and not is_owner(event.sender_id):
                await _reply(event, _owner_only_hint(event))
                return
            await _handle_menu_action(event, action)
            return

        # 1.5 举报理由按钮 → 进入「指定理由」目标输入
        if text in REASON_TEXT_TO_KEY:
            pending_action[event.sender_id] = ("rep_reason_target", REASON_TEXT_TO_KEY[text])
            await _reply(event, f"已选理由：{text}\n请发送要举报的用户/频道用户名或链接：",
                         buttons=report_menu_kb())
            return

        # 1.2 已保存群选群按钮 → 确认拉取
        m_pick = re.match(r"^📤 (\d+)·", text)
        if m_pick:
            pick_no = int(m_pick.group(1))
            await _campaign_group_chosen(event, accounts, pick_no)
            return

        # 1.3 删除群按钮 → 二次确认（退群+删记录）
        m_del = re.match(r"^🗑 (\d+)·", text)
        if m_del:
            g = ((state.get("del_group_map_by") or {}).get(
                str(event.sender_id)) or {}).get(m_del.group(1))
            if not g:
                await _reply(event, "⚠️ 列表已过期，请重新点「🗑 删除群」。", buttons=_groups_kb(event))
                return
            gid, title = g[0], g[1] or g[2] or str(g[0])
            state["pending_del_group"] = (gid, title)
            await _reply(event,
                f"⚠️ 确认删除群「{title}」(id={gid})？\n\n"
                "将执行：\n"
                "  1) 让所有在该群的账号退出该群\n"
                "  2) 删除 Bot 表里的群记录\n\n"
                "退群不可逆（需重新加群才能回来）。点「⚠️ 确认退群并删除」执行，点「↩️ 取消」返回。",
                buttons=group_del_confirm_kb())
            return

        # 2. 数字快捷选群（从「我的群」返回的群序号，预留）
        # 3. 有 pending 输入 → 消费
        action = pending_action.get(event.sender_id)
        if action:
            del pending_action[event.sender_id]
            await _consume_input(event, action, text)
            return

        # 4. 登录流程进行中 → 跳过，让 on_auth_reply 独享
        if config.LOGIN_STATE:
            return

        # 5. 未登录状态下多余输入提示
        if not accounts:
            await _reply(event, "尚未登录账号，请点「账号管理」→「添加账号」。", buttons=_main_kb(event))
            return

        # 5. 自动识别群链接 → 加入并拉人
        if text.startswith("/"):
            return
        m = re.search(r"(?:https?://)?t\.me/(?:joinchat/|\+)?[A-Za-z0-9_\-]+", text)
        if m and not state["busy"] and accounts:
            await _auto_addgroup(event, accounts, m.group(0))

    # ---------- 内联键盘回调（消息附带按钮：系统设置 / 选群） ----------
    @bot.on(events.CallbackQuery)
    async def on_callback(event):
        if not is_authorized(event.sender_id):
            await event.answer("⛔ 无权限", alert=True)
            return
        data = (event.data or b"").decode("utf-8", "replace")
        try:
            if data.startswith("st:"):
                if not is_authorized(event.sender_id):
                    await event.answer("\u26d4 \u65e0\u6743\u9650", alert=True)
                    return
                await _cb_settings(event, data[3:])
            elif data.startswith("db:"):
                await _cb_dashboard(event, data[3:])
            elif data.startswith("gp:"):
                await _cb_grouppick(event, data[3:])
            else:
                await event.answer()
        except Exception as e:
            log.warning(f"[回调] {data!r} 处理失败: {e}", exc_info=True)
            try:
                await event.answer(f"❌ {type(e).__name__}", alert=True)
            except Exception:
                pass

    async def _cb_settings(event, rest):
        """设置内联按钮：st:recent / st:repeat / st:parse / st:speed:+5 / st:quota:-10 / st:home"""
        if rest == "home":
            await event.answer()
            await _push_main_menu(event)
            return
        s = _load_settings()
        tip = ""
        if rest == "recent":
            new = 0 if s.get("recent_only_days") else 7
            s["recent_only_days"] = new
            tip = "已开启：只拉近7天活跃成员" if new else "已关闭：拉全部有效成员"
        elif rest == "repeat":
            new = not s.get("allow_repeat", False)
            s["allow_repeat"] = new
            tip = "已开启：同一人可重复推" if new else "已关闭：每人只推一次"
        elif rest == "parse":
            order = [None, "html", "md"]
            cur = s.get("parse_mode") if s.get("parse_mode") in order else None
            nxt = order[(order.index(cur) + 1) % 3]
            s["parse_mode"] = nxt
            tip = f"文本模式：{PARSE_LABEL[nxt]}"
        elif rest.startswith("speed:") or rest.startswith("quota:"):
            kind, op = rest.split(":", 1)
            if op == "show":
                cur = s.get("min_delay") if kind == "speed" else s.get("daily_limit")
                await event.answer(f"当前：{cur}" + ("s" if kind == "speed" else " 条/日"))
                return
            try:
                delta = int(op)
            except ValueError:
                await event.answer("无效步进", alert=True)
                return
            if kind == "speed":
                s["min_delay"] = max(1, min(int(s.get("min_delay", 5)) + delta, 600))
                s["max_delay"] = s["min_delay"] + 10
                tip = f"发送间隔：{s['min_delay']}s（随机 +10s）"
            else:
                s["daily_limit"] = max(1, min(int(s.get("daily_limit", 50)) + delta, 100000))
                tip = f"每日上限：{s['daily_limit']} 条/账号"
        else:
            await event.answer()
            return
        ops_set("settings", s)
        _apply_settings_to_state()
        await event.edit(settings_menu_text(state.get("recent_only_days"),
                                            state.get("allow_repeat")),
                         buttons=_settings_kb())
        await event.answer(tip)

    async def _cb_grouppick(event, arg):
        """选群内联按钮：gp:<序号> / gp:back"""
        if arg == "back":
            await event.answer()
            await _reply(event, campaign_menu_text() + "\n\n" + campaign_text(event.sender_id),
                         buttons=campaign_menu_kb())
            return
        await event.answer("正在拉取成员…")
        try:
            n = int(arg)
        except ValueError:
            return
        await _campaign_group_chosen(event, accounts, n)

    def _menu_kb_for_action(action):
        if action in ("camp_step3",):  # 文案输入时保留群发菜单
            return campaign_menu_kb
        if action == "add_group_prompt" or action == "batch_import_prompt":
            return groups_menu_kb
        if action == "acc_add_prompt":
            return accounts_menu_kb
        if action == "acc_edit_profile_prompt":
            return accounts_menu_kb
        return None

    # ---------- 消费一个 pending 输入 ----------
    async def _consume_input(event, action, text):
        if not can_do(event.sender_id, action):
            await _reply(event, _owner_only_hint(event))
            return
        # 指定理由举报：pending 是元组 ("rep_reason_target", reason_key)
        if isinstance(action, tuple) and action and action[0] == "rep_reason_target":
            reason_key = action[1]
            await _start_report(event, accounts, mode="custom",
                               target=text, reason_key=reason_key)
            return
        if action == "rep_user_prompt":
            await _start_report(event, accounts, mode="user", target=text)
            return
        if action == "rep_channel_ai_prompt":
            await _start_report(event, accounts, mode="super", target=text)
            return
        if action == "camp_step3":
            touch_list(event.sender_id)
            set_campaign(event.sender_id, text=text)
            _audit(event, "save_text", f"{len(text)} \u5b57")
            # 自动检查账号状态
            ready = await _check_accounts_ready(event, accounts)
            if not ready:
                await _reply(event,
                    "❌ 没有可用账号，无法群发。请先去「👥 账号管理」处理。",
                    buttons=campaign_menu_kb())
                return
            # HTML 自动检测（仅当文本模式为自动/纯文本时）
            pm = state.get("parse_mode")
            import re as _re
            html_like = bool(_re.search(r"</?(?:b|i|u|s|code|pre|a|br)\b[^>]*>", text))
            note = ""
            if html_like and pm in (None, "html"):
                note = "\n已检测到 HTML 标签，将按 HTML 格式发送。"
            body = (campaign_menu_text() + "\n\n✅ 文案已保存：\n"
                    + text[:100] + ("…" if len(text) > 100 else "")
                    + note + "\n\n" + campaign_text(event.sender_id)
                    + f"\n\n✅ {len(ready)} 个账号就绪，名单 {db_count_targets()} 人。\n点「③ ✅ 确认开跑」开始群发。")
            await _reply(event, body, buttons=campaign_menu_kb())
        elif action == "add_group_prompt":
            await _finish_addgroup(event, accounts, text)
        elif action == "batch_import_prompt":
            await _finish_batchimport(event, accounts, text)
        elif action == "acc_add_prompt":
            _audit(event, "acc_add", text)
            await _reply(event, f"🔄 开始添加账号 {text} …", buttons=accounts_menu_kb())
            await _add_account_interactive(bot, text, event.chat_id, event.sender_id)
        elif action == "acc_edit_profile_prompt":
            await _run_editprofile(event, accounts, text)
        elif action == "set_speed_prompt":
            try:
                sec = max(1, int(text))
                s = _load_settings()
                s["min_delay"], s["max_delay"] = sec, sec + 10
                ops_set("settings", s)
                state["min_delay"], state["max_delay"] = sec, sec + 10
                await _reply(event, f"⚡ 间隔已设为 {sec}-{sec+10}s", buttons=_settings_kb())
            except ValueError:
                await _reply(event, "❌ 请输入数字秒数", buttons=_settings_kb())
        elif action == "set_quota_prompt":
            try:
                q = max(1, int(text))
                s = _load_settings()
                s["daily_limit"] = q
                ops_set("settings", s)
                state["daily_limit"] = q
                await _reply(event, f"🎯 每账号每日上限已设为 {q} 条", buttons=_settings_kb())
            except ValueError:
                await _reply(event, "❌ 请输入数字条数", buttons=_settings_kb())
        elif action == "set_parallel_prompt":
            await _reply(event, "⏩ 并行账号数由系统按可用账号自动分配，无需手动设置。\n"
                                "当前可用账号越多，自动分配越快。", buttons=_settings_kb())
        elif action == "set_parsemode":
            order = [None, "html", "md"]
            label = {None: "纯文本", "html": "HTML", "md": "Markdown"}
            cur = state.get("parse_mode") or None
            try:
                nxt = order[(order.index(cur) + 1) % len(order)]
            except ValueError:
                nxt = None
            s = _load_settings()
            s["parse_mode"] = nxt
            ops_set("settings", s)
            state["parse_mode"] = nxt
            tip = {
                None: "原样发送，不做任何格式解析",
                "html": '支持 <b>加粗</b> <i>斜体</i> <a href="https://t.me">链接</a> <code>代码</code>',
                "md": "支持 **加粗** *斜体* [链接](https://t.me) `代码`",
            }
            await _reply(event,
                f"✍️ 文本模式已切换为：{label[nxt]}\n\n用法：{tip[nxt]}\n\n"
                "（再点一次「文本模式」继续切换：纯文本 → HTML → Markdown）",
                buttons=_settings_kb())

    # ---------- 数据看板 ----------
    DASH_MODE = {}  # {chat_id: bool} False=每人统计 True=账号明细

    async def _dashboard(event, show_accounts=False, edit=False):
        sent = db_sent_global()
        lines = [
            "\U0001f4ca 数据看板",
            f"• 已发(去重): {sent} 人",
            f"• 文案池: {db_count_pool()} 条",
            f"• 已加群: {db_group_count()} 个",
            f"• 在线账号: {len(accounts)} 个",
            "—",
        ]
        if show_accounts:
            lines.append("各账号明细（今日/累计）：")
            for acc_no, client, _ph in accounts:
                s = db_load_stats(acc_no)
                lines.append(f"  [{acc_no}] 今日 {s['sent_today']} | 累计 {s['total_sent']}")
        else:
            lines.append("每个人的操作（近 7 天：任务数 | 发出）：")
            rows = db_campaign_leaderboard(7)
            if rows:
                for r_uid, r_name, n_task, n_sent, n_target in rows:
                    who = (r_name or actor_name(r_uid)) + ("（主人）" if r_uid == OWNER_ID else "")
                    lines.append(f"  • {who}: {n_task} 次 | {n_sent} 发出")
            else:
                lines.append("  （近 7 天还没有群发记录）")
        cl = get_list_claim()
        if cl:
            lines.append(f"\U0001f512 名单占用：{cl.get('name')}（{cl.get('group') or '-'}）")
        if is_authorized(event.sender_id):
            lines.append(f"\U0001f465 操作员 {len(OPERATORS)} 人（/oplist）；审计：/oplog")
        text = "\n".join(lines)
        kb = dashboard_inline_kb(show_accounts)
        if edit:
            await event.edit(text, buttons=kb)
        else:
            DASH_MODE[event.chat_id] = show_accounts
            await _reply(event, text, buttons=kb)

    async def _cb_dashboard(event, rest):
        """看板内联按钮：db:acc 账号明细 / db:who 每人统计 / db:ref 刷新 / db:home 返回"""
        chat = event.chat_id
        mode = DASH_MODE.get(chat, False)
        if rest == "home":
            await event.answer()
            await _push_main_menu(event)
            return
        if rest == "acc":
            mode = True
        elif rest == "who":
            mode = False
        elif rest == "ref":
            pass  # 保持当前模式重新渲染
        DASH_MODE[chat] = mode
        await _dashboard(event, show_accounts=mode, edit=True)
        await event.answer()

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

    # ---------- 群发运营（新版：选群→文案→自动检查→确认开跑） ----------
    async def _campaign_pick_group(event, accounts):
        """① 选群入口：列出已保存的群（groups_info）供按钮选择，无需手动输入。"""
        if _no_accounts(event):
            return
        groups = db_get_all_groups()
        if not groups:
            await _reply(event,
                "❌ 还没有已保存的群。\n"
                "先去「📥 群管理」加群（加群会自动保存群信息），再回来选群。",
                buttons=campaign_menu_kb())
            return
        if len(groups) > 30:
            groups = groups[:30]
        # 序号→群 映射存 state，点击后回查
        group_map = {str(i): g for i, g in enumerate(groups, 1)}
        state.setdefault("group_pick_map_by", {})[str(event.sender_id)] = group_map
        await _reply(event,
            f"📤 请选择要拉取的群（共 {len(groups)} 个，点下方按钮）：",
            buttons=group_pick_inline_kb(groups))

    async def _campaign_group_chosen(event, accounts, pick_no):
        """选完群：记录运营群 → 清空旧名单 → 自动拉成员 → 提示写文案。"""
        group_map = (state.get("group_pick_map_by") or {}).get(str(event.sender_id)) or {}
        g = group_map.get(str(pick_no))
        if not g:
            await _reply(event, "⚠️ 该序号无效，请重新点「① 选群」。", buttons=campaign_menu_kb())
            return
        gid, title, username, _mc, _creator = g
        target = username or str(gid)
        if state["busy"]:
            await _reply(event, _busy_tip(event))
            return
        uid = event.sender_id
        ok, holder = claim_list(uid, actor_name(uid), title or target)
        if not ok:
            await _reply(event,
                f"⛔ 名单正被 {holder.get('name') or '其他人'} 占用"
                f"（{holder.get('group') or '未选群'}）。\n"
                "名单全库只有一份，重新选群会清掉对方的名单，所以只能排队。\n"
                "等对方跑完，或 30 分钟后自动解锁；紧急情况找主人点「🛑 停止任务」。",
                buttons=campaign_menu_kb())
            return
        _set_busy(uid)
        try:
            cleared = db_clear_targets()
            await _reply(event,
                f"✅ 已选群「{title or target}」\n"
                f"🧹 已清空旧名单（{cleared} 人）\n"
                f"🔄 正在拉取成员到名单…")
            r = None
            for acc_no, client, _ph in accounts:
                r = await collect_members(client, target,
                                         recent_only_days=state.get("recent_only_days", 0))
                if not r.startswith("❌"):
                    break
                # 该账号找不到群，换下一个账号
            if r is None:
                r = "❌ 没有可用账号，无法拉取"
            await _reply(event, r)
            if r.startswith("❌"):
                release_list(uid)
                return  # 拉取失败，不进入文案环节
            set_campaign(uid, group=target, group_title=title or target,
                         target_count=db_count_targets())
            _audit(event, "pick_group",
                   f"{title or target} → {db_count_targets()} 人")
            await _reply(event,
                "📝 群已选好，现在直接发送文案（支持 HTML：如 <b>加粗</b> <a href=\"https://t.me\">链接</a>）。\n"
                "发完会自动检查账号，然后提示确认开跑。",
                buttons=campaign_menu_kb())
            # 进入文案输入等待
            pending_action[uid] = "camp_step3"
        except Exception as e:
            await _reply(event, f"❌ 拉取成员失败：{e}", buttons=campaign_menu_kb())
        finally:
            _clear_busy()

    async def _check_accounts_ready(event, accounts):
        """自动检查账号状态：逐个检测连接+可用性，返回就绪账号列表（并实时报告）。"""
        ready = []
        lines = ["🔍 自动检查账号状态："]
        for acc_no, client, _ph in accounts:
            try:
                await client.connect()
                me = await client.get_me()
                try:
                    await client.get_dialogs(limit=1)
                    st = "✅ 可用"
                    ready.append((acc_no, client, _ph))
                except Exception:
                    st = "⏳ 受限"
                lines.append(f"  [账号{acc_no}] {st}")
            except Exception as e:
                lines.append(f"  [账号{acc_no}] ❌ 冻结（{str(e)[:30]}）")
        await _reply(event, "\n".join(lines))
        return ready

    async def _do_start(event, accounts):
        """③ 确认开跑：必须群+名单+文案齐全才放行。"""
        if _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, _busy_tip(event))
            return
        camp = get_campaign(event.sender_id)
        if not camp.get("group"):
            await _reply(event, "❌ 还没选群。请先点「① 选群」。", buttons=campaign_menu_kb())
            return
        text = camp.get("text")
        if not text:
            await _reply(event, "❌ 还没写文案。请点「② 写文案」后直接发文案。", buttons=campaign_menu_kb())
            return
        targets = db_load_targets()
        if not targets:
            await _reply(event, "❌ 名单为空（可能该群没有可拉的有效成员）。请重新点「① 选群」拉一次。", buttons=campaign_menu_kb())
            return
        _start_send_campaign(event, accounts, text)

    def _start_send_campaign(event, accounts, text):
        """真正启动多账号群发。"""
        targets = db_load_targets()
        if not targets:
            asyncio.ensure_future(_reply(event, "❌ 名单为空，请先拉取名单。"))
            return
        uid = event.sender_id
        camp = get_campaign(uid)
        sent_before = db_sent_global()
        _set_busy(uid)
        state["paused"] = False
        state["stop"] = False
        touch_list(uid)
        cid = db_campaign_start(uid, actor_name(uid), camp.get("group", ""),
                                camp.get("group_title", ""), len(targets), text)
        _audit(event, "start_send",
               f"{camp.get('group_title') or camp.get('group') or '-'} \u76ee\u6807 {len(targets)} \u4eba")
        set_campaign(uid, started=int(time.time()), campaign_id=cid)
        asyncio.ensure_future(
            _run_and_finish(event, accounts, text, targets, sent_before))

    async def _run_and_finish(event, accounts, text, targets, sent_before=0):
        uid = event.sender_id
        crashed = None
        try:
            await _reply(event,
                f"🚀 开始群发：{len(accounts)}个账号 | 目标 {len(targets)} 人 | "
                f"间隔 {state['min_delay']}-{state['max_delay']}s\n"
                "每 15 秒自动汇报进度。可用「暂停/继续/停止」控制。")
            try:
                result = await send_to_list_multi(accounts, targets, text, event.chat_id, bot=bot)
            except Exception as e:
                crashed = e
                result = (f"❌ 群发异常中断：{type(e).__name__}: {str(e)[:200]}\n"
                          "号池与名单已释放，草稿保留，可以直接重新开跑。")
            await _reply(event, result, buttons=_main_kb(event))
            done_n = max(0, db_sent_global() - sent_before)
            cid = (get_campaign(uid) or {}).get("campaign_id")
            if cid:
                db_campaign_finish(cid, done_n, "crashed" if crashed else "done")
            _audit(event, "finish_send",
                   f"发出 {done_n} / 目标 {len(targets)}")
            if not crashed:
                # 发完清除一次性运营状态
                clear_campaign(uid)
        finally:
            release_list(uid)
            _clear_busy()
            state["paused"] = False

    # ---------- 举报中心：统一入口 ----------
    async def _start_report(event, accounts, mode, target, reason_key=None):
        """启动一次举报任务（user/custom/super）。"""
        if not accounts:
            await _reply(event, "⚠️ 当前没有可用账号，请先添加账号。", buttons=report_menu_kb())
            return
        if state["busy"]:
            await _reply(event, _busy_tip(event), buttons=report_menu_kb())
            return
        _set_busy(event.sender_id)
        try:
            await _reply(event, f"🔍 准备举报目标：{target}\n模式：{mode}\n正在分配可用账号...",
                         buttons=report_menu_kb())
            ai_cfg = load_ai_config()
            if mode == "user":
                res = await report_user(target, status_cb=lambda m: _reply(event, m, buttons=report_menu_kb()))
            elif mode == "custom":
                res = await report_custom(target, reason_key,
                                         status_cb=lambda m: _reply(event, m, buttons=report_menu_kb()))
            elif mode == "super":
                status_note = "（已启用 AI 自动生成理由）" if ai_cfg else "（未配置 AI，使用兜底理由）"
                await _reply(event, f"🤖 AI 批量举报 {status_note}", buttons=report_menu_kb())
                res = await report_super(target, ai_cfg=ai_cfg,
                                        status_cb=lambda m: _reply(event, m, buttons=report_menu_kb()))
            else:
                res = None
            if res:
                body = (
                    f"✅ 举报完成！\n\n"
                    f"📌 目标: {res.get('target', target)}\n"
                    + (f"原因: {res.get('reason_name', '')}\n" if res.get('reason_name') else "")
                    + f"👤 账号: {res['total']} | ✅ {res['success']} | ⚠️ {res['partial']} | ❌ {res['fail']}\n"
                    f"⏳ 状态: {res['summary']}\n"
                    + (f"\n🎯 策略: {res['strategy']}\n" if res.get('strategy') else "")
                    + (f"\n📊 原因分布：\n{res['stats_text']}\n" if res.get('stats_text') else "")
                    + (f"\n📋 详情：\n{res['text']}" if res.get('text') else "")
                )
                await _reply(event, body, buttons=report_menu_kb())
            else:
                await _reply(event, "❌ 举报未执行（无可用账号或目标解析失败）。", buttons=report_menu_kb())
        finally:
            _clear_busy()

    # ---------- 过滤 & 改资料 ----------
    async def _run_filter(event, accounts):
        """账号过滤：检测 Bot 已登录的推送账号状态（不是收集来的用户名单）。"""
        if not accounts:
            await _reply(event, "⚠️ 当前没有已登录的推送账号。\n先点「添加账号」登录。",
                         buttons=accounts_menu_kb())
            return
        await _reply(event, "🔄 正在逐个检测推送账号（连接/会话/限流）…")
        r = await check_login_accounts(accounts)
        await _reply(event, r, buttons=accounts_menu_kb())

    async def _run_editprofile(event, accounts, name=None, random_mode=False):
        if _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, _busy_tip(event))
            return
        _set_busy(event.sender_id)
        try:
            if random_mode:
                await _reply(event,
                    "🎲 开始一键养号：随机英文姓名 + 随机真实风景头像 + 补随机用户名…\n"
                    "每账号间隔 2 秒，稍等。")
                r = await edit_all_profiles(event.chat_id, random_names=True,
                                            random_avatar=True, username_mode="random")
                await _reply(event, r, buttons=accounts_menu_kb())
                return
            # 「跳过」= 不改名字，只处随机用户名
            if name and name.strip() in ("跳过", "skip", "-"):
                name = None
            uname = (name or "").strip()
            await _reply(
                event,
                "🔄 正在批量修改账号资料…\n"
                f"  统一名字：{uname or '(不改)'}\n"
                "  用户名：无用户名的账号自动随机生成（已有的不动）",
            )
            r = await edit_all_profiles(event.chat_id, name=name or None, username_mode="random")
            await _reply(event, r, buttons=accounts_menu_kb())
        finally:
            _clear_busy()

    # ---------- 加群 & 批量导入 & 自动识别 ----------
    async def _finish_addgroup(event, accounts, text):
        _audit(event, "add_group", (text or "")[:80])
        if _no_accounts(event):
            return
        if state["busy"]:
            await _reply(event, _busy_tip(event))
            return
        await _reply(event, f"🔄 正在加入 {text} …")
        _set_busy(event.sender_id)
        try:
            r, ent, used = await join_group_all_accounts(accounts, text)
            await _reply(event, r)
            if not r.startswith("✅"):
                if r.startswith("⏳"):
                    await _reply(event, "群主批准后再点一次「加群」发同一链接，即可入表+拉名单。", buttons=_groups_kb(event))
                return
            await _reply(event, "正在读取群成员到名单…")
            # 用加群返回的实体拉人：邀请链接是一次性凭证，拿原链接再解会报 expired
            target = ent if ent is not None else text
            r2 = await collect_members(used or accounts[0][1], target)
            await _reply(event, r2, buttons=_groups_kb(event))
        finally:
            _clear_busy()

    async def _finish_batchimport(event, accounts, text):
        _audit(event, "batch_import",
               f"{len([l for l in text.splitlines() if l.strip()])} \u4e2a\u94fe\u63a5")
        links = [ln for ln in text.splitlines() if ln.strip()]
        if not links:
            await _reply(event, "没有识别到链接。", buttons=_groups_kb(event))
            return
        if _no_accounts(event):
            return
        _set_busy(event.sender_id)
        try:
            for i, ln in enumerate(links, 1):
                m = re.search(r"(?:https?://)?t\.me/(?:joinchat/|\+)?[A-Za-z0-9_\-]+", ln.strip())
                if not m:
                    await _reply(event, f"[{i}] ⚠️ 跳过（非链接）: {ln.strip()[:40]}")
                    continue
                link = m.group(0)
                try:
                    r1, ent, used = await join_group_all_accounts(accounts, link)
                except Exception:
                    r1, ent, used = "加群失败", None, None
                try:
                    r2 = await collect_members(used or accounts[0][1], ent if ent is not None else link,
                                           recent_only_days=state.get("recent_only_days", 0))
                except Exception:
                    r2 = "拉人失败"
                await _reply(event, f"[{i}/{len(links)}] {link}\n{r1}\n{r2}")
            await _reply(event, f"✅ 批量导入完成，共处理 {len(links)} 个链接。", buttons=_groups_kb(event))
        finally:
            _clear_busy()

    async def _auto_addgroup(event, accounts, link):
        if state["busy"]:
            await _reply(event, _busy_tip(event))
            return
        uid = event.sender_id
        ok, holder = claim_list(uid, actor_name(uid), link)
        if not ok:
            await _reply(event,
                f"⛔ 名单正被 {holder.get('name') or '其他人'} 占用"
                f"（{holder.get('group') or '-'}），重新拉人会清掉对方的名单。\n"
                "请等对方跑完，或到「群发运营 → ① 选群」里排队。")
            return
        _audit(event, "auto_addgroup", link[:80])
        await _reply(event, "检测到群链接，正在加入并读取成员…")
        _set_busy(uid)
        try:
            r, ent, used = await join_group_all_accounts(accounts, link)
            await _reply(event, r)
            r2 = await collect_members(used or accounts[0][1], ent if ent is not None else link,
                                     recent_only_days=state.get("recent_only_days", 0))
            await _reply(event, r2, buttons=_main_kb(event))
        finally:
            _clear_busy()

    # 初始化：把持久化设置应用到 state
    _apply_settings_to_state()
