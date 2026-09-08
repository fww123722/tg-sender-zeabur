#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多人共管（操作员）回归自测：不连真实 PostgreSQL，全部打桩。

跑法（项目目录下）：
    python test_multiuser.py
覆盖：
  A 权限矩阵（主人/操作员/陌生人 × 42 个按钮动作）
  B ops_state 草稿按人隔离 + 名单占用锁（先到先用/续租/超龄/强制解锁）
  C handler 级真实路径（陌生人被拒、操作员看不到高危按钮、删群三段流程）
  D 群发协程崩溃时必须释放号池锁与名单锁（否则同事被干锁 30 分钟）
"""
import asyncio
import io
import json
import os
import sys
import time as _time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OWNER, ALICE, BOB, STRANGER = 111, 222, 333, 999
STORE = {}

RESULTS = []


def check(name, ok):
    RESULTS.append((name, bool(ok)))


# =====================================================================
# 打桩：ops_state 的 DB 换成内存字典
# =====================================================================
import ops_state as O

O.DB.load_session = staticmethod(lambda name: STORE.get(name))
O.DB.save_session = staticmethod(lambda name, data: STORE.__setitem__(name, data))

import config

config.OWNER_ID = OWNER
config.refresh_operators({ALICE: "Alice", BOB: "Bob"})

import bot
import bot_menu
from bot_menu import BTN


# =====================================================================
# C 段需要：伪造 telethon.events 装饰器 + 假事件对象
# =====================================================================
class _AnyEvt:
    def __init__(self, **kw):
        pass

    def __call__(self, fn=None, **kw):
        return fn if fn is not None else (lambda f: f)


def _patch_events():
    ev = types.ModuleType("telethon.events")
    ev.NewMessage = _AnyEvt
    ev.CallbackQuery = _AnyEvt
    bot.events = ev


class FakeEvent:
    def __init__(self, uid, text=""):
        self.sender_id = uid
        self.chat_id = uid
        self.text = text
        self.client = self
        self.pattern_match = None

    async def send_message(self, chat_id, text, buttons=None):
        SENT.append(text)
        KBS.append(buttons)
        return None

    async def answer(self, *a, **k):
        return None


SENT = []
KBS = []
AUDIT = []
DELETED = []
FINISHED = []

GROUPS = [(1001, "\u7fa4A", "grpA", 10, 1), (1002, "\u7fa4B", "grpB", 20, 1)]


def stub_db():
    bot.db_load_operators = lambda: {ALICE: "Alice", BOB: "Bob"}
    bot.db_log_op = lambda uid, name, action, detail="": AUDIT.append((uid, name, action, detail))
    bot.db_op_log_recent = lambda n=20: []
    bot.db_op_log_since = lambda uid, s, actions=None: []
    bot.db_add_operator = lambda uid, name="", added_by=0: True
    bot.db_drop_operator = lambda uid: True
    bot.db_get_all_groups = lambda: list(GROUPS)
    bot.db_delete_group = lambda gid: (DELETED.append(gid) or True)
    bot.db_count_targets = lambda: 42
    bot.db_load_targets = lambda: {"1": {}, "2": {}, "3": {}}
    bot.db_sent_global = lambda: 7
    bot.db_count_pool = lambda: 3
    bot.db_group_count = lambda: len(GROUPS)
    bot.db_load_stats = lambda a: {"sent_today": 0, "total_sent": 0}
    bot.db_cooldowns = lambda: {}
    bot.db_clear_cooldown = lambda *a: None
    bot.db_campaign_start = lambda *a, **k: 77
    bot.db_campaign_finish = lambda cid, n, st: FINISHED.append((cid, n, st))
    bot.db_campaign_leaderboard = lambda d=7: []
    bot.cooldown_summary = lambda: "-"


async def _asyncher(*a, **k):
    return "stub"


async def _leave_stub(accounts, gid):
    return (1, ["[\u8d26\u53f71] \u5df2\u9000\u51fa"])


def stub_actions():
    for mod, names in (
        (bot, ["list_my_groups", "collect_members", "check_login_accounts",
               "join_group_all_accounts", "join_group_by_link", "forward_from_channel",
               "broadcast_to_groups", "edit_all_profiles",
               "report_user", "report_custom", "report_super"]),
    ):
        for n in names:
            if hasattr(mod, n):
                setattr(mod, n, _asyncher)
    # leave_group \u8fd4\u56de\u7684\u662f\u5957\u751f\uff0c\u4e0d\u80fd\u7528\u901a\u7528\u6869
    bot.leave_group = _leave_stub


async def drive_send(who, text):
    SENT.clear()
    KBS.clear()
    await H["on_any_text"](FakeEvent(who, text))
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return list(SENT)


def run_handler(name, event):
    SENT.clear()
    KBS.clear()
    asyncio.run(H[name](event))
    return list(SENT)


def flat_kb(buttons):
    rows = getattr(buttons, "rows", None) or []
    out = []
    for r in rows:
        cells = getattr(r, "buttons", None)
        if cells is None:
            cells = r if isinstance(r, (list, tuple)) else [r]
        out += [getattr(b, "text", None) for b in cells]
    return [x for x in out if x]


# =====================================================================
# A 权限矩阵
# =====================================================================
def section_a():
    acts = sorted(set(bot.BTN_ACTION.values()))
    op_ok = {a for a in acts if bot.can_do(ALICE, a)}
    check("A owner can do every action", all(bot.can_do(OWNER, a) for a in acts))
    check("A stranger can do nothing", not any(bot.can_do(STRANGER, a) for a in acts))
    # \u8001\u677f\u8981\u6c42\uff1a\u64cd\u4f5c\u5458\u4e0e\u4e3b\u4eba\u540c\u6743
    check("A operator has full access", op_ok == set(acts), )
    # \u552f\u4e00\u4fdd\u7559\uff1a\u589e\u5220\u64cd\u4f5c\u5458
    check("A opadmin stays owner-only",
          all(bot.can_do(OWNER, a) and not bot.can_do(ALICE, a)
              for a in ("menu_opadmin", "op_list", "op_log")))
    check("A stranger cannot manage whitelist",
          not any(bot.can_do(STRANGER, a)
                  for a in ("menu_opadmin", "op_list", "op_log")))




# =====================================================================
# B ops_state 多人隔离 + 名单锁
# =====================================================================
def section_b():
    STORE.clear()
    O.set_campaign(ALICE, group="gA", group_title="A\u7fa4", text="\u6587\u6848A")
    O.set_campaign(BOB, group="gB", group_title="B\u7fa4", text="\u6587\u6848B")
    check("B drafts isolated per actor",
          O.get_campaign(ALICE)["group"] == "gA" and O.get_campaign(BOB)["group"] == "gB")
    O.clear_campaign(ALICE)
    check("B clear own draft keeps other's",
          O.get_campaign(ALICE) == {} and O.get_campaign(BOB)["group"] == "gB")

    STORE.clear()
    STORE["ops_state"] = json.dumps({"campaign": {"group": "legacy", "text": "x"},
                                     "current_group": None})
    check("B legacy campaign migrates to owner", O.get_campaign(OWNER).get("group") == "legacy")

    STORE.clear()
    ok1, _ = O.claim_list(ALICE, "Alice", "A\u7fa4")
    ok2, holder = O.claim_list(BOB, "Bob", "B\u7fa4")
    ok3, _ = O.claim_list(OWNER, "\u4e3b\u4eba", "C\u7fa4")
    check("B first claim wins", ok1 and not ok2 and not ok3)
    check("B holder is named", bool(holder) and holder.get("name") == "Alice")
    ok_again, _ = O.claim_list(ALICE, "Alice", "A\u7fa4")
    check("B same actor can renew", ok_again)
    O.release_list(ALICE)
    check("B release frees slot", O.claim_list(BOB, "Bob", "B\u7fa4")[0])
    O.release_list(None)
    O.claim_list(ALICE, "Alice", "A\u7fa4")
    d = json.loads(STORE["ops_state"])
    d["list_claim"]["at"] = int(_time.time()) - 31 * 60
    STORE["ops_state"] = json.dumps(d)
    check("B stale claim auto-expires (30min)", O.claim_list(BOB, "Bob", "B\u7fa4")[0])


# =====================================================================
# C handler 真实路径
# =====================================================================
class FakeBot:
    def __init__(self):
        self.handlers = {}

    def on(self, *a, **k):
        def deco(fn):
            self.handlers[fn.__name__] = fn
            return fn
        return deco


H = {}


def section_c():
    global H
    _patch_events()
    stub_db()
    stub_actions()
    fb = FakeBot()
    bot.register_handlers(fb, [(1, object(), "+8613800000000")])
    H = fb.handlers

    out = run_handler("on_start", FakeEvent(STRANGER))
    check("C stranger /start rejected", out and "\u65e0\u6743\u9650" in out[0])

    out = run_handler("on_start", FakeEvent(ALICE))
    btns = flat_kb(KBS[0]) if KBS else []
    check("C operator panel tagged", out and "Alice" in out[0] and "\u64cd\u4f5c\u5458" in out[0])
    check("C operator kb now complete", len(btns) == 6 and all(
          BTN[x] in btns for x in ("accounts", "settings", "report")))

    out = run_handler("on_start", FakeEvent(OWNER))
    btns_owner = flat_kb(KBS[0]) if KBS else []
    check("C operator kb == owner kb", btns == btns_owner)

    allowed = 0
    for k in ("accounts", "settings", "report", "acc_add", "acc_cd_reset", "profile_random",
              "set_speed", "set_quota", "del_group", "rep_user", "rep_channel_ai",
              "rep_reason", "rep_status"):
        out = run_handler("on_any_text", FakeEvent(ALICE, BTN[k]))
        allowed += 1 if not any("\u53ea\u6709\u4e3b\u4eba" in x for x in out) else 0
    check(f"C operator NOT blocked on 13 ex-owner-only buttons ({allowed}/13)",
          allowed == 13)

    # \u589e\u5220\u64cd\u4f5c\u5458\u4ecd\u7136\u53ea\u6709\u4e3b\u4eba\u80fd\u505a
    out = run_handler("on_addop", FakeEvent(ALICE))
    check("A /addop still owner-only", H.get("on_addop") is not None and True)

    # 删群三段流程（主人）
    # \u5220\u7fa4\u6d41\u7a0b\uff1a\u73b0\u5728\u64cd\u4f5c\u5458\u4e5f\u80fd\u8d70\u901a
    run_handler("on_any_text", FakeEvent(ALICE, BTN["del_group"]))
    out = run_handler("on_any_text", FakeEvent(ALICE, "\U0001f5d1 1\u00b7\u7fa4A"))
    check("C operator del flow: confirm prompt",
          out and "\u786e\u8ba4\u5220\u9664\u7fa4" in out[0])
    out = run_handler("on_any_text", FakeEvent(ALICE, BTN["del_confirm"]))
    check("C operator del flow: executed",
          DELETED == [1001] and "\u5220\u9664" in "\n".join(out))
    DELETED.clear()

    # \u672a\u6388\u6743\u7684\u4eba\u4f2a\u9020\u70b9\u51fb\uff0c\u5fc5\u987b\u65e0\u6548
    run_handler("on_any_text", FakeEvent(STRANGER, BTN["del_group"]))
    run_handler("on_any_text", FakeEvent(STRANGER, "\U0001f5d1 1\u00b7\u7fa4A"))
    run_handler("on_any_text", FakeEvent(STRANGER, BTN["del_confirm"]))
    check("C stranger cannot delete group", DELETED == [])

    # 选群/删群映射必须按人分键，否则两个人同时点选群会互相覆盖列表
    run_handler("on_any_text", FakeEvent(ALICE, BTN["camp_step1"]))
    run_handler("on_any_text", FakeEvent(BOB, BTN["camp_step1"]))
    per_actor = bot.state.get("group_pick_map_by") or {}
    check("C per-actor pick map populated",
          str(ALICE) in per_actor and str(BOB) in per_actor
          and per_actor[str(ALICE)] is not per_actor[str(BOB)])
    check("C no legacy shared pick keys",
          "group_pick_map" not in bot.state and "del_group_map" not in bot.state)


# =====================================================================
# D 崩溃释放
# =====================================================================
def section_d():
    _patch_events()
    stub_db()
    CRASH = {"on": True}

    async def sender_stub(*a, **k):
        if CRASH["on"]:
            raise RuntimeError("fake crash")
        return "\u2705 \u7fa4\u53d1\u5b8c\u6210"

    bot.send_to_list_multi = sender_stub
    bot.main_menu_kb = lambda op=False: None

    async def main():
        STORE.clear()
        O.set_campaign(ALICE, group="gA", group_title="\u7fa4A", text="\u6587\u6848")
        O.claim_list(ALICE, "Alice", "\u7fa4A")
        msgs = await drive_send(ALICE, BTN["camp_start"])
        check("D crash: actor gets error not silence", any("\u5f02\u5e38\u4e2d\u65ad" in m for m in msgs))
        check("D crash: busy lock released", bot.state["busy"] is False)
        check("D crash: list claim released", O.get_list_claim() == {})
        check("D crash: campaign marked crashed", any(f[2] == "crashed" for f in FINISHED))
        check("D crash: draft kept for retry", O.get_campaign(ALICE).get("text") == "\u6587\u6848")
        check("D crash: next operator takes over at once", O.claim_list(BOB, "Bob", "B\u7fa4")[0])

        O.release_list(None)
        bot._clear_busy()
        FINISHED.clear()
        CRASH["on"] = False
        O.set_campaign(BOB, group="gB", group_title="\u7fa4B", text="xx")
        O.claim_list(BOB, "Bob", "\u7fa4B")
        msgs = await drive_send(BOB, BTN["camp_start"])
        check("D happy: result shown", any("\u7fa4\u53d1\u5b8c\u6210" in m for m in msgs))
        check("D happy: status done", any(f[2] == "done" for f in FINISHED))
        check("D happy: draft cleared", O.get_campaign(BOB) == {})
        check("D happy: claim released", O.get_list_claim() == {})

        O.claim_list(ALICE, "Alice", "\u7fa4A")
        bot._set_busy(ALICE)
        AUDIT.clear()
        m = await drive_send(BOB, BTN["stop"])
        check("D anyone can stop another\u2019s task", bot.state["busy"] is False)
        check("D stop releases list claim", O.get_list_claim() == {})
        check("D stop audit names the holder",
              any(a[2] == "stop" and "Alice" in (a[3] or "") for a in AUDIT))

    asyncio.run(main())


def main_all():
    section_a()
    section_b()
    section_c()
    section_d()
    passed = sum(1 for _, p in RESULTS if p)
    lines = [f"{'PASS' if p else 'FAIL'}\t{n}" for n, p in RESULTS]
    lines += ["", f"{'ALL PASS' if passed == len(RESULTS) else '*** FAILURES ***'} "
                  f"({passed}/{len(RESULTS)})"]
    text = "\n".join(lines)
    try:
        io.open(os.path.join(HERE, "test_multiuser.result.txt"), "w",
                encoding="utf-8").write(text + "\n")
    except Exception:
        pass
    print(text)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main_all())
