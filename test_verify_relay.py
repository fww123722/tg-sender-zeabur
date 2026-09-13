# -*- coding: utf-8 -*-
"""入群验证「人在环」中继（B 方案）自测：不连真实 TG/PG，全部打桩。

核心断言不是「能不能过验证」，而是 **没有人的动作，系统一个字节都不许发给验证 bot**。

跑法：见 .openclaw/tmp/vr2.ps1，或项目目录下
    python test_verify_relay.py
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

import config  # noqa: E402
import verify_relay as V  # noqa: E402

FAILS = []


def ck(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else "  << " + str(extra)[:200]))
    if not cond:
        FAILS.append(name)


class Btn:
    def __init__(self, text, i, j, kind="callback", pwd=False):
        self.text = text
        self.i, self.j = i, j
        self.kind = kind
        self.requires_password = pwd
        self.url = "https://example.com/x" if kind == "url" else None
        self.data = b"\x01demo" if kind == "callback" else None


class Msg:
    """假验证消息：记录被点了几次。"""

    def __init__(self, chat_id, mid, text, buttons, out=False, is_private=True):
        self.chat_id = chat_id
        self.id = mid
        self.text = text
        self.raw_text = text
        self.buttons = buttons
        self.out = out
        self.is_private = is_private
        self.action = None
        self.clicks = 0
        self.clicked_at = None

    async def click(self, i=None, j=None, **kw):
        self.clicks += 1
        self.clicked_at = (i, j)
        return True


class FakeClient:
    def __init__(self):
        self.phone = "100"
        self.handlers = []
        self.sent = []          # 主动 send_message 记录
        self.msgs = {}          # id -> Msg（测试里手动注册，模拟服务器上的原消息）
        self.me = type("M", (), {"id": 9})()

    def add_event_handler(self, fn, matcher=None):
        self.handlers.append(fn)

    async def send_message(self, chat, text):
        self.sent.append((chat, text))
        return True

    async def get_messages(self, chat_id, ids=None):
        return self.msgs.get(ids)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, uid, text, buttons=None):
        self.sent.append((uid, text, buttons))
        return True


class Ev:
    def __init__(self, msg, client):
        self.message = msg
        self.client = client


def btns(*row):
    """把文字列表摆成 Telethon 的二维按钮（每行 2 个）。"""
    flat, out = [], []
    for k, b in enumerate(row):
        if isinstance(b, tuple):
            flat.append(Btn(b[0], k // 2, k % 2, b[1], len(b) > 2 and b[2]))
        else:
            flat.append(Btn(b, k // 2, k % 2))
    for k in range(0, len(flat), 2):
        out.append(flat[k:k + 2])
    return out


def reset():
    V._armed.clear()
    V._items.clear()
    V._hooked.clear()
    V._seq["n"] = 0


def reg(client, msg):
    """把假消息登记到账号的「服务器」上，让 get_messages 取到。"""
    client.msgs[msg.id] = msg
    return msg


async def main():
    config.NOTIFY_UID = 111
    bot = FakeBot()
    V.set_bot(bot)
    V.PUSH_COOLDOWN = 0   # 测试里先关掉 45s 频控（与「频控」无关的用例不被它阻）；H12b 单独测它
    dbrows = []
    import db
    db.db_log_op = lambda *a, **k: dbrows.append(a)

    c = FakeClient()

    # ---- H1 没 arm 之前：验证题来了也不许转、不许发任何东西 ----
    reset()
    V.attach(c, "100")
    m = reg(c, Msg(777, 1, "Please complete the verification to join", btns("Verify")))
    await V._capture(c, "100", Ev(m, c))
    ck("H1 未 arm 时不转发（0 条推送）", len(bot.sent) == 0, bot.sent)
    ck("H1 未 arm 时代账号没发过任何消息", len(c.sent) == 0 and m.clicks == 0)

    # ---- H2 arm 后把题原文+按钮转给老板，仍不自动提交 ----
    V.arm(c, "100", "https://t.me/+abcDEF", "验证群")
    await V._capture(c, "100", Ev(m, c))
    ck("H2 撞题后转了 1 条给老板", len(bot.sent) == 1, bot.sent)
    if bot.sent:
        uid, text, kb = bot.sent[0]
        ck("H2 发给的是通知人(111)", uid == 111, uid)
        ck("H2 原话照抄进正文", "Please complete the verification" in text)
        ck("H2 按钮按序号列出", "1. Verify" in text)
        ck("H2 带群链接", "t.me/+abcDEF" in text)
        ck("H2 键盘里有对应回调", kb and b"vr:" in kb[0][0].data, kb)
    ck("H2 转发≠提交：账号仍 0 次外发", len(c.sent) == 0 and m.clicks == 0)

    iid = list(V._items.keys())[0]

    # ---- H3 老板不点：永远不会有第二次点击 ----
    ck("H3 待选题仍在等人", len(V.open_items()) == 1)

    # ---- H4 点了才提交，且一次性的 ----
    res = await V.click_option(iid, 1, 111)
    ck("H4 按人选择提交一次", res.startswith("✅") and m.clicks == 1, res)
    ck("H4 点到的是老板选的那个位置", m.clicked_at == (0, 0), m.clicked_at)
    res2 = await V.click_option(iid, 1, 111)
    ck("H4 重复点同一题被拒（不刷屏）", res2.startswith("⚠️") and m.clicks == 1, res2)
    ck("H4 消费后待选清空", len(V.open_items()) == 0)

    # ---- H5 只提交老板选的那个，绝不自挑带 verify 字样的按钮 ----
    m2 = reg(c, Msg(778, 2, "Choose one to verify", btns("跳过 Skip", "进入 Enter")))
    V.arm(c, "100", "https://t.me/+xyz", "群2")
    await V._capture(c, "100", Ev(m2, c))
    iid2 = [k for k in V._items if V._items[k]["msg_id"] == 2][0]
    o2 = V._items[iid2]["opts"]
    ck("H5 两个按钮都原样列出、不排序不自选",
       [x["text"] for x in o2] == ["跳过 Skip", "进入 Enter"], o2)
    r = await V.click_option(iid2, 1, 111)
    ck("H5 人选「跳过」就只提交跳过", r.startswith("✅") and m2.clicked_at == (0, 0), r)

    # ---- H6 需密码/外链按钮：拒绝代点 ----
    m3 = reg(c, Msg(779, 3, "verify please", btns(("Pay pwd", "callback", True),
                                                   ("Open url", "url"))))
    V.arm(c, "100", "https://t.me/+p", "群3")
    await V._capture(c, "100", Ev(m3, c))
    iid3 = [k for k in V._items if V._items[k]["msg_id"] == 3][0]
    rp = await V.click_option(iid3, 1, 111)
    ck("H6 requires_password 拒绝代填", rp.startswith("⛔") and m3.clicks == 0, rp)
    ru = await V.click_option(iid3, 2, 111)
    ck("H6 URL 按钮不代开（题还在，可再选别的）", ru.startswith("🔗") and m3.clicks == 0, ru)

    # ---- H7 打字型（中文题干）：原样发一次，且只一次 ----
    n_push = len(bot.sent)
    m4 = reg(c, Msg(780, 4, "请回答：小明有几个苹果？输入答案", btns()))
    V.arm(c, "100", "https://t.me/+q", "群4")
    await V._capture(c, "100", Ev(m4, c))
    ck("H7 无按钮的中文题也能转给老板", len(bot.sent) == n_push + 1, len(bot.sent) - n_push)
    iid4 = [k for k in V._items if V._items[k]["msg_id"] == 4][0]
    r4 = await V.submit_answer(iid4, "3 个", 111)
    ck("H7 原样发老板打的字", r4.startswith("✅") and c.sent and c.sent[-1] == (780, "3 个"), c.sent)
    r4b = await V.submit_answer(iid4, "3 个", 111)
    ck("H7 同题不重复发", r4b.startswith("⚠️") and len([x for x in c.sent if x[0] == 780]) == 1, r4b)
    ck("H7 答案超长拒发", (await V.submit_answer(iid4, "x" * 200, 111)).startswith("⚠️"))

    # ---- H8 解析老板的输入格式 ----
    ck("H8 解析 ans", V.parse_answer("ans ab12 42") == ("ab12", "42"), V.parse_answer("ans ab12 42"))
    ck("H8 非 ans 前缀不当答案", V.parse_answer("加群 https://t.me/+a") is None)
    ck("H8 光 ans 无编号不提交", V.parse_answer("ans") == ("", ""))

    # ---- H9 不像验证题的闲聊不打扰老板 ----
    n_before = len(bot.sent)
    m5 = Msg(781, 5, "大家早上好呀", btns())
    await V._capture(c, "100", Ev(m5, c))
    ck("H9 普通闲聊不转发", len(bot.sent) == n_before, bot.sent[n_before:])
    m6 = Msg(782, 6, "hello verify me", btns(), out=True)
    await V._capture(c, "100", Ev(m6, c))
    ck("H10 自己发出去的消息不回环", len(bot.sent) == n_before)
    m6b = Msg(784, 61, "点击按钮完成验证", btns("Verify"), is_private=False)
    await V._capture(c, "100", Ev(m6b, c))
    ck("H10b 群里的普通消息不当验证题捣走（只盯私聊）", len(bot.sent) == n_before,
       bot.sent[n_before:])

    # ---- H11 过期作废：不提交 ----
    V.arm(c, "100", "https://t.me/+r", "群7")
    m7 = reg(c, Msg(783, 7, "需要人机验证，请点按钮", btns("Verify me")))
    await V._capture(c, "100", Ev(m7, c))
    iid7 = [k for k in V._items if V._items[k]["msg_id"] == 7][0]
    V._items[iid7]["at"] -= (V.VERIFY_TTL + 60)
    re_ = await V.click_option(iid7, 1, 111)
    ck("H11 超 15 分钟的题作废不提交", re_.startswith("⚠️") and m7.clicks == 0, re_)

    # ---- H12 每题上限 + 频控：不许把老板刷爆 ----
    reset()
    pushed0 = len(bot.sent)
    V.PUSH_COOLDOWN = 0            # 测试里关掉 45s 频控，只考「几题上限」这一条
    V.arm(c, "100", "https://t.me/+s", "群12")
    for n in range(12):
        await V._capture(c, "100", Ev(Msg(800 + n, 100 + n, "verify 请完成验证", btns("V")), c))
    ck("H12 一个窗最多转 6 题", len(bot.sent) - pushed0 == V.MAX_PUSH_PER_ARM,
       len(bot.sent) - pushed0)
    V.PUSH_COOLDOWN = 45
    pushed1 = len(bot.sent)
    V.arm(c, "200", "https://t.me/+u", "群12b")
    for n in range(3):
        await V._capture(c, "200", Ev(Msg(830 + n, 130 + n, "verify 请完成验证", btns("V")), c))
    ck("H12b 45s 频控生效：连发 3 题只转 1 题", len(bot.sent) - pushed1 == 1,
       len(bot.sent) - pushed1)

    # ---- H13 disarm / forget_arm 清干净 ----
    reset()
    V.PUSH_COOLDOWN = 0
    V.arm(c, "100", "https://t.me/+t", "群13")
    await V._capture(c, "100", Ev(Msg(810, 110, "verify 验证一下", btns("Go")), c))
    ck("H13 有一条待选", len(V.open_items()) == 1)
    V.forget_arm("100", 111)
    ck("H13 停盯后清空题目与窗口", len(V.open_items()) == 0 and "100" not in V._armed)
    await V._capture(c, "100", Ev(Msg(811, 111, "verify 验证一下", btns("Go")), c))
    ck("H13 停盯后不再收题", len(V.open_items()) == 0)
    V.PUSH_COOLDOWN = 45

    # ---- H14 护栏文字不许被删（防以后有人「顺手改成自动」）----
    src = open(os.path.join(HERE, "verify_relay.py"), encoding="utf-8").read()
    ck("H14 无自动算术（不定义解题函数/不 eval）",
       "def solve" not in src and "def _solve" not in src and "eval(" not in src
       and "op ==" not in src)
    ck("H14 无关键字自动挑按钮", "keys = [" not in src and "verify_words" not in src
       and ".click(" not in src.split("async def _do_click")[0].split("async def click_option")[-1])
    ck("H14 全文件只有 _do_click 里一处真点击", src.count(".click(") == 1, src.count(".click("))
    ck("H14 点击只发生在人工动作区（_do_click 之后）",
       src.index(".click(") > src.index("async def _do_click"))
    ck("H14 提交前必查 taken（一次性）", src.count('it.get("taken")') >= 2)
    ck("H14 不自动 /start 验证 bot", '"/start"' not in src and "'/start'" not in src)
    ck("H14 只监听进来的消息", "out" in src)

    print(f"\nRESULT fail={len(FAILS)}" + (": " + " | ".join(FAILS) if FAILS else ""))
    print("VERIFY_RELAY_OK" if not FAILS else "VERIFY_RELAY_FAIL")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
