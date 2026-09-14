# -*- coding: utf-8 -*-
"""群发 UI 精简验证（老板 09-14：开跑后只发一条消息，带暂停/取消，进度原地精简）。

覆盖：
  A) bot_menu.campaign_ctl_kb：三个阶段的内联键（在跑/暂停/收尾）
  B) sender 真跑：全程只有一条消息（0 次新发），编辑复用同一条，撞限流也不新发，
     收尾摘掉控制键
  C) bot.py 接线（源码级）：开跑消息带内联控制键、控制动作不再追发提示
"""
import asyncio
import inspect
import io
import os
import sys

os.environ.setdefault("API_ID", "123456")
os.environ.setdefault("API_HASH", "abc")
os.environ.setdefault("BOT_TOKEN", "***")
os.environ.setdefault("OWNER_ID", "111")
os.environ.setdefault("DATABASE_URL", "postgresql://u:***@127.0.0.1:5432/none")

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)

FAILS = []


def ck(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else "  << " + str(extra)[:170]))
    if not cond:
        FAILS.append(name)


# ---------------- A) 内联控制键盘 ----------------
import bot_menu as M

kb_run = M.campaign_ctl_kb("run")
kb_paused = M.campaign_ctl_kb("paused")
kb_done = M.campaign_ctl_kb("done")


def flat(rows):
    out = []
    for r in rows or []:
        for b in r:
            out.append((getattr(b, "text", None), getattr(b, "data", None)))
    return out


f_run, f_p = flat(kb_run), flat(kb_paused)
ck("A1 在跑态就一排 2 键（暂停+取消）", len(kb_run) == 1 and len(f_run) == 2, f_run)
ck("A2 在跑态给「暂停」不给「继续」",
   f_run[0][0] == M.BTN["pause"] and M.BTN["resume"] not in [t for t, _ in f_run], f_run)
ck("A3 取消键是内联「✖ 取消」", f_run[0][1] == b"cp:pause" and f_run[1][1] == b"cp:cancel", f_run)
ck("A4 暂停态顶成「继续」", f_p[0][0] == M.BTN["resume"] and f_p[0][1] == b"cp:resume", f_p)
ck("A5 收尾不再挂控制键（点了也没用）", kb_done is None, kb_done)

# ---------------- B) sender 全程只有一条消息 ----------------
import sender as S
from telethon.errors import FloodWaitError

S.db_add_sent = lambda a, u: None
S.db_bump_sent = lambda a: None
S.db_mark_cooldown = lambda *a, **k: None
S.db_clear_cooldown = lambda *a, **k: None
S.db_cooldowns = lambda: {}
_store, _stats = {}, {}
S.db_load_sent = lambda a: _store.setdefault(a, set())
S.db_load_stats = lambda a: _stats.setdefault(a, {"sent_today": 0, "total_sent": 0})
_keep = {k: S.state.get(k) for k in
         ("stop", "paused", "daily_limit", "min_delay", "max_delay",
          "allow_repeat", "parse_mode", "refresh", "live")}
S.state.update({"stop": False, "paused": False, "daily_limit": 9999, "min_delay": 0,
                "max_delay": 0, "allow_repeat": True, "parse_mode": None,
                "refresh": False, "live": None})
S.EDIT_MIN_INTERVAL = 0.0
S.REPORT_INTERVAL = 0.5

SENT_NEW = []          # 任何「新发一条」都记这里（目标：0 次）
NEW_MSG = []           # 重发出去的假消息对象（看之后是否又回到「只改这一条」）


class Flood(FloodWaitError):
    def __init__(self, seconds=1):
        Exception.__init__(self)
        self.seconds = seconds


class Prog:
    """假进度消息：记录每次编辑；按「尝试次数」抛限流（不是成功次数）。"""

    def __init__(self, flood_at=()):
        self.id = 5
        self.chat_id = 111
        self.message = "🚀 群发中"
        self.edits = []
        self.markups = []
        self.tries = 0
        self.flood_at = set(flood_at)

    async def edit(self, text, buttons=None):
        self.tries += 1
        if (self.tries - 1) in self.flood_at:
            raise Flood(1)
        self.edits.append(text)
        self.markups.append(buttons)
        self.message = text
        return self


class Cli:
    def __init__(self, no):
        self.no = str(no)

    async def get_entity(self, e):
        return f"E:{e}"

    async def get_input_entity(self, e):
        return f"I:{e}"

    async def send_message(self, chat, text, buttons=None, **kw):
        SENT_NEW.append(text)
        m = Prog()
        NEW_MSG.append(m)
        return m


async def media(client, entity, text, file=None, image=None):
    return True, None


S.safe_send_media = media
_real_sleep = asyncio.sleep


async def fast(x=0):
    await _real_sleep(0.01 if x else 0)


def accs(n):
    return [(i, Cli(i), f"p{i}") for i in range(1, n + 1)]


def tgs(n):
    return {f"u{i}": {"username": f"nam{i}", "access_hash": i} for i in range(1, n + 1)}


async def main():
    # B1 正常跑完：不新发任何一条，全部编辑在同一条上
    SENT_NEW.clear(); _store.clear(); _stats.clear()
    S.asyncio.sleep = fast
    try:
        p = Prog()
        r = await S.send_to_list_multi(accs(2), tgs(6), "TXT", Cli(0),
                                       bot=Cli(0), msg=p, ctl_kb=M.campaign_ctl_kb)
        ck("B1 全程 0 条新发消息", len(SENT_NEW) == 0, SENT_NEW)
        ck("B2 进度全编辑在同一条消息上", len(p.edits) >= 2, len(p.edits))
        ck("B3 进度文本精简(<=2行/无账号明细)",
           all(len(e.split("\n")) <= 2 for e in p.edits), p.edits[-1:])
        ck("B4 进度条固定 8 格", any(("▱" * 8) in e or ("▰" * 8) in e
                                or ("▰" in e and "▱" in e) for e in p.edits), p.edits[:3])
        ck("B5 收尾摘掉控制键", p.markups[-1] is None, p.markups[-1])
        ck("B6 汇总写回同一条（不是新发）", p.edits[-1] == r and "✅ 群发完成" in r, r[:80])
        ck("B7 跑起来时带内联暂停键",
           any(mb and getattr(mb[0][0], "data", None) == b"cp:pause" for mb in p.markups[:-1]),
           [type(x).__name__ for x in p.markups])

        # B8 编辑撞限流：退避重试，依然不新发
        SENT_NEW.clear(); _store.clear(); _stats.clear()
        p2 = Prog(flood_at={0, 1, 2})   # 开头连拄 3 次限流，之后恢复正常
        r2 = await S.send_to_list_multi(accs(2), tgs(4), "TXT", Cli(0),
                                        bot=Cli(0), msg=p2, ctl_kb=M.campaign_ctl_kb)
        ck("B8 撞 FloodWait 也不新发第二条", len(SENT_NEW) == 0, SENT_NEW)
        ck("B8 限流后仍把汇总改上去了",
           bool(p2.edits) and "✅ 群发完成" in p2.edits[-1], p2.edits[-3:])

        # B9 原消息被删：只重发一条顶上去，之后继续改这一条（不逐条刷屏）
        SENT_NEW.clear(); NEW_MSG.clear(); _store.clear(); _stats.clear()

        class Gone(Prog):
            async def edit(self, text, buttons=None):
                raise RuntimeError("MESSAGE_ID_INVALID: message to edit not found")

        p3 = Gone()
        r3 = await S.send_to_list_multi(accs(2), tgs(4), "TXT", Cli(0),
                                        bot=Cli(0), msg=p3, ctl_kb=M.campaign_ctl_kb)
        ck("B9 消息没了才重发，且只重发 1 条", len(SENT_NEW) == 1, SENT_NEW)
        ck("B9 重发后改回「只编辑那一条」",
           bool(NEW_MSG) and len(NEW_MSG[0].edits) >= 1, [len(x.edits) for x in NEW_MSG])
        ck("B9 汇总在最后那一条上落地",
           bool(NEW_MSG) and "✅ 群发完成" in NEW_MSG[0].edits[-1],
           NEW_MSG[0].edits[-1:] if NEW_MSG else "无新消息")

        # B10 中途暂停：状态并进同一条进度，不另发「已暂停」
        # 用确定性触发：第 2 次真发送时置暂停，假 sleep 跑够次数后自动放行
        # （不靠真实计时，否则 sleep 被快进会抢跑，测出来是假阴性）
        SENT_NEW.clear(); NEW_MSG.clear(); _store.clear(); _stats.clear()
        p4 = Prog()
        CALLS = {"send": 0, "tick": 0}
        base_media = media

        async def media2(client, entity, txt, file=None, image=None):
            CALLS["send"] += 1
            if CALLS["send"] == 2:
                S.state["paused"] = True
                S.state["refresh"] = True
            return await base_media(client, entity, txt, file=file, image=image)

        async def fast2(x=0):
            CALLS["tick"] += 1
            if CALLS["tick"] > 40:
                S.state["paused"] = False
            await _real_sleep(0.001)

        S.safe_send_media = media2
        S.asyncio.sleep = fast2
        try:
            r4 = await S.send_to_list_multi(accs(2), tgs(6), "TXT", Cli(0),
                                            bot=Cli(0), msg=p4, ctl_kb=M.campaign_ctl_kb)
        finally:
            S.safe_send_media = media
            S.asyncio.sleep = _real_sleep
        ck("B10 暂停也不追发消息", len(SENT_NEW) == 0, SENT_NEW)
        ck("B10 暂停状态写进了那条进度里",
           any("已暂停" in e for e in p4.edits), p4.edits[:6])
        ck("B10 暂停时键盘换成「继续」",
           any(mb and getattr(mb[0][0], "data", None) == b"cp:resume" for mb in p4.markups),
           [getattr(x[0][0], "data", None) if x else None for x in p4.markups])
    finally:
        S.asyncio.sleep = _real_sleep
        for k, v in _keep.items():
            S.state[k] = v

    # ---------------- C) bot.py 接线（源码级） ----------------
    B = io.open(os.path.join(D, "bot.py"), encoding="utf-8", newline="\n").read()
    seg = B[B.index("async def _run_and_finish"):]
    seg = seg[:seg.index("# ---------- 举报中心")]
    ck("C1 开跑那条消息就带内联控制键",
       'buttons=campaign_ctl_kb("run")' in seg, seg[:200])
    ck("C2 开跑文本精简（不带间隔参数、不贴主菜单）",
       "间隔" not in seg and "buttons=_main_kb(event)" not in seg, "")
    ck("C3 控制键盘传给 sender", "ctl_kb=campaign_ctl_kb" in seg, "")
    ck("C4 收尾清 live 并释锁", 'state["live"] = None' in seg and "release_list(uid)" in seg, "")

    ctl = B[B.index("async def _camp_ctl"):]
    ctl = ctl[:ctl.index("内联键盘回调")]
    ck("C5 暂停/继续/取消合并成一个口子",
       'action == "cancel"' in ctl and "state[\"refresh\"] = True" in ctl, ctl[:150])
    ck("C6 控制动作不追发提示（旧 _reply 文案已下线）",
       "已暂停（点「继续」恢复）" not in ctl and "▶️ 已继续\", buttons=_camp_kb" not in ctl
       and "await _reply(event, tip" not in ctl, "")
    ck("C7 取消 = 停任务并放名单锁", 'release_list(b.get("uid"))' in ctl, "")
    ck("C8 内联点击只弹 toast", "await event.answer(tip)" in ctl, "")
    ck("C9 底部键旧文案已从 bot 消失",
       '"⏸ 已暂停（点「继续」恢复）"' not in B and '"🛑 已停止当前任务"' not in B, "")

    cb = B[B.index("if data.startswith(\"cp:\")"):] if 'data.startswith("cp:")' in B else ""
    ck("C10 cp: 回调已接进 on_callback", bool(cb) and "_camp_ctl(event, data[3:], via_cb=True)" in cb, "")

    st = B[B.index('elif action == "camp_status"'):]
    st = st[:st.index('elif action == "camp_start"')]
    ck("C11 查看进度在有那条消息时不新发",
       "state[\"refresh\"] = True" in st and "if m is None" in st, st[:200])

    ms = inspect.getsource(S.send_to_list_multi)
    ck("C12 sender 不再「改不动就新发」",
       "进度消息编辑失败，改为新发一条" not in ms, "")
    ck("C13 编辑有节流 + 限流退避", "_throttle_edit()" in ms and "asyncio.sleep(min(int(secs or 0)" in ms, "")
    ck("C14 心跳响应控制点击(refresh)", 'state.get("refresh")' in ms, "")

    # 行尾不能写坏：bot.py 本就是纯 CRLF（CR==LF），LF 文件不该被动成 CRLF
    raw = io.open(os.path.join(D, "bot.py"), encoding="utf-8", newline="").read()
    ck("C15 bot.py 行尾没写坏（纯 CRLF）",
       raw.count("\r\n") == raw.count("\n") > 100, (raw.count("\r"), raw.count("\n")))
    for f in ("sender.py", "bot_menu.py", "config.py"):
        t = io.open(os.path.join(D, f), encoding="utf-8", newline="").read()
        ck("C15 %s 保持 LF" % f, "\r" not in t, t.count("\r"))

    print("\n" + ("CAMPAIGN_UI_OK" if not FAILS else "FAILED: " + "; ".join(FAILS)))
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
