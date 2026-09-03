#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bot 菜单键盘体系：所有按钮定义 + 键盘构造 + 菜单文本，集中管理。

bot.py 只负责 handler 逻辑，不混入按钮文字和键盘布局。
"""
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
    # 群发运营子菜单
    "camp_step1": "① 选择群",
    "camp_step2": "② 拉取名单",
    "camp_step3": "③ 写文案",
    "camp_step4": "④ 账号准备",
    "camp_start": "⑤ 🚀 开始群发",
    "camp_status": "📋 查看进度",
    # 群管理子菜单
    "my_groups": "我的群",
    "add_group": "加群",
    "batch_import": "批量导入",
    # 账号管理子菜单
    "acc_list": "账号列表",
    "acc_add": "添加账号",
    "acc_edit_profile": "批量改资料",
    "acc_filter": "账号过滤",
    # 数据看板
    "dashboard_view": "📊 查看数据",
    # 设置子菜单
    "set_speed": "发送间隔",
    "set_quota": "每日上限",
    "set_parallel": "并行账号数",
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
}

# ---- 所有按钮 → 动作标识 ----
BTN_ACTION = {
    BTN["campaign"]: "menu_campaign",
    BTN["groups"]: "menu_groups",
    BTN["accounts"]: "menu_accounts",
    BTN["dashboard"]: "menu_dashboard",
    BTN["settings"]: "menu_settings",
    BTN["camp_step1"]: "camp_step1",
    BTN["camp_step2"]: "camp_step2",
    BTN["camp_step3"]: "camp_step3",
    BTN["camp_step4"]: "camp_step4",
    BTN["camp_start"]: "camp_start",
    BTN["camp_status"]: "camp_status",
    BTN["my_groups"]: "my_groups",
    BTN["add_group"]: "add_group_prompt",
    BTN["batch_import"]: "batch_import_prompt",
    BTN["acc_list"]: "acc_list",
    BTN["acc_add"]: "acc_add_prompt",
    BTN["acc_edit_profile"]: "acc_edit_profile_prompt",
    BTN["acc_filter"]: "acc_filter",
    BTN["dashboard_view"]: "dashboard_view",
    BTN["set_speed"]: "set_speed_prompt",
    BTN["set_quota"]: "set_quota_prompt",
    BTN["set_parallel"]: "set_parallel_prompt",
    BTN["pause"]: "pause",
    BTN["resume"]: "resume",
    BTN["stop"]: "stop",
    BTN["refresh"]: "refresh",
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
    "camp_step1",  # 选群：输入群链接/ID
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
    "camp_step1": "请发送群链接或群ID：",
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
def main_menu_kb():
    """主菜单：5 个主按钮"""
    return _kb([
        (BTN["campaign"], BTN["groups"]),
        (BTN["accounts"], BTN["dashboard"]),
        (BTN["settings"],),
    ])


def campaign_menu_kb():
    """群发运营菜单：5 步 + 查看进度 + 返回"""
    return _kb([
        (BTN["camp_step1"], BTN["camp_step2"]),
        (BTN["camp_step3"], BTN["camp_step4"]),
        (BTN["camp_start"],),
        (BTN["camp_status"], BTN["pause"], BTN["resume"], BTN["stop"]),
        (BTN["back"],),
    ])


def groups_menu_kb():
    """群管理菜单：我的群 / 加群 / 批量导入 / 返回"""
    return _kb([
        (BTN["my_groups"], BTN["add_group"]),
        (BTN["batch_import"],),
        (BTN["back"],),
    ])


def accounts_menu_kb():
    """账号管理菜单：列表 / 添加 / 改资料 / 过滤"""
    return _kb([
        (BTN["acc_list"], BTN["acc_add"]),
        (BTN["acc_edit_profile"], BTN["acc_filter"]),
        (BTN["back"],),
    ])


def settings_menu_kb():
    """系统设置菜单：间隔 / 上限 / 并行数"""
    return _kb([
        (BTN["set_speed"], BTN["set_quota"]),
        (BTN["set_parallel"],),
        (BTN["back"],),
    ])


def dashboard_menu_kb():
    """数据看板：刷新 + 返回"""
    return _kb([
        (BTN["refresh"],),
        (BTN["back"],),
    ])


# =====================================================================
#  各菜单文本
# =====================================================================
def main_menu_text(accounts, groups_count, targets_count, sent_count, pool_count, busy: bool):
    """主菜单文本，含实时数据摘要。"""
    return (
        "📋 控制面板\n\n"
        f"👤 账号: {len(accounts)} 个在线 | "
        f"📁 群组: {groups_count} 个\n"
        f"📋 名单: {targets_count} 人 | "
        f"已发(去重): {sent_count} 人\n"
        f"📝 文案池: {pool_count} 条\n"
        f"{'⏳ 正在执行任务中…' if busy else '🟢 空闲中'}\n\n"
        "请选择功能："
    )


def campaign_menu_text():
    """群发运营菜单文本。"""
    return (
        "🚀 群发运营\n\n"
        "请按顺序完成各步骤，系统会记住进度，\n"
        "下次回来接着继续。\n\n"
        "① 选择群 — 确定目标群\n"
        "② 拉取名单 — 从群拉取成员\n"
        "③ 写文案 — 输入群发内容\n"
        "④ 账号准备 — 检查账号状态\n"
        "⑤ 开始群发— 多账号自动发送\n\n"
        "提示：按顺序走，第①步完成后才能做第②步。"
    )


def groups_menu_text():
    return "📥 群管理\n\n查看已加入的群、添加新群、批量导入群链接。"


def accounts_menu_text():
    return "👥 账号管理\n\n查看账号状态、添加新账号、批量修改资料、过滤检测。"


def settings_menu_text():
    return "⚙️ 系统设置\n\n调整发送间隔、每日上限、并行账号数。"