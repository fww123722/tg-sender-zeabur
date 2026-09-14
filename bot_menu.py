#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bot 菜单键盘体系：所有按钮定义 + 键盘构造 + 菜单文本，集中管理。

bot.py 只负责 handler 逻辑，不混入按钮文字和键盘布局。
"""
from telethon import Button
from telethon.tl.types import (KeyboardButton, KeyboardButtonRow,
                               KeyboardButtonStyle, ReplyKeyboardMarkup)


# =====================================================================
#  按钮文字
# =====================================================================
BTN = {
    # ---- 主菜单（3×2 栅格）----
    "campaign": "🚀 群发运营",
    "groups": "📥 群管理",
    "accounts": "👥 账号管理",
    "dashboard": "📊 数据看板",
    "settings": "⚙️ 系统设置",
    "report": "🚨 举报中心",
    # ---- 群发运营：不选群了，就两步（写文案 → 确认开跑）----
    "camp_step3": "① 写文案",
    "camp_start": "② 确认开跑",
    "camp_status": "📈 查看进度",
    # 成员页被锁时的备用入口（撞锁已会自动接力，这个是手动多翻几条）
    "camp_speakers": "🗣 采发言人",
    # ---- 群管理 ----
    "my_groups": "📋 我的群",
    "add_group": "➕ 加群",
    "batch_import": "📥 批量导入",
    "pending_joins": "📨 在途申请",
    "regroup": "🔄 重拉成员",
    "del_group": "🗑 删除群",
    # 进群后持续补录：老板「读了成员不能就不管了」，这里给状态+手动补扫入口
    "watch": "📡 自动补录",
    "watch_now": "⚡ 立即补扫",
    "watch_status": "📊 补录情况",
    "del_confirm": "⚠️ 确认退群并删除",
    "del_cancel": "取消",
    # ---- 账号管理 ----
    "acc_list": "📋 账号列表",
    "acc_add": "➕ 添加账号",
    "acc_edit_profile": "✏️ 批量改资料",
    "acc_filter": "🔍 账号过滤",
    "acc_cd_reset": "🧊 重置冷却",
    # ---- 改资料 ----
    "profile_random": "🎲 随机小名",
    "profile_name": "✏️ 统一名字",
    # ---- 数据看板 ----
    "dashboard_view": "📊 查看数据",
    # ---- 系统设置 ----
    "set_speed": "⏱ 发送间隔",
    "set_quota": "🎯 每日上限",
    "set_parallel": "🚦 并行账号数",
    "set_parsemode": "¶ 文本模式",
    "set_recent": "🕒 近7天活跃",
    "set_repeat": "🔁 重复推广",
    # ---- 文案池 ----
    "pool": "📚 文案池",
    "pool_add": "➕ 加文案",
    "pool_list": "📋 看文案",
    "pool_clear": "🗑 清空",
    "pool_random": "🔀 随机轮换",
    # ---- 任务控制 ----
    "pause": "⏸ 暂停",
    "resume": "▶️ 继续",
    "stop": "🛑 停止任务",
    "refresh": "🔄 刷新",
    # ---- 举报 ----
    "rep_user": "👤 举报用户/频道",
    "rep_channel_ai": "🤖 AI 批量举报",
    "rep_reason": "📋 指定理由",
    "rep_status": "⏳ 冷却状态",
    # ---- 导航：整排只认两个概念——🏠 回主菜单（永远单独占最后一行），
    #      挂在消息上的内联列表用 ✖ 取消（退出列表，跟底部键盘无关）。
    #      「‹ 返回XX」不再摆上键盘，只留着让改结构前的老消息点了还能用。----
    "back": "🏠 主菜单",
    "cancel_pick": "✖ 取消",
    "back_list": "‹ 返回列表",
    "back_campaign": "‹ 返回群发运营",
    "back_groups": "‹ 返回群管理",
    "back_accounts": "‹ 返回账号管理",
    "back_settings": "‹ 返回系统设置",
    "back_report": "‹ 返回举报中心",
}

# ---- 所有按钮 → 动作标识 ----
BTN_ACTION = {
    BTN["campaign"]: "menu_campaign",
    BTN["groups"]: "menu_groups",
    BTN["accounts"]: "menu_accounts",
    BTN["dashboard"]: "menu_dashboard",
    BTN["settings"]: "menu_settings",
    BTN["report"]: "menu_report",
    BTN["camp_step3"]: "camp_step3",
    BTN["camp_start"]: "camp_start",
    BTN["camp_status"]: "camp_status",
    BTN["camp_speakers"]: "camp_speakers_prompt",
    BTN["my_groups"]: "my_groups",
    BTN["add_group"]: "add_group_prompt",
    BTN["batch_import"]: "batch_import_prompt",
    BTN["pending_joins"]: "pending_joins",
    BTN["regroup"]: "regroup_menu",
    BTN["del_group"]: "del_group_menu",
    BTN["watch"]: "watch_status",
    BTN["watch_now"]: "watch_now",
    BTN["watch_status"]: "watch_status",
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
    BTN["pool"]: "menu_pool",
    BTN["pool_add"]: "pool_add_prompt",
    BTN["pool_list"]: "pool_list",
    BTN["pool_clear"]: "pool_clear",
    BTN["pool_random"]: "pool_random",
    BTN["pause"]: "pause",
    BTN["resume"]: "resume",
    BTN["stop"]: "stop",
    BTN["refresh"]: "refresh",
    BTN["rep_user"]: "rep_user_prompt",
    BTN["rep_channel_ai"]: "rep_channel_ai_prompt",
    BTN["rep_reason"]: "rep_reason_menu",
    BTN["rep_status"]: "rep_status",
    BTN["cancel_pick"]: "noop",
    BTN["back_list"]: "pending_joins",
    BTN["back_report"]: "back_report",
    BTN["back_accounts"]: "back_accounts",
    BTN["back_groups"]: "back_groups",
    BTN["back_settings"]: "back_settings",
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
    "pool_add_prompt",  # 文案池：输入一条文案
    "rep_user_prompt",  # 举报用户：输入用户名/链接
    "rep_channel_ai_prompt",  # AI批量：输入频道/群组
    "camp_speakers_prompt",  # 采发言人：输入群链接（可带条数）
}

INPUT_HINTS = {
    "add_group_prompt": "请发送群链接或群ID（t.me/xxx / t.me/+xxx / 群ID）：",
    "batch_import_prompt": "请发送多个群链接，一行一个：",
    "acc_add_prompt": "请输入手机号（含国家码，如 +8613800138000）：",
    "acc_edit_profile_prompt": "请输入统一名字（发「跳过」则不改名）：",
    "set_speed_prompt": "请输入发送间隔秒数（例如 5 表示 5-15秒）：",
    "set_quota_prompt": "请输入每账号每日上限条数（例如 50）：",
    "set_parallel_prompt": "请输入并行发送的账号数（例如 3）：",
    "camp_step3": "请输入要群发的文案内容（可多行文字）：",
    "pool_add_prompt": "请发送要存入文案池的内容（一次一条）：",
    "rep_user_prompt": "请发送要举报的用户/频道（@username 或 t.me/xxx）：",
    "rep_channel_ai_prompt": "请发送要 AI 批量举报的频道/群组用户名或链接：",
    "camp_speakers_prompt": (
        "请发送群链接或群ID（t.me/xxx 或 数字ID）：\n"
        "可以在后面加要翻的历史条数，例：t.me/xxx 3000\n"
        "不写默认 2000 条（上限 20000）。只追加进名单，不清空。"),
}


# =====================================================================
#  键盘构造工具（iOS 风：语义色 + 统一栅格）
# =====================================================================
#  Telegram 原生只给三个语义色，正好对上 iOS 的调调：
#    success=绿（主行动）  danger=红（不可逆）  primary=蓝（推进下一步）
#    不标色的就是默认灰（导航/查看类）——层级靠颜色差，不靠 emoji 堆砌。
#  只按「动作是什么性质」上色，不按菜单名字上色，所以新增按钮不会漏色。
STYLE_BY_ACTION = {
    # 绿：主行动（按下就干活）
    "camp_start": "success",
    "add_group_prompt": "success",
    "batch_import_prompt": "success",
    "acc_add_prompt": "success",
    "pool_add_prompt": "success",
    "vjr": "success",              # ✅ 我过了，重试
    # 蓝：推进下一步 / 进入下一环节
    "camp_step3": "primary",
    "camp_speakers_prompt": "primary",
    "resume": "primary",
    "profile_random": "primary",
    "acc_edit_profile_prompt": "primary",
    "vja": "primary",              # 📡 继续盯验证消息
    # 红：不可逆 / 停当前任务
    "stop": "danger",
    "del_group_menu": "danger",
    "del_confirm": "danger",
    "pool_clear": "danger",
    "watch_now": "primary",
    "watch_status": "primary",
    # 其余（查看/刷新/导航）保持默认灰，灰就是「不抢眼睛」
}

_STYLE_ATTR = {"success": "bg_success", "danger": "bg_danger", "primary": "bg_primary"}
_STYLE_CACHE = {}

# 万一服务端不认这个新字段，关掉它就退回纯文字键盘（不会断发消息）
IOS_STYLE = True


def _ios_style(name):
    if not IOS_STYLE or not name:
        return None
    if name not in _STYLE_CACHE:
        try:
            _STYLE_CACHE[name] = KeyboardButtonStyle(**{_STYLE_ATTR[name]: True})
        except Exception:
            _STYLE_CACHE[name] = None
    return _STYLE_CACHE[name]


def _kb(rows, styles=None):
    """由按钮文字行列表构造 ReplyKeyboardMarkup。

    rows：每行一个元组/列表，元素是按钮文字。
    styles：{文字: success/danger/primary} 可选项，用于动态生成的行（如列表项）；
            静态菜单不需要传——按 BTN_ACTION 查动作名自动上色。
    结果对象上带 `.plain`：不带任何样式的同一键盘，发送失败时 bot 会自动降级用它。
    """
    styles = styles or {}
    styled_rows, plain_rows = [], []
    for row in rows:
        s_btns, p_btns = [], []
        for t in row:
            st = styles.get(t) or STYLE_BY_ACTION.get(BTN_ACTION.get(t, ""))
            btn_style = _ios_style(st)
            s_btns.append(KeyboardButton(t, style=btn_style) if btn_style
                          else KeyboardButton(t))
            p_btns.append(KeyboardButton(t))
        styled_rows.append(KeyboardButtonRow(s_btns))
        plain_rows.append(KeyboardButtonRow(p_btns))
    kb = ReplyKeyboardMarkup(styled_rows, resize=True)
    kb.plain = ReplyKeyboardMarkup(plain_rows, resize=True)
    return kb


# =====================================================================
#  各菜单键盘
# =====================================================================
#  排版铁律（老板说「还是很不合理」之后定死的）：
#   1. 任何菜单最多 3 行功能键，🏠 主菜单永远单独占最后一行；
#   2. 同一件事只给一个入口（选群/重拉/采发言人/批量导入 以前共 5 个入口）；
#   3. 颜色只说「现在该点哪个」：绿=下一步，蓝=推进，红=不可逆，灰=看看；
#   4. 挂在消息上的列表用「✖ 取消」，底部键盘用「🏠 主菜单」，两者不混用。
# =====================================================================
def main_menu_kb(is_operator: bool = False):
    """主菜单：3×2 六格，操作员与admin完全同一套（老板要求功能全开）。"""
    return _kb([
        (BTN["campaign"], BTN["groups"], BTN["accounts"]),
        (BTN["dashboard"], BTN["settings"], BTN["report"]),
    ])


def campaign_menu_kb(stage=0, running=False, paused=False):
    """群发运营键盘：**只给此刻真能按的键**。

    不再选群（老板：「群发推广不再需要选择群聊」）：目标准备就是全库名单，
    按录入时间从新到旧发。所以只剩两步：写文案 → 确认开跑。
    stage：0=还差文案　1=文案已有（可开跑）——只把该按那一步标绿，排版不变。
    running/paused：有任务在跑时整排换成控制键，不会把开跑键重复摆着让人重发。
    """
    if running or paused:
        go = BTN["resume"] if paused else BTN["pause"]
        return _kb([
            (go, BTN["stop"]),
            (BTN["camp_status"],),
            (BTN["back"],),
        ], {go: "success"})
    rows = [
        (BTN["camp_step3"], BTN["camp_start"]),
        (BTN["camp_status"],),
        (BTN["back"],),
    ]
    hot = BTN["camp_step3"] if stage == 0 else BTN["camp_start"]
    return _kb(rows, {hot: "success"})


def _clean_title(g, n=18):
    """列表项统一取标题：去换行、限长、补省略号，不顶坏键盘栅格。"""
    t = (g[1] or g[2] or str(g[0]))
    t = " ".join(str(t).split())
    return (t[:n] + "…") if len(t) > n else t


def group_pick_kb(groups):
    """已废弃：群发不再选群。保留签名以防旧调用点，直接回主菜单键盘。"""
    return main_menu_kb()


def _row(*btns):
    """Button.inline() 返回单个按钮对象，这里打包成一整行。"""
    return list(btns)


def group_pick_inline_kb(groups):
    """已废弃：群发不再选群（老板：默认打所有群的人，从新到旧）。

    旧消息上可能还挂着 gp: 按钮，所以不删：现在点它只回主菜单，不再清名单。
    """
    return []


def settings_inline_kb(recent_on=False, repeat_on=False, parse_label="纯文本",
                       speed=None, quota=None):
    """系统设置（内联键盘）：开关用绿/灰表示开/关，数字项用 +/- 步进。

    旧消息上的面板，保留可点；新消息走底部键盘。
    补录相关不在这上面：那个完全自动跑，不给开关也不给窗口设置。
    """
    def _sw(on):
        return "🟢 开" if on else "⚪ 关"
    sp = f"⏱ 发送间隔　{speed}s" if speed else "⏱ 发送间隔"
    q = f"🎯 每日上限　{quota}" if quota else "🎯 每日上限"
    return [
        _row(Button.inline(sp, b"st:speed:show")),
        _row(Button.inline("−1", b"st:speed:-1", style="primary"),
             Button.inline("＋1", b"st:speed:+1", style="primary"),
             Button.inline("＋5", b"st:speed:+5", style="primary"),
             Button.inline("＋10", b"st:speed:+10", style="primary")),
        _row(Button.inline(q, b"st:quota:show")),
        _row(Button.inline("−10", b"st:quota:-10", style="primary"),
             Button.inline("＋10", b"st:quota:+10", style="primary"),
             Button.inline("＋50", b"st:quota:+50", style="primary"),
             Button.inline("＋100", b"st:quota:+100", style="primary")),
        _row(Button.inline(f"¶ 文本模式　{parse_label}", b"st:parse", style="primary")),
        _row(Button.inline(f"🕒 近7天活跃　{_sw(recent_on)}", b"st:recent",
                           style="success" if recent_on else None),
             Button.inline(f"🔁 重复推广　{_sw(repeat_on)}", b"st:repeat",
                           style="success" if repeat_on else None)),
        _row(Button.inline(f"🔁 重复推广　{_sw(repeat_on)}", b"st:repeat",
                           style="success" if repeat_on else None)),
        _row(Button.inline(BTN["back"], b"st:home")),
    ]


def groups_menu_kb(is_operator: bool = False):
    """群管理：只看/进/盯/删四件事，三行功能键 + 返回。

    减负记录：「批量导入」已并进「加群」（发一条=加一个，发多行=批量），
    「重拉成员」并到「自动补录」子菜单里——顶层从 7 个入口压到 5 个。
    """
    return _kb([
        (BTN["my_groups"], BTN["add_group"]),
        (BTN["watch"], BTN["pending_joins"]),
        (BTN["del_group"],),
        (BTN["back"],),
    ])


def watch_menu_kb():
    """成员补录子菜单：自动的事不给开关，只给「立即补扫 / 重拉一个群 / 看情况」。"""
    return _kb([
        (BTN["watch_now"], BTN["regroup"]),
        (BTN["watch_status"],),
        (BTN["back_groups"],),
    ], {BTN["watch_now"]: "primary"})


def group_repull_inline_kb(groups):
    """重拉成员选群键盘（内联）：gr:<序号>，每行 2 个。groups 同 db_get_all_groups()。"""
    rows = []
    cur = []
    for i, g in enumerate(groups, 1):
        cur.append(Button.inline(f"{i} · {_clean_title(g)}", f"gr:{i}".encode(),
                                style="primary"))
        if len(cur) == 2:
            rows.append(cur)
            cur = []
    if cur:
        rows.append(cur)
    rows.append([Button.inline(BTN["cancel_pick"], b"gr:back")])
    return rows


def group_del_kb(groups):
    """删除群键盘：单列序号·标题（全标红：这一排按下去都是不可逆的）。"""
    rows = []
    styles = {}
    for i, g in enumerate(groups, 1):
        t = f"{i} · {_clean_title(g, 22)}"
        styles[t] = "danger"
        rows.append((t,))
    rows.append((BTN["back_groups"],))
    return _kb(rows, styles)


def group_del_confirm_kb():
    """删除群二次确认：确认（红）与取消（灰）各占一行。"""
    return _kb([
        (BTN["del_confirm"],),
        (BTN["del_cancel"],),
    ])


def accounts_menu_kb():
    """账号管理：列表/添加 + 改资料/体检 + 重置冷却。"""
    return _kb([
        (BTN["acc_list"], BTN["acc_add"]),
        (BTN["acc_edit_profile"], BTN["acc_filter"]),
        (BTN["acc_cd_reset"],),
        (BTN["back"],),
    ])


def profile_menu_kb():
    """改资料子菜单：随机/统一两个动作呓一行，不拆成三行。"""
    return _kb([
        (BTN["profile_random"], BTN["profile_name"]),
        (BTN["back_accounts"],),
    ])


def settings_menu_kb(recent_on=False, repeat_on=False, speed=None, quota=None):
    """系统设置键盘（全局唯一一套）：两行功能键 + 主菜单。

    补录相关不在这上面：那个完全自动跑，不给开关也不给窗口设置。
    """
    return _kb([
        (BTN["set_speed"], BTN["set_quota"]),
        (BTN["set_parsemode"], BTN["pool"]),
        (BTN["set_recent"], BTN["set_repeat"]),
        (BTN["back"],),
    ], {BTN["set_recent"]: "success" if recent_on else None,
        BTN["set_repeat"]: "success" if repeat_on else None})


def settings_speed_quota_kb(speed=None, quota=None):
    """调数值时的临时键盘：只给「当前值 + 返回」，不让人在输数字时误点别的开关。"""
    sp = f"⏱ 当前 {speed}s" if speed else BTN["set_speed"]
    q = f"🎯 当前 {quota} 条" if quota else BTN["set_quota"]
    return _kb([
        (sp, q),
        (BTN["back_settings"],),
    ])


def pool_menu_kb():
    """文案池菜单：加/看 一行，轮换/清空 一行，返回单独。"""
    return _kb([
        (BTN["pool_add"], BTN["pool_list"]),
        (BTN["pool_random"], BTN["pool_clear"]),
        (BTN["back_settings"],),
    ])


def pool_inline_kb(rows):
    """文案列表内联键盘：每条一个删除按钮 pl:<id>（全红），底部清空/返回。"""
    btns = []
    for i, (source, msg_id, _text) in enumerate(rows, 1):
        btns.append([Button.inline(f"删 第{i}条", ("pl:d:%d" % msg_id).encode(),
                                   style="danger")])
    btns.append([Button.inline("🗑 全部清空", b"pl:clear", style="danger"),
                 Button.inline(BTN["cancel_pick"], b"pl:back")])
    return btns


def dashboard_menu_kb():
    """（旧回复键盘，已被 dashboard_inline_kb 取代，保留兼容）"""
    return _kb([
        (BTN["refresh"],),
        (BTN["back"],),
    ])


def dashboard_inline_kb(show_accounts=False):
    """看板内联键盘：三个视图互切（每人统计 / 未发送 / 账号明细）+ 刷新 + 返回。

    show_accounts 兼容旧参数：False=每人统计，True=账号明细，"unsent"=未发送明细。
    当前所在视图前面标 ▸，免得只靠颜色猜自己看的是哪页。
    """
    cur = "un" if show_accounts == "unsent" else ("acc" if show_accounts else "who")

    def _b(label, key, style=None):
        mark = "▸ " if cur == key else "　 "
        st = style or ("primary" if cur == key else None)
        return Button.inline(mark + label, f"db:{key}".encode(), style=st)

    return [
        _row(_b("👤 每人统计", "who")),
        _row(_b("❗ 未发送", "un", "danger")),
        _row(_b("📇 账号明细", "acc")),
        _row(Button.inline(BTN["refresh"], b"db:ref"),
             Button.inline(BTN["back"], b"db:home")),
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
def main_menu_text(accounts, groups_count, unsent_count, pool_count, busy: bool,
                   role: str = "owner", name: str = "", busy_tip: str = ""):
    """主菜单文本，含实时数据摘要。多人使用时标出当前身份。"""
    # actor_name(owner) 已返回 admin，不拼前缀以免「admin：admin」
    if role == "owner":
        tag = name or "admin"
    else:
        tag = ("操作员：" + name) if name else "操作员"
    head = f"📋 控制面板｜{tag}\n\n"
    lock = f"\n🔒 当前占用：{busy_tip}\n" if busy and busy_tip else ""
    return (
        head +
        f"👤 账号: {len(accounts)} 个在线 | "
        f"📁 群组: {groups_count} 个 | "
        f"📨 未发送: {unsent_count} 人\n"
        f"📝 文案池: {pool_count} 条\n"
        f"{'⏳ 任务中…' if busy else '🟢 空闲中'}{lock}\n\n"
        + ("" if role == "owner"
           else "ℹ️ 操作员：功能已全开，仅增删操作员需找admin。\n")
    )


def campaign_menu_text():
    """群发运营菜单文本（不选群版：就两步）。"""
    return (
        "🚀 群发运营｜就两步\n\n"
        "① 写文案 — 直接发文案（支持 HTML）\n"
        "② 确认开跑 — 自动检查账号后开跑\n\n"
        "不用选群：目标自动就是一切群里的人，按录入时间从新到旧发\n"
        "（刚补录进来的新面孔先收到），发过的人自动跳过。\n"
        "绿键 = 现在该按的那个；跑起来后这一排会变成暂停/继续/停止。"
    )


def groups_menu_text(is_operator: bool = False):
    return ("📥 群管理\n\n"
            "📋「我的群」— 看账号在哪些群\n"
            "➕「加群」— 发一个链接加一个；一次发多个（一行一个）就是批量导入\n"
            "📡「自动补录」— 进群后持续收新人，子菜单里有重拉/补扫\n"
            "📨「在途申请」— 等群主批准 / 等你人工验证的群\n"
            "⚠️「删除群」— 先退群再删记录，不可逆")


def watch_menu_text():
    """成员补录子菜单文本：说清「为什么一直在读」，常驻自动、没得关。"""
    return ("📡 自动补录｜进群后一直读，不用人再点\n\n"
            "盯着的群有人发言、有人进群 → 当场进名单；\n"
            "　　每 20 分钟再按水位线补扫一轮（重启/掉线漏的补回来）。\n"
            "全程只追加不清空，不会动别人正在跑的名单。\n\n"
            "⚡「立即补扫」— 现在就把所有群过一轮\n"
            "🔄「重拉成员」— 挑一个群重新拉一次\n\n")


def accounts_menu_text():
    return ("👥 账号管理\n\n查看账号、添加账号、批量改资料、账号过滤。\n"
            "🧊「重置冷却」清空限流记账，冷却中的账号立即恢复派活。")


def profile_menu_text():
    return (
        "📝 批量改资料\n\n"
        "🎲 随机小名 — 随机「小+水果/蔬菜」名，补用户名；无头像的补随机风景照\n"
        "✏️ 统一名字 — 所有账号改成同一个名字\n\n"
        "⚠️ 每个账号间隔 2 秒防风控。"
    )


def settings_menu_text(recent_on=None, repeat_on=None):
    lines = ["⚙️ 系统设置", "",
             "• 发送间隔 / 每日上限 — 点进去输数字",
             "• 文本模式 — 点击切换 纯文本 → HTML → Markdown"]
    if recent_on is not None:
        lines.append(f"• 近7天活跃 — {'✅ 开' if recent_on else '❌ 关'}")
    if repeat_on is not None:
        lines.append(f"• 重复推广 — {'✅ 开（同一人可再推）' if repeat_on else '❌ 关（每人只推一次）'}")
    lines += ["", "当前值都标在下面按钮上，改完自动生效（底部不再重复摆开关）。"]
    return "\n".join(lines)


def pool_menu_text(count=0, random_on=False):
    """文案池菜单文本。"""
    return (
        "📚 文案池｜%d 条\n\n"
        "➕ 加文案 — 发一条存一条\n"
        "📋 看文案 — 列出、逐条删\n"
        "🔀 随机轮换 — %s\n\n"
        "开启后每人随机挑一条发，不用手写文案。"
        % (count, "✅ 开" if random_on else "❌ 关")
    )


def verify_relay_kb(iid, it=None):
    """验证题中继面板：把验证 bot 原话里的按钮**原样**列回去。

    一个按钮对应一次人工点击，编号 = 展示时的序号（回调 vr:<iid>:c:<序号>）。
    系统不自选、不猜题：老板不点，这里一个字节都不会发出去。
    选项一律不标色（标绿会诱着人乱点），只「忽略」标灰、链接按钮标蓝。"""
    rows = []
    opts = (it or {}).get("opts") or []
    cur = []
    for n, o in enumerate(opts, 1):
        label = f"{n}. {(o.get('text') or '')[:16]}"
        cur.append(Button.inline(label, f"vr:{iid}:c:{n}".encode()))
        if len(cur) == 2:
            rows.append(cur)
            cur = []
    if cur:
        rows.append(cur)
    link = ((it or {}).get("link") or "").strip()
    if link:
        url = link if link.startswith("http") else "https://" + link.lstrip("/")
        if url.startswith("http"):
            rows.append([Button.url("🔗 打开群", url=url, style="primary")])
    rows.append([Button.inline(BTN["cancel_pick"], f"vr:{iid}:x".encode(),
                              style="danger")])
    return rows


def pending_joins_inline_kb(rows):
    """在途申请列表（内联）：每行一个，点开看详情+可执行动作。
    rows: [{'kind','title','token','acc','link','age_min'}...]，按传入顺序编号。"""
    out = []
    cur = []
    for idx, r in enumerate(rows, 1):
        icon = "🔒" if r.get("kind") == "verify" else "⏳"
        title = _clean_title([None, r.get("title") or r.get("token"), None], 16)
        cur.append(Button.inline(f"{icon} {idx} · {title}", f"vj:{idx}".encode()))
        if len(cur) == 2:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    out.append([Button.inline(BTN["cancel_pick"], b"vj:back")])
    return out


def pending_join_detail_kb(idx, kind="verify"):
    """单条在途申请的动作面板：盯题=蓝，重试=绿，返回列表=灰。"""
    rows = []
    if kind == "verify":
        rows.append([Button.inline("📡 继续盯验证消息", f"vja:{idx}".encode(),
                                   style="primary")])
    rows.append([Button.inline("✅ 我过了，重试", f"vjr:{idx}".encode(),
                              style="success")])
    rows.append([Button.inline(BTN["back_list"], b"vj:back")])
    return rows


def report_menu_text():
    return (
        "🚨 举报中心\n\n"
        "👤 举报用户/频道 — 选理由直接举报\n"
        "📋 指定理由举报 — 先选理由再发目标\n"
        "🤖 AI 批量举报 — 拉消息→AI生成理由→多账号齐发\n"
        "⏳ 账号冷却状态 — 查看可用/休息中账号\n\n"
        "⚠️ 举报后账号进入30分钟冷却，防止风控。"
    )