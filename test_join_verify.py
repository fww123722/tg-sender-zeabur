# -*- coding: utf-8 -*-
"""加群「需批准(A)/需人工验证(C)」回归自测：不连真实 PG/TG，全部打桩。

跑法（项目目录下，需环境变量，见 vr2.ps1）：
    python test_join_verify.py

覆盖：
 A1 首次加需批准的群 -> ⏳ 且记账在途，Import 只调一次
 A2 同一账号再发同一链接 -> 不重复提交（Import 调用次数不变），文案含「未重复提交」
 A3 群主批准后（预检查返回 ChatInviteAlready）-> ✅ 认出已通过、返回实体、清掉在途
 A4 在途期间预检查显示 bot_verification -> 升级为 🔒（而不是继续当等批准）
 C1 公开群带 bot_verification_icon -> 首次就 🔒，且不去 JoinChannel（不硬闯）
 C2 🔒 挂起后再发同一链接 -> 仍是 🔒，不重复提交
 I1 账号隔离：A 号有在途申请不挡 B 号（B 号照常 Import）
 Q1 join_group_all_accounts 遇 🔒 立即收手并把账号标出来（entity=None，调用方据此跳过拉人）
 R1 pending_joins_report 能列出 ⏳/🔒 两类
"""
import asyncio
import os
import sys

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
from telethon.tl.types import ChatInviteAlready  # noqa: E402

C.db_add_group = lambda *a, **k: None  # 不写库

FAILS = []


def ck(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else "  << " + str(extra)[:200]))
    if not cond:
        FAILS.append(name)


class InviteSent(C.InviteRequestSentError):
    def __init__(self):
        Exception.__init__(self)
        self.message = "JOIN_REQUEST_SEND"


class AlreadyIn(C.UserAlreadyParticipantError):
    def __init__(self):
        Exception.__init__(self)
        self.message = "USER_ALREADY_PARTICIPANT"


class Obj:
    """属性袋，模拟 TL 对象。"""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def chan(cid=5551, title="测试群", **kw):
    kw.setdefault("username", "tgrp")
    kw.setdefault("megagroup", True)
    kw.setdefault("broadcast", False)
    kw.setdefault("join_request", None)
    kw.setdefault("bot_verification_icon", None)
    return Obj(id=cid, title=title, **kw)


class FakeClient:
    """按脚本回应 Telethon 请求；记录每种请求的调用次数。"""

    def __init__(self, phone="100", import_behaviour=None, check_result=None,
                 entity=None, join_behaviour=None):
        self.phone = phone
        self.import_behaviour = import_behaviour  # None=成功, 或异常实例
        self.check_result = check_result
        self.entity = entity
        self.join_behaviour = join_behaviour
        self.calls = {"ImportChatInviteRequest": 0, "CheckChatInviteRequest": 0,
                      "JoinChannelRequest": 0}

    async def get_me(self):
        return Obj(id=1, username="u" + self.phone, phone=self.phone)

    async def get_entity(self, e):
        if isinstance(e, Obj):
            return e
        if self.entity is None:
            # 鐪熷疄 Telethon 瀵硅В鏋愪笉鍑虹殑鐩爣浼氭姏寮傚父锛岃€屼笉鏄繑鍥?None
            raise ValueError("Cannot find any data type for the given id")
        return self.entity

    async def iter_dialogs(self, limit=300):
        for _ in ():
            yield _

    async def __call__(self, request):
        from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
        from telethon.tl.functions.channels import JoinChannelRequest
        name = type(request).__name__
        self.calls[name] = self.calls.get(name, 0) + 1
        if name == "ImportChatInviteRequest":
            if isinstance(self.import_behaviour, Exception):
                raise self.import_behaviour
            return Obj(updates=None, chats=None)
        if name == "CheckChatInviteRequest":
            if isinstance(self.check_result, Exception):
                raise self.check_result
            return self.check_result
        if name == "JoinChannelRequest":
            if isinstance(self.join_behaviour, Exception):
                raise self.join_behaviour
            return Obj(updates=None)
        raise AssertionError("unexpected request " + name)


async def main():
    LINK = "https://t.me/+AAA111bbb"
    LINK_PUB = "https://t.me/somegroup1"

    # ---------- A1 首次申请 ----------
    c1 = FakeClient("100", import_behaviour=InviteSent(),
                    check_result=Obj(title="测试群", bot_verification=None, request_needed=True))
    txt, ent = await C.join_group_by_link(c1, LINK, acc="100")
    ck("A1 ⏳文案", txt.startswith("⏳"), txt)
    ck("A1 不返回实体(未进群)", ent is None, ent)
    ck("A1 Import调用1次", c1.calls["ImportChatInviteRequest"] == 1, c1.calls)
    pend = C._pending_get(LINK, c1, "100")
    ck("A1 已记在途(kind=request)", bool(pend) and pend.get("kind") == "request", pend)

    # ---------- A2 重复提交防护 ----------
    txt2, _ = await C.join_group_by_link(c1, LINK, acc="100")
    ck("A2 提示已在途未重复提交", ("未重复提交" in txt2) and txt2.startswith("⏳"), txt2)
    ck("A2 Import仍只1次", c1.calls["ImportChatInviteRequest"] == 1, c1.calls)
    ck("A2 带上等待分钟数", "分钟" in txt2, txt2)

    # ---------- A3 批准后认出已通过 ----------
    ch = chan(5551, "测试群")
    c1.check_result = ChatInviteAlready.__new__(ChatInviteAlready)
    c1.check_result.chat = ch
    txt3, ent3 = await C.join_group_by_link(c1, LINK, acc="100")
    ck("A3 ✅认出已通过", txt3.startswith("✅") and "批准" in txt3, txt3)
    ck("A3 返回群实体", ent3 is ch, ent3)
    ck("A3 在途已清除", C._pending_get(LINK, c1, "100") is None, C._pending_get(LINK, c1, "100"))

    # ---------- A4 在途期间发现要验证 -> 升级 🔒 ----------
    c4 = FakeClient("104", import_behaviour=InviteSent(),
                    check_result=Obj(title="验证群", bot_verification=Obj(), request_needed=True))
    await C.join_group_by_link(c4, "https://t.me/+VVV444", acc="104")
    t4, _ = await C.join_group_by_link(c4, "https://t.me/+VVV444", acc="104")
    ck("A4 升级为🔒", t4.startswith("🔒"), t4)
    ck("A4 明确拒绝代过验证", ("不会代你过" in t4) or ("我不会代你" in t4), t4)
    ck("A4 不重复Import", c4.calls["ImportChatInviteRequest"] == 1, c4.calls)

    # ---------- C1 公开群带验证机器人：首次就识别，不硬闯 ----------
    pub = chan(777, "公开验证群", bot_verification_icon=Obj())
    c5 = FakeClient("105", entity=pub)
    t5, e5 = await C.join_group_by_link(c5, LINK_PUB, acc="105")
    ck("C1 首次即🔒", t5.startswith("🔒"), t5)
    ck("C1 未尝试JoinChannel", c5.calls["JoinChannelRequest"] == 0, c5.calls)
    ck("C1 挂起kind=verify", (C._pending_get(LINK_PUB, c5, "105") or {}).get("kind") == "verify",
       C._pending_get(LINK_PUB, c5, "105"))
    ck("C1 无实体", e5 is None, e5)

    # ---------- C2 已挂起再发同一链接：仍🔒且不重复动作 ----------
    t6, _ = await C.join_group_by_link(c5, LINK_PUB, acc="105")
    ck("C2 仍🔒", t6.startswith("🔒"), t6)
    ck("C2 未再Join", c5.calls["JoinChannelRequest"] == 0, c5.calls)

    # ---------- I1 账号隔离 ----------
    cA = FakeClient("110", import_behaviour=InviteSent(),
                    check_result=Obj(title="群", bot_verification=None, request_needed=True))
    await C.join_group_by_link(cA, LINK, acc="110")
    cB = FakeClient("111", import_behaviour=InviteSent(),
                    check_result=Obj(title="群", bot_verification=None, request_needed=True))
    tB, _ = await C.join_group_by_link(cB, LINK, acc="111")
    ck("I1 B号未被A号在途挡住", cB.calls["ImportChatInviteRequest"] == 1, cB.calls)
    ck("I1 B号自己也发了申请", tB.startswith("⏳"), tB)

    # ---------- Q1 多账号编排：🔒 立即收手 ----------
    cQ1 = FakeClient("120", import_behaviour=InviteSent(),
                     check_result=Obj(title="群", bot_verification=None, request_needed=True))
    cQ2 = FakeClient("121", entity=chan(888, "公开验证群2", bot_verification_icon=Obj()))
    accs = [(1, cQ1, "120"), (2, cQ2, "121")]
    body, entQ, usedQ = await C.join_group_all_accounts(accs, LINK_PUB)
    ck("Q1 🔒也算收手并回文案", body.startswith("🔒"), body[:120])
    ck("Q1 标出使用账号", "121" in body, body[:160])
    ck("Q1 无实体(调用方据此跳过拉人)", entQ is None, entQ)

    # ---------- 已在群里：清在途 ----------
    cD = FakeClient("130", import_behaviour=AlreadyIn(),
                    check_result=Obj(title="群", bot_verification=None, request_needed=True))
    tD, _ = await C.join_group_by_link(cD, "https://t.me/+DDD130", acc="130")
    ck("D ✅已在群", tD.startswith("✅"), tD)

    # ---------- R1 在途清单 ----------
    rep = C.pending_joins_report()
    ck("R1 列出在途条数", "在途入群申请" in rep, rep[:150])
    ck("R1 含⏳或🔒标记", ("⏳" in rep) or ("🔒" in rep), rep[:200])
    ck("R1 给出下一步指引", "同一链接" in rep, rep[:220])

    # ---------- 记账异常不许带崩加群 ----------
    import ops_state as _O
    real = _O.DB.save_session
    _O.DB.save_session = staticmethod(lambda name, data: (_ for _ in ()).throw(RuntimeError("db down")))
    try:
        cE = FakeClient("140", import_behaviour=InviteSent(),
                        check_result=Obj(title="群", bot_verification=None, request_needed=True))
        tE, _ = await C.join_group_by_link(cE, "https://t.me/+EEE140", acc="140")
        ck("E DB挂掉仍能返回⏳(不抛)", tE.startswith("⏳"), tE)
    finally:
        _O.DB.save_session = staticmethod(real)

    print("\nRESULT pass=%d fail=%d" % (len(RESULTS_OK()), len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        return 1
    print("JOIN_VERIFY_OK")
    return 0


def RESULTS_OK():
    return [n for n in _ALL if n not in FAILS]


_ALL = []
_orig_ck = ck


def ck(name, cond, extra=""):  # noqa: F811
    _ALL.append(name)
    _orig_ck(name, cond, extra)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
