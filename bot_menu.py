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
    "camp_pool": "📚 从池选",
    "camp_start": "② 确认开跑",
    "camp_status": "📈 查看进度",
    # 成员页被锁时的备用入口（撞锁已会自动接力，这个是手动多翻几条）
    "camp_speakers": "🗣 采发言人",
    # ---- 群管理 ----
    "my_groups": "📋 我的群",
    "add_group": "➕ 加群",
    "batch_import": "📥 批量导入",
    "pending_joins": "📨 在途申请",
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
    "profile_random": "🍉 水果名批改",
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
# ---- 文案池（老板 20:27：只要 新建/已有/删除，全部走消息键盘）----
    "pool": "📚 文案池",
    "pool_add": "➕ 新建文案",
    "pool_list": "📋 已有文案",
    "pool_del": "🗑 删除文案",
    "pool_use": "🚀 选一条去群发",
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
    BTN["camp_pool"]: "camp_pool",
    BTN["camp_start"]: "camp_start",
    BTN["camp_status"]: "camp_status",
    BTN["camp_speakers"]: "camp_speakers_prompt",
    BTN["my_groups"]: "my_groups",
    BTN["add_group"]: "add_group_prompt",
    BTN["batch_import"]: "batch_import_prompt",
    BTN["pending_joins"]: "pending_joins",
    BTN["del_group"]: "del_group_menu",
    BTN["watch"]: "watch_status",
    BTN["watch_now"]: "watch_now",
    BTN["watch_status"]: "watch_status",
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
    BTN["pool_del"]: "pool_del_menu",
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
    "profile_bio_prompt",  # 水果名批改：先问简介（可跳过）
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
    "pool_add_prompt": "请发送要新建的文案内容（一次一条，存进文案池）：",
    "profile_bio_prompt": ("请发送【简介】内容（每个号都改成这条，70 字以内）：\n"
                           "发「跳过」= 只改名字，不动简介。"),
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
    "camp_pool": "primary",
    "camp_speakers_prompt": "primary",
    "resume": "primary",
    "profile_random": "primary",
    "acc_edit_profile_prompt": "primary",
    "vja": "primary",              # 📡 继续盯验证消息
    # 红：不可逆 / 停当前任务
    "stop": "danger",
    "del_group_menu": "danger",
    "pool_clear": "danger",
    "watch_now": "primary",
    "watch_status": "primary",
    # 其余（查看/刷新/导航）保持默认灰，灰就是「不抢眼睛」
}

_STYLE_ATTR = {"success": "bg_success", "danger": "bg_danger", "primary": "bg_primary"}
_STYLE_CACHE = {}

# 万一服务端不认这个新字段，关掉它就退回纯文字键盘（不会断发消息）
# 老板 20:27「把键盘颜色改回去」：颜色方案（iOS 语义色）全部下线，
# 这里关死；下面即使有人传了 styles 也不会生效，回到默认灰键盘。
IOS_STYLE = False


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
    """群发运营键盘（底部键盘）：**只给此刻真能按的键**。
    按录入时间从新到旧发。所以只剩两步：① 文案（手打 / 从池选）→ ② 确认开跑。
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
        (BTN["camp_step3"], BTN["camp_pool"]),
        (BTN["camp_start"],),
        (BTN["camp_status"],),
        (BTN["back"],),
    ]
    hot = BTN["camp_step3"] if stage == 0 else BTN["camp_start"]
    return _kb(rows, {hot: "success"})


def campaign_ctl_kb(phase="run"):
    """进度消息自带的内联控制键盘：暂停/继续 + 取消，就这一排。

    老板要求：开跑后只有一条消息，控制按钮挂在这条消息上，点一次只弹
    toast，不追发消息。phase：run=在发（给暂停）　paused=已暂停（给继续）
    done=收尾（不再给可点的键，防点空）。"""
    if phase == "done":
        return None
    paused = phase == "paused"
    go = BTN["resume"] if paused else BTN["pause"]
    act = "resume" if paused else "pause"
    return [[Button.inline(go, f"cp:{act}".encode()),
             Button.inline(BTN["cancel_pick"], b"cp:cancel")]]


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
    """系统设置（消息附带内联键盘）：老板 21:25「我要的是消息附带键盘，不是底部键盘」。

    开关用文字 开/关 表示，数字项用 +/- 步进；不靠颜色表达状态。
    补录相关不在这上面：那个完全自动跑，不给开关也不给窗口设置。
    """
    def _sw(on):
        return "🟢 开" if on else "⚪ 关"
    sp = f"⏱ 发送间隔　{speed}s" if speed else "⏱ 发送间隔"
    q = f"🎯 每日上限　{quota}" if quota else "🎯 每日上限"
    return [
        _row(Button.inline(sp, b"st:speed:show")),
        _row(Button.inline("−1", b"st:speed:-1"),
             Button.inline("＋1", b"st:speed:+1"),
             Button.inline("＋5", b"st:speed:+5"),
             Button.inline("＋10", b"st:speed:+10")),
        _row(Button.inline(q, b"st:quota:show")),
        _row(Button.inline("−10", b"st:quota:-10"),
             Button.inline("＋10", b"st:quota:+10"),
             Button.inline("＋50", b"st:quota:+50"),
             Button.inline("＋100", b"st:quota:+100")),
        _row(Button.inline(f"¶ 文本模式　{parse_label}", b"st:parse")),
        _row(Button.inline(f"🕒 近7天活跃　{_sw(recent_on)}", b"st:recent"),
             Button.inline(f"🔁 重复推广　{_sw(repeat_on)}", b"st:repeat")),
        _row(Button.inline(BTN["pool"], b"st:pool")),
        _row(Button.inline(BTN["back"], b"st:home")),
    ]


def groups_menu_kb(is_operator: bool = False):
    """群管理：只看/进/盯/删四件事，三行功能键 + 返回。

    减负记录：「批量导入」已并进「加群」（发一条=加一个，发多行=批量），
    「自动补录」已改成全自动（定期拉成员 + 读窗口内消息），所以上一层只留四个入口。
    """
    return _kb([
        (BTN["my_groups"], BTN["add_group"]),
        (BTN["watch"], BTN["pending_joins"]),
        (BTN["del_group"],),
        (BTN["back"],),
    ])


def watch_menu_kb():
    """成员补录子菜单：全自动不给开关，只留「立即补扫 / 看情况」。"""
    return _kb([
        (BTN["watch_now"],),
        (BTN["watch_status"],),
        (BTN["back_groups"],),
    ])


def group_del_kb(groups):
    """删除群键盘：**消息附带内联键盘**（老板 21:45），callback 直接带真实 gid（gd:<gid>）。

    不再靠按钮文字正则匹配 → 不依赖序号顺序，列表变了也不会误删。
    """
    rows = []
    cur = []
    for g in groups:
        gid = int(g[0] or 0)
        cur.append(Button.inline(f"🗑 {_clean_title(g, 22)}", f"gd:{gid}".encode()))
        if len(cur) == 2:
            rows.append(cur)
            cur = []
    if cur:
        rows.append(cur)
    rows.append([Button.inline(BTN["back_groups"], b"gd:back")])
    return rows


def group_del_confirm_kb(pending_gid=0):
    """删除群二次确认（也是消息附带内联键盘）：确认/取消各一行，callback 带 gid。"""
    return [
        [Button.inline(BTN["del_confirm"], f"gdc:y:{int(pending_gid or 0)}".encode())],
        [Button.inline(BTN["del_cancel"], f"gdc:n:{int(pending_gid or 0)}".encode())],
    ]


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
    """（已废弃：设置改回消息附带内联键盘）保留签名以防旧调用点。"""
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
    """文案池菜单（消息附带内联键盘）：新建 / 已有 / 选一条去群发 / 删除。"""
    return [
        _row(Button.inline(BTN["pool_add"], b"pl:new"),
             Button.inline(BTN["pool_list"], b"pl:list")),
        _row(Button.inline(BTN["pool_use"], b"pl:use")),
        _row(Button.inline(BTN["pool_del"], b"pl:del")),
        _row(Button.inline(BTN["back_settings"], b"pl:home")),
    ]


def pool_use_kb(items):
    """「选一条去群发」键盘：每条一个按钮，回调带真实 msg_id（pl:u:<id>）。

    items: [(source, msg_id, text), ...]，与池列表同序编号。
    """
    btns, cur = [], []
    for i, (_src, mid, t) in enumerate(items, 1):
        head = " ".join(str(t or "").split())[:14] or "（空）"
        cur.append(Button.inline(f"{i} · {head}",
                                 ("pl:u:%d" % int(mid)).encode()))
        if len(cur) == 2:
            btns.append(cur)
            cur = []
    if cur:
        btns.append(cur)
    btns.append([Button.inline(BTN["cancel_pick"], b"pl:back")])
    return btns


def pool_del_kb(items):
    """删除文案（消息附带内联键盘）：每条一个按钮，回调直接带真实 msg_id。

    走 callback 不靠按钮文字正则匹配，所以不会出现「删文案误触发退群」那种撞车。
    items: [(source, msg_id, text), ...]，按传入顺序编号。
    """
    btns, cur = [], []
    for i, (_src, mid, t) in enumerate(items, 1):
        head = " ".join(str(t or "").split())[:12] or "（空）"
        cur.append(Button.inline(f"🗑 {i} · {head}",
                                 ("pl:d:%d" % int(mid)).encode()))
        if len(cur) == 2:
            btns.append(cur)
            cur = []
    if cur:
        btns.append(cur)
    btns.append([Button.inline(BTN["cancel_pick"], b"pl:back")])
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

    def _b(label, key):
        mark = "▸ " if cur == key else "　 "
        return Button.inline(mark + label, f"db:{key}".encode())

    return [
        _row(_b("👤 每人统计", "who")),
        _row(_b("❗ 未发送", "un")),
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
    """主菜单：一行身份，一行状态+数据。"""
    if role == "owner":
        tag = name or "admin"
    else:
        tag = ("操作员：" + name) if name else "操作员"
    lock = f"｜占用：{busy_tip}" if busy and busy_tip else ""
    run_state = "⏳ 任务中" if busy else "🟢 空闲"     # 3.11: 反斜杠不能进 f-string 表达式
    op_note = "\nℹ️ 增删操作员需找admin。" if role != "owner" else ""
    return (f"📋 控制面板｜{tag}\n"
            f"{run_state}{lock}｜👤{len(accounts)} 📁{groups_count} "
            f"📨未发{unsent_count} 📝池{pool_count}" + op_note)


def campaign_menu_text():
    """群发运营：两步说完（① 文案有两个入口：手打 / 从池选）。"""
    return ("🚀 群发运营｜① 文案（✏️ 手打 或 📚 从池选）→ ② 确认开跑\n"
            "默认打所有群的人（新的先发），发过的自动跳过。")


def groups_menu_text(is_operator: bool = False):
    return ("\U0001f4e5 \u7fa4\u7ba1\u7406\n"
            "\u2795 \u52a0\u7fa4 \u2014 \u53d1\u94fe\u63a5\u52a0\u7fa4\uff0c\u591a\u884c=\u6279\u91cf\u5bfc\u5165\n"
            "\U0001f4e1 \u8865\u5f55\u5168\u81ea\u52a8 \u00b7 \U0001f4e8 \u5728\u9014=\u7b49\u6279\u51c6/\u9a8c\u8bc1\n"
            "\U0001f5d1 \u5220\u7fa4 \u2014 \u5148\u9000\u7fa4\u518d\u5220\u8bb0\u5f55\uff0c\u4e0d\u53ef\u9006")


def watch_menu_text():
    """成员补录子菜单：常驻自动、没得关。"""
    return ("\U0001f4e1 \u81ea\u52a8\u8865\u5f55\uff5c\u5e38\u9a7b\u81ea\u52a8\uff0c\u4e0d\u7528\u4eba\u70b9\n"
            "\u6bcf\u8f6e\uff1a\u62c9\u7fa4\u6210\u5458\uff08\u8fd1 3 \u5929\u8fdb\u7fa4\u7684\uff09+ \u8bfb\u8fd1 3 \u5929\u6d88\u606f\u6536\u53d1\u8a00\u4eba\n"
            "\u5e73\u65f6\u53d1\u8a00/\u8fdb\u7fa4\u5f53\u573a\u5165\u540d\u5355\uff0c\u53ea\u8ffd\u52a0\u4e0d\u6e05\u7a7a\u3002")


def accounts_menu_text():
    return ("👥 账号管理｜列表 / 添加 / 改资料 / 过滤 / 重置冷却\n"
            "🔑 加群号自动定（在群最多的那个号）：只加群、不参与群发\n"
            "🧊 重置冷却 = 清限流记账，冷却中的号立刻恢复派活")


def profile_menu_text():
    return ("📝 批量改资料\n"
            "🍉 水果名批改：姓=随机水果，名=主页万人做单群（简介可自定义）\n"
            "✏️ 统一名字：所有号同一个名。每号隔 2 秒防风控。")


def settings_menu_text(recent_on=None, repeat_on=None):
    """设置页：当前值已标在按钮上，不再重复念开关。"""
    return ("\u2699\ufe0f 系统设置\uff5c\u70b9\u6309\u94ae\u8f93\u6570\u5b57\uff0c\u5f53\u524d\u503c\u5c31\u6807\u5728\u952e\u4e0a\uff0c\u6539\u5b8c\u5373\u751f\u6548\n"
            "\u00b6 文\u672c\u6a21\u5f0f\uff1a\u70b9\u4e00\u4e0b\u5207 \u7eaf\u6587\u672c \u2192 HTML \u2192 Markdown")


def pool_menu_text(count=0, random_on=False):
    """文案池菜单文本（一行版）。random_on 仅为旧调用兼容。"""
    return ("\U0001f4da 文\u6848\u6c60\uff5c%d 条\n"
            "\u2795 新\u5efa\uff08发一条存一条\uff09\u00b7 \U0001f4cb 已有 \u00b7 \U0001f5d1 删除" % count)


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
            rows.append([Button.url("🔗 打开群", url=url)])
    rows.append([Button.inline(BTN["cancel_pick"], f"vr:{iid}:x".encode())])
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
        rows.append([Button.inline("📡 继续盯验证消息", f"vja:{idx}".encode())])
    rows.append([Button.inline("✅ 我过了，重试", f"vjr:{idx}".encode())])
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
