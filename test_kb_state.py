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


def _styled(b):
    """内联按钮到底有没有被上色（telethon 会给个空 style 对象，所以看三个色标志）。"""
    st = getattr(b, "style", None)
    return bool(st) and any(getattr(st, k, False) for k in
                            ("bg_success", "bg_danger", "bg_primary"))


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
                     ("accounts", M.accounts_menu_kb())]:
        vals = styles(kb)
        ck("K4 %s 一个色都不带" % name, all(v is None for v in vals.values()), vals)
    ck("K4 总开关确实是关的", M.IOS_STYLE is False, M.IOS_STYLE)

    def _icls(rows):
        """内联键盘（[[Button,...],...]）-> {文字: 颜是否有色}。"""
        out = {}
        for row in rows:
            for b in row:
                s = getattr(b, "style", None)
                out[b.text] = any(getattr(s, k, False) for k in
                                  ("bg_success", "bg_danger", "bg_primary")) if s else False
        return out

    for name, rows in [("settings", M.settings_inline_kb(speed=5, quota=50)),
                       ("pool", M.pool_menu_kb()),
                       ("pool_del", M.pool_del_kb([("manual", 11, "AAA"),
                                                   ("manual", 12, "BBB")]))]:
        vals = _icls(rows)
        ck("K4 %s(内联) 一个色都不带" % name, not any(vals.values()), vals)

    # ---------- K5 降级键盘：底部键盘都带 .plain 且无颜色 ----------
    for name, kb in [("campaign", k0), ("groups", M.groups_menu_kb()),
                     ("accounts", M.accounts_menu_kb()), ("report", M.report_menu_kb())]:
        plain = getattr(kb, "plain", None)
        ck("K5 %s 带 .plain" % name, isinstance(plain, ReplyKeyboardMarkup), type(plain))
        ck("K5 %s .plain 文字相同且无色" % name,
           plain is not None and flat(plain) == flat(kb)
           and all(v is None for v in styles(plain).values()), flat(plain) if plain else None)

    # ---------- K6 删群（消息附带内联键盘，老板 21:45）：callback 带真实 gid ----------
    groups = [(1, "锁名单群A", "ua", 5, 0), (2, None, None, 1, 0)]
    kd = M.group_del_kb(groups)
    ck("K6 删群键盘是消息附带内联键盘",
       isinstance(kd, list) and isinstance(kd[0], list) and not hasattr(kd, "rows"), type(kd))
    ck("K6 删群 callback 带真实 gid（不再靠序号猜群）",
       kd[0][0].data == b"gd:1" and kd[0][1].data == b"gd:2", [b.data for r in kd for b in r])
    ck("K6 删群标题去换行限长且无色",
       "\n" not in kd[0][0].text and len(kd[0][0].text) < 40
       and not any(_styled(b) for r in kd for b in r), kd[0][0].text)
    ck("K6 删群有返回行", kd[-1][0].data == b"gd:back", kd[-1][0].data)
    kc = M.group_del_confirm_kb(1001)
    ck("K6 二次确认也是内联，callback 带 gid",
       kc[0][0].data == b"gdc:y:1001" and kc[1][0].data == b"gdc:n:1001",
       [b.data for r in kc for b in r])
    src_bot = io.open(os.path.join(HERE, "bot.py"), encoding="utf-8").read()
    ck("K6 bot.py 接上了 gd: / gdc: 两路回调",
       '"gd:"' in src_bot and '"gdc:"' in src_bot, "删群回调没接")
    ck("K6 删群不再走底部文字+序号映射表",
       "del_group_map_by" not in src_bot, "还在用序号映射表（会误删）")
    # 删文案已改内联 callback：靠 msg_id 不靠文字，从根本上不可能误退群
    pd = M.pool_del_kb([("manual", 11, "AAA promo"), ("manual", 12, "BBB")])
    ck("K6 删文案按钮 callback 带真实 msg_id",
       pd[0][0].data == b"pl:d:11" and pd[0][1].data == b"pl:d:12",
       [b.data for r in pd for b in r])
    ck("K6 删文案不再依赖文字正则",
       'm_pool = re.match' not in src_bot, "bot.py 还在用文字匹配删文案")

    # ---------- K7 其余内联键盘 ----------
    # 选群已废弃：group_pick_inline_kb 现在返回空（旧消息上的 gp: 按钮点了只回主菜单）
    gi = M.group_pick_inline_kb(groups)
    ck("K7 内联选群已废弃返回空", gi == [], gi)
    # 重拉成员已整块删除（老板 21:45：补录改为全部群拉成员+读近3天消息）
    ck("K7 重拉成员键盘已从 bot_menu 删除", not hasattr(M, "group_repull_inline_kb"), "还在")
    ck("K7 bot_menu 不再有 regroup 按钮/动作",
       "regroup" not in M.BTN and "regroup_menu" not in list(M.BTN_ACTION.values()),
       [k for k in M.BTN if "regroup" in k])
    st = M.settings_inline_kb(recent_on=True, repeat_on=False, speed=5, quota=50)
    lab = {b.text: b for row in st for b in row}
    ck("K7 设置内联面板开关行只一套",
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

    # ---------- K9 文案池/设置都是「消息附带键盘」（老板 21:25）----------
    pl = [b.text for row in M.pool_menu_kb() for b in row]
    ck("K9 文案池菜单是内联键盘（list of rows）",
       isinstance(M.pool_menu_kb(), list) and isinstance(M.pool_menu_kb()[0], list),
       type(M.pool_menu_kb()))
    ck("K9 文案池键盘=新建/已有/删除+返回",
       pl == [M.BTN["pool_add"], M.BTN["pool_list"], M.BTN["pool_del"],
              M.BTN["back_settings"]], pl)
    ck("K9 文案池键盘不再摆随机轮换/清空",
       M.BTN["pool_random"] not in pl and M.BTN["pool_clear"] not in pl, pl)
    ck("K9 三键名字就是老板说的那三个",
       (M.BTN["pool_add"], M.BTN["pool_list"], M.BTN["pool_del"])
       == ("➕ 新建文案", "📋 已有文案", "🗑 删除文案"),
       (M.BTN["pool_add"], M.BTN["pool_list"], M.BTN["pool_del"]))
    ck("K9 文案池四件事都有 callback",
       [b.data for row in M.pool_menu_kb() for b in row]
       == [b"pl:new", b"pl:list", b"pl:del", b"pl:home"],
       [b.data for row in M.pool_menu_kb() for b in row])
    ck("K9 bot.py 接了 pl:new/pl:list/pl:del/pl:home",
       all(('arg == "%s"' % a) in src_bot for a in ("new", "list", "del", "home")), "回调没接全")

    # 设置：必须是消息附带（内联）键盘，不再发底部 ReplyKeyboard
    ss = M.settings_inline_kb(recent_on=True, repeat_on=True, speed=7, quota=100,
                              parse_label="HTML")
    sl = {b.text: b.data.decode() for row in ss for b in row}
    ck("K9 设置键盘是内联键盘", isinstance(ss, list) and isinstance(ss[0], list), type(ss))
    ck("K9 设置内联带齐四件事",
       all(any(v.startswith(p) for v in sl.values())
           for p in ("st:speed", "st:quota", "st:parse", "st:recent")), list(sl))
    ck("K9 设置内联带当前值", any("7s" in t for t in sl) and any("100" in t for t in sl), list(sl))
    ck("K9 设置内联能进文案池", "st:pool" in sl.values(), list(sl.values()))
    ck("K9 设置内联有回主菜单", "st:home" in sl.values(), list(sl.values()))
    ck("K9 bot.py 的 _settings_kb 已改发内联面板",
       "return settings_inline_kb(" in src_bot, "还在发底部键盘")
    ck("K9 bot.py 不再把 settings_menu_kb 当新键盘用",
       src_bot.count("buttons=_settings_kb()") > 0 and "settings_menu_kb(" not in
       src_bot.split("def _settings_kb")[1].split("def _watch_kb")[0], "设置页还在用底部键盘")

    # ---------- K10 自动补录必须真读满近 3 天（老板 23:00：人不可能这么少）----------
    mw = io.open(os.path.join(HERE, "member_watch.py"), encoding="utf-8").read()
    ck("K10 消息上限不是当初的300（活跃群 300 条cover不住3天）",
       '"MEMBER_SWEEP_LIMIT", "300")' not in mw, "还是 300 条就停")
    ck("K10 是分页读（offset_id 往前翻），不是一遍 iter",
       "offset_id=offset" in mw and "SWEEP_MAX_PAGES" in mw, "没分页")
    ck("K10 只有翻过窗口边界才算读完（full 标记）",
       "full = True" in mw and '"full"' in mw, "没覆盖率概念")
    ck("K10 basic 群能解出实体（先 Channel 再 Chat）",
       "async def _entity_of" in mw and "PeerChat(int(gid))" in mw, "还是只试 PeerChannel")
    ck("K10 拉成员也走 _entity_of（不再直接丢 _peer）",
       "collect_members(client, entity" in mw, "拉成员还在用错的 peer")
    ck("K10 补扫周期是一天（老板：每天发一次就好）",
       '"MEMBER_SWEEP_MIN", "1440")' in mw, "不是每天一轮")
    ck("K10 完成后汇报「读了多少条 + 覆盖到哪天」",
       "_cover_str" in mw and "读了 {read} 条" in mw, "不报覆盖率，无法发现没读满")
    ck("K10 没读满时要显式警告",
       "没读满" in mw, "静默少读")
    ck("K10 bot.py 补扫提示不再写「只读新消息」",
       "只读窗口内的新消息" not in src_bot, "文案还是旧的，误导老板")

    print()
    print("RESULT pass=%d fail=%d" % (ck.total - len(FAILS), len(FAILS)))
    if FAILS:
        print("KBSTATE_FAILED:", FAILS)
        return 1
    print("KBSTATE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
