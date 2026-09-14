# -*- coding: utf-8 -*-
"""加群账号（专职加群、不群发、自动补加）回归自测：全打桩，不连库不联网。

覆盖：
  J1 选号：在群最多的号被定为加群号，写入 ops_state
  J2 沿用：已定且在线 → 不重选
  J3 掉线换号：定过的号不在线 → 重选在线里群最多的
  J4 send_pool：群发剔掉加群号；只剩它一个不剔
  J5 pick_for_join：加群只用加群号；它不在线退回全池
  J6 backfill：缺的公开群补加、私密群跳过、都在的不动
  J7 缓存：get_join_acc 只读库一次；set 后缓存同步
  J8 接线：bot 加群入口/账号列表/开跑号数、sender 剔号、main 启动钩子
"""
import asyncio
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("API_ID", "123456")
os.environ.setdefault("API_HASH", "abc")
os.environ.setdefault("BOT_TOKEN", "***")
os.environ.setdefault("OWNER_ID", "111")
os.environ.setdefault("DATABASE_URL", "postgresql://u:***@127.0.0.1:5432/none")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import ops_state as O   # noqa: E402

STORE = {}
O.DB.load_session = staticmethod(lambda name: STORE.get(name))
O.DB.save_session = staticmethod(lambda name, data: STORE.__setitem__(name, data))

import joinacc as J     # noqa: E402

R = []


def ck(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else "  << " + str(extra)[:150]))
    if not cond:
        R.append(name)


class FakeDlg:
    def __init__(self, title):
        self.entity = type("E", (), {"title": title})()


class Cli:
    """假客户端：n_dialog 个群 + 若干私聊；authorized 可控。

    注意：is_user_authorized 在 telethon 是 async，桩必须也是，
    否则测不出「漏 await」这类真 bug。
    """

    def __init__(self, no, n_dialog, authorized=True):
        self.no, self.n, self.auth = no, n_dialog, authorized
        self.connected = False

    async def is_user_authorized(self):
        return self.auth

    async def connect(self):
        self.connected = True

    async def iter_dialogs(self):
        for i in range(self.n):
            yield FakeDlg(f"g{i}")
        yield FakeDlg(None)          # 私聊不算群


def row(no, cli):
    return (no, cli, f"+861380000{no:04d}")


def reset():
    STORE.clear()
    J._CACHE.update({"v": None, "loaded": False})


async def main():
    # ---- J1 自动选群最多的号 ----
    reset()
    accs = [row(1, Cli(1, 5)), row(2, Cli(2, 12)), row(3, Cli(3, 9))]
    tip = await J.ensure(accs)
    ck("J1 群最多的账号2被定为加群号", J.get_join_acc() == 2, tip)
    ck("J1 ops_state 落库", "join_acc" in str(STORE.values()), STORE)
    ck("J1 简报提到账号2", "账号2" in tip, tip)

    # ---- J2 已定且在线 → 沿用不重选 ----
    tip2 = await J.ensure(accs)
    ck("J2 沿用不重选", "沿用" in tip2 and J.get_join_acc() == 2, tip2)

    # ---- J3 定过的号掉线 → 在线里重选（账号3=9群 > 账号1=5群）----
    reset()
    accs3 = [row(1, Cli(1, 5)), row(3, Cli(3, 9)), row(2, Cli(2, 99, authorized=False))]
    await J.ensure(accs3)
    ck("J3 掉线的2被跳过，重选3", J.get_join_acc() == 3, J.get_join_acc())

    # ---- J4 send_pool ----
    reset()
    ck("J4 没定过 → 全量", J.send_pool(accs) == accs)
    J.set_join_acc(2)
    ck("J4 剔掉加群号", [a[0] for a in J.send_pool(accs)] == [1, 3], J.send_pool(accs))
    ck("J4 只剩加群号不剔（别罢工）", [a[0] for a in J.send_pool([accs[1]])] == [2])

    # ---- J5 pick_for_join ----
    pj = await J.pick_for_join(accs)
    ck("J5 加群优先用加群号", pj == [accs[1]], pj)
    pj3 = await J.pick_for_join(accs3)
    ck("J5 它不在线退回全池", pj3 == accs3, pj3)
    ck("J5 加群号不在线时不把它送进去", 2 not in [a[0] for a in pj3] or pj3 == accs3, pj3)

    # ---- J6 backfill ----
    reset()
    J.set_join_acc(1)
    groups = [(101, "公开群A", "pubA", 100, ""),     # 不在 → 补加成功
              (102, "公开群B", "pubB", 50, ""),      # 不在 → 补加被拒
              (103, "私密群C", "", 60, ""),          # 无 username → 跳过
              (104, "已在的群", "pubD", 10, "")]     # 已在 → 不动
    import db as DBM
    import collector as C
    DBM.db_get_all_groups = lambda: groups
    joined = []

    async def fake_resolve(client, gid):
        if gid == 104:
            return object()
        raise ValueError("not in")

    async def fake_join(client, link, acc=None):
        joined.append(link)
        if "pubB" in link:
            return ("❌ 被拒", None)
        return ("✅ 已加入", object())

    C._resolve_entity = fake_resolve
    C.join_group_by_link = fake_join
    notes = []
    ok, skip, body = await J.backfill([row(1, Cli(1, 4))], notify=notes.append)
    ck("J6 补上 1 个、跳过 2 个", ok == 1 and skip == 2, (ok, skip, body))
    ck("J6 只对缺的群动手", joined == ["t.me/pubA", "t.me/pubB"], joined)
    ck("J6 私密群标注跳过", "私密群" in body, body)
    ck("J6 汇报发给了 notify", bool(notes), notes)

    # ---- J7 缓存：读库只一次 ----
    reset()
    calls = {"n": 0}
    orig_get = O.get

    def spy_get(k):
        calls["n"] += 1
        return orig_get(k)
    O.get = spy_get
    J.get_join_acc(); J.get_join_acc(); J.get_join_acc()
    ck("J7 连读 3 次只查库 1 次", calls["n"] == 1, calls["n"])
    J.set_join_acc(7)
    ck("J7 set 后缓存立即可见", J.get_join_acc() == 7 and calls["n"] == 1, calls["n"])
    O.get = orig_get

    # ---- J8 接线（源码级）----
    bot_src = io.open(os.path.join(HERE, "bot.py"), encoding="utf-8").read()
    sen_src = io.open(os.path.join(HERE, "sender.py"), encoding="utf-8").read()
    main_src = io.open(os.path.join(HERE, "main.py"), encoding="utf-8").read()
    menu_src = io.open(os.path.join(HERE, "bot_menu.py"), encoding="utf-8").read()
    ck("J8 bot 引入 joinacc", "import joinacc" in bot_src)
    ck("J8 三处加群都走 await pick_for_join",
       bot_src.count("join_group_all_accounts(await joinacc.pick_for_join(accounts)") == 3,
       bot_src.count("join_group_all_accounts(await joinacc.pick_for_join(accounts)"))
    ck("J8 账号列表标明加群号", "🔑 只加群" in bot_src and "get_join_acc" in bot_src, "")
    ck("J8 开跑号数按剔后算", "npool = len(joinacc.send_pool(accounts))" in bot_src
       and "f\"🚀 群发中 · {npool} 号" in bot_src, "")
    ck("J8 sender 群发剔加群号", "from joinacc import send_pool" in sen_src
       and "accounts = send_pool(accounts)" in sen_src, "")
    ck("J8 main 启动+热替换都跑 boot", main_src.count("joinacc.boot(") == 2, "")
    ck("J8 账号菜单说明加群号", "只加群、不参与群发" in menu_src, "")

    # is_user_authorized() 在 telethon 是 async —— 全仓不许漏 await（filter.py 栽过的坑）
    import re as _re
    bad = []
    for fn in ("joinacc.py", "bot.py", "main.py", "accounts.py", "filter.py",
               "member_watch.py", "collector.py", "sender.py"):
        for i, ln in enumerate(io.open(os.path.join(HERE, fn), encoding="utf-8").read().split("\n"), 1):
            if "is_user_authorized()" in ln and "await" not in ln and "def " not in ln:
                bad.append(f"{fn}:{i}")
    ck("J8 全仓 is_user_authorized 都有 await", not bad, bad)

    print("\n" + ("JOINACC_OK" if not R else "FAILED: " + "; ".join(R)))
    sys.exit(1 if R else 0)


asyncio.run(main())
