# -*- coding: utf-8 -*-
"""键盘状态化 + iOS 语义色回归自测（不连 TG/PG，纯函数 + 源码级断言）。

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
    # ---------- K1 空闲态：两步（写文案→确认开跑），stage 决定谁标绿 ----------
    k0 = M.campaign_menu_kb(stage=0)
    k1 = M.campaign_menu_kb(stage=1)
    s0, s1 = styles(k0), styles(k1)
    ck("K1 stage0 绿=①写文案", s0[M.BTN["camp_step3"]] == "success", s0)
    ck("K1 stage1 绿=②确认开跑", s1[M.BTN["camp_start"]] == "success", s1)
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
    ck("K2 停止键标红", styles(kr)[M.BTN["stop"]] == "danger", styles(kr))
    ck("K2 只 3 行（控制键精简）", len(kr.rows) == 3, fr)

    # ---------- K3 暂停中：继续键顶上来且标绿 ----------
    kp = M.campaign_menu_kb(running=True, paused=True)
    fp = flat(kp)
    ck("K3 暂停态给「继续」不给「暂停」",
       M.BTN["resume"] in fp and M.BTN["pause"] not in fp, fp)
    ck("K3 继续标绿=下一步", styles(kp)[M.BTN["resume"]] == "success", styles(kp))
    ck("K3 暂停态同样无群发步骤键", all(x not in fp for x in (M.BTN["camp_step3"], M.BTN["camp_start"])), fp)

    # ---------- K4 语义色按动作性质上，不按菜单名 ----------
    sr = styles(M.report_menu_kb())
    ck("K4 停止/删除类=红",
       styles(M.groups_menu_kb())[M.BTN["del_group"]] == "danger"
       and sr[M.BTN["rep_user"]] is None, "删除群该红")
    ck("K4 举报不给绿/红（灰=常规）",
       all(v is None for k, v in sr.items() if k == M.BTN["rep_user"]), sr)
    sk = styles(M.accounts_menu_kb())
    ck("K4 添加账号=绿（主行动）", sk[M.BTN["acc_add"]] == "success", sk)
    ck("K4 导航键一律不上色",
       sk[M.BTN["back"]] is None and s0[M.BTN["back"]] is None, "back 不该有色")

    # ---------- K5 降级键盘：每个键盘都带 .plain 且无颜色 ----------
    for name, kb in [("campaign", k0), ("groups", M.groups_menu_kb()),
                     ("accounts", M.accounts_menu_kb()), ("report", M.report_menu_kb()),
                     ("pool", M.pool_menu_kb()), ("settings", M.settings_menu_kb())]:
        plain = getattr(kb, "plain", None)
        ck("K5 %s 带 .plain" % name, isinstance(plain, ReplyKeyboardMarkup), type(plain))
        ck("K5 %s .plain 文字相同且无色" % name,
           plain is not None and flat(plain) == flat(kb)
           and all(v is None for v in styles(plain).values()), flat(plain) if plain else None)

    # ---------- K6 动态列表键盘也能上色（删除列表整排标红） ----------
    groups = [(1, "锁名单群A", "ua", 5, 0), (2, None, None, 1, 0)]
    kd = M.group_del_kb(groups)
    sd = styles(kd)
    ck("K6 删除列表项全红",
       all(v == "danger" for t, v in sd.items() if t != M.BTN["back_groups"]), sd)
    ck("K6 列表项无 emoji 堆砌、带序号",
       flat(kd)[0].startswith("1 · "), flat(kd))
    ck("K6 标题去换行限长",
       "\n" not in flat(kd)[0] and len(flat(kd)[0]) < 40, flat(kd))

    # ---------- K7 内联键盘走原生 style 字符串（不是 TL 对象） ----------
    # 选群已废弃：group_pick_inline_kb 现在返回空（旧消息上的 gp: 按钮点了只回主菜单）
    gi = M.group_pick_inline_kb(groups)
    ck("K7 内联选群已废弃返回空", gi == [], gi)
    gr = M.group_repull_inline_kb(groups)
    ck("K7 重拉 callback 仍是 gr:", gr[0][0].data == b"gr:1", gr[0][0].data)
    st = M.settings_inline_kb(recent_on=True, repeat_on=False, speed=5, quota=50)
    lab = {b.text: b for row in st for b in row}
    recent_btn = [b for t, b in lab.items() if t.startswith("🕒")][0]
    ck("K7 开关状态用绿/灰表达", getattr(recent_btn.style, "bg_success", False) is True,
       recent_btn.style)
    ck("K7 开关关闭不给绿色",
       not getattr([b for t, b in lab.items() if t.startswith("🔁")][0].style,
                   "bg_success", False), "关不该绿")

    # ---------- K8 源码级：所有群发键盘都必须经过状态判断 ----------
    src = io.open(os.path.join(HERE, "bot.py"), encoding="utf-8").read()
    ck("K8 没有裸调用 campaign_menu_kb()", "campaign_menu_kb()" not in src,
       "有地方绕过了状态判断")
    ck("K8 _camp_kb 读 busy/paused", 'state.get("busy")' in src and 'state.get("paused")' in src, "")
    ck("K8 忙时提示不再让用户找①", "先点「🛑 停止任务」" in src, "")
    # 文案里不能残留已改名的旧按钮（点了找不到）
    stale = ["③ ✅ 确认开跑", "🔙 返回主菜单", "🗣 采发言人(锁名单时用)", "📋 查看进度"]
    for s in stale:
        ck("K8 无旧按钮名残留：%s" % s, s not in src, "bot.py 还在写旧名")

    print()
    print("RESULT pass=%d fail=%d" % (ck.total - len(FAILS), len(FAILS)))
    if FAILS:
        print("KBSTATE_FAILED:", FAILS)
        return 1
    print("KBSTATE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
