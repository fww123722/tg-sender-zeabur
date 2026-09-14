# -*- coding: utf-8 -*-
"""键盘状态化回归自测（不连 TG/PG，纯函数 + 源码级断言）。

老板 20:27「把键盘颜色改回去」：iOS 语义色全部下线，
所以 K1/K2/K3/K4/K6/K7 现在钉的是「一个色都不许带」，不是「谁该绿」。
颜色表 STYLE_BY_ACTION 仍留着（改回 True 就能用），只是不再生效。

跑法：python test_kb_state.py

为什么要这份测试：老板 12:58「底部按钮还是麻烦，说不出来」。
根因不是丑，是**键盘不看状态**——跑一半时还摆着「① 选群」，
而①会清空名单，误点一次就是事故。所以「跑起来必须没有①」这条要钉住。
"""
import asyncio
import io
import os
import sys

os.environ.setdefault("API_ID", "123456")
os.environ.setdefault("API_HASH", "abc")
os.environ.setdefault("BOT_TOKEN", "***")
os.environ.setdefault("OWNER_ID", "111")
os.environ.setdefault("DATABASE_URL", "postgresql://u:***@127.0.0.1:5432/none")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bot_menu as M  # noqa: E402
from telethon.tl.types import ReplyKeyboardMarkup  # noqa: E402

FAILS = []


def ck(name, cond, extra=""):
    ck.total += 1
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else "  << " + str(extra)[:200]))
    if not cond:
        FAILS.append(name)


ck.total = 0


def flat(kb):
    """键盘 -> 按钮文字列表。"""
    return [b.text for row in kb.rows for b in row.buttons]


def styles(kb):
    """键盘 -> {文字: 语义色名或 None}。"""
    out = {}
    for row in kb.rows:
        for b in row.buttons:
            st = getattr(b, "style", None)
            tag = None
            if st is not None:
                if getattr(st, "bg_success", False):
                    tag = "success"
                elif getattr(st, "bg_danger", False):
                    tag = "danger"
                elif getattr(st, "bg_primary", False):
                    tag = "primary"
            out[b.text] = tag
    return out


def main():
    # ---------- K1 空闲态：两步（写文案→确认开跑），颜色已全关 ----------
    k0 = M.campaign_menu_kb(stage=0)
    k1 = M.campaign_menu_kb(stage=1)
    s0, s1 = styles(k0), styles(k1)
    ck("K1 颜色已改回去（stage0 全无色）", all(v is None for v in s0.values()), s0)
    ck("K1 颜色已改回去（stage1 全无色）", all(v is None for v in s1.values()), s1)
    ck("K1 两态排版完全一致（按钮不跳位）",
       flat(k0) == flat(k1), (flat(k0), flat(k1)))
    ck("K1 空闲态不给停止键（没得可停）", M.BTN["stop"] not in flat(k0), flat(k0))
    ck("K1 空闲态仍有查看进度键", M.BTN["camp_status"] in flat(k0), flat(k0))

    # ---------- K2 运行中：绝不能出现①选群（它会清空名单） ----------
    kr = M.campaign_menu_kb(running=True)
    fr = flat(kr)
    ck("K2 运行中无群发步骤键", all(x not in fr for x in (M.BTN["camp_step3"], M.BTN["camp_start"])), fr)
    ck("K2 运行中无②写文案", M.BTN["camp_step3"] not in fr, fr)
    ck("K2 运行中无③确认开跑", M.BTN["camp_start"] not in fr, fr)
    ck("K2 运行中给暂停+停止+进度",
       all(x in fr for x in (M.BTN["pause"], M.BTN["stop"], M.BTN["camp_status"])), fr)
    ck("K2 停止键也不带颜色", styles(kr)[M.BTN["stop"]] is None, styles(kr))
    ck("K2 只 3 行（控制键精简）", len(kr.rows) == 3, fr)

    # ---------- K3 暂停中：继续键顶上来且标绿 ----------
    kp = M.campaign_menu_kb(running=True, paused=True)
    fp = flat(kp)
    ck("K3 暂停态给「继续」不给「暂停」",
       M.BTN["resume"] in fp and M.BTN["pause"] not in fp, fp)
    ck("K3 继续键不带颜色", styles(kp)[M.BTN["resume"]] is None, styles(kp))
    ck("K3 暂停态同样无群发步骤键", all(x not in fp for x in (M.BTN["camp_step3"], M.BTN["camp_start"])), fp)

    # ---------- K4 全键盘零颜色（老板要求改回去）----------
    for name, kb in [("groups", M.groups_menu_kb()), ("report", M.report_menu_kb()),
                     ("accounts", M.accounts_menu_kb()), ("settings", M.settings_menu_kb()),
                     ("pool", M.pool_menu_kb())]:
        vals = styles(kb)
        ck("K4 %s 一个色都不带" % name, all(v is None for v in vals.values()), vals)
    ck("K4 总开关确实是关的", M.IOS_STYLE is False, M.IOS_STYLE)

    # ---------- K5 降级键盘：每个键盘都带 .plain 且无颜色 ----------
    for name, kb in [("campaign", k0), ("groups", M.groups_menu_kb()),
                     ("accounts", M.accounts_menu_kb()), ("report", M.report_menu_kb()),
                     ("pool", M.pool_menu_kb()), ("settings", M.settings_menu_kb())]:
        plain = getattr(kb, "plain", None)
        ck("K5 %s 带 .plain" % name, isinstance(plain, ReplyKeyboardMarkup), type(plain))
        ck("K5 %s .plain 文字相同且无色" % name,
           plain is not None and flat(plain) == flat(kb)
           and all(v is None for v in styles(plain).values()), flat(plain) if plain else None)

    # ---------- K6 删除列表：文字格式必须和 bot.py 的正则对上（否则点了没反应）----------
    import re as _re
    groups = [(1, "锁名单群A", "ua", 5, 0), (2, None, None, 1, 0)]
    kd = M.group_del_kb(groups)
    ck("K6 删群项格式 = 🗑 N · 标题", flat(kd)[0].startswith("🗑 1 · "), flat(kd))
    ck("K6 删群标题去换行限长",
       "\n" not in flat(kd)[0] and len(flat(kd)[0]) < 40, flat(kd))
    pd = M.pool_del_kb([("manual", 11, "AAA promo"), ("manual", 12, "BBB")])
    ck("K6 删文案项格式 = 🗑 文案 N · 开头", flat(pd)[0].startswith("🗑 文案 1 · "), flat(pd))
    src_bot = io.open(os.path.join(HERE, "bot.py"), encoding="utf-8").read()
    g_re = _re.search(r'm_del = re\.match\(r"(.*?)",', src_bot)
    p_re = _re.search(r'm_pool = re\.match\(r"(.*?)",', src_bot)
    ck("K6 bot.py 两个删除正则都在", bool(g_re) and bool(p_re), (g_re, p_re))
    if g_re and p_re:
        ck("K6 删群正则匹配得上删群按钮",
           _re.match(g_re.group(1), flat(kd)[0]) is not None, (g_re.group(1), flat(kd)[0]))
        ck("K6 删文案正则匹配得上删文案按钮",
           _re.match(p_re.group(1), flat(pd)[0]) is not None, (p_re.group(1), flat(pd)[0]))
        # 两条正则必须互斥，不然删文案会被当成删群（=退群事故）
        ck("K6 删群正则不会误吃删文案按钮",
           _re.match(g_re.group(1), flat(pd)[0]) is None, "撞车：会去退群!")
        ck("K6 删文案正则不会误吃删群按钮",
           _re.match(p_re.group(1), flat(kd)[0]) is None, "撞车")

    # ---------- K7 内联键盘走原生 style 字符串（不是 TL 对象） ----------
    # 选群已废弃：group_pick_inline_kb 现在返回空（旧消息上的 gp: 按钮点了只回主菜单）
    gi = M.group_pick_inline_kb(groups)
    ck("K7 内联选群已废弃返回空", gi == [], gi)
    gr = M.group_repull_inline_kb(groups)
    ck("K7 重拉 callback 仍是 gr:", gr[0][0].data == b"gr:1", gr[0][0].data)
    st = M.settings_inline_kb(recent_on=True, repeat_on=False, speed=5, quota=50)
    lab = {b.text: b for row in st for b in row}

    def _color(b):
        """telethon 默认就给一个空 style 对象，只看三个色标志是否全假。"""
        s = getattr(b, "style", None)
        return any(getattr(s, k, False) for k in ("bg_success", "bg_danger", "bg_primary"))

    ck("K7 旧内联面板也不再上色",
       not any(_color(b) for b in lab.values()),
       {t: _color(b) for t, b in lab.items()})
    ck("K7 旧内联面板不重复摆开关（只一套开关行）",
       sum(1 for t in lab if t.startswith("🔁")) == 1, list(lab))

    # ---------- K8 源码级：所有群发键盘都必须经过状态判断 ----------
    src = io.open(os.path.join(HERE, "bot.py"), encoding="utf-8").read()
    ck("K8 没有裸调用 campaign_menu_kb()", "campaign_menu_kb()" not in src,
       "有地方绕过了状态判断")
    ck("K8 _camp_kb 读 busy/paused", 'state.get("busy")' in src and 'state.get("paused")' in src, "")
    ck("K8 忙时提示不再让用户找①", "先点「🛑 停止任务」" in src, "")
    # 文案里不能残留已改名的旧按钮（点了找不到）
    stale = ["③ ✅ 确认开跑", "🔙 返回主菜单", "🗣 采发言人(锁名单时用)", "📋 查看进度",
             "➕ 加文案", "📋 看文案", "🔀 随机轮换"]
    for s in stale:
        ck("K8 无旧按钮名残留：%s" % s, s not in src, "bot.py 还在写旧名")

    # ---------- K9 文案池只剩三件事（老板 20:27）----------
    fp_ = flat(M.pool_menu_kb())
    ck("K9 文案池键盘=新建/已有/删除+返回",
       fp_ == [M.BTN["pool_add"], M.BTN["pool_list"], M.BTN["pool_del"],
               M.BTN["back_settings"]], fp_)
    ck("K9 文案池键盘不再摆随机轮换/清空",
       M.BTN["pool_random"] not in fp_ and M.BTN["pool_clear"] not in fp_, fp_)
    ck("K9 三键名字就是老板说的那三个",
       (M.BTN["pool_add"], M.BTN["pool_list"], M.BTN["pool_del"])
       == ("➕ 新建文案", "📋 已有文案", "🗑 删除文案"),
       (M.BTN["pool_add"], M.BTN["pool_list"], M.BTN["pool_del"]))
    ck("K9 文案池菜单动作已接 pool_del_menu",
       M.BTN_ACTION[M.BTN["pool_del"]] == "pool_del_menu"
       and '"pool_del_menu"' in src, M.BTN_ACTION[M.BTN["pool_del"]])
    ck("K9 已有文案不再挂内联按钮", "pool_inline_kb(rows) if rows" not in src, "还在挂")
    # 设置页：全部走消息键盘（底部），不靠内联开关
    fs = flat(M.settings_menu_kb())
    ck("K9 设置键盘是消息键盘且含四大项",
       all(x in fs for x in (M.BTN["set_speed"], M.BTN["set_quota"],
                             M.BTN["set_parsemode"], M.BTN["back"])), fs)
    ck("K9 设置不超 4 行（老板：子菜单别超 3 行）", len(M.settings_menu_kb().rows) <= 4, fs)

    print()
    print("RESULT pass=%d fail=%d" % (ck.total - len(FAILS), len(FAILS)))
    if FAILS:
        print("KBSTATE_FAILED:", FAILS)
        return 1
    print("KBSTATE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
