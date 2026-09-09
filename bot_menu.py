#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bot 菜单键盘体系：所有按钮定义 + 键盘构造 + 菜单文本，集中管理。

bot.py 只负责 handler 逻辑，不混入按钮文字和键盘布局。
"""
from telethon import Button
from telethon.tl.types import KeyboardButton, KeyboardButtonRow, ReplyKeyboardMarkup


# =====================================================================
#  按钮文字
# =====================================================================
BTN = {
    # 主菜单
    "campaign": "🚀 群发运营",
    "groups": "📥 群管理",
    "accounts": "👥 账号管理",
    "dashboard": "📊 数据看板",
    "settings": "⚙️ 系统设置",
    "report": "🚨 举报",
    # 群发运营子菜单（新版 3 步：选群→文案→确认开跑）
    "camp_step1": "① 选群",
    "camp_step3": "② 写文案",
    "camp_start": "③ ✅ 确认开跑",
    "camp_status": "📋 查看进度",
    # 群管理子菜单
    "my_groups": "我的群",
    "add_group": "加群",
    "batch_import": "批量导入",
    "del_group": "🗑 删除群",
    "del_confirm": "⚠️ 确认退群并删除",
    "del_cancel": "↩️ 取消",
    # 账号管理子菜单
    "acc_list": "账号列表",
    "acc_add": "添加账号",
    "acc_edit_profile": "批量改资料",
    "acc_filter": "账号过滤",
    "acc_cd_reset": "🧊 重置冷却",
    # 改资料子菜单
    "profile_random": "🎲 随机小名(蔬果)",
    "profile_name": "✏️ 统一名字(手动输入)",
    # 数据看板
    "dashboard_view": "📊 查看数据",
    # 设置子菜单
    "set_speed": "发送间隔",
    "set_quota": "每日上限",
    "set_parallel": "并行账号数",
    "set_parsemode": "文本模式",
    "set_recent": "近7天活跃",
    "set_repeat": "重复推广",
    # 通用
    "back": "🔙 返回主菜单",
    "back_campaign": "🔙 返回群发运营",
    "back_groups": "🔙 返回群管理",
    "back_accounts": "🔙 返回账号管理",
    "back_settings": "🔙 返回系统设置",
    "pause": "⏸ 暂停",
    "resume": "▶️ 继续",
    "stop": "🛑 停止任务",
    "refresh": "🔄 刷新",
    # 举报子菜单
    "rep_user": "👤 举报用户/频道",
    "rep_channel_ai": "🤖 AI 批量举报",
    "rep_reason": "📋 指定理由举报",
    "rep_status": "⏳ 账号冷却状态",
    "back_report": "🔙 返回举报菜单",
}

# ---- 所有按钮 → 动作标识 ----
BTN_ACTION = {
    BTN["campaign"]: "menu_campaign",
    BTN["groups"]: "menu_groups",
    BTN["accounts"]: "menu_accounts",
    BTN["dashboard"]: "menu_dashboard",
    BTN["settings"]: "menu_settings",
    BTN["report"]: "menu_report",
    BTN["camp_step1"]: "camp_step1",
    BTN["camp_step3"]: "camp_step3",
    BTN["camp_start"]: "camp_start",
    BTN["camp_status"]: "camp_status",
    BTN["my_groups"]: "my_groups",
    BTN["add_group"]: "add_group_prompt",
    BTN["batch_import"]: "batch_import_prompt",
    BTN["del_group"]: "del_group_menu",
    BTN["del_confirm"]: "del_confirm",
    BTN["del_cancel"]: "del_cancel",
    BTN["acc_list"]: "acc_list",
    BTN["acc_add"]: "acc_add_prompt",
    BTN["acc_edit_profile"]: "profile_menu",
    BTN["acc_filter"]: "acc_filter",
    BTN["acc_cd_reset"]: "acc_cd_reset",
    BTN["profile_random"]: "profile_random",
    BTN["profile_name"]: "acc_edit_profile_prompt",
    BTN["dashboard_view"]: "dashboard_view",
    BTN["set_speed"]: "set_speed_prompt",
    BTN["set_quota"]: "set_quota_prompt",
    BTN["set_parallel"]: "set_parallel_prompt",
    BTN["set_parsemode"]: "set_parsemode",
    BTN["set_recent"]: "set_recent_filter",
    BTN["set_repeat"]: "set_repeat",
    BTN["pause"]: "pause",
    BTN["resume"]: "resume",
    BTN["stop"]: "stop",
    BTN["refresh"]: "refresh",
    BTN["rep_user"]: "rep_user_prompt",
    BTN["rep_channel_ai"]: "rep_channel_ai_prompt",
    BTN["rep_reason"]: "rep_reason_menu",
    BTN["rep_status"]: "rep_status",
    BTN["back_report"]: "back_report",
    BTN["back_accounts"]: "back_accounts",
    BTN["back_groups"]: "back_groups",
    BTN["back"]: "back_home",
}

# 需要输入等待的动作
INPUT_ACTIONS = {
    "add_group_prompt",
    "batch_import_prompt",
    "acc_add_prompt",
    "acc_edit_profile_prompt",
    "set_speed_prompt",
    "set_quota_prompt",
    "set_parallel_prompt",
    "camp_step3",  # 写文案：输入内容
    "rep_user_prompt",  # 举报用户：输入用户名/链接
    "rep_channel_ai_prompt",  # AI批量：输入频道/群组
}

INPUT_HINTS = {
    "add_group_prompt": "请发送群链接或群ID（支持 t.me/xxx / t.me/+xxx / 群ID）：",
    "batch_import_prompt": "请发送多个群链接，一行一个：",
    "acc_add_prompt": "请输入要添加的账号手机号（含国家码，如 +8613800138000）：",
    "acc_edit_profile_prompt": "请输入统一名字（所有账号改成这个名字；发「跳过」则不改名字）。\n同时会自动为没有用户名的账号随机生成可用用户名：",
    "set_speed_prompt": "请输入发送间隔秒数（例如 5 表示 5-15秒）：",
    "set_quota_prompt": "请输入每账号每日上限条数（例如 50）：",
    "set_parallel_prompt": "请输入并行发送的账号数（例如 3）：",
    "camp_step3": "请输入要群发的文案内容（可多行文字）：",
    "rep_user_prompt": "请发送要举报的用户/频道用户名或链接（@username 或 t.me/xxx）：",
    "rep_channel_ai_prompt": "请发送要 AI 批量举报的频道/群组用户名或链接：",
}


# =====================================================================
#  键盘构造工具
# =====================================================================
def _kb(rows):
    """由按钮文字行列表构造 ReplyKeyboardMarkup。"""
    return ReplyKeyboardMarkup(
        [KeyboardButtonRow([KeyboardButton(t) for t in row]) for row in rows],
        resize=True,
    )


# =====================================================================
#  各菜单键盘
# =====================================================================
def main_menu_kb(is_operator: bool = False):
    """主菜单。

    主人：3×2（群发/群管理/账号 + 看板/设置/举报）
    操作员与主人同权（老板要求全开），两者渲染同一套键盘。
    """
    if is_operator:
        return _kb([
            (BTN["campaign"], BTN["groups"]),
            (BTN["dashboard"],),
        ])
    return _kb([
        (BTN["campaign"], BTN["groups"], BTN["accounts"]),
        (BTN["dashboard"], BTN["settings"], BTN["report"]),
    ])


def campaign_menu_kb():
    """群发运营菜单（新版）：选群 / 写文案 / 确认开跑 + 进度控制"""
    return _kb([
        (BTN["camp_step1"], BTN["camp_step3"]),
        (BTN["camp_start"],),
        (BTN["camp_status"], BTN["pause"], BTN["resume"], BTN["stop"]),
        (BTN["back"],),
    ])


def group_pick_kb(groups):
    """已保存群选择键盘：单列 📤 序号·标题。groups: db_get_all_groups() 返回的行。"""
    rows = []
    for i, g in enumerate(groups, 1):
        title = (g[1] or g[2] or str(g[0]))[:24]
        rows.append((f"📤 {i}·{title}",))
    rows.append((BTN["back"],))
    return _kb(rows)


def _row(*btns):
    """Button.inline() 返回单个按钮对象，这里打包成一整行。"""
    return list(btns)


def group_pick_inline_kb(groups):
    """选群（内联键盘，附在消息上）。callback data = gp:<序号>，每行 2 个。"""
    rows = []
    cur = []
    for i, g in enumerate(groups, 1):
        title = (g[1] or g[2] or str(g[0]))[:20]
        cur.append(Button.inline(f"📤 {i}·{title}", f"gp:{i}".encode()))
        if len(cur) == 2:
            rows.append(cur)
            cur = []
    if cur:
        rows.append(cur)
    rows.append([Button.inline(BTN["back_campaign"], b"gp:back")])
    return rows


def settings_inline_kb(recent_on=False, repeat_on=False, parse_label="纯文本",
                       speed=None, quota=None):
    """系统设置（内联键盘，附在消息上）：当前值直接标在按钮上，
    数字项用 +/- 步进按钮，不需要用户发文字。data 全 ASCII。"""
    sp = f"⚡ 发送间隔 {speed}s" if speed else "⚡ 发送间隔"
    q = f"🎯 每日上限 {quota}" if quota else "🎯 每日上限"
    return [
        _row(Button.inline(sp, b"st:speed:show")),
        _row(Button.inline("－1", b"st:speed:-1"), Button.inline("＋1", b"st:speed:+1"),
             Button.inline("＋5", b"st:speed:+5"), Button.inline("＋10", b"st:speed:+10")),
        _row(Button.inline(q, b"st:quota:show")),
        _row(Button.inline("－10", b"st:quota:-10"), Button.inline("＋10", b"st:quota:+10"),
             Button.inline("＋50", b"st:quota:+50"), Button.inline("＋100", b"st:quota:+100")),
        _row(Button.inline(f"✍️ 文本模式：{parse_label}", b"st:parse")),
        _row(Button.inline(f"🕒 近7天活跃：{'✅ 开' if recent_on else '❌ 关'}", b"st:recent"),
             Button.inline(f"🔁 重复推广：{'✅ 开' if repeat_on else '❌ 关'}", b"st:repeat")),
        _row(Button.inline(BTN["back"], b"st:home")),
    ]


def groups_menu_kb(is_operator: bool = False):
    """群管理菜单：我的群 / 加群 / 批量导入 / 删除群 / 返回（操作员同权）"""
    if is_operator:
        return _kb([
            (BTN["my_groups"], BTN["add_group"]),
            (BTN["batch_import"],),
            (BTN["back"],),
        ])
    return _kb([
        (BTN["my_groups"], BTN["add_group"]),
        (BTN["batch_import"], BTN["del_group"]),
        (BTN["back"],),
    ])


def group_del_kb(groups):
    """删除群键盘：单列 🗑 序号·标题。groups: db_get_all_groups() 返回的行。"""
    rows = []
    for i, g in enumerate(groups, 1):
        title = (g[1] or g[2] or str(g[0]))[:24]
        rows.append((f"🗑 {i}·{title}",))
    rows.append((BTN["back_groups"],))
    return _kb(rows)


def group_del_confirm_kb():
    """删除群二次确认键盘。"""
    return _kb([
        (BTN["del_confirm"],),
        (BTN["del_cancel"],),
    ])


def accounts_menu_kb():
    """账号管理菜单：列表 / 添加 / 改资料 / 过滤 / 重置冷却"""
    return _kb([
        (BTN["acc_list"], BTN["acc_add"]),
        (BTN["acc_edit_profile"], BTN["acc_filter"]),
        (BTN["acc_cd_reset"],),
        (BTN["back"],),
    ])


def profile_menu_kb():
    """改资料子菜单：随机一键 / 手动统一名字 / 返回"""
    return _kb([
        (BTN["profile_random"],),
        (BTN["profile_name"],),
        (BTN["back_accounts"],),
    ])


def settings_menu_kb():
    """系统设置菜单：间隔 / 上限 / 文本模式 / 近7天活跃 / 重复推广"""
    return _kb([
        (BTN["set_speed"], BTN["set_quota"]),
        (BTN["set_parsemode"],),
        (BTN["set_recent"], BTN["set_repeat"]),
        (BTN["back"],),
    ])


def dashboard_menu_kb():
    """（旧回复键盘，已被 dashboard_inline_kb 取代，保留兼容）"""
    return _kb([
        (BTN["refresh"],),
        (BTN["back"],),
    ])


def dashboard_inline_kb(show_accounts=False):
    """看板内联键盘：「每人统计」↔「账号明细」互切 + 刷新 + 返回主菜单。
    返回 rows 列表（每项是一行 Button），可直接传给 send_message/edit 的 buttons。"""
    if show_accounts:
        toggle = Button.inline("👤 每人统计", b"db:who")
    else:
        toggle = Button.inline("📇 账号明细", b"db:acc")
    return [
        [toggle],
        [Button.inline(BTN["refresh"], b"db:ref"),
         Button.inline(BTN["back"], b"db:home")],
    ]
def report_menu_kb():
    """举报菜单：三种模式 + 状态 + 返回主菜单"""
    return _kb([
        (BTN["rep_user"], BTN["rep_reason"]),
        (BTN["rep_channel_ai"], BTN["rep_status"]),
        (BTN["back"],),
    ])


def reason_menu_kb():
    """理由选择键盘（10 种两列）+ 返回举报菜单"""
    from reasons import REPORT_REASONS
    keys = list(REPORT_REASONS.keys())
    rows = []
    for i in range(0, len(keys), 2):
        rows.append(tuple(REPORT_REASONS[k][0] for k in keys[i:i + 2]))
    rows.append((BTN["back_report"],))
    return _kb(rows)


# =====================================================================
#  各菜单文本
# =====================================================================
def main_menu_text(accounts, groups_count, targets_count, sent_count, pool_count, busy: bool,
                   role: str = "owner", name: str = "", busy_tip: str = ""):
    """主菜单文本，含实时数据摘要。多人使用时标出当前身份。"""
    head = f"📋 控制面板｜{('主人：' + name) if role == 'owner' else ('操作员：' + name)}\n\n" if name else "📋 控制面板\n\n"
    lock = f"\n🔒 当前占用：{busy_tip}\n" if busy and busy_tip else ""
    return (
        head +
        f"👤 账号: {len(accounts)} 个在线 | "
        f"📁 群组: {groups_count} 个\n"
        f"📋 名单: {targets_count} 人 | "
        f"已发(去重): {sent_count} 人\n"
        f"📝 文案池: {pool_count} 条\n"
        f"{'⏳ 正在执行任务中…' if busy else '🟢 空闲中'}{lock}\n\n"
        + ("" if role == "owner" else "ℹ️ 你是操作员：功能已全部开放，只有增删操作员需找主人。\n")
        + "请选择功能："
    )


def campaign_menu_text():
    """群发运营菜单文本（新版 3 步）。"""
    return (
        "🚀 群发运营\n\n"
        "① 选群 — 从已保存的群里点按钮选择，自动拉成员进名单\n"
        "② 写文案 — 发送文案（支持 HTML 格式）\n"
        "③ 确认开跑 — 自动检查账号后一键群发\n\n"
        "发完文案会自动检查账号并提示确认。\n"
        "进度每 15 秒自动汇报，可暂停/继续/停止。"
    )


def groups_menu_text(is_operator: bool = False):
    base = "📥 群管理\n\n查看已加入的群、添加新群、批量导入群链接。"
    if is_operator:
        return base + "、删除群记录。\n"
    return (base + "、删除群记录。\n"
            "💡「删除群」会先让在该群的账号退群，再删除记录，不可逆。")


def accounts_menu_text():
    return ("👥 账号管理\n\n查看账号状态、添加新账号、批量修改资料。\n"
            "💡「账号过滤」检测的是 **Bot 已登录的推送账号**（不是收集来的用户名单）。\n"
            "💡「🧊 重置冷却」清空所有账号的限流冷却记账，让冷却中的账号立即恢复派活。")


def profile_menu_text():
    return (
        "📝 批量改资料\n\n"
        "🎲 随机小名 — 每账号随机中文小名（姓固定「小」+ 水果/蔬菜，如 小苹果/小西瓜），"
        "并补随机用户名；头像只给**没有头像**的账号补随机风景图，已有头像的跳过不换\n"
        "✏️ 统一名字 — 手动输入一个名字，所有账号改成同一个\n\n"
        "⚠️ 每次修改间隔 2 秒防风控；补的头像为在线下载的真实风景照。"
    )


def settings_menu_text(recent_on=None, repeat_on=None):
    lines = ["⚙️ 系统设置", "",
             "下面的按钮直接调，不用打字：",
             "• 发送间隔 / 每日上限 — 点 －／＋ 步进调数，点当前值可查看",
             "• 文本模式 — 点击切换：纯文本 → HTML → Markdown"]
    if recent_on is not None:
        lines.append(f"• 近7天活跃 — 当前：{'✅ 开（只拉近7天上线过的成员）' if recent_on else '❌ 关（拉全部有效成员）'}")
    if repeat_on is not None:
        lines.append(f"• 重复推广 — 当前：{'✅ 开（同一人可再次推送）' if repeat_on else '❌ 关（每人只推一次）'}")
    lines += ["", "💡 设置直接标在按钮上，改完自动保存并生效。"]
    return "\n".join(lines)


def report_menu_text():
    return (
        "🚨 举报中心\n\n"
        "👤 举报用户/频道 — 选理由直接举报\n"
        "📋 指定理由举报 — 先选理由再发目标\n"
        "🤖 AI 批量举报 — 拉消息→AI生成理由→多账号齐发\n"
        "⏳ 账号冷却状态 — 查看可用/休息中账号\n\n"
        "⚠️ 举报后账号进入30分钟冷却，防止风控。"
    )