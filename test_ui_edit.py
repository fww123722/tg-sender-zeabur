# -*- coding: utf-8 -*-
"""举报进度「刷屏」修复验证（老板：进度必须在原消息上编辑，不要每条都新发）。

旧 _edit_or_send：msg.edit() 抛任何异常（除 not modified）就 return _reply(...)
= 发一条新消息。心跳每 1~2 秒编辑一次，极易撞 Telegram 每聊天 ~1 edit/s 的频控
（FLOOD_WAIT）→ 被吞掉后退化成刷屏。

新行为断言：
 1) 编辑成功            -> 0 条新消息
 2) 编辑撞 FloodWait    -> 退避重试，最终成功，0 条新消息
 3) 连续编辑被节流      -> 两次真实 edit 间隔 >= EDIT_MIN_INTERVAL
 4) 编辑永久失败        -> 兜底走 client.edit_message（仍不是 send_message）
 5) 两条路径都失败      -> 才允许新发（且只发 1 条）
 6) msg is None         -> 首条才新发
"""
import asyncio
import os
import sys
import time

os.environ.setdefault("API_ID", "123456")
os.environ.setdefault("API_HASH", "abc")
os.environ.setdefault("BOT_TOKEN", "***")
os.environ.setdefault("OWNER_ID", "111")
os.environ.setdefault("DATABASE_URL", "postgresql://u:***@127.0.0.1:5432/none")

D = r"C:\Users\amdin\.openclaw\workspace-group-bot\projects\tg-sender-zeabur"
sys.path.insert(0, D)

import bot  # noqa: E402
from telethon.errors import FloodWaitError  # noqa: E402

FAILS = []


def ck(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else "  << " + str(extra)[:170]))
    if not cond:
        FAILS.append(name)


class Flood(FloodWaitError):
    def __init__(self, seconds=2):
        Exception.__init__(self)
        self.seconds = seconds


class FakeMsg:
    """假消息：按脚本决定编辑成功/抛错，并记录每次成功编辑的时间戳。"""

    def __init__(self, script, mid=777):
        self.id = mid
        self.chat_id = 111
        self.script = list(script)
        self.edits = []          # 成功编辑的时间戳
        self.attempts = 0        # edit() 被调用次数

    async def edit(self, text, buttons=None):
        self.attempts += 1
        action = self.script.pop(0) if self.script else "ok"
        if action == "ok":
            self.edits.append(time.time())
            return self
        raise action


class FakeClient:
    def __init__(self, edit_ok=True):
        self.edit_calls = 0
        self.edit_ok = edit_ok

    async def edit_message(self, chat, msg_id, text, buttons=None):
        self.edit_calls += 1
        if not self.edit_ok:
            raise RuntimeError("bot was kicked")
        return True


class FakeEvent:
    def __init__(self, client):
        self.client = client
        self.chat_id = 111


SENT = []
_orig_send = None


async def fake_send(chat_id, text, buttons=None, **kw):
    SENT.append(text)
    return FakeMsg(["ok"])


async def main():
    _orig_send = bot._reply
    bot._reply = lambda ev, text, buttons=None: fake_send(ev.chat_id, text, buttons)
    bot.EDIT_MIN_INTERVAL = 0.35   # 测试里把节流压小，跑得快
    t0 = time.time()

    # ---- 1) 正常编辑：不新发 ----
    SENT.clear()
    m = FakeMsg(["ok", "ok", "ok"])
    ev = FakeEvent(FakeClient())
    for i in range(3):
        await bot._edit_or_send(m, ev, f"进度 {i}")
    ck("1 正常编辑 0 条新消息", len(SENT) == 0, SENT)
    ck("1 编辑发生 3 次", len(m.edits) == 3, m.edits)

    # ---- 2) 撞 FloodWait：重试后成功，仍然不新发 ----
    SENT.clear()
    m2 = FakeMsg([Flood(1), Flood(1), "ok"])
    await bot._edit_or_send(m2, ev, "🚀 2/7")
    ck("2 FloodWait 后重试成功", len(m2.edits) == 1, m2.edits)
    ck("2 FloodWait 不新发消息", len(SENT) == 0, SENT)
    ck("2 至少尝试 3 次", m2.attempts >= 3, m2.attempts)

    # ---- 3) 节流生效：两次成功编辑间隔 >= EDIT_MIN_INTERVAL ----
    m3 = FakeMsg(["ok"] * 4)
    for i in range(4):
        await bot._edit_or_send(m3, ev, f"tick {i}")
    gaps = [m3.edits[i + 1] - m3.edits[i] for i in range(len(m3.edits) - 1)]
    ck("3 编辑被节流(间隔>=%.2fs)" % bot.EDIT_MIN_INTERVAL,
       all(g >= bot.EDIT_MIN_INTERVAL - 0.02 for g in gaps), [round(g, 3) for g in gaps])

    # ---- 4) msg.edit 永久失败 -> 兜底 client.edit_message，不新发 ----
    SENT.clear()
    cli = FakeClient(edit_ok=True)
    ev4 = FakeEvent(cli)
    m4 = FakeMsg([RuntimeError("bad message reference")] * 6)
    await bot._edit_or_send(m4, ev4, "🚀 3/7")
    ck("4 走 client.edit_message 兜底", cli.edit_calls == 1, cli.edit_calls)
    ck("4 兜底成功也不新发", len(SENT) == 0, SENT)

    # ---- 5) 两条路都失败 -> 才允许新发，且只发 1 条 ----
    SENT.clear()
    cli5 = FakeClient(edit_ok=False)
    m5 = FakeMsg([RuntimeError("nope")] * 8)
    await bot._edit_or_send(m5, FakeEvent(cli5), "🚀 4/7")
    ck("5 彻底失败才新发 1 条", len(SENT) == 1, SENT)
    ck("5 新发内容就是进度原文", SENT and SENT[0] == "🚀 4/7", SENT)

    # ---- 6) 首条（msg=None）才新发 ----
    SENT.clear()
    await bot._edit_or_send(None, ev, "🔍 举报目标")
    ck("6 首条无消息时新发 1 条", len(SENT) == 1, SENT)

    # ---- 7) not modified 视为成功，不新发也不重复编辑 ----
    SENT.clear()

    class NotMod(FakeMsg):
        async def edit(self, text, buttons=None):
            self.attempts += 1
            raise FloodWaitError.__name__ and RuntimeError("A wait is required") \
                if False else (_ for _ in ()).throw(
                    type("E", (Exception,), {})("message_not_modified: The request was "
                                                "not cancelled because new message is identical"))

    m7 = NotMod([])
    await bot._edit_or_send(m7, ev, "同样的字")
    ck("7 not modified 不新发", len(SENT) == 0, SENT)

    # ---- 8) 源码级：_say 必须复用同一个 rep_msg 走 _edit_or_send ----
    src = open(os.path.join(D, "bot.py"), encoding="utf-8").read()
    seg = src[src.index("async def _start_report"):]
    seg = seg[:seg.index("async def _run_filter")]
    ck("8 _say 首条 _reply / 之后 _edit_or_send",
       "_reply(event, text, buttons=report_menu_kb())" in seg and "_edit_or_send(rep_msg, event, text" in seg)
    ck("8 旧退化路径已删除",
       'if msg is not None:\n        try:\n            return await (msg.edit' not in src)
    ck("8 无 \\r", "\r" not in src)

    print("\nRESULT elapsed=%.1fs fail=%d" % (time.time() - t0, len(FAILS)))
    bot._reply = _orig_send
    if FAILS:
        print("FAILED: " + "; ".join(FAILS))
        sys.exit(1)
    print("UI_EDIT_OK")


asyncio.run(main())
