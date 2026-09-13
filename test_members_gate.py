# -*- coding: utf-8 -*-
"""拉名单「只读到管理员」识别自测：不连真实 PG/TG，全部打桩。

跑法（项目目录下，需环境变量，见 vr3.ps1）：
    python test_members_gate.py

背景：群主把「成员页」设成只显示管理员后，Telegram 服务端只会下发管理员那几个
号，旧代码照样回一句「✅ 拉取完成：新增有效成员 3 人」，老板看到的就是
「只能读取几个成员」但不知道为什么。现在拿 participants_count 做对照，
读到的远少于真实人数时改报 ⚠️ 并说清是服务端限制。

覆盖：
 M1 真实 5000 人只读到 3 人 -> ⚠️（不是 ✅），文案带 5000/3 两个数
 M2 ⚠️ 时已经读到的号照样入表（能用这部分先跑）
 M3 全名单能读（读到数 == 真实数）-> 仍报 ✅，不许误报
 M4 人数受 limit 截断（读到 == limit < 真实）-> 报 ✅ 且提上限，不许当「被限制」
 M5 participants_count 拿不到（ChatFull 请求炸了）-> 保守走 ✅，不瞎判 ⚠️
 M6 小群读到 0 人 -> ✅（真没人的群不许谎称被限制）
 M7 can_view_participants=False 时，⚠️ 文案要点名「成员列表不可见」
 M8 全是机器人（都被排除）时不许误判成被限制
 M9 超大群只放行一部分 -> ⚠️ 但归因要写成「超大群列不全」，不许推到管理员身上
 M10 读到数正好撞自家 limit=5000 -> 算上限截断（✅ + 提上限），不算被限制
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("API_ID", "123456")
os.environ.setdefault("API_HASH", "abc")
os.environ.setdefault("BOT_TOKEN", "***")
os.environ.setdefault("OWNER_ID", "111")
os.environ.setdefault("DATABASE_URL", "postgresql://u:***@127.0.0.1:5432/none")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

STORE = {}
import ops_state as O  # noqa: E402

O.DB.load_session = staticmethod(lambda name: STORE.get(name))
O.DB.save_session = staticmethod(lambda name, data: STORE.__setitem__(name, data))

import collector as C  # noqa: E402
from telethon.tl.types import Channel, User  # noqa: E402
from telethon.tl.functions.channels import GetFullChannelRequest  # noqa: E402

TARGETS = []  # (uid, username, access_hash)
C.db_add_targets = lambda items: TARGETS.extend(items)
C.db_count_targets = lambda: len(TARGETS)

FAILS = []


def ck(name, cond, extra=""):
    ck.total += 1
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else "  << " + str(extra)[:220]))
    if not cond:
        FAILS.append(name)


ck.total = 0


def chan(cid=7001, title="限制群"):
    return Channel(id=cid, title=title, photo=None,
                   date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                   megagroup=True, username="g%d" % cid, participants_count=None)


def user(uid, uname=None):
    return User(id=uid, username=uname or "u%d" % uid, first_name="N%d" % uid)


class Obj:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class FakeClient:
    """只喂 collect_members 需要的那几个接口。"""

    def __init__(self, phone="200", members=None, total=None, full_behaviour=None,
                 can_view=None, hidden=None, admins=None):
        self.phone = phone
        self.members = members or []
        self.total = total
        self.full_behaviour = full_behaviour  # None=正常回 full_chat；异常=炸
        self.can_view = can_view
        self.hidden = hidden
        self.admins = admins
        self.calls = {"GetFullChannelRequest": 0}
        self.channel = chan()

    async def get_me(self):
        return User(id=1, username="me" + self.phone, first_name="me")

    async def get_entity(self, e):
        return self.channel

    async def iter_participants(self, entity, limit=None):
        out = self.members if limit is None else self.members[:limit]
        for u in out:
            yield u

    async def __call__(self, request):
        name = type(request).__name__
        self.calls[name] = self.calls.get(name, 0) + 1
        if isinstance(request, GetFullChannelRequest):
            if isinstance(self.full_behaviour, Exception):
                raise self.full_behaviour
            if self.total is None:
                raise RuntimeError("no full info")
            return Obj(full_chat=Obj(participants_count=self.total,
                                    can_view_participants=self.can_view,
                                    participants_hidden=self.hidden,
                                    admins_count=self.admins))
        raise AssertionError("unexpected request " + name)


async def main():
    global TARGETS

    # ---------- M1/M2 真实 5000 人只读到 3 人 ----------
    TARGETS = []
    c1 = FakeClient("201", members=[user(11), user(12), user(13)], total=5000, admins=3)
    r1 = await C.collect_members(c1, "g7001")
    ck("M1 改判 ⚠️ 不谎报✅", r1.startswith("⚠️") and "拉取完成" not in r1, r1[:160])
    ck("M1 文案带真实人数 5000", "5000" in r1, r1[:200])
    ck("M1 文案带实读 3 人", "只读到 3 人" in r1, r1[:200])
    ck("M1 说是服务端限制不是失败", "限制" in r1 and "过期" in r1, r1[:240])
    ck("M1 指出去路（管理员/群主关限制）", "管理员" in r1.split("✔")[-1], r1[:260])
    ck("M2 读到的 3 人照样入表", len(TARGETS) == 3, TARGETS)
    ck("M2 只查了一次 full", c1.calls["GetFullChannelRequest"] == 1, c1.calls)

    # ---------- M3 全名单能读 ----------
    TARGETS = []
    many = [user(100 + i) for i in range(40)]
    c2 = FakeClient("202", members=many, total=40)
    r2 = await C.collect_members(c2, "g7001")
    ck("M3 读全了仍报 ✅", r2.startswith("✅") and "新增有效成员 40 人" in r2, r2[:160])
    ck("M3 不误提「只显示管理员」", "只显示管理员" not in r2, r2[:200])

    # ---------- M4 被 limit 截断（不是被限制） ----------
    TARGETS = []
    c3 = FakeClient("203", members=[user(500 + i) for i in range(20)], total=5000)
    r3 = await C.collect_members(c3, "g7001", limit=20)
    ck("M4 撞上限时报 ✅", r3.startswith("✅"), r3[:160])
    ck("M4 明确提单次上限", "上限 20 人" in r3, r3[:200])
    ck("M4 不冒充「成员页被限制」", "只显示管理员" not in r3, r3[:200])

    # ---------- M5 拿不到 participants_count ----------
    TARGETS = []
    c4 = FakeClient("204", members=[user(601), user(602)], total=None)
    r4 = await C.collect_members(c4, "g7001")
    ck("M5 查不到总数时保守报 ✅", r4.startswith("✅"), r4[:160])
    ck("M5 不瞎判 ⚠️", "⚠️" not in r4, r4[:200])

    TARGETS = []
    c5 = FakeClient("205", members=[user(701)], total=300,
                    full_behaviour=RuntimeError("boom"))
    r5 = await C.collect_members(c5, "g7001")
    ck("M5b full 请求抛异常也不炸", r5.startswith("✅"), r5[:160])

    # ---------- M6 真没人的小群 ----------
    TARGETS = []
    c6 = FakeClient("206", members=[], total=0)
    r6 = await C.collect_members(c6, "g7001")
    ck("M6 空群报 ✅ 0 人", r6.startswith("✅") and "新增有效成员 0 人" in r6, r6[:160])

    # ---------- M7 can_view=False 要在文案里点名 ----------
    TARGETS = []
    c7 = FakeClient("207", members=[user(801)], total=900, can_view=False, admins=1)
    r7 = await C.collect_members(c7, "g7001")
    ck("M7 仍判 ⚠️", r7.startswith("⚠️"), r7[:120])
    ck("M7 点名「成员列表不可见」", "不可见" in r7, r7[:240])
    ck("M7 带上管理员数", "管理员 1 人" in r7, r7[:240])

    # ---------- M8 排除项不许被算进「实读」漏报 ----------
    # 机器号/无用户名会被排除，但 seen 仍应计数，否则全 bot 的群会被误判成被限制
    TARGETS = []
    bots = [User(id=900 + i, username="b%d" % i, first_name="B%d" % i, bot=True)
            for i in range(5)]
    c8 = FakeClient("208", members=list(bots), total=5)
    r8 = await C.collect_members(c8, "g7001")
    ck("M8 全是机器人时不误判被限制", r8.startswith("✅"), r8[:160])
    ck("M8 排除说明里有机器人 5", "机器人 5" in r8, r8[:200])

    # ---------- M9 超大群只放行一部分：不能归因成「只显示管理员」 ----------
    # 注意要跑在自家 limit=5000 之下，否则先被上限截断（那是 M4 的情形）。
    TARGETS = []
    c9 = FakeClient("209", members=[user(1000 + i) for i in range(2000)],
                    total=50000, admins=4)
    r9 = await C.collect_members(c9, "g7001")
    ck("M9 仍报 ⚠️ 拿不全", r9.startswith("⚠️"), r9[:120])
    ck("M9 不推到「只显示管理员」", "只显示管理员" not in r9, r9[:260])
    ck("M9 说清是超大群列不全", "超大群" in r9, r9[:280])
    ck("M9 带上两个真实数字", "50000" in r9 and "2000" in r9, r9[:200])

    # ---------- M10 读到数正好撞自家 limit：算截断，不算被限制 ----------
    TARGETS = []
    c10 = FakeClient("210", members=[user(20000 + i) for i in range(5000)],
                     total=50000, admins=4)
    r10 = await C.collect_members(c10, "g7001")
    ck("M10 撞 limit 时报 ✅ 并提上限", r10.startswith("✅") and "上限 5000 人" in r10, r10[:180])
    ck("M10 不谎称拿全（带真实人数）", "50000" in r10, r10[:200])

    print()
    print("RESULT pass=%d fail=%d" % (ck.total - len(FAILS), len(FAILS)))
    if FAILS:
        print("MEMBERS_GATE_FAILED:", FAILS)
        return 1
    print("MEMBERS_GATE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
