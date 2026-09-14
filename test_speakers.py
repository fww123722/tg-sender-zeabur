# -*- coding: utf-8 -*-
"""「采发言人」（成员页被锁时的备用通道）回归自测：不连真实 PG/TG，全打桩。

跑法（项目目录下，需环境变量，见 vr3.ps1）：
    python test_speakers.py

覆盖：
 S1 正常采集：发言条数统计、按活跃度排序入名单、排除 bot/注销/自己/无用户名
 S2 min 用户批量升级成功 -> 有 username 的能入表
 S3 升级失败（抛异常）-> 拿不到对象的跳过，不炸整批
 S4 FloodWait 中断 -> 已读到的部分照样入库，文案带 ⏳ 且仍是 ✅ 开头
 S5 匿名消息（无 sender_id）-> 计入 anon，不进 counts
 S6 群解析失败 -> ❌ 开头（调用方据此换账号）
 S7 开关 recent_only_days 生效
 S8 重复发言人只记一次（按发言次数计数而非重复入表）
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

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
from telethon.errors import FloodWaitError  # noqa: E402
from telethon.tl.types import Channel, User  # noqa: E402

TARGETS = []
C.db_add_targets = lambda items: TARGETS.extend(items)
C.db_count_targets = lambda: len(TARGETS)

FAILS = []


def ck(name, cond, extra=""):
    ck.total += 1
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else "  << " + str(extra)[:220]))
    if not cond:
        FAILS.append(name)


ck.total = 0


def chan(cid=8001, title="锁名单群"):
    return Channel(id=cid, title=title, photo=None,
                   date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                   megagroup=True, username="sp%d" % cid)


def user(uid, uname=None, bot=False, deleted=False, min_=False, days_ago=0):
    st = (UserStatusOffline(datetime.now(timezone.utc) - timedelta(days=days_ago))
          if days_ago else UserStatusRecently())
    return User(id=uid, username=uname, first_name="N%d" % uid,
                bot=bot, deleted=deleted, min=min_, status=st,
                access_hash=uid * 3)


from telethon.tl.types import UserStatusOffline, UserStatusRecently  # noqa: E402


class Msg:
    def __init__(self, sid, sender):
        self.sender_id = sid
        self.sender = sender


class FakeClient:
    """喂 collect_speakers / collect_members 需要的接口。"""

    def __init__(self, phone="300", msgs=None, total=None,
                 flood_after=None, flood_secs=90,
                 upgrade_map=None, upgrade_exc=None, resolve_exc=None,
                 participants=None):
        self.phone = phone
        self.msgs = msgs or []
        self.total = total
        self.participants = participants if participants is not None else []
        self.flood_after = flood_after   # 读到第 N 条时抛 FloodWait
        self.flood_secs = flood_secs
        self.upgrade_map = upgrade_map or {}     # PeerUser id -> User
        self.upgrade_exc = upgrade_exc
        self.resolve_exc = resolve_exc
        self.channel = chan()
        self.calls = {"GetFullChannelRequest": 0, "get_entity": 0}

    async def iter_participants(self, entity, limit=None):
        for u in self.participants[:limit] if limit else self.participants:
            yield u

    async def get_me(self):
        return User(id=1, username="me", first_name="me",
                   status=UserStatusRecently(), access_hash=7)

    async def get_entity(self, e):
        self.calls["get_entity"] += 1
        if isinstance(e, list):
            if self.upgrade_exc:
                raise self.upgrade_exc
            out = []
            for peer in e:
                u = self.upgrade_map.get(peer.user_id)
                if u is not None:
                    out.append(u)
            return out
        if isinstance(e, (int, str)) and self.resolve_exc:
            raise self.resolve_exc
        return self.channel

    async def iter_messages(self, entity, limit=None):
        n = 0
        for m in self.msgs:
            if limit is not None and n >= limit:
                return
            n += 1
            if self.flood_after is not None and n > self.flood_after:
                raise FloodWaitError(None, self.flood_secs)
            yield m

    async def __call__(self, request):
        name = type(request).__name__
        self.calls[name] = self.calls.get(name, 0) + 1
        if name == "GetFullChannelRequest":
            if self.total is None:
                raise RuntimeError("no full info")
            return _Resp(_Full(self.total))
        raise AssertionError("unexpected " + name)


class _Full:
    def __init__(self, total):
        self.participants_count = total
        self.can_view_participants = False
        self.participants_hidden = True
        self.admins_count = 3


class _Resp:
    def __init__(self, full_chat):
        self.full_chat = full_chat


def msgs(spec):
    """spec: [(uid, User or None)] -> Msg 列表；None=匿名"""
    return [Msg(sid, sender) for sid, sender in spec]


async def main():
    global TARGETS

    # ---------- S1 正常采集 + 清洗 ----------
    TARGETS = []
    us = {
        11: user(11, "alice"),
        12: user(12, "bob"),
        13: user(13, None, bot=False),          # 无用户名 -> 排除
        14: user(14, "abot", bot=True),         # bot -> 排除
        15: user(15, "gone", deleted=True),     # 注销 -> 排除
        1:  user(1, "me"),                      # 自己 -> 排除
    }
    m1 = FakeClient("301", total=5000, msgs=msgs([
        (11, us[11]), (11, us[11]), (12, us[12]),
        (13, us[13]), (14, us[14]), (15, us[15]), (1, us[1]),
    ]))
    r1 = await C.collect_speakers(m1, "sp8001")
    ck("S1 ✅ 开头", r1.startswith("✅"), r1[:150])
    ck("S1 入名单 2 人", len(TARGETS) == 2, TARGETS)
    ck("S1 按活跃度排序（alice 在前）", TARGETS[0][0] == "11", TARGETS)
    ck("S1 排除项文案",
       "机器人 1" in r1 and "已注销 1" in r1 and "账号自己 1" in r1
       and "拿不到可发对象 1" in r1, r1[:280])
    ck("S1 带真实人数", "5000" in r1, r1[:200])

    # ---------- S2 min 用户升级 ----------
    TARGETS = []
    min_u = user(21, None, min_=True)
    full_u = user(21, "raised")
    m2 = FakeClient("302", total=5000,
                    msgs=msgs([(21, min_u), (21, min_u), (21, min_u)]),
                    upgrade_map={21: full_u})
    r2 = await C.collect_speakers(m2, "sp8001")
    ck("S2 升级后入表", len(TARGETS) == 1 and TARGETS[0][1] == "raised", TARGETS)
    ck("S2 文案提补全", "补全" in r2, r2[:240])

    # ---------- S3 升级炸 -> 跳过不整批炸 ----------
    TARGETS = []
    m3 = FakeClient("303", total=5000, msgs=msgs([(31, user(31, None, min_=True))]),
                    upgrade_exc=RuntimeError("boom"))
    r3 = await C.collect_speakers(m3, "sp8001")
    ck("S3 不炸仍是 ✅", r3.startswith("✅"), r3[:150])
    ck("S3 拿不到对象被排除", len(TARGETS) == 0 and "拿不到可发对象 1" in r3, (TARGETS, r3[:260]))

    # ---------- S4 FloodWait 半路断 ----------
    TARGETS = []
    m4 = FakeClient("304", total=5000, flood_after=2, flood_secs=90,
                    msgs=msgs([(41, user(41, "a")), (42, user(42, "b")),
                               (43, user(43, "c"))]))
    r4 = await C.collect_speakers(m4, "sp8001")
    ck("S4 读到的一半入库", len(TARGETS) == 2, TARGETS)
    ck("S4 仍 ✅ 且带 ⏳", r4.startswith("✅") and "⏳" in r4 and "90" in r4, r4[:200])

    # ---------- S5 匿名 ----------
    TARGETS = []
    m5 = FakeClient("305", total=10, msgs=msgs([(None, None), (51, user(51, "e"))]))
    r5 = await C.collect_speakers(m5, "sp8001")
    ck("S5 匿名计数", "1 条拿不到作者" in r5, r5[:240])
    ck("S5 正常号入表", len(TARGETS) == 1, TARGETS)

    # ---------- S6 解析失败 ----------
    m6 = FakeClient("306", resolve_exc=ValueError("Cannot find any entity"))
    m6.channel = None  # 强制 get_entity 走异常
    r6 = await C.collect_speakers(m6, "nonsense")
    ck("S6 解析失败 ❌ 开头", r6.startswith("❌"), r6[:150])

    # ---------- S7 近N天活跃开关 ----------
    TARGETS = []
    m7 = FakeClient("307", total=100, msgs=msgs([
        (71, user(71, "fresh", days_ago=1)),
        (72, user(72, "stale", days_ago=90)),
    ]))
    r7 = await C.collect_speakers(m7, "sp8001", recent_only_days=7)
    ck("S7 只留近7天", len(TARGETS) == 1 and TARGETS[0][0] == "71", TARGETS)
    ck("S7 排除久未上线", "久未上线 1" in r7 and "仅保留近7天" in r7, r7[:260])

    # ---------- S8 去重 ----------
    TARGETS = []
    u8 = user(81, "dup")
    m8 = FakeClient("308", total=100, msgs=msgs([(81, u8)] * 9))
    await C.collect_speakers(m8, "sp8001")
    ck("S8 同一个人只入一次", len(TARGETS) == 1, TARGETS)

    # ---------- S9 空历史 ----------
    TARGETS = []
    m9 = FakeClient("309", total=50, msgs=[])
    r9 = await C.collect_speakers(m9, "sp8001")
    ck("S9 无历史也如实报", r9.startswith("✅") and "0 个不同发言人" in r9, r9[:200])
    ck("S9 提醒只采到活跃者", "活跃发言人" in r9, r9[:300])

    # ---------- S10 撞锁自动接力采发言人（不用人再点） ----------
    TARGETS = []
    m10 = FakeClient("310", total=5000, msgs=msgs([
        (91, user(91, "spk1")), (92, user(92, "spk2")),
    ]))
    # 成员列表只下发 3 个管理员 -> collect_members 会判 ⚠️
    m10.participants = [user(1001, "adm1"), user(1002, "adm2"), user(1003, "adm3")]
    r10 = await C.collect_members_or_speakers(m10, "sp8001")
    ck("S10 自动接力：开头仍 ⚠️", r10.startswith("⚠️"), r10[:120])
    ck("S10 文案写明自动采", "自动" in r10 and "采发言人" in r10, r10[-400:])
    ck("S10 发言人已自动入表",
       {t[0] for t in TARGETS} >= {"91", "92"}, TARGETS)
    ck("S10 管理员也已入表", {"1001", "1002", "1003"} <= {t[0] for t in TARGETS}, TARGETS)

    # ---------- S11 拿全（✅）时不多跑一次历史 ----------
    TARGETS = []
    m11 = FakeClient("311", total=2, msgs=[])
    m11.participants = [user(121, "ok1"), user(122, "ok2")]
    r11 = await C.collect_members_or_speakers(m11, "sp8001")
    ck("S11 ✅ 不接力", r11.startswith("✅") and "自动" not in r11, r11[:200])
    ck("S11 只入了成员", len(TARGETS) == 2, TARGETS)

    # ---------- S12 ❌（不在群）也不接力 ----------
    m12 = FakeClient("312", resolve_exc=ValueError("no entity"))
    m12.channel = None
    r12 = await C.collect_members_or_speakers(m12, "nope")
    ck("S12 ❌ 不接力", r12.startswith("❌") and "自动" not in r12, r12[:200])

    print()
    print("RESULT pass=%d fail=%d" % (ck.total - len(FAILS), len(FAILS)))
    if FAILS:
        print("SPEAKERS_FAILED:", FAILS)
        return 1
    print("SPEAKERS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
